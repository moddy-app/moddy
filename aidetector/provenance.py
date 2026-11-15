"""OpenAI Content Provenance check response -> a small, display-safe finding.

``POST /v1/content_provenance_checks`` (OpenAI) looks for OpenAI's own
provenance signals in an image: its **C2PA** manifest and its invisible
**SynthID** watermark. The watermark lives in the pixels, so it survives what
strips C2PA — screenshots, re-saves, most social networks — which is exactly
where the local check (``verify.py`` + ``manifest.py``) goes silent.

Response shape (documented contract)::

    {"object": "content_provenance_check", "created_at": 1785170052,
     "results": [
        {"type": "c2pa", "outcome": "detected", "validation_state": "trusted",
         "issuer": "OpenAI OpCo, LLC", "model": "gpt-image",
         "generated_at": "2026-07-27T18:34:12Z"},
        {"type": "synthid", "outcome": "not_detected",
         "model": null, "generated_at": null}]}

Rules the parser follows (they come from OpenAI's guide):

- C2PA ``detected`` is only ever returned for a ``trusted`` / ``valid``
  manifest naming OpenAI with an AI-generation action, so ``detected`` is the
  only outcome that counts; ``invalid`` manifests are never evidence;
- ``not_detected`` proves nothing — OpenAI only recognises its own signals;
- checks that do not apply are omitted, not returned as ``not_detected``.

Pure: no network, no Discord. The gateway client calls the API; this module
only interprets what came back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from aidetector.manifest import _clean as _clean_text
from aidetector.manifest import parse_when

SIGNAL_SYNTHID = "synthid"
SIGNAL_C2PA = "c2pa"

#: Outcome of the check as the cards see it.
STATUS_DETECTED = "detected"
STATUS_NOT_DETECTED = "not_detected"
#: The call could not be made or failed (no key, no access, rate limit, error).
STATUS_UNAVAILABLE = "unavailable"

_VALIDATION_STATES = ("trusted", "valid", "invalid", "not_present")


def _clean(value: Any, limit: int = 60) -> Optional[str]:
    """Display-safe single line (same rules as ``manifest._clean``)."""
    return _clean_text(value, limit)


@dataclass
class ProviderProvenance:
    """What OpenAI's provenance check found for one image."""

    status: str
    #: Signal that proved it (``synthid`` preferred, then ``c2pa``) when detected.
    signal: Optional[str] = None
    model: Optional[str] = None
    generated_at: Optional[datetime] = None
    issuer: Optional[str] = None
    #: ``validation_state`` of the C2PA entry, when OpenAI returned one.
    c2pa_state: Optional[str] = None
    synthid_checked: bool = False

    @property
    def detected(self) -> bool:
        return self.status == STATUS_DETECTED


def unavailable() -> ProviderProvenance:
    return ProviderProvenance(status=STATUS_UNAVAILABLE)


def parse_response(data: Any) -> ProviderProvenance:
    """API JSON -> :class:`ProviderProvenance`. Never raises.

    Anything malformed reads as ``not_detected``: a verdict is only ever
    built from an explicit ``"outcome": "detected"`` entry.
    """
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return ProviderProvenance(status=STATUS_NOT_DETECTED)

    found = ProviderProvenance(status=STATUS_NOT_DETECTED)
    detected = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == SIGNAL_C2PA:
            state = entry.get("validation_state")
            if state in _VALIDATION_STATES:
                found.c2pa_state = state
            # The guide: detected only for trusted/valid OpenAI manifests.
            # Belt and braces — never trust an "invalid" one even if marked.
            if entry.get("outcome") == STATUS_DETECTED and state in ("trusted", "valid"):
                detected[SIGNAL_C2PA] = entry
        elif kind == SIGNAL_SYNTHID:
            found.synthid_checked = True
            if entry.get("outcome") == STATUS_DETECTED:
                detected[SIGNAL_SYNTHID] = entry

    if not detected:
        return found
    # SynthID first: it is the signal the local C2PA check cannot see. The
    # other entry still fills a missing model / date / issuer.
    order = [s for s in (SIGNAL_SYNTHID, SIGNAL_C2PA) if s in detected]
    found.status = STATUS_DETECTED
    found.signal = order[0]
    for signal in order:
        entry = detected[signal]
        found.model = found.model or _clean(entry.get("model"))
        found.generated_at = found.generated_at or parse_when(entry.get("generated_at"))
        if signal == SIGNAL_C2PA:
            found.issuer = found.issuer or _clean(entry.get("issuer"))
    return found
