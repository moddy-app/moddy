"""/ocr + the Transcribe menu on images (docs/OCR.md).

The service's error mapping, the card rendering and the size handling — the
gateway is faked, nothing leaves the process.
"""

from __future__ import annotations

import io
import types

import pytest
from PIL import Image

from gateway.errors import ModelRateLimitError, QuotaExceededError
from gateway.clients.vision import OcrResult as VisionOcr
from gateway.spec import QuotaTarget
from services.ocr_service import (
    CARD_TEXT_LIMIT, MAX_INLINE_BYTES, ErrorCode, OcrError, OcrService,
    find_image_attachment, shrink_for_vision, split_for_card,
)
from utils.ocr_views import build_ocr_message, render_error_card


class FakeAttachment:
    def __init__(self, data=b"\x89PNG", *, filename="a.png", content_type="image/png", size=None):
        self._data = data
        self.filename, self.content_type = filename, content_type
        self.size = len(data) if size is None else size

    async def read(self):
        return self._data


class FakeVision:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def document_text(self, image, **kw):
        self.calls.append((image, kw))
        if self.error:
            raise self.error
        return self.result


def _service(vision):
    gw = types.SimpleNamespace(vision=vision, google_vision_available=lambda: True)
    return OcrService(types.SimpleNamespace(gateway=gw, stats=None))


def test_find_image_attachment():
    msg = types.SimpleNamespace(attachments=[
        FakeAttachment(filename="v.ogg", content_type="audio/ogg"),
        FakeAttachment(filename="shot.webp", content_type=None),
    ])
    assert find_image_attachment(msg).filename == "shot.webp"
    assert find_image_attachment(types.SimpleNamespace(attachments=[])) is None


def test_preflight_errors():
    svc = _service(FakeVision())
    with pytest.raises(OcrError) as e:
        svc.preflight(None)
    assert e.value.code == ErrorCode.NO_IMAGE
    with pytest.raises(OcrError) as e:
        svc.preflight(FakeAttachment(size=30 * 1024 * 1024))
    assert e.value.code == ErrorCode.TOO_LARGE
    down = OcrService(types.SimpleNamespace(gateway=None))
    with pytest.raises(OcrError) as e:
        down.preflight(FakeAttachment())
    assert e.value.code == ErrorCode.UNAVAILABLE


async def test_extract_success_and_quota_targets():
    vision = FakeVision(VisionOcr(text="Bonjour\nle monde", language="fr"))
    result = await _service(vision).extract(FakeAttachment(), user_id=1, guild_id=2, source="slash")
    assert result.text == "Bonjour\nle monde" and result.language == "fr"
    quota = vision.calls[0][1]["quota"]
    assert QuotaTarget.user(1, "ocr_command") in quota
    assert QuotaTarget.guild(2, "ocr_command") in quota
    assert vision.calls[0][1]["call_type"] == "ocr_command"


@pytest.mark.parametrize("error,code", [
    (ModelRateLimitError("google_vision", "document_text", "rpmo", 1000, 3600.0),
     ErrorCode.MONTHLY_CAP),
    (QuotaExceededError(QuotaTarget.user(1, "ocr_command")), ErrorCode.QUOTA),
])
async def test_extract_maps_gateway_errors(error, code):
    with pytest.raises(OcrError) as e:
        await _service(FakeVision(error=error)).extract(
            FakeAttachment(), user_id=1, guild_id=None, source="slash")
    assert e.value.code == code


async def test_empty_text_is_an_error():
    with pytest.raises(OcrError) as e:
        await _service(FakeVision(VisionOcr(text="  "))).extract(
            FakeAttachment(), user_id=1, guild_id=None, source="context")
    assert e.value.code == ErrorCode.EMPTY


async def test_oversized_images_are_reencoded_before_the_call():
    noise = Image.effect_noise((2000, 1500), 120).convert("RGB")
    buf = io.BytesIO()
    noise.save(buf, "BMP")
    big = buf.getvalue()
    assert len(big) > MAX_INLINE_BYTES
    vision = FakeVision(VisionOcr(text="x"))
    await _service(vision).extract(FakeAttachment(big, filename="a.bmp", content_type="image/bmp"),
                                   user_id=1, guild_id=None, source="slash")
    sent, kw = vision.calls[0]
    assert len(sent) < MAX_INLINE_BYTES and kw["mime"] == "image/jpeg"


def test_shrink_rejects_garbage():
    with pytest.raises(OcrError) as e:
        shrink_for_vision(b"nope")
    assert e.value.code == ErrorCode.UNREADABLE


def test_long_text_is_split_with_a_file():
    text = ("ligne de texte\n" * 400).strip()
    out = split_for_card(text)
    assert out.truncated and len(out.text) <= CARD_TEXT_LIMIT + 2
    assert out.full_text == text
    view, files = build_ocr_message(out, locale="fr", requester_id=5)
    assert files and files[0].filename == "ocr.txt"


def test_card_escapes_code_fences():
    view, files = build_ocr_message(split_for_card("a ``` b"), locale="en-US")
    texts = [getattr(i, "content", "") for i in view.walk_children()]
    block = next(t for t in texts if t.startswith("```"))
    assert block.count("```") == 2 and not files


def test_error_card_falls_back_to_generic():
    assert render_error_card("does_not_exist", "fr") is not None
