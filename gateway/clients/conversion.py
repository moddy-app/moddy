"""
``gw.cloudconvert`` — office documents and ebooks for ``/convert``.

``await gw.cloudconvert.convert(import_url=..., input_format="docx", output_format="pdf",
filename="report.docx", quota=[...])`` -> ``{"url", "filename", "size"}``: a 24-hour
download link of the converted file. The import URL rides in ``metadata`` so it never
reaches the staff webhook log (see the adapter).
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..spec import CallSpec, QuotaPlan
from ..executor import GatewayExecutor
from ..ratelimit import UNIT_REQUESTS

MODEL_CONVERT = "convert"


class CloudConvertClient:
    def __init__(self, executor: GatewayExecutor):
        self._executor = executor

    async def convert(self, *, import_url: str, input_format: str, output_format: str,
                      filename: str, output_filename: Optional[str] = None,
                      size_bytes: Optional[int] = None, quota: Optional[QuotaPlan] = None,
                      call_type: str = "convert_cloud", correlation_id: Optional[str] = None,
                      metadata: Optional[dict] = None) -> dict:
        meta = dict(metadata or {})
        meta["import_url"] = import_url
        payload = {"input_format": input_format, "output_format": output_format,
                   "filename": filename, "output_filename": output_filename,
                   "size_bytes": size_bytes}
        spec = CallSpec(
            provider="cloudconvert",
            operation="convert",
            model=MODEL_CONVERT,
            payload=payload,
            quota=quota or [],
            call_type=call_type,
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata=meta,
            rate_cost={UNIT_REQUESTS: 1},
        )
        return await self._executor.execute(spec) or {}
