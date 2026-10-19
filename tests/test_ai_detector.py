"""AI detector: C2PA verification, classification, PNG metadata, cards, module.

The C2PA cases use *real* signed images, produced at test time with
c2pa-python's Builder and the test-only certificate chain in
``tests/data/c2pa/`` (see its README). With the matching test root configured
as the trust list they validate as ``Trusted`` — exactly the production path,
only the anchors differ. Real-world images dropped in ``tests/data/c2pa/real/``
are checked against a snapshot of the official trust list.

    pip install -r requirements.txt -r requirements-dev.txt && pytest tests/test_ai_detector.py
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import zlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from aidetector import analysis as an
from aidetector import formats, manifest as mf, png_text, verify

DATA = Path(__file__).parent / "data" / "c2pa"
IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"

try:
    import c2pa  # noqa: F401
    HAS_C2PA = True
except Exception:  # noqa: BLE001
    HAS_C2PA = False

needs_c2pa = pytest.mark.skipif(not HAS_C2PA, reason="c2pa-python not installed")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def plain_image(fmt: str = "JPEG", color=(20, 90, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format=fmt)
    return buf.getvalue()


MIMES = {"JPEG": formats.MIME_JPEG, "PNG": formats.MIME_PNG, "WEBP": formats.MIME_WEBP}


def sign(raw: bytes, mime: str, actions, *, generator="Test Generator",
         extra_assertions=(), parent: bytes = None) -> bytes:
    """Sign ``raw`` with the test chain; ``parent`` becomes a parentOf ingredient."""
    import c2pa
    certs = (DATA / "es256_certs.pem").read_bytes()
    key = (DATA / "es256_private.key").read_bytes()
    signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(
        alg=b"es256", sign_cert=certs, private_key=key, ta_url=None))
    if parent is not None:
        actions = [{"action": "c2pa.opened", "parameters": {"ingredientIds": ["parent"]}},
                   *actions]
    definition = {
        "claim_generator_info": [{"name": generator, "version": "1.0"}],
        "format": mime,
        "title": "test",
        "assertions": [{"label": "c2pa.actions", "data": {"actions": list(actions)}},
                       *extra_assertions],
    }
    builder = c2pa.Builder(definition)
    if parent is not None:
        builder.add_ingredient({"title": "parent", "relationship": "parentOf", "label": "parent"},
                               mime, io.BytesIO(parent))
    out = io.BytesIO()
    builder.sign(signer, mime, io.BytesIO(raw), out)
    return out.getvalue()


def trusted_settings() -> str:
    return verify.build_settings((DATA / "test_anchors.pem").read_text())


def official_settings() -> str:
    return verify.build_settings((DATA / "C2PA-TRUST-LIST.pem").read_text())


AI_CREATED = [{"action": "c2pa.created", "when": "2026-10-01T12:00:00Z",
               "softwareAgent": {"name": "ChatGPT"},
               "digitalSourceType": IPTC + "trainedAlgorithmicMedia"}]
CAPTURE = [{"action": "c2pa.created", "when": "2026-09-01T08:30:00Z",
            "digitalSourceType": IPTC + "digitalCapture"}]
EXIF = {"label": "stds.exif", "data": {
    "@context": {"exif": "http://ns.adobe.com/exif/1.0/", "tiff": "http://ns.adobe.com/tiff/1.0/"},
    "tiff:Make": "Leica Camera AG", "tiff:Model": "LEICA M11-P",
    "exif:GPSLatitude": "48,51.5000N", "exif:GPSLongitude": "2,17.6000E",
    "exif:BodySerialNumber": "SN-998877", "tiff:Artist": "Jeanne Exemple"}}
AUTHOR = {"label": "stds.schema-org.CreativeWork", "data": {
    "@context": "https://schema.org", "@type": "CreativeWork",
    "author": [{"@type": "Person", "name": "Jeanne Exemple"}]}}
#: Strings from the manifests above that must never reach a card.
PRIVATE_STRINGS = ("48,51", "GPS", "SN-998877", "Jeanne", "Exemple")


# --------------------------------------------------------------------------- #
# formats
# --------------------------------------------------------------------------- #

class TestFormats:
    def test_sniff_by_magic_bytes(self):
        assert formats.sniff_mime(plain_image("PNG")) == formats.MIME_PNG
        assert formats.sniff_mime(plain_image("JPEG")) == formats.MIME_JPEG
        assert formats.sniff_mime(plain_image("WEBP")) == formats.MIME_WEBP
        assert formats.sniff_mime(plain_image("GIF")) is None
        assert formats.sniff_mime(b"") is None

    def test_declared_mime(self):
        assert formats.declared_mime("a.PNG", None) == formats.MIME_PNG
        assert formats.declared_mime("x", "image/jpeg; charset=binary") == formats.MIME_JPEG
        assert formats.declared_mime("x.jpg", "image/jpg") == formats.MIME_JPEG
        assert formats.declared_mime("anim.gif", "image/gif") is None
        assert formats.declared_mime("voice.ogg", "audio/ogg") is None

    def test_c2pa_marker(self):
        assert not formats.may_contain_c2pa(plain_image())
        assert formats.may_contain_c2pa(b"....jumb....c2pa....")


# --------------------------------------------------------------------------- #
# PNG generation metadata (template 5)
# --------------------------------------------------------------------------- #

def png_with_text(**chunks) -> bytes:
    info = PngInfo()
    for key, (value, compressed) in chunks.items():
        if compressed:
            info.add_itxt(key, value, zip=True)
        else:
            info.add_text(key, value)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


A1111 = ("a castle on a hill, secret prompt words\nNegative prompt: blurry\n"
         "Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 12345, "
         "Size: 832x1216, Model hash: abcdef, Model: juggernautXL_v9")


class TestPngText:
    def test_stable_diffusion_parameters(self):
        meta = png_text.detect_generation_metadata(png_with_text(parameters=(A1111, False)))
        assert meta.tool == png_text.TOOL_STABLE_DIFFUSION
        assert meta.keywords == ["parameters"]
        assert meta.settings == {"Model": "juggernautXL_v9", "Sampler": "DPM++ 2M Karras",
                                 "Steps": "28", "CFG scale": "7", "Size": "832x1216"}
        assert "castle" not in json.dumps(meta.settings)  # the prompt never leaves

    def test_comfyui_prompt_and_workflow_compressed(self):
        graph = {"4": {"class_type": "CheckpointLoaderSimple",
                       "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
                 "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "private prompt"}}}
        data = png_with_text(prompt=(json.dumps(graph), True), workflow=("{}", False))
        meta = png_text.detect_generation_metadata(data)
        assert meta.tool == png_text.TOOL_COMFYUI
        assert meta.keywords == ["prompt", "workflow"]
        assert meta.settings == {"Model": "sd_xl_base_1.0.safetensors"}

    def test_comfyui_wins_over_parameters(self):
        data = png_with_text(parameters=(A1111, False), workflow=("{}", False))
        assert png_text.detect_generation_metadata(data).tool == png_text.TOOL_COMFYUI

    def test_ztxt_chunk(self):
        body = b"parameters\x00\x00" + zlib.compress(A1111.encode())
        chunk = (len(body).to_bytes(4, "big") + b"zTXt" + body
                 + zlib.crc32(b"zTXt" + body).to_bytes(4, "big"))
        png = plain_image("PNG")
        data = png[:33] + chunk + png[33:]  # right after IHDR
        assert png_text.detect_generation_metadata(data).tool == png_text.TOOL_STABLE_DIFFUSION

    def test_unrelated_text_and_garbage(self):
        assert png_text.detect_generation_metadata(
            png_with_text(Software=("GIMP 2.10", False))) is None
        assert png_text.detect_generation_metadata(plain_image("JPEG")) is None
        truncated = png_with_text(parameters=(A1111, False))[:40]
        assert png_text.detect_generation_metadata(truncated) is None
        assert png_text.detect_generation_metadata(formats.PNG_SIGNATURE + b"\xff" * 64) is None

    def test_backticks_cannot_break_the_card(self):
        data = png_with_text(parameters=("x\nSteps: 20, Model: evil`model", False))
        assert "`" not in png_text.detect_generation_metadata(data).settings["Model"]


# --------------------------------------------------------------------------- #
# Manifest classification (synthetic stores)
# --------------------------------------------------------------------------- #

def store(manifests, active="m0", state="Trusted"):
    return {"active_manifest": active, "manifests": manifests, "validation_state": state}


def manifest(actions, *, issuer="OpenAI", generator="ChatGPT", ingredients=(), assertions=()):
    return {
        "claim_generator_info": [{"name": generator, "version": "1"}],
        "signature_info": {"issuer": issuer, "alg": "Es256", "common_name": "Signer Person",
                           "time": "2026-10-02T10:00:00+00:00"},
        "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": actions}}, *assertions],
        "ingredients": list(ingredients),
    }


class TestClassification:
    def test_ai_full(self):
        s = mf.summarize(store({"m0": manifest(AI_CREATED)}))
        v = s.verdict
        assert v.kind == mf.KIND_AI_FULL
        assert (v.tool, v.issuer) == ("ChatGPT", "OpenAI")
        assert v.when == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)

    def test_ai_full_falls_back_to_generator_and_signature_time(self):
        actions = [{"action": "c2pa.created", "digitalSourceType": "trainedAlgorithmicMedia"}]
        v = mf.summarize(store({"m0": manifest(actions, generator="Adobe Firefly",
                                               issuer="Adobe Inc.")})).verdict
        assert (v.kind, v.tool, v.issuer) == (mf.KIND_AI_FULL, "Adobe Firefly", "Adobe Inc.")
        assert v.when == datetime(2026, 10, 2, 10, tzinfo=timezone.utc)

    def test_ai_full_survives_later_non_ai_edits(self):
        edited = manifest([{"action": "c2pa.cropped"}], issuer="Adobe Inc.", generator="Photoshop",
                          ingredients=[{"relationship": "parentOf", "active_manifest": "m1"}])
        v = mf.summarize(store({"m0": edited, "m1": manifest(AI_CREATED)})).verdict
        assert v.kind == mf.KIND_AI_FULL
        assert (v.tool, v.issuer) == ("ChatGPT", "OpenAI")  # the creator, not the editor

    def test_partial_composite_on_photo(self):
        fill = manifest([{"action": "c2pa.edited", "softwareAgent": {"name": "Adobe Firefly"},
                          "digitalSourceType": IPTC + "compositeWithTrainedAlgorithmicMedia"}],
                        issuer="Adobe Inc.", generator="Adobe Photoshop",
                        ingredients=[{"relationship": "parentOf", "active_manifest": "m1"}])
        camera = manifest(CAPTURE, issuer="Leica Camera AG", generator="Leica")
        v = mf.summarize(store({"m0": fill, "m1": camera})).verdict
        assert (v.kind, v.tool, v.issuer) == (mf.KIND_AI_PARTIAL, "Adobe Firefly", "Adobe Inc.")

    def test_partial_trained_on_an_edit(self):
        actions = [{"action": "c2pa.opened"},
                   {"action": "c2pa.placed", "digitalSourceType": IPTC + "trainedAlgorithmicMedia"}]
        assert mf.summarize(store({"m0": manifest(actions)})).verdict.kind == mf.KIND_AI_PARTIAL

    def test_partial_ai_creation_mixed_with_capture(self):
        comp = manifest(AI_CREATED, ingredients=[{"relationship": "componentOf", "active_manifest": "m1"}])
        v = mf.summarize(store({"m0": comp, "m1": manifest(CAPTURE, issuer="Sony")})).verdict
        assert v.kind == mf.KIND_AI_PARTIAL

    def test_input_to_ingredient_is_not_history(self):
        gen = manifest(AI_CREATED, ingredients=[{"relationship": "inputTo", "active_manifest": "m1"}])
        s = mf.summarize(store({"m0": gen, "m1": manifest(CAPTURE, issuer="Sony")}))
        assert s.verdict.kind == mf.KIND_AI_FULL
        assert s.ingredient_count == 1 and len(s.manifests) == 1

    def test_photo(self):
        s = mf.summarize(store({"m0": manifest(CAPTURE, issuer="Leica Camera AG",
                                               generator="Leica", assertions=[EXIF, AUTHOR])}))
        v = s.verdict
        assert (v.kind, v.device, v.issuer) == (mf.KIND_PHOTO, "LEICA M11-P", "Leica Camera AG")
        assert v.when == datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)

    def test_computational_capture_counts_as_photo(self):
        actions = [{"action": "c2pa.created", "digitalSourceType": IPTC + "computationalCapture"}]
        exif = {"label": "stds.exif", "data": {"exif:Make": "Google", "exif:Model": "Pixel 10 Pro"}}
        v = mf.summarize(store({"m0": manifest(actions, issuer="Google LLC", assertions=[exif])})).verdict
        assert (v.kind, v.device) == (mf.KIND_PHOTO, "Google Pixel 10 Pro")

    def test_photo_edited(self):
        edit = manifest([{"action": "c2pa.opened"}, {"action": "c2pa.cropped"},
                         {"action": "c2pa.color_adjustments", "softwareAgent": "Lightroom Classic 14"},
                         {"action": "c2pa.cropped"}],
                        issuer="Adobe Inc.", generator="Adobe Lightroom",
                        ingredients=[{"relationship": "parentOf", "active_manifest": "m1"}])
        cam = manifest(CAPTURE, issuer="Leica Camera AG", assertions=[EXIF])
        v = mf.summarize(store({"m0": edit, "m1": cam})).verdict
        assert v.kind == mf.KIND_PHOTO_EDITED
        assert v.edits == ["c2pa.cropped", "c2pa.color_adjustments"]
        assert v.tool == "Adobe Lightroom"  # first edit has no softwareAgent: its generator
        assert (v.device, v.issuer) == ("LEICA M11-P", "Leica Camera AG")

    def test_no_rule_applies(self):
        actions = [{"action": "c2pa.created", "digitalSourceType": IPTC + "digitalCreation"}]
        assert mf.summarize(store({"m0": manifest(actions)})).verdict is None
        assert mf.summarize({"manifests": {}}).verdict is None
        assert mf.summarize({"active_manifest": "x", "manifests": {"x": "bad"}}).verdict is None

    def test_legacy_v1_shapes(self):
        legacy = {"claim_generator": "Adobe_Photoshop/25.0 adobe_c2pa/0.7.6",
                  "signature_info": {"issuer": "Adobe Inc."},
                  "assertions": [{"label": "c2pa.actions", "data": {"actions": [
                      {"action": "c2pa.created", "softwareAgent": "Adobe Firefly 2",
                       "digitalSourceType": IPTC + "trainedAlgorithmicMedia"}]}}]}
        s = mf.summarize(store({"m0": legacy}))
        assert s.active.generator == "Adobe Photoshop"
        assert s.verdict.tool == "Adobe Firefly 2"

    def test_cycles_and_depth_are_bounded(self):
        a = manifest(AI_CREATED, ingredients=[{"relationship": "parentOf", "active_manifest": "m1"}])
        b = manifest([], ingredients=[{"relationship": "parentOf", "active_manifest": "m0"}])
        assert len(mf.summarize(store({"m0": a, "m1": b})).manifests) == 2

    def test_privacy_whitelist(self):
        s = mf.summarize(store({"m0": manifest(CAPTURE, issuer="Leica Camera AG",
                                               assertions=[EXIF, AUTHOR])}))
        dumped = repr(s)
        for secret in PRIVATE_STRINGS + ("Signer Person",):
            assert secret not in dumped

    def test_display_values_are_sanitised(self):
        actions = [{"action": "c2pa.created", "softwareAgent": {"name": "**Evil** `tool` <@1>"},
                    "digitalSourceType": "trainedAlgorithmicMedia"}]
        tool = mf.summarize(store({"m0": manifest(actions)})).verdict.tool
        assert tool == "Evil tool 1"


# --------------------------------------------------------------------------- #
# Real signed files (c2pa-python)
# --------------------------------------------------------------------------- #

@needs_c2pa
class TestVerification:
    @pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
    def test_trusted_ai_image_every_format(self, fmt):
        mime = MIMES[fmt]
        signed = sign(plain_image(fmt), mime, AI_CREATED, generator="ChatGPT")
        result = an.analyze(signed, trusted_settings())
        assert result.mime == mime
        assert result.validation_state == verify.STATE_TRUSTED
        assert result.reason == an.REASON_VERDICT
        assert result.kind == mf.KIND_AI_FULL
        assert result.verdict.tool == "ChatGPT"
        assert result.verdict.issuer == "C2PA Test Signing Cert"

    def test_production_trust_list_does_not_trust_the_test_signer(self):
        signed = sign(plain_image(), formats.MIME_JPEG, AI_CREATED)
        result = an.analyze(signed, official_settings())
        assert result.has_c2pa and result.validation_state == "Valid"
        assert result.reason == an.REASON_UNTRUSTED
        assert result.kind is None  # never act on a non-Trusted store

    def test_tampered_file_is_not_trusted(self):
        signed = bytearray(sign(plain_image(), formats.MIME_JPEG, AI_CREATED))
        signed[-10] ^= 0xFF  # flip pixels after the manifest (hashed region)
        result = an.analyze(bytes(signed), trusted_settings())
        assert result.kind is None
        assert result.reason in (an.REASON_INVALID, an.REASON_ERROR)

    def test_no_manifest(self):
        result = an.analyze(plain_image(), trusted_settings())
        assert (result.reason, result.kind, result.has_c2pa) == (an.REASON_NO_DATA, None, False)

    def test_unsupported_format(self):
        assert an.analyze(plain_image("GIF"), trusted_settings()).reason == an.REASON_UNSUPPORTED

    def test_trust_list_not_loaded_yet(self):
        signed = sign(plain_image(), formats.MIME_JPEG, AI_CREATED)
        assert an.analyze(signed, None).reason == an.REASON_TRUST_UNAVAILABLE

    def test_photo_then_retouch_chain(self):
        cam = sign(plain_image(), formats.MIME_JPEG, CAPTURE, generator="Leica FOTOS",
                   extra_assertions=[EXIF, AUTHOR])
        edited = sign(cam, formats.MIME_JPEG,
                      [{"action": "c2pa.cropped"},
                       {"action": "c2pa.color_adjustments", "softwareAgent": {"name": "Lightroom"}}],
                      generator="Adobe Lightroom", parent=cam)
        assert an.analyze(cam, trusted_settings()).kind == mf.KIND_PHOTO
        result = an.analyze(edited, trusted_settings())
        assert result.kind == mf.KIND_PHOTO_EDITED
        assert result.verdict.device == "LEICA M11-P"
        assert result.verdict.edits == ["c2pa.cropped", "c2pa.color_adjustments"]
        for secret in PRIVATE_STRINGS:
            assert secret not in repr(result.summary)

    def test_generative_fill_on_photo(self):
        cam = sign(plain_image(), formats.MIME_JPEG, CAPTURE)
        filled = sign(cam, formats.MIME_JPEG,
                      [{"action": "c2pa.edited", "softwareAgent": {"name": "Adobe Firefly"},
                        "digitalSourceType": IPTC + "compositeWithTrainedAlgorithmicMedia"}],
                      parent=cam)
        result = an.analyze(filled, trusted_settings())
        assert (result.kind, result.verdict.tool) == (mf.KIND_AI_PARTIAL, "Adobe Firefly")

    def test_untrusted_c2pa_png_still_reports_unsigned_parameters(self):
        png = png_with_text(parameters=(A1111, False))
        signed = sign(png, formats.MIME_PNG, AI_CREATED)
        result = an.analyze(signed, official_settings())
        assert result.reason == an.REASON_VERDICT
        assert result.kind == mf.KIND_UNSIGNED  # C2PA not trusted -> template 5

    def test_settings_keep_the_network_off_and_list_the_ekus(self):
        settings = json.loads(trusted_settings())
        assert settings["verify"]["remote_manifest_fetch"] is False
        assert settings["verify"]["ocsp_fetch"] is False
        assert settings["core"]["allowed_network_hosts"] == []
        assert settings["trust"]["trust_config"].split() == [
            "1.3.6.1.4.1.62558.2.1", "1.3.6.1.5.5.7.3.36",
            "1.3.6.1.5.5.7.3.4", "1.3.6.1.5.5.7.3.8"]

    def test_garbage_never_raises(self):
        junk = formats.PNG_SIGNATURE + b"caBX" + b"\x00" * 200
        result = an.analyze(junk, trusted_settings())
        assert result.kind is None


def _real_images():
    expected = json.loads((DATA / "real" / "expected.json").read_text())["files"]
    files = sorted(p for p in (DATA / "real").iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    return [(p, expected.get(p.name, {})) for p in files]


@needs_c2pa
@pytest.mark.parametrize("path,expected", _real_images() or [pytest.param(
    None, None, marks=pytest.mark.skip(reason="no real C2PA image in tests/data/c2pa/real/ yet"))])
def test_real_world_image(path, expected):
    """Real files (OpenAI, Adobe, Leica…) verified offline against the official
    trust list snapshot. Expected verdicts live in real/expected.json."""
    result = an.analyze(path.read_bytes(), official_settings())
    kind = expected.get("kind") if isinstance(expected, dict) else expected
    assert result.kind == kind, (result.reason, result.validation_state)
    if isinstance(expected, dict):
        if expected.get("issuer"):
            assert result.verdict.issuer == expected["issuer"]
        if expected.get("tool"):
            assert result.verdict.tool == expected["tool"]
    if result.summary is not None:
        dumped = repr(result.summary)
        assert "GPS" not in dumped


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

def verdict_result(kind, **fields) -> an.Analysis:
    if kind == mf.KIND_UNSIGNED:
        return an.Analysis(reason=an.REASON_VERDICT, mime=formats.MIME_PNG,
                           generation=png_text.GenerationMetadata(tool="ComfyUI",
                                                                  keywords=["prompt"]))
    verdict = mf.C2paVerdict(kind=kind, **fields)
    summary = mf.ManifestSummary(
        validation_state="Trusted",
        manifests=[mf.ManifestFacts(label="m0", issuer=fields.get("issuer"), signature_alg="Es256",
                                    generator=fields.get("tool"), device=fields.get("device"))],
        actions=[mf.ActionRecord(action="c2pa.created", source_type="trainedAlgorithmicMedia",
                                 software=fields.get("tool"))],
        verdict=verdict)
    return an.Analysis(reason=an.REASON_VERDICT, mime=formats.MIME_JPEG, has_c2pa=True,
                       validation_state="Trusted", summary=summary)


WHEN = datetime.fromtimestamp(1792371800, tz=timezone.utc)


class TestCards:
    def test_ai_full_matches_template_1(self):
        from utils.ai_detector_views import verdict_texts
        title, body, footer = verdict_texts(
            verdict_result(mf.KIND_AI_FULL, tool="ChatGPT", issuer="OpenAI", when=WHEN), "fr")
        assert title == "Contenu généré par intelligence artificielle"
        assert body == ("Cette image a été entièrement générée par **ChatGPT** (OpenAI) "
                        "le **<t:1792371800:f>**.")
        assert footer.startswith("Cette information provient de la signature C2PA d'OpenAI "
                                 "intégrée au fichier")
        assert footer.endswith("[**En savoir plus**](https://moddy.app/ai-detector)")

    def test_french_elision_only_before_vowels(self):
        from utils.ai_detector_views import of_issuer
        assert of_issuer("OpenAI", "fr") == "d'OpenAI"
        assert of_issuer("Google LLC", "fr") == "de Google LLC"
        assert of_issuer("OpenAI", "en-US") == "OpenAI"

    def test_ai_partial_matches_template_2(self):
        from utils.ai_detector_views import verdict_texts
        _, body, footer = verdict_texts(
            verdict_result(mf.KIND_AI_PARTIAL, tool="Adobe Firefly", issuer="Adobe Inc."), "fr")
        assert body == ("Cette image a été modifiée à l'aide d'outils d'IA générative via "
                        "**Adobe Firefly** (Adobe Inc.).")
        assert "sans toujours préciser la zone concernée" in footer

    def test_photo_matches_template_3(self):
        from utils.ai_detector_views import verdict_texts
        _, body, footer = verdict_texts(verdict_result(
            mf.KIND_PHOTO, issuer="Leica Camera AG", device="LEICA M11-P", when=WHEN), "fr")
        assert body == ("Cette photographie a été prise avec un **LEICA M11-P** le "
                        "**<t:1792371800:f>** et n'a fait l'objet d'aucune modification par IA.")
        assert footer.startswith("La signature C2PA apposée par Leica Camera AG lors de la prise de vue")

    def test_photo_edited_matches_template_4(self):
        from utils.ai_detector_views import verdict_texts
        _, body, _ = verdict_texts(verdict_result(
            mf.KIND_PHOTO_EDITED, issuer="Leica Camera AG", device="LEICA M11-P",
            tool="Adobe Lightroom", edits=["c2pa.cropped", "c2pa.color_adjustments"]), "fr")
        assert body == ("Cette photographie a été prise avec un **LEICA M11-P**, puis retouchée "
                        "avec **Adobe Lightroom** (recadrage, couleurs), sans recours à l'IA générative.")

    def test_unsigned_matches_template_5(self):
        from utils.ai_detector_views import verdict_texts
        title, body, footer = verdict_texts(verdict_result(mf.KIND_UNSIGNED), "fr")
        assert title == "Contenu probablement généré par intelligence artificielle"
        assert body == "Cette image contient des paramètres de génération propres à **ComfyUI**."
        assert "sans valeur de preuve" in footer

    def test_fallbacks_without_date_tool_or_device(self):
        from utils.ai_detector_views import verdict_texts
        _, body, _ = verdict_texts(verdict_result(mf.KIND_AI_FULL, issuer="OpenAI"), "fr")
        assert body == "Cette image a été entièrement générée par **OpenAI**."
        _, body, _ = verdict_texts(verdict_result(
            mf.KIND_PHOTO_EDITED, issuer="Sony", edits=["c2pa.cropped"]), "fr")
        assert "un **appareil photo**, puis retouchée (recadrage)" in body

    def test_card_layout_matches_the_mockup(self):
        from discord import ui
        from utils.ai_detector_views import ACCENT, build_verdict_view
        view = build_verdict_view(
            verdict_result(mf.KIND_AI_FULL, tool="ChatGPT", issuer="OpenAI", when=WHEN), "fr",
            button=(111, 222, 333))
        container, row = view.children
        assert isinstance(container, ui.Container) and container.accent_colour.value == ACCENT == 27134
        title, separator, footer = container.children
        assert isinstance(title, ui.TextDisplay) and title.content.startswith(
            "### <:ai_shield:1561544570809098342> **Contenu généré par intelligence artificielle**\n")
        assert isinstance(separator, ui.Separator) and separator.visible
        assert separator.spacing == discord.SeparatorSpacing.small
        assert isinstance(footer, ui.TextDisplay) and footer.content.startswith("-# ")
        assert isinstance(row, ui.ActionRow)  # the button lives OUTSIDE the container
        button = row.children[0].item
        assert button.custom_id == "moddy:aidet:info:111:222:333"
        assert button.label == "Plus d'informations"

    def test_no_card_without_verdict(self):
        from utils.ai_detector_views import build_verdict_view
        assert build_verdict_view(an.Analysis(reason=an.REASON_NO_DATA), "fr") is None

    @pytest.mark.parametrize("kind", [mf.KIND_AI_FULL, mf.KIND_PHOTO_EDITED, mf.KIND_UNSIGNED])
    @pytest.mark.parametrize("locale", ["fr", "en-US", "es-ES", "pt-BR", "de"])
    def test_details_render_in_every_language(self, kind, locale):
        from utils.ai_detector_views import build_check_view, build_details_view
        result = verdict_result(kind, tool="Lightroom", issuer="Leica Camera AG",
                                device="LEICA M11-P", when=WHEN, edits=["c2pa.cropped"])
        for view in (build_details_view(result, locale), build_check_view(result, locale)):
            text = json.dumps(view.to_components(), ensure_ascii=False)
            assert "[ai_detector." not in text
            assert "https://moddy.app/ai-detector" in text

    def test_details_never_show_private_data(self):
        from utils.ai_detector_views import build_details_view
        manifests = {"m0": manifest(CAPTURE, issuer="Leica Camera AG", assertions=[EXIF, AUTHOR])}
        result = an.Analysis(reason=an.REASON_VERDICT, has_c2pa=True, validation_state="Trusted",
                             summary=mf.summarize(store(manifests)))
        text = json.dumps(build_details_view(result, "fr").to_components(), ensure_ascii=False)
        assert "LEICA M11-P" in text
        for secret in PRIVATE_STRINGS + ("Signer Person",):
            assert secret not in text

    @pytest.mark.parametrize("reason", [an.REASON_NO_DATA, an.REASON_UNTRUSTED, an.REASON_INVALID,
                                        an.REASON_UNCLASSIFIED, an.REASON_UNSUPPORTED])
    def test_check_view_explains_missing_verdicts(self, reason):
        from utils.ai_detector_views import build_check_view
        text = json.dumps(build_check_view(an.Analysis(reason=reason), "fr").to_components(),
                          ensure_ascii=False)
        assert "Aucune information vérifiable" in text and "[ai_detector." not in text

    @pytest.mark.parametrize("reason", ["gone", "too_large", "busy", "untrusted", "nonsense"])
    def test_error_cards_resolve(self, reason):
        from utils.ai_detector_views import render_error_card
        text = json.dumps(render_error_card(reason, "en-US").to_components())
        assert "[ai_detector." not in text


def _keys(node, prefix=""):
    if isinstance(node, dict):
        out = set()
        for key, value in node.items():
            out |= _keys(value, f"{prefix}.{key}" if prefix else key)
        return out
    return {prefix}


def _placeholders(text):
    return set(re.findall(r"{(\w+)}", text))


@pytest.mark.parametrize("locale", ["en-US", "es-ES", "pt-BR", "de"])
def test_i18n_complete_and_placeholders_consistent(locale):
    root = Path(__file__).parent.parent / "locales"
    fr = json.loads((root / "fr.json").read_text(encoding="utf-8"))
    other = json.loads((root / f"{locale}.json").read_text(encoding="utf-8"))
    for section in (("ai_detector",), ("modules", "ai_detector")):
        a, b = fr, other
        for key in section:
            a, b = a[key], b[key]
        assert _keys(a) == _keys(b), f"{locale}: {sorted(_keys(a) ^ _keys(b))}"
        for key in _keys(a):
            va, vb = a, b
            for part in key.split("."):
                va, vb = va[part], vb[part]
            # French-only elision helper; other languages use {issuer} directly.
            allowed = _placeholders(va) | ({"issuer"} if "of_issuer" in va else set())
            assert _placeholders(vb) <= allowed, (locale, key)


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #

class TestModule:
    def test_defaults_and_normalisation(self):
        from modules.ai_detector import AiDetectorModule, CATEGORIES, normalize_config
        assert AiDetectorModule.DEFAULT_ENABLED is True
        assert normalize_config(None) == {"categories": list(CATEGORIES), "ignored_channel_ids": []}
        cfg = normalize_config({"categories": ["photo", "bogus", "ai_full"],
                                "ignored_channel_ids": ["12", 12, "x", 13], "state": "paused"})
        assert cfg == {"categories": ["ai_full", "photo"], "ignored_channel_ids": [12, 13],
                       "state": "paused"}

    async def test_validate_config(self):
        from modules.ai_detector import AiDetectorModule
        m = AiDetectorModule(None, 1)
        assert (await m.validate_config({"categories": ["ai_full"], "ignored_channel_ids": []}))[0]
        assert not (await m.validate_config({"categories": ["nope"]}))[0]
        assert not (await m.validate_config({"categories": [], "ignored_channel_ids": ["a"]}))[0]
        assert not (await m.validate_config({"categories": [],
                                             "ignored_channel_ids": list(range(26))}))[0]

    async def test_allowed_kinds_and_channels(self):
        from modules.ai_detector import AiDetectorModule
        m = AiDetectorModule(None, 1)
        await m.load_config({"categories": ["photo"], "ignored_channel_ids": [50]})
        assert sorted(m.allowed_kinds()) == [mf.KIND_PHOTO, mf.KIND_PHOTO_EDITED]
        assert not m.handles_channel(SimpleNamespace(id=50))
        assert not m.handles_channel(SimpleNamespace(id=51, parent_id=50))  # thread
        assert m.handles_channel(SimpleNamespace(id=52, parent_id=None))

    def _message(self, *, bot=False, channel_id=10):
        me = SimpleNamespace(id=1)
        channel = MagicMock()
        channel.id = channel_id
        channel.parent_id = None
        channel.permissions_for.return_value = SimpleNamespace(send_messages=True)
        attachment = SimpleNamespace(id=99, filename="a.png", content_type="image/png", size=10)
        return SimpleNamespace(author=SimpleNamespace(bot=bot), guild=SimpleNamespace(id=5, me=me),
                               channel=channel, id=77, attachments=[attachment],
                               reply=AsyncMock()), attachment

    async def _module(self, monkeypatch, result, config=None):
        from modules.ai_detector import AiDetectorModule
        from services.ai_detector_service import AiDetectorService
        service = MagicMock()
        service.candidate_images = AiDetectorService.candidate_images
        service.analyze_message = AsyncMock(side_effect=lambda msg, **kw: (
            (msg.attachments[0], result)
            if result.kind is not None and result.kind in kw.get("kinds", [result.kind]) else None))
        bot = SimpleNamespace(ai_detector=service, stats=None)
        m = AiDetectorModule(bot, 5)
        await m.load_config(config or {})
        monkeypatch.setattr("utils.guild_language.guild_locale", AsyncMock(return_value="fr"))
        return m, service

    async def test_replies_with_the_card_and_the_button(self, monkeypatch):
        result = verdict_result(mf.KIND_AI_FULL, tool="ChatGPT", issuer="OpenAI", when=WHEN)
        m, service = await self._module(monkeypatch, result)
        message, attachment = self._message()
        await m.on_message(message)
        message.reply.assert_awaited_once()
        kwargs = message.reply.await_args.kwargs
        assert kwargs["mention_author"] is False
        assert kwargs["allowed_mentions"].users is False
        row = kwargs["view"].children[1]
        assert row.children[0].item.custom_id == f"moddy:aidet:info:10:77:{attachment.id}"
        service.record.assert_called_once_with(mf.KIND_AI_FULL, "auto")

    @pytest.mark.parametrize("case", ["bot_author", "ignored_channel", "category_off", "no_verdict"])
    async def test_stays_silent(self, monkeypatch, case):
        result = verdict_result(mf.KIND_AI_FULL, tool="ChatGPT", issuer="OpenAI")
        config = {}
        if case == "ignored_channel":
            config = {"ignored_channel_ids": [10]}
        if case == "category_off":
            config = {"categories": ["photo", "unsigned"]}
        if case == "no_verdict":
            result = an.Analysis(reason=an.REASON_UNTRUSTED)
        m, _ = await self._module(monkeypatch, result, config)
        message, _ = self._message(bot=case == "bot_author")
        await m.on_message(message)
        message.reply.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Default-enabled modules in the ModuleManager
# --------------------------------------------------------------------------- #

class FakeDB:
    def __init__(self, modules):
        self.modules = modules

    async def get_guild(self, gid):
        return {"data": {"modules": self.modules}}

    async def update_guild_data(self, gid, path, value):
        self.modules[path.split(".", 1)[1]] = value


def make_manager(modules):
    from modules.ai_detector import AiDetectorModule
    from modules.module_manager import ModuleManager
    bot = SimpleNamespace(db=FakeDB(modules), module_slash_commands={})
    mgr = ModuleManager(bot)
    mgr.register_module(AiDetectorModule)
    return mgr, bot


class TestDefaultEnabled:
    async def test_runs_where_nothing_is_stored(self):
        mgr, _ = make_manager({})
        module = await mgr.get_module_instance(1, "ai_detector")
        assert module is not None and module.enabled and module.state == "active"
        assert (await mgr.get_module_statuses(1))["ai_detector"] == "active"
        assert await mgr.get_module_state(1, "ai_detector") == "active"

    async def test_pause_without_a_saved_config(self):
        mgr, bot = make_manager({})
        await mgr.load_guild_modules(1)
        ok, err = await mgr.set_module_state(1, "ai_detector", "paused")
        assert ok and err is None
        assert bot.db.modules["ai_detector"]["state"] == "paused"
        assert bot.db.modules["ai_detector"]["categories"]  # defaults written along
        assert not mgr.active_modules[1]["ai_detector"].enabled

        mgr.active_modules.clear()  # restart: the pause survives
        assert not (await mgr.get_module_instance(1, "ai_detector")).enabled

    async def test_deleted_or_emptied_config_goes_back_to_defaults(self):
        mgr, bot = make_manager({"ai_detector": {"categories": ["photo"], "state": "paused"}})
        await mgr.load_guild_modules(1)
        assert not mgr.active_modules[1]["ai_detector"].enabled
        assert await mgr.delete_module_config(1, "ai_detector")
        assert mgr.active_modules[1]["ai_detector"].enabled

        bot.db.modules["ai_detector"] = {}  # dashboard push of an empty config
        recap = await mgr.reload_module(1, "ai_detector")
        assert recap["ok"] and recap["action"] == "updated" and recap["enabled"]

    async def test_limited_guild_may_still_tune_it(self, monkeypatch):
        from utils import global_sanctions
        mgr, _ = make_manager({})
        monkeypatch.setattr(global_sanctions, "get_context_level",
                            AsyncMock(return_value=global_sanctions.GlobalLevel.LIMITED))
        assert await mgr._blocked_as_new_module(1, "ai_detector") is None

    async def test_manage_panel_available_before_any_save(self):
        from modules.configs._common import build_manage_view
        mgr, bot = make_manager({})
        bot.module_manager = mgr
        assert await build_manage_view(bot, 1, "fr", "ai_detector") is not None


# --------------------------------------------------------------------------- #
# Service: trust list cache + downloads
# --------------------------------------------------------------------------- #

class FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.set_calls = []

    async def get(self, key):
        return self.value

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, ex))
        self.value = value


PEM = (DATA / "test_anchors.pem").read_text()


class TestService:
    def _service(self, redis=None):
        from services.ai_detector_service import AiDetectorService
        return AiDetectorService(SimpleNamespace(redis=redis, stats=None))

    async def test_trust_list_from_redis_first(self, monkeypatch):
        service = self._service(FakeRedis(PEM))
        download = AsyncMock()
        monkeypatch.setattr(service, "_download", download)
        assert await service.refresh_trust_list()
        assert service.trust_ready
        download.assert_not_awaited()

    async def test_download_is_cached_for_24h(self, monkeypatch):
        from services.ai_detector_service import REDIS_KEY, TRUST_LIST_TTL
        redis = FakeRedis(None)
        service = self._service(redis)
        monkeypatch.setattr(service, "_download", AsyncMock(return_value=PEM))
        assert await service.refresh_trust_list()
        assert redis.set_calls == [(REDIS_KEY, TRUST_LIST_TTL)] and TRUST_LIST_TTL == 86400

    async def test_failed_download_keeps_the_list_in_use(self, monkeypatch):
        redis = FakeRedis(None)
        service = self._service(redis)
        monkeypatch.setattr(service, "_download", AsyncMock(return_value=PEM))
        await service.refresh_trust_list()
        redis.value = None  # expired in Redis
        monkeypatch.setattr(service, "_download", AsyncMock(return_value=None))
        assert await service.refresh_trust_list()
        assert service.trust_ready

    async def test_garbage_in_redis_is_ignored(self, monkeypatch):
        service = self._service(FakeRedis("<html>rate limited</html>"))
        monkeypatch.setattr(service, "_download", AsyncMock(return_value=None))
        assert not await service.refresh_trust_list()
        assert not service.trust_ready

    @needs_c2pa
    async def test_attachment_downloaded_from_the_original_url_and_cached(self):
        service = self._service()
        service._load(PEM)
        signed = sign(plain_image(), formats.MIME_JPEG, AI_CREATED)
        attachment = SimpleNamespace(id=5, size=len(signed), filename="x.jpg",
                                     content_type="image/jpeg", read=AsyncMock(return_value=signed))
        result = await service.analyze_attachment(attachment)
        assert result.kind == mf.KIND_AI_FULL
        attachment.read.assert_awaited_once_with(use_cached=False)  # url, not proxy_url
        assert await service.analyze_attachment(attachment) is result
        attachment.read.assert_awaited_once()

    async def test_size_cap_and_busy(self):
        from services.ai_detector_service import MAX_FILE_BYTES, MAX_WAITING
        service = self._service()
        big = SimpleNamespace(id=1, size=MAX_FILE_BYTES + 1, read=AsyncMock())
        assert (await service.analyze_attachment(big)).reason == an.REASON_TOO_LARGE
        big.read.assert_not_awaited()
        service._waiting = MAX_WAITING
        small = SimpleNamespace(id=2, size=10, read=AsyncMock())
        assert (await service.analyze_attachment(small, automatic=True)).reason == an.REASON_BUSY

    async def test_download_failure(self):
        service = self._service()
        failing = SimpleNamespace(id=3, size=10, read=AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "gone")))
        assert (await service.analyze_attachment(failing)).reason == an.REASON_DOWNLOAD_FAILED
        assert service.cached(3) is None  # failures are not remembered

    async def test_strongest_verdict_wins(self, monkeypatch):
        service = self._service()
        photo = verdict_result(mf.KIND_PHOTO, issuer="Sony")
        ai = verdict_result(mf.KIND_AI_FULL, issuer="OpenAI")
        results = {1: photo, 2: ai}
        monkeypatch.setattr(service, "analyze_attachment",
                            AsyncMock(side_effect=lambda a, **kw: results[a.id]))
        atts = [SimpleNamespace(id=i, filename=f"{i}.png", content_type="image/png") for i in (1, 2)]
        message = SimpleNamespace(attachments=atts)
        attachment, result = await service.analyze_message(message)
        assert attachment.id == 2 and result.kind == mf.KIND_AI_FULL
        found = await service.analyze_message(message, kinds=[mf.KIND_PHOTO])
        assert found[0].id == 1
