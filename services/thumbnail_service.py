"""
services/thumbnail_service.py
Generates a real low-resolution overview PNG directly from a slide's own
pyramid, regardless of where the slide lives.

Why this exists
────────────────
The external pathology API's `thumbnail_url` isn't guaranteed to be a real
pre-rendered raster image — in mock/local mode it was literally aliased to
the raw WSI file itself, which a browser <img> tag cannot decode (TIFF is
not a web image format). Rather than trust whatever thumbnail_url happens
to be, we generate the overview ourselves from the slide's own pyramid, via
the exact same Range-request mechanism the tile-streaming proxy uses. That
means it works uniformly for ANY slide reachable via slide_url — a local
mock file today, a real GCS-hosted slide tomorrow — with no special-casing
per source.

Approach
────────
1. Open the slide with tifffile via a Range-reading file-like object (a
   plain file handle for local:// URLs; a small HTTP Range client for
   gs:// / https:// URLs — mirrors what geotiff.js does in the browser,
   just server-side and once).
2. tifffile groups a WSI's reduced-resolution IFDs into `series[0].levels`
   — pick the smallest one. Decoding only that level keeps generation cheap
   regardless of the full slide's size (typically hundreds of KB, a few
   pyramid IFDs deep).
3. If the file has no pyramid at all (a single full-res IFD) and that IFD
   is too large to decode directly, skip generation — the frontend already
   degrades gracefully (hides the overview placeholder on a 404). Forcing
   a decode of a multi-billion-pixel image just for a thumbnail would be a
   multi-GB memory spike; those slides need re-encoding as pyramidal TIFFs
   anyway (see tiff_gcs_viewer_notes.md) for the real zoom experience to be
   usable, not just the thumbnail.
4. Encode to PNG, cache to disk keyed by a hash of slide_url.

Missing optional deps (tifffile / imagecodecs / Pillow) degrade to "no
thumbnail available" rather than a hard failure.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from pathlib import Path
from typing import Optional

from config.settings import Settings, get_settings
from services._slide_io import open_range_reader

logger = logging.getLogger(__name__)

# Skip generation for non-pyramidal slides whose single IFD exceeds this
# many pixels — decoding directly would be a multi-GB memory spike.
_MAX_DIRECT_DECODE_PIXELS = 50_000_000   # ~50 MP, e.g. roughly 7000x7000
_THUMBNAIL_MAX_DIM = 512                 # long-side cap for the output PNG


class ThumbnailService:
    """Generates + caches WSI overview thumbnails. Singleton per process."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._cache_dir = Path(self._settings.thumbnail_cache_dir)
        # Per-cache-key locks prevent duplicate concurrent generation of the
        # same slide's thumbnail (e.g. two browser tabs opening it at once).
        self._locks: dict[str, asyncio.Lock] = {}

    async def open(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        pass

    async def get_thumbnail_png(self, slide_url: str) -> Optional[bytes]:
        """
        Return cached/generated PNG bytes for *slide_url*, or None if a
        thumbnail cannot be produced (unsupported file, no pyramid + too
        large, missing codecs, etc.) — callers should 404 in that case.
        """
        cache_path = self._cache_path(slide_url)
        if cache_path.exists():
            return cache_path.read_bytes()

        lock = self._locks.setdefault(cache_path.name, asyncio.Lock())
        async with lock:
            if cache_path.exists():          # double-check after acquiring lock
                return cache_path.read_bytes()

            png_bytes = await asyncio.to_thread(_generate_thumbnail, slide_url)
            if png_bytes is None:
                return None

            cache_path.write_bytes(png_bytes)
            return png_bytes

    def invalidate(self, slide_url: str) -> None:
        """Force-remove a cached thumbnail so the next request regenerates it."""
        self._cache_path(slide_url).unlink(missing_ok=True)

    def _cache_path(self, slide_url: str) -> Path:
        key = hashlib.sha1(slide_url.encode("utf-8")).hexdigest()[:20]
        return self._cache_dir / f"{key}.png"


# ── Generation (runs in a worker thread — sync, blocking I/O + CPU) ─────────

def _generate_thumbnail(slide_url: str) -> Optional[bytes]:
    try:
        import tifffile
    except ImportError:
        logger.warning(
            "ThumbnailService: tifffile not installed — cannot generate "
            "thumbnails. pip install tifffile imagecodecs Pillow"
        )
        return None

    fh = None
    try:
        fh = open_range_reader(slide_url)
        with tifffile.TiffFile(fh) as tf:
            series = tf.series[0]
            levels = getattr(series, "levels", None) or [series]

            if len(levels) > 1:
                arr = levels[-1].asarray()   # smallest pyramid level
            else:
                page = tf.pages[0]
                pixels = page.shape[0] * page.shape[1]
                if pixels > _MAX_DIRECT_DECODE_PIXELS:
                    logger.info(
                        "ThumbnailService: %s has no pyramid and is too large "
                        "(%d px) to thumbnail directly — skipping. Re-encode "
                        "as a tiled, pyramidal TIFF for a fast overview.",
                        slide_url, pixels,
                    )
                    return None
                arr = page.asarray()

        return _encode_png(arr)

    except Exception as exc:
        logger.warning(
            "ThumbnailService: failed to generate thumbnail for %s: %s",
            slide_url, exc,
        )
        return None
    finally:
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass


def _encode_png(arr) -> bytes:
    from PIL import Image

    if arr.ndim >= 3 and arr.shape[-1] > 3:
        arr = arr[..., :3]   # drop alpha/extra channels
    img = Image.fromarray(arr).convert("RGB")

    w, h = img.size
    longest = max(w, h)
    if longest > _THUMBNAIL_MAX_DIM:
        scale = _THUMBNAIL_MAX_DIM / longest
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


