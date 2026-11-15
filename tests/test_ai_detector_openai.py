"""AI detector × OpenAI Content Provenance check (SynthID + OpenAI C2PA).

Covers the pure parser (``aidetector/provenance.py``), the OpenAI adapter's
multipart call, the gateway client's accounting keys, the service decision
(when OpenAI is asked, caching, failures), the module's opt-in, the cards in
the five locales and the /config select.

    pip install -r requirements.txt -r requirements-dev.txt && pytest tests/test_ai_detector_openai.py
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from aidetector import analysis as an
from aidetector import manifest as mf
from aidetector import provenance as pv

LOCALES = ("fr", "en-US", "es-ES", "pt-BR", "de")

DETECTED_SYNTHID = {
    "object": "content_provenance_check", "created_at": 1785170052,
    "results": [
        {"type": "c2pa", "outcome": "not_detected", "validation_state": "not_present",
         "issuer": None, "model": None, "generated_at": None},
        {"type": "synthid", "outcome": "detected", "model": "gpt-image",
         "generated_at": "2026-07-27T18:34:12Z"},
    ],
}
DETECTED_C2PA = {
    "object": "content_provenance_check", "created_at": 1785170052,
    "results": [{"type": "c2pa", "outcome": "detected", "validation_state": "trusted",
                 "issuer": "OpenAI OpCo, LLC", "model": "gpt-image",
                 "generated_at": "2026-07-27T18:34:12Z"}],
}
NOTHING = {
    "object": "content_provenance_check", "created_at": 1785170052,
    "results": [{"type": "c2pa", "outcome": "not_detected", "validation_state": "not_present"},
                {"type": "synthid", "outcome": "not_detected", "model": None,
                 "generated_at": None}],
}


def png_bytes(size=(512, 512)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

class TestParser:
    def test_synthid_detection(self):
        found = pv.parse_response(DETECTED_SYNTHID)
        assert found.detected and found.signal == pv.SIGNAL_SYNTHID
        assert found.model == "gpt-image"
        assert found.generated_at == datetime(2026, 7, 27, 18, 34, 12, tzinfo=timezone.utc)
        assert found.c2pa_state == "not_present" and found.synthid_checked

    def test_c2pa_detection(self):
        found = pv.parse_response(DETECTED_C2PA)
        assert found.detected and found.signal == pv.SIGNAL_C2PA
        assert found.issuer == "OpenAI OpCo, LLC" and found.c2pa_state == "trusted"

    def test_synthid_preferred_and_merged(self):
        data = {"results": [
            {"type": "c2pa", "outcome": "detected", "validation_state": "valid",
             "issuer": "OpenAI OpCo, LLC", "model": None, "generated_at": "2026-01-02T00:00:00Z"},
            {"type": "synthid", "outcome": "detected", "model": "gpt-image", "generated_at": None},
        ]}
        found = pv.parse_response(data)
        assert found.signal == pv.SIGNAL_SYNTHID
        assert found.model == "gpt-image" and found.generated_at.year == 2026
        assert found.issuer == "OpenAI OpCo, LLC"

    def test_not_detected(self):
        found = pv.parse_response(NOTHING)
        assert found.status == pv.STATUS_NOT_DETECTED and not found.detected

    def test_invalid_manifest_never_counts(self):
        data = {"results": [{"type": "c2pa", "outcome": "detected", "validation_state": "invalid"}]}
        assert not pv.parse_response(data).detected

    @pytest.mark.parametrize("data", [None, [], "x", {}, {"results": "x"},
                                      {"results": [None, 3, {"type": "other"}]}])
    def test_garbage_reads_not_detected(self, data):
        assert pv.parse_response(data).status == pv.STATUS_NOT_DETECTED

    def test_markdown_is_stripped(self):
        data = {"results": [{"type": "synthid", "outcome": "detected",
                             "model": "**gpt**`[x](y)`@everyone", "generated_at": None}]}
        model = pv.parse_response(data).model
        assert not any(c in model for c in "*`[]@")


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #

class TestAnalysis:
    def test_provider_detection_is_a_verdict(self):
        result = an.Analysis(reason=an.REASON_NO_DATA, mime="image/png")
        result.with_provider(pv.parse_response(DETECTED_SYNTHID))
        assert result.kind == mf.KIND_AI_CONFIRMED and result.reason == an.REASON_VERDICT
        assert result.provider_checked

    def test_not_detected_keeps_the_reason(self):
        result = an.Analysis(reason=an.REASON_UNTRUSTED, mime="image/png")
        result.with_provider(pv.parse_response(NOTHING))
        assert result.kind is None and result.reason == an.REASON_UNTRUSTED
        assert result.provider_checked

    def test_unavailable_is_not_checked(self):
        result = an.Analysis(reason=an.REASON_NO_DATA).with_provider(pv.unavailable())
        assert result.kind is None and not result.provider_checked

    def test_priority(self):
        assert mf.KIND_PRIORITY[:2] == (mf.KIND_AI_FULL, mf.KIND_AI_CONFIRMED)


# --------------------------------------------------------------------------- #
# Gateway
# --------------------------------------------------------------------------- #

class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
        self.headers = {}
        self.url = "https://api.openai.com/v1/content_provenance_checks"

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class TestGateway:
    def _adapter(self, response):
        from gateway.adapters.openai import OpenAIAdapter
        adapter = OpenAIAdapter("sk-test")
        adapter._session = _FakeSession(response)
        return adapter

    def _spec(self, data=b"\x89PNG..."):
        from gateway.spec import CallSpec
        return CallSpec(provider="openai", operation="content_provenance",
                        model="content_provenance", payload={"mime": "image/png",
                                                             "filename": "a.png"},
                        quota=[], call_type="t", correlation_id="c", binary=data)

    async def test_multipart_upload(self):
        import aiohttp
        adapter = self._adapter(_FakeResponse(200, DETECTED_SYNTHID))
        result = await adapter.execute(self._spec())
        assert result.data == DETECTED_SYNTHID
        url, kwargs = adapter._session.calls[0]
        assert url == "https://api.openai.com/v1/content_provenance_checks"
        form = kwargs["data"]
        assert isinstance(form, aiohttp.FormData)
        field = form._fields[0]
        assert field[0]["name"] == "file" and field[1]["Content-Type"] == "image/png"
        assert "json" not in kwargs

    async def test_400_is_data_not_an_error(self):
        adapter = self._adapter(_FakeResponse(400, {"error": {"message": "bad"}}))
        result = await adapter.execute(self._spec())
        assert result.data == {"rejected": True, "results": []}

    async def test_404_raises(self):
        from gateway.errors import ProviderError
        adapter = self._adapter(_FakeResponse(404, {"error": "no access"}))
        with pytest.raises(ProviderError):
            await adapter.execute(self._spec())

    async def test_session_has_no_global_content_type(self):
        from gateway.adapters.openai import OpenAIAdapter
        adapter = OpenAIAdapter("sk-test")
        await adapter.start()
        try:
            assert "Content-Type" not in adapter._session.headers
        finally:
            await adapter.stop()

    async def test_client_accounting_keys(self):
        from gateway.clients.provenance import (
            MODEL_AUTOMATIC, MODEL_ON_DEMAND, ProvenanceClient,
        )
        executor = SimpleNamespace(execute=AsyncMock(return_value=NOTHING))
        client = ProvenanceClient(executor)
        await client.check(b"img", mime="image/png")
        await client.check(b"img", mime="image/png", automatic=True)
        specs = [c.args[0] for c in executor.execute.await_args_list]
        assert [s.model for s in specs] == [MODEL_ON_DEMAND, MODEL_AUTOMATIC]
        assert all(s.binary == b"img" and "img" not in json.dumps(s.payload) for s in specs)

    def test_rate_rules_declared(self):
        from gateway.config import GatewayConfig
        rules = GatewayConfig().model_rate_limits
        assert ("openai", "content_provenance") in rules
        assert ("openai", "content_provenance_auto") in rules


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

def _gateway(response=None, error=None):
    check = AsyncMock(return_value=response, side_effect=error)
    return SimpleNamespace(available=True, openai_available=lambda: True,
                           provenance=SimpleNamespace(check=check))


def _attachment(data, *, id=7, width=512, height=512):
    return SimpleNamespace(id=id, size=len(data), filename="x.png", content_type="image/png",
                           width=width, height=height, read=AsyncMock(return_value=data))


class TestService:
    def _service(self, gateway=None):
        from services.ai_detector_service import AiDetectorService
        return AiDetectorService(SimpleNamespace(redis=None, stats=None, gateway=gateway))

    async def test_not_asked_by_default(self):
        gw = _gateway(DETECTED_SYNTHID)
        service = self._service(gw)
        result = await service.analyze_attachment(_attachment(png_bytes()))
        assert result.kind is None and result.provider is None
        gw.provenance.check.assert_not_awaited()

    async def test_on_demand_detection(self):
        gw = _gateway(DETECTED_SYNTHID)
        service = self._service(gw)
        result = await service.analyze_attachment(_attachment(png_bytes()), provider_check=True,
                                                  metadata={"user_id": 1})
        assert result.kind == mf.KIND_AI_CONFIRMED
        kwargs = gw.provenance.check.await_args.kwargs
        assert kwargs["automatic"] is False and kwargs["mime"] == "image/png"
        # Cached: the details button does not ask again.
        assert service.cached(7) is result

    async def test_cached_without_provider_is_rechecked_on_demand(self):
        gw = _gateway(DETECTED_SYNTHID)
        service = self._service(gw)
        att = _attachment(png_bytes())
        first = await service.analyze_attachment(att)
        assert first.kind is None
        second = await service.analyze_attachment(att, provider_check=True)
        assert second.kind == mf.KIND_AI_CONFIRMED
        assert gw.provenance.check.await_count == 1

    async def test_automatic_skips_small_images(self):
        gw = _gateway(DETECTED_SYNTHID)
        service = self._service(gw)
        att = _attachment(png_bytes((64, 64)), width=64, height=64)
        result = await service.analyze_attachment(att, automatic=True, provider_check=True)
        assert result.kind is None
        gw.provenance.check.assert_not_awaited()

    async def test_automatic_uses_its_own_budget(self):
        gw = _gateway(DETECTED_SYNTHID)
        service = self._service(gw)
        await service.analyze_attachment(_attachment(png_bytes()), automatic=True,
                                         provider_check=True)
        assert gw.provenance.check.await_args.kwargs["automatic"] is True

    async def test_failure_reads_unavailable_and_never_raises(self):
        from gateway.errors import ModelRateLimitError
        gw = _gateway(error=ModelRateLimitError("openai", "content_provenance", "rpm", 10))
        service = self._service(gw)
        result = await service.analyze_attachment(_attachment(png_bytes()), provider_check=True)
        assert result.kind is None and result.provider.status == pv.STATUS_UNAVAILABLE

    async def test_rejected_file_reads_unavailable(self):
        service = self._service(_gateway({"rejected": True, "results": []}))
        result = await service.analyze_attachment(_attachment(png_bytes()), provider_check=True)
        assert result.provider.status == pv.STATUS_UNAVAILABLE

    async def test_no_gateway_means_no_attempt(self):
        service = self._service(None)
        result = await service.analyze_attachment(_attachment(png_bytes()), provider_check=True)
        assert result.provider is None

    async def test_local_verdict_skips_openai(self):
        from PIL.PngImagePlugin import PngInfo
        gw = _gateway(DETECTED_SYNTHID)
        service = self._service(gw)
        info = PngInfo()
        info.add_text("parameters", "a cat\nSteps: 20, Sampler: Euler, CFG scale: 7, Model: sdxl")
        buf = io.BytesIO()
        Image.new("RGB", (512, 512)).save(buf, "PNG", pnginfo=info)
        result = await service.analyze_attachment(_attachment(buf.getvalue()),
                                                  provider_check=True)
        assert result.kind == mf.KIND_UNSIGNED
        gw.provenance.check.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #

class TestModule:
    def test_opt_in_default_off(self):
        from modules.ai_detector import normalize_config
        assert normalize_config(None)["openai_check"] is False
        assert normalize_config({"openai_check": "yes"})["openai_check"] is False
        assert normalize_config({"openai_check": True})["openai_check"] is True

    async def test_validate(self):
        from modules.ai_detector import AiDetectorModule
        m = AiDetectorModule(None, 1)
        base = {"categories": ["ai_full"], "ignored_channel_ids": []}
        assert (await m.validate_config({**base, "openai_check": True}))[0]
        assert not (await m.validate_config({**base, "openai_check": "on"}))[0]

    def test_confirmed_follows_the_ai_full_category(self):
        from modules.ai_detector import KIND_CATEGORY
        assert KIND_CATEGORY[mf.KIND_AI_CONFIRMED] == "ai_full"

    @pytest.mark.parametrize("opted_in,categories,expected", [
        (False, ["ai_full"], False),
        (True, ["ai_full"], True),
        (True, ["photo"], False),  # ai_confirmed switched off: no point asking
    ])
    async def test_module_passes_provider_check(self, opted_in, categories, expected):
        from modules.ai_detector import AiDetectorModule
        service = SimpleNamespace(candidate_images=lambda m: [object()],
                                  analyze_message=AsyncMock(return_value=None))
        bot = SimpleNamespace(ai_detector=service)
        module = AiDetectorModule(bot, 1)
        await module.load_config({"categories": categories, "openai_check": opted_in})
        channel = SimpleNamespace(id=10, parent_id=None)
        message = SimpleNamespace(author=SimpleNamespace(bot=False),
                                  guild=SimpleNamespace(id=1, me=None), channel=channel)
        await module.on_message(message)
        assert service.analyze_message.await_args.kwargs["provider_check"] is expected


# --------------------------------------------------------------------------- #
# Cards + i18n
# --------------------------------------------------------------------------- #

def _confirmed(data=DETECTED_SYNTHID) -> an.Analysis:
    return an.Analysis(reason=an.REASON_NO_DATA, mime="image/png").with_provider(
        pv.parse_response(data))


def _texts(view) -> str:
    out = []

    def walk(item):
        content = getattr(item, "content", None)
        if isinstance(content, str):
            out.append(content)
        for child in getattr(item, "children", []) or []:
            walk(child)
    for item in view.children:
        walk(item)
    return "\n".join(out)


class TestCards:
    @pytest.mark.parametrize("locale", LOCALES)
    def test_verdict_texts(self, locale):
        from utils.ai_detector_views import verdict_texts
        title, body, footer = verdict_texts(_confirmed(), locale)
        assert "gpt-image" in body and "OpenAI" in body and "<t:" in body
        assert "SynthID" in footer and "[" not in title
        _, _, c2pa_footer = verdict_texts(_confirmed(DETECTED_C2PA), locale)
        assert "C2PA" in c2pa_footer

    def test_no_date_no_model(self):
        from utils.ai_detector_views import verdict_texts
        data = {"results": [{"type": "synthid", "outcome": "detected",
                             "model": None, "generated_at": None}]}
        _, body, _ = verdict_texts(_confirmed(data), "en-US")
        assert body == "This image was generated by **OpenAI**."

    @pytest.mark.parametrize("locale", LOCALES)
    def test_details_and_check_views(self, locale):
        from utils.ai_detector_views import build_check_view, build_details_view
        text = _texts(build_details_view(_confirmed(), locale))
        assert "SynthID" in text and "gpt-image" in text and "OpenAI" in text
        none = an.Analysis(reason=an.REASON_NO_DATA, mime="image/png").with_provider(
            pv.parse_response(NOTHING))
        from utils.i18n import t
        check = _texts(build_check_view(none, locale))
        assert t("ai_detector.reasons.provider_not_detected", locale=locale) in check
        failed = an.Analysis(reason=an.REASON_NO_DATA).with_provider(pv.unavailable())
        assert t("ai_detector.reasons.provider_unavailable", locale=locale) in \
            _texts(build_check_view(failed, locale))

    def test_no_openai_line_when_not_attempted(self):
        from utils.ai_detector_views import build_check_view
        text = _texts(build_check_view(an.Analysis(reason=an.REASON_NO_DATA), "en-US"))
        assert "OpenAI's verification service found" not in text

    @pytest.mark.parametrize("locale", LOCALES)
    def test_i18n_keys_present(self, locale):
        data = json.load(open(f"locales/{locale}.json", encoding="utf-8"))
        verdict = data["ai_detector"]["verdicts"]["ai_confirmed"]
        assert set(verdict) == {"title", "body", "body_no_date", "footer_synthid", "footer_c2pa"}
        cfg = data["modules"]["ai_detector"]["config"]["openai"]
        for option in ("off", "on"):
            assert len(cfg[option]["label"]) <= 100 and len(cfg[option]["description"]) <= 100


class TestConfigPanel:
    def _select(self, view):
        from modules.configs.ai_detector_config import _CID_OPENAI
        found = []

        def walk(item):
            if getattr(item, "custom_id", None) == _CID_OPENAI:
                found.append(item)
            for child in getattr(item, "children", []) or []:
                walk(child)
        for item in view.children:
            walk(item)
        return found[0]

    @pytest.mark.parametrize("stored,default", [(None, "off"), ({"openai_check": True}, "on")])
    def test_select_shows_the_stored_choice(self, stored, default):
        from modules.configs.ai_detector_config import AiDetectorConfigView
        view = AiDetectorConfigView(None, 1, 2, "fr", current_config=stored)
        select = self._select(view)
        assert [o.value for o in select.options if o.default] == [default]
