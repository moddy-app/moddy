"""One image in, one :class:`Analysis` out — the whole decision, synchronous.

Order of the checks (cheapest first, C2PA before unsigned hints):

1. the bytes must be PNG, JPEG or WebP (sniffed, never the file name);
2. a C2PA marker present -> c2pa-python verifies the store against the
   official trust list. Only ``Trusted`` stores are classified;
3. otherwise (or when the store gave no verdict) a PNG may still carry
   unsigned Stable Diffusion / ComfyUI parameters -> template 5.

``reason`` explains a missing verdict. The automatic module stays silent on
every one of them; ``/ai-check`` turns them into an explanation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aidetector import formats, manifest, png_text, verify

REASON_VERDICT = "verdict"
REASON_UNSUPPORTED = "unsupported"
REASON_NO_DATA = "no_data"
REASON_UNTRUSTED = "untrusted"
REASON_INVALID = "invalid"
REASON_UNCLASSIFIED = "unclassified"
REASON_TRUST_UNAVAILABLE = "trust_unavailable"
REASON_ERROR = "error"
# Set by the service, before any byte is analysed.
REASON_TOO_LARGE = "too_large"
REASON_DOWNLOAD_FAILED = "download_failed"
REASON_BUSY = "busy"


@dataclass
class Analysis:
    """The outcome for one image."""

    reason: str
    mime: Optional[str] = None
    size: int = 0
    #: Whether the file carried a C2PA store at all (trusted or not).
    has_c2pa: bool = False
    validation_state: Optional[str] = None
    summary: Optional[manifest.ManifestSummary] = None
    generation: Optional[png_text.GenerationMetadata] = None

    @property
    def kind(self) -> Optional[str]:
        """Verdict kind (``manifest.KIND_*``) or None."""
        if self.summary is not None and self.summary.verdict is not None:
            return self.summary.verdict.kind
        if self.generation is not None:
            return manifest.KIND_UNSIGNED
        return None

    @property
    def verdict(self) -> Optional[manifest.C2paVerdict]:
        return self.summary.verdict if self.summary is not None else None

    @property
    def rank(self) -> int:
        """Lower is stronger; images without a verdict rank last."""
        kind = self.kind
        if kind in manifest.KIND_PRIORITY:
            return manifest.KIND_PRIORITY.index(kind)
        return len(manifest.KIND_PRIORITY)


def analyze(data: bytes, settings_json: Optional[str]) -> Analysis:
    """Analyse ``data``. ``settings_json`` is :func:`verify.build_settings`
    output, or None while the trust list is not loaded yet. Never raises."""
    mime = formats.sniff_mime(data)
    result = Analysis(reason=REASON_NO_DATA, mime=mime, size=len(data))
    if mime is None:
        result.reason = REASON_UNSUPPORTED
        return result

    if formats.may_contain_c2pa(data):
        if settings_json is None:
            result.reason = REASON_TRUST_UNAVAILABLE
        else:
            read = verify.read_manifest_store(data, mime, settings_json)
            if read.error:
                result.reason = REASON_ERROR
            elif read.store is not None:
                result.has_c2pa = True
                result.validation_state = read.validation_state
                if read.trusted:
                    result.summary = manifest.summarize(read.store)
                    result.reason = (REASON_VERDICT if result.summary.verdict
                                     else REASON_UNCLASSIFIED)
                elif read.validation_state == "Valid":
                    result.reason = REASON_UNTRUSTED
                else:
                    result.reason = REASON_INVALID

    if result.reason != REASON_VERDICT and mime == formats.MIME_PNG:
        generation = png_text.detect_generation_metadata(data)
        if generation is not None:
            result.generation = generation
            result.reason = REASON_VERDICT
    return result
