"""c2pa-python glue: read and verify a file's Content Credentials.

Synchronous on purpose — the native reader parses and hashes the whole file,
so the service always calls :func:`read_manifest_store` through
``asyncio.to_thread``. A fresh ``Context`` is built per call from a JSON string
(cheap: one PEM parse) rather than shared across threads.

Trust configuration
-------------------
- ``trust.trust_anchors``: the official C2PA trust list (conformance program),
  fetched by the service and cached in Redis;
- ``trust.trust_config``: the EKUs a signing certificate may carry —
  c2pa-kp-claimSigning, id-kp-documentSigning, id-kp-emailProtection and
  id-kp-timeStamping;
- network off: no remote manifest fetch, no OCSP, and an empty host
  allow-list. The file comes from a stranger; reading it must never make the
  bot contact a URL it names.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("moddy.aidetector.verify")

TRUST_LIST_URL = (
    "https://raw.githubusercontent.com/c2pa-org/conformance-public/"
    "refs/heads/main/trust-list/C2PA-TRUST-LIST.pem"
)

ALLOWED_EKUS = (
    "1.3.6.1.4.1.62558.2.1",   # c2pa-kp-claimSigning
    "1.3.6.1.5.5.7.3.36",      # id-kp-documentSigning
    "1.3.6.1.5.5.7.3.4",       # id-kp-emailProtection
    "1.3.6.1.5.5.7.3.8",       # id-kp-timeStamping
)

STATE_TRUSTED = "Trusted"


def is_available() -> bool:
    """True when c2pa-python (and its native library) can be imported."""
    try:
        import c2pa  # noqa: F401
    except Exception:  # noqa: BLE001 — ImportError, or OSError from the .so
        return False
    return True


def looks_like_pem_bundle(text: Optional[str]) -> bool:
    return bool(text) and "-----BEGIN CERTIFICATE-----" in text


def build_settings(trust_anchors: str) -> str:
    """The c2pa settings JSON used for every read."""
    return json.dumps({
        "version": 1,
        "core": {"allowed_network_hosts": []},
        "verify": {
            "verify_trust": True,
            "verify_after_reading": True,
            "remote_manifest_fetch": False,
            "ocsp_fetch": False,
        },
        "trust": {
            "trust_anchors": trust_anchors,
            "trust_config": "\n".join(ALLOWED_EKUS) + "\n",
        },
    })


@dataclass
class ReadResult:
    """Outcome of one read. ``store`` is None when the file has no manifest."""

    store: Optional[Dict[str, Any]]
    validation_state: Optional[str]
    error: Optional[str] = None

    @property
    def trusted(self) -> bool:
        return self.store is not None and self.validation_state == STATE_TRUSTED


def read_manifest_store(data: bytes, mime: str, settings_json: str) -> ReadResult:
    """Parse + verify ``data``. Never raises: errors come back in ``error``."""
    try:
        import c2pa
    except Exception as exc:  # noqa: BLE001
        return ReadResult(None, None, error=f"c2pa unavailable: {exc}")

    try:
        with c2pa.Context.from_json(settings_json) as context:
            reader = c2pa.Reader.try_create(mime, io.BytesIO(data), None, context=context)
            if reader is None:
                return ReadResult(None, None)
            with reader:
                store = json.loads(reader.json())
                return ReadResult(store, reader.get_validation_state())
    except Exception as exc:  # noqa: BLE001 — C2paError family, malformed JUMBF…
        logger.debug("C2PA read failed: %s", exc)
        return ReadResult(None, None, error=str(exc)[:200])
