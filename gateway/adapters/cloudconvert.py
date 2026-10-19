"""
CloudConvert adapter — office documents (DOCX/ODT/PPTX/XLSX/ODS <-> PDF) and ebooks
for ``/convert``. Everything that has a light local library is converted on Moddy
itself; this provider is only for layout-faithful office rendering.

One operation, ``convert``: a CloudConvert *job* of three tasks —
``import/url`` (CloudConvert downloads the file from the Discord CDN URL),
``convert`` and ``export/url`` (a download link valid 24 hours). The job is
created and polled until it finishes; the answer holds only the export link,
file name and size (``{"url", "filename", "size"}``): the bytes are fetched by
the caller, which decides whether they fit in a Discord upload or whether the
link itself is handed to the user.

The CDN URL travels in ``CallSpec.metadata["import_url"]``, never in ``payload``
(payload is forwarded to the staff webhook log, and a Discord CDN link is signed
and private). The API key (``CLOUDCONVERT_API_KEY``, a v2 API key with the
``task.read`` / ``task.write`` scopes) never appears in an error message.

Errors: 401/403 -> ``ProviderError`` (key problem, not retried), 402 -> credits
used up (``ProviderError(402)``), 429 -> ``RateLimitError``, a failed job ->
``ProviderError(422, <CloudConvert code>)`` (not retried); 5xx are retried.
``CLOUDCONVERT_SANDBOX=true`` targets the sandbox (free, files are not real).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import aiohttp

from .base import BaseAdapter, AdapterResult
from ..spec import CallSpec
from ..errors import ConfigurationError, ProviderError, RateLimitError
from ..ratelimit import UNIT_REQUESTS

logger = logging.getLogger("moddy.gateway.cloudconvert")

_USER_AGENT = "Moddy/1.0 (+https://moddy.app)"
_POLL_SECONDS = 1.5
_JOB_DEADLINE = 110.0   # below the gateway timeout of the operation (120 s)


def api_base(sandbox: Optional[bool] = None) -> str:
    if sandbox is None:
        sandbox = os.environ.get("CLOUDCONVERT_SANDBOX", "").lower() in ("1", "true", "yes")
    return "https://api.sandbox.cloudconvert.com/v2" if sandbox else "https://api.cloudconvert.com/v2"


def build_job(import_url: str, input_format: str, output_format: str, filename: str,
              output_filename: Optional[str] = None) -> dict:
    """The job body: import by URL -> convert -> export link."""
    convert = {"operation": "convert", "input": "import", "input_format": input_format,
               "output_format": output_format}
    if output_filename:
        convert["filename"] = output_filename
    return {
        "tag": "moddy-convert",
        "tasks": {
            "import": {"operation": "import/url", "url": import_url, "filename": filename},
            "convert": convert,
            "export": {"operation": "export/url", "input": "convert", "inline": False,
                       "archive_multiple_files": False},
        },
    }


def parse_finished(job: dict) -> dict:
    """``{"url", "filename", "size"}`` from a finished job, or the typed error."""
    data = job.get("data") or job
    status = data.get("status")
    tasks = data.get("tasks") or []
    if status == "error":
        failed = next((t for t in tasks if t.get("status") == "error"), {})
        code = str(failed.get("code") or "CONVERSION_FAILED")[:60]
        raise ProviderError("cloudconvert", 422, code)
    for task in tasks:
        if task.get("operation") == "export/url" and task.get("status") == "finished":
            files = ((task.get("result") or {}).get("files")) or []
            if files and files[0].get("url"):
                return {"url": files[0]["url"], "filename": files[0].get("filename"),
                        "size": int(files[0].get("size") or 0)}
    raise ProviderError("cloudconvert", 502, "no export file")


class CloudConvertAdapter(BaseAdapter):
    provider = "cloudconvert"

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ConfigurationError("CLOUDCONVERT_API_KEY is required for CloudConvertAdapter")
        self._api_key = api_key
        self._base = api_base()
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(headers={
            "User-Agent": _USER_AGENT, "Authorization": f"Bearer {self._api_key}"})
        logger.info("CloudConvert adapter ready (%s)", self._base)

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _json(self, method: str, url: str, **kwargs) -> dict:
        async with self._session.request(method, url, **kwargs) as resp:
            if resp.status == 429:
                try:
                    retry = float(resp.headers.get("Retry-After", 0)) or None
                except (TypeError, ValueError):
                    retry = None
                raise RateLimitError("cloudconvert", retry)
            if resp.status == 402:
                raise ProviderError("cloudconvert", 402, "credits")
            if resp.status >= 400:
                # Never echo the request (it carries the import URL).
                raise ProviderError("cloudconvert", resp.status, (await resp.text())[:160])
            try:
                data = await resp.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError) as exc:
                raise ProviderError("cloudconvert", 502, "non-JSON answer") from exc
        if not isinstance(data, dict):
            raise ProviderError("cloudconvert", 502, "unexpected answer")
        return data

    async def execute(self, spec: CallSpec) -> AdapterResult:
        if spec.operation != "convert":
            raise ConfigurationError(f"Unsupported CloudConvert operation: {spec.operation!r}")
        payload = spec.payload or {}
        import_url = (spec.metadata or {}).get("import_url")
        if not import_url:
            raise ConfigurationError("convert needs metadata['import_url']")
        body = build_job(import_url, payload["input_format"], payload["output_format"],
                         payload.get("filename") or f"input.{payload['input_format']}",
                         payload.get("output_filename"))
        created = await self._json("POST", f"{self._base}/jobs", json=body)
        job_id = ((created.get("data") or {}).get("id"))
        if not job_id:
            raise ProviderError("cloudconvert", 502, "no job id")
        deadline = time.monotonic() + _JOB_DEADLINE
        job = created
        while (job.get("data") or {}).get("status") not in ("finished", "error"):
            if time.monotonic() > deadline:
                raise ProviderError("cloudconvert", 504, "job still running")
            await asyncio.sleep(_POLL_SECONDS)
            job = await self._json("GET", f"{self._base}/jobs/{job_id}")
        return AdapterResult(data=parse_finished(job), rate_cost={UNIT_REQUESTS: 1})
