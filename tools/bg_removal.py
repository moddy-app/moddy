"""Background removal for /image, with the u2netp salient-object model.

The model (``u2netp``, ~4.6 MB, the small U^2-Net used by rembg) is run
**directly** with ``onnxruntime`` + ``numpy``: the ``rembg`` package itself is not
installed, it drags scipy, scikit-image, pymatting and numba along.

Memory is what Railway bills, so everything here is lazy and short-lived:

* nothing heavy is imported until the first removal;
* the model file is downloaded once (sha256 verified, atomic write) into
  ``U2NETP_DIR`` (default: the system temp directory);
* the ONNX session is created on the first removal and **dropped after
  :data:`IDLE_SECONDS` without use** (10 minutes);
* one inference at a time (a lock), always in a worker thread.

The module is Discord-free. The download itself is injected as an async
``fetch`` callable so the service decides how to reach the network.
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import io
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import image_ops
from .media_errors import MediaError

logger = logging.getLogger("moddy.bg_removal")

#: Official rembg release asset. Override with ``U2NETP_URL`` (e.g. a mirror).
MODEL_URL = os.environ.get(
    "U2NETP_URL", "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx")
#: sha256 of that file. It was computed from the official release asset (its md5,
#: 8e83ca70e441ab06c318d82300c84806, is the one published by rembg). Override with
#: ``U2NETP_SHA256`` if the model is ever re-hosted or updated.
MODEL_SHA256 = os.environ.get(
    "U2NETP_SHA256", "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8").lower()
MODEL_FILENAME = "u2netp.onnx"
MODEL_MAX_BYTES = 12 * 1024 * 1024   # sanity bound while downloading

IDLE_SECONDS = 10 * 60
INPUT_SIDE = 320
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)

Fetch = Callable[[str], Awaitable[bytes]]


def default_model_dir() -> Path:
    return Path(os.environ.get("U2NETP_DIR") or Path(tempfile.gettempdir()) / "moddy-models")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def model_is_valid(data: bytes, expected: Optional[str] = None) -> bool:
    return bool(data) and len(data) <= MODEL_MAX_BYTES and \
        sha256_of(data) == (expected or MODEL_SHA256).lower()


# --------------------------------------------------------------------------- #
# Pure image <-> tensor maths (synchronous, run in a thread)
# --------------------------------------------------------------------------- #


def preprocess(rgb_image):
    """RGB Pillow image -> ``(1, 3, 320, 320)`` float32 tensor (rembg's recipe)."""
    import numpy as np
    from PIL import Image
    small = rgb_image.convert("RGB").resize((INPUT_SIDE, INPUT_SIDE), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.float32)
    peak = float(arr.max())
    arr = arr / (peak if peak > 0 else 1.0)
    arr = (arr - np.array(_MEAN, dtype=np.float32)) / np.array(_STD, dtype=np.float32)
    return arr.transpose(2, 0, 1)[None, ...].astype(np.float32)


def mask_from_output(output, size):
    """Model output (any shape holding 320x320) -> ``L`` mask at ``size`` (w, h)."""
    import numpy as np
    from PIL import Image
    pred = np.asarray(output, dtype=np.float32).reshape(INPUT_SIDE, INPUT_SIDE)
    low, high = float(pred.min()), float(pred.max())
    pred = (pred - low) / (high - low) if high > low else np.zeros_like(pred)
    mask = Image.fromarray((pred * 255).astype("uint8"), "L")
    return mask.resize(size, Image.LANCZOS)


def apply_mask(image, mask):
    """RGBA copy of ``image`` whose alpha is ``mask`` (kept below any existing alpha)."""
    from PIL import ImageChops
    rgba = image.convert("RGBA")
    alpha = mask
    if "A" in image.getbands():
        alpha = ImageChops.multiply(mask, image.getchannel("A"))
    rgba.putalpha(alpha)
    return rgba


def subject_ratio(mask) -> float:
    """Share (0-1) of the picture the cut-out keeps; used to spot empty results."""
    histogram = mask.histogram()
    total = sum(histogram) or 1
    return sum(histogram[128:]) / total


# --------------------------------------------------------------------------- #
# The remover
# --------------------------------------------------------------------------- #


class BackgroundRemover:
    """Lazy, single-flight, self-unloading u2netp runner."""

    def __init__(self, model_dir: Optional[Path] = None, *, idle_seconds: float = IDLE_SECONDS,
                 sha256: Optional[str] = None, clock: Callable[[], float] = time.monotonic):
        self.model_dir = Path(model_dir) if model_dir else default_model_dir()
        self.idle_seconds = idle_seconds
        self.sha256 = (sha256 or MODEL_SHA256).lower()
        self._clock = clock
        self._session = None
        self._input_name: Optional[str] = None
        self._last_used = 0.0
        self._lock = asyncio.Lock()          # one inference at a time
        self._download_lock = asyncio.Lock()
        self._unload_task: Optional[asyncio.Task] = None

    # -- state ------------------------------------------------------------ #
    @property
    def model_path(self) -> Path:
        return self.model_dir / MODEL_FILENAME

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def model_on_disk(self) -> bool:
        try:
            return self.model_path.is_file() and self.model_path.stat().st_size > 0
        except OSError:
            return False

    # -- model file ------------------------------------------------------- #
    async def ensure_model(self, fetch: Fetch) -> Path:
        """Make sure a verified model file exists, downloading it once."""
        async with self._download_lock:
            if self.model_on_disk():
                data = await asyncio.to_thread(self.model_path.read_bytes)
                if model_is_valid(data, self.sha256):
                    return self.model_path
                logger.warning("u2netp on disk failed its checksum, downloading again")
            try:
                data = await fetch(MODEL_URL)
            except MediaError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("u2netp download failed: %s", exc)
                raise MediaError("model_unavailable") from exc
            if not model_is_valid(data, self.sha256):
                logger.error("u2netp download rejected: checksum mismatch (%d bytes)", len(data))
                raise MediaError("model_unavailable")
            await asyncio.to_thread(self._write_atomic, data)
            return self.model_path

    def _write_atomic(self, data: bytes) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.model_path.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, self.model_path)

    # -- session ---------------------------------------------------------- #
    def _load_session(self) -> None:
        """Create the ONNX session (sync). Small arenas: this runs on a shared box."""
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    def _infer_sync(self, raw: bytes) -> image_ops.Processed:
        frames, _, _, total = image_ops.load_frames(raw, animated_ok=False)
        image = frames[0]
        frames.clear()
        if self._session is None:
            self._load_session()
        tensor = preprocess(image)
        outputs = self._session.run(None, {self._input_name: tensor})
        mask = mask_from_output(outputs[0], image.size)
        del tensor, outputs
        if subject_ratio(mask) < 0.005:
            raise MediaError("no_subject")
        cut = apply_mask(image, mask)
        notes = ["animation_lost"] if total > 1 else []
        buf = io.BytesIO()
        cut.save(buf, "PNG", optimize=False, compress_level=6)
        return image_ops.Processed(
            data=buf.getvalue(), format_key="png", width=cut.width, height=cut.height,
            has_alpha=True, notes=notes)

    async def remove(self, raw: bytes, fetch: Fetch) -> image_ops.Processed:
        """Cut the subject out of ``raw``; returns a transparent PNG."""
        await self.ensure_model(fetch)
        async with self._lock:
            try:
                result = await asyncio.to_thread(self._infer_sync, raw)
            except MediaError:
                raise
            except MemoryError as exc:
                raise MediaError("too_many_pixels", max=image_ops.MAX_PIXELS // 1_000_000) from exc
            except Exception as exc:  # noqa: BLE001 — onnxruntime raises its own zoo
                logger.warning("background removal failed: %s", exc)
                self.unload()
                raise MediaError("model_unavailable") from exc
            self._last_used = self._clock()
            self._schedule_unload()
            return result

    # -- idle unload ------------------------------------------------------ #
    def _schedule_unload(self) -> None:
        if self._unload_task is not None and not self._unload_task.done():
            return
        try:
            self._unload_task = asyncio.get_running_loop().create_task(self._unload_when_idle())
        except RuntimeError:
            self._unload_task = None

    async def _unload_when_idle(self) -> None:
        while self._session is not None:
            remaining = self.idle_seconds - (self._clock() - self._last_used)
            if remaining <= 0:
                async with self._lock:   # never drop the model under a running inference
                    if self._clock() - self._last_used >= self.idle_seconds:
                        self.unload()
                        return
                continue
            await asyncio.sleep(min(remaining, 60) if remaining > 1 else remaining + 0.05)

    def unload(self) -> None:
        """Drop the ONNX session and give the memory back."""
        had = self._session is not None
        self._session = None
        self._input_name = None
        if had:
            gc.collect()
            self._trim()
            logger.info("u2netp unloaded")

    @staticmethod
    def _trim() -> None:
        """Ask glibc to hand freed pages back to the OS (no-op elsewhere)."""
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        if self._unload_task is not None:
            self._unload_task.cancel()
        self.unload()
