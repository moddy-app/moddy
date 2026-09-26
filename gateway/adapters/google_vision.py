"""
Google Cloud Vision adapter — SafeSearch and document OCR.

Two operations, one endpoint (``images:annotate``):

* ``safe_search``   → ``SAFE_SEARCH_DETECTION``: likelihoods for adult / racy /
  violence / medical / spoof. Used by the automod ``image_nsfw`` feature.
* ``document_text`` → ``DOCUMENT_TEXT_DETECTION``: dense-text OCR (layout,
  multilingual). Used by the ``/ocr`` command.

Each feature is billed separately by Google and each has its own free tier
(1000 units / month). The monthly allowance is enforced by the gateway rate
limiter (calendar-month, fail-closed rules — see ``gateway/config.py``), never
here.

Authentication is an API key (``GOOGLE_VISION_API_KEY``) passed as the ``key``
query parameter, which is the simplest credential the REST API accepts — no
service-account JSON to ship in the container.

Like the Groq adapter, the image bytes travel on ``CallSpec.binary`` and are
base64-encoded only at send time, so the payload the staff webhook logger
renders never carries them.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError
from ..ratelimit import UNIT_REQUESTS

logger = logging.getLogger("moddy.gateway.google_vision")

_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

# Google likelihood enum, in increasing order.
LIKELIHOODS = (
    "UNKNOWN", "VERY_UNLIKELY", "UNLIKELY", "POSSIBLE", "LIKELY", "VERY_LIKELY",
)

_FEATURE_BY_OPERATION = {
    "safe_search": "SAFE_SEARCH_DETECTION",
    "document_text": "DOCUMENT_TEXT_DETECTION",
}


def build_request(operation: str, image: bytes, *, language_hints=None) -> dict:
    """The ``images:annotate`` request body for one image and one feature."""
    feature = _FEATURE_BY_OPERATION.get(operation)
    if feature is None:
        raise ConfigurationError(f"Unsupported Google Vision operation: {operation!r}")
    request: dict = {
        "image": {"content": base64.b64encode(image).decode("ascii")},
        "features": [{"type": feature}],
    }
    if language_hints:
        request["imageContext"] = {"languageHints": list(language_hints)}
    return {"requests": [request]}


def parse_safe_search(response: dict) -> dict:
    """``{adult, racy, violence, medical, spoof}`` → likelihood names."""
    annotation = response.get("safeSearchAnnotation") or {}
    out = {}
    for key in ("adult", "racy", "violence", "medical", "spoof"):
        value = annotation.get(key) or "UNKNOWN"
        out[key] = value if value in LIKELIHOODS else "UNKNOWN"
    return out


def parse_document_text(response: dict) -> dict:
    """``{text, language}`` from a DOCUMENT_TEXT_DETECTION response."""
    full = response.get("fullTextAnnotation") or {}
    text = (full.get("text") or "").strip()
    if not text:
        # Fallback: the first textAnnotation carries the whole text block.
        annotations = response.get("textAnnotations") or []
        if annotations:
            text = (annotations[0].get("description") or "").strip()
    language = None
    for page in full.get("pages") or []:
        langs = (page.get("property") or {}).get("detectedLanguages") or []
        if langs:
            language = langs[0].get("languageCode")
            break
    if language is None:
        annotations = response.get("textAnnotations") or []
        if annotations:
            language = annotations[0].get("locale")
    return {"text": text, "language": language}


class GoogleVisionAdapter(BaseAdapter):
    provider = "google_vision"

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ConfigurationError("GOOGLE_VISION_API_KEY is required for GoogleVisionAdapter")
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json"}
        )
        logger.info("Google Vision adapter ready")

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation not in _FEATURE_BY_OPERATION:
            raise ConfigurationError(f"Unsupported Google Vision operation: {spec.operation!r}")
        if not spec.binary:
            raise ConfigurationError(f"{spec.operation} requires image bytes on CallSpec.binary")

        body = build_request(
            spec.operation, spec.binary,
            language_hints=(spec.payload or {}).get("language_hints"),
        )
        async with self._session.post(
            _ENDPOINT, params={"key": self._api_key}, json=body
        ) as resp:
            await self._raise_for_status(resp)
            data = await resp.json()

        responses = data.get("responses") or [{}]
        first = responses[0] or {}
        if first.get("error"):
            err = first["error"]
            raise ProviderError(
                "google_vision", int(err.get("code") or 400), str(err.get("message", ""))[:500]
            )

        if spec.operation == "safe_search":
            result = parse_safe_search(first)
        else:
            result = parse_document_text(first)
        return AdapterResult(data=result, rate_cost={UNIT_REQUESTS: 1})

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status == 429:
            retry_after: Optional[float] = None
            try:
                retry_after = float(resp.headers.get("Retry-After", 0)) or None
            except (ValueError, TypeError):
                pass
            raise RateLimitError("google_vision", retry_after)
        if resp.status >= 400:
            try:
                body = await resp.text()
            except Exception:
                body = ""
            # Never echo the request URL (it carries the API key).
            raise ProviderError("google_vision", resp.status, body[:500])
