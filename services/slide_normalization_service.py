"""
services/slide_normalization_service.py
Classifies each slide as already well-supported by the client-side tile
viewer, or in need of re-encoding into a clean, standard pyramidal TIFF —
and performs that re-encoding once, in the background, cached to disk.

Why this exists
────────────────
Investigating a real slowdown on a Philips-format TIFF found two things a
single vendor's export can get "wrong" relative to what the browser's
GeoTIFFTileSource actually expects well: a metadata inconsistency tifffile
itself warns about, and tiles picked far larger (512x512, 14-27KB each)
than necessary — both confirmed fixed by re-encoding with pyvips into a
clean, 256x256-tiled pyramidal TIFF (tiles then averaged 3.5-6KB, and the
metadata warning disappeared). Rather than hand-fix each problematic file,
this service applies that recipe automatically to any future slide that
needs it, while leaving already-good formats (e.g. Aperio SVS, which the
client library has explicit native support for) untouched.

Guarantees
──────────
• The ORIGINAL file is never opened for writing, moved, or deleted — only
  ever read from, either directly (local) or via Range requests (remote).
  It remains the single, authoritative, full-detail-and-metadata copy.
• Tile serving always has a safe fallback to the original: before
  classification, during background conversion, and if conversion fails.
  Normalization can only make things faster — it can never break serving.
• Conversion runs in a background asyncio task, not on the request path —
  it can take well over a minute for a large slide, and must not hold up
  a browser waiting to open a slide.
"""
from __future__ import annotations

import asyncio
import gc
import hashlib
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional

from config.settings import Settings, get_settings
from services._slide_io import download_to_temp, open_range_reader
from services.gcs_stream_service import is_local_url, local_url_to_path, path_to_local_url

logger = logging.getLogger(__name__)

# Vendor "kind" values (as reported by tifffile's series[0].kind) that the
# client-side geotiff-tilesource library already handles well and that
# don't need re-encoding.
_ALLOWLISTED_KINDS = {"svs"}

# Health-check thresholds — even an allow-listed vendor gets normalized if
# its actual pyramid doesn't look right (defensive fallback for an atypical
# or damaged file claiming a well-supported format).
_MIN_PYRAMID_LEVELS = 2
_MAX_HEALTHY_TILE_DIM = 512

# Normalized output parameters — validated by hand against a real Philips
# WSI file: Q=92 matches/slightly exceeds the source's sharpness (Laplacian
# variance 261.3 vs 253.5 baseline; Q=85 measured a real ~8% softening from
# JPEG decode->recompress generation loss), and 256x256 tiles averaged
# 3.5-6KB vs the original's unhealthy 512x512 tiles at 14-27KB.
_TILE_SIZE = 256
_JPEG_QUALITY = 92


class SlideNormalizationService:
    """Singleton per process — same lifecycle pattern as ThumbnailService."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._cache_dir = Path(self._settings.normalized_slide_cache_dir)
        # slide_url -> "skip" | "normalize". Classification is stable for a
        # process's lifetime — re-opening the TIFF header on every tile
        # request would defeat the point.
        self._classification: dict[str, str] = {}
        # slide_url -> in-flight background conversion task, so concurrent
        # tile requests for the same never-before-seen slide don't each
        # start their own conversion.
        self._inflight: dict[str, asyncio.Task] = {}

    async def open(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        for task in list(self._inflight.values()):
            task.cancel()

    async def resolve_serving_url(self, slide_url: str) -> str:
        """
        Return the URL tile-streaming should actually read from: a cached
        normalized copy if one is ready, otherwise *slide_url* unchanged —
        kicking off background normalization as a side effect if this slide
        needs it and isn't already being converted.
        """
        cache_path = self._cache_path(slide_url)
        if cache_path.exists():
            return path_to_local_url(cache_path)

        kind = self._classification.get(slide_url)
        if kind is None:
            kind = await asyncio.to_thread(_classify, slide_url)
            self._classification[slide_url] = kind

        if kind == "skip":
            return slide_url

        if slide_url not in self._inflight:
            task = asyncio.create_task(self._convert_and_cache(slide_url))
            self._inflight[slide_url] = task
            task.add_done_callback(lambda _t, u=slide_url: self._inflight.pop(u, None))

        return slide_url   # serve the original while conversion runs

    def invalidate(self, slide_url: str) -> None:
        """Force-remove a cached normalized copy so it's regenerated."""
        self._cache_path(slide_url).unlink(missing_ok=True)
        self._classification.pop(slide_url, None)

    def _cache_path(self, slide_url: str) -> Path:
        key = hashlib.sha1(slide_url.encode("utf-8")).hexdigest()[:20]
        return self._cache_dir / f"{key}.tif"

    async def _convert_and_cache(self, slide_url: str) -> None:
        cache_path = self._cache_path(slide_url)
        logger.info("SlideNormalizationService: normalizing %s", slide_url)
        t0 = time.perf_counter()
        try:
            await asyncio.to_thread(_convert_sync, slide_url, cache_path)
            logger.info(
                "SlideNormalizationService: normalized %s in %.1fs -> %s",
                slide_url, time.perf_counter() - t0, cache_path,
            )
        except Exception as exc:
            logger.warning(
                "SlideNormalizationService: normalization failed for %s "
                "after %.1fs (%s) -- serving the original slide indefinitely.",
                slide_url, time.perf_counter() - t0, exc,
            )


# ── Classification (runs in a worker thread — cheap, header-only reads) ────

def _decide(kind: str, is_tiled: bool, num_levels: int, tile_dim: int) -> str:
    """Pure decision logic, kept separate from I/O so it's trivially unit-testable."""
    healthy = (
        is_tiled
        and num_levels >= _MIN_PYRAMID_LEVELS
        and 0 < tile_dim <= _MAX_HEALTHY_TILE_DIM
    )
    if kind.lower() in _ALLOWLISTED_KINDS and healthy:
        return "skip"
    return "normalize"


def _classify(slide_url: str) -> str:
    try:
        import tifffile
    except ImportError:
        return "skip"   # can't inspect -- don't block tile serving on a missing dep

    fh = None
    try:
        fh = open_range_reader(slide_url)
        with tifffile.TiffFile(fh) as tf:
            series = tf.series[0]
            page0 = tf.pages[0]
            levels = getattr(series, "levels", None) or [series]
            tile_dim = max(page0.tilewidth or 0, page0.tilelength or 0)
            return _decide(
                kind=str(getattr(series, "kind", "") or ""),
                is_tiled=bool(page0.is_tiled),
                num_levels=len(levels),
                tile_dim=tile_dim,
            )
    except Exception as exc:
        logger.warning(
            "SlideNormalizationService: classification failed for %s (%s) "
            "-- leaving unnormalized.", slide_url, exc,
        )
        return "skip"
    finally:
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass


# ── Conversion (runs in a worker thread — sync, blocking I/O + CPU) ────────

def _convert_sync(slide_url: str, dst_path: Path) -> None:
    import pyvips

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    # Written into the SAME directory as dst_path so the final os.replace
    # is an atomic same-filesystem rename -- no reader ever sees a partial file.
    tmp_out = dst_path.with_suffix(dst_path.suffix + ".tmp")

    with tempfile.TemporaryDirectory() as tmp_dir:
        if is_local_url(slide_url):
            src_path = local_url_to_path(slide_url)
        else:
            # libvips' TIFF loader needs a real filesystem path -- it can't
            # consume the Range-reading file-like object tifffile/geotiff.js
            # can. Stream the original down once; the remote copy in GCS is
            # never touched.
            src_path = Path(tmp_dir) / "source.tif"
            download_to_temp(slide_url, src_path)

        description = _read_image_description(src_path)

        img = pyvips.Image.new_from_file(str(src_path), access="sequential", page=0)
        img = img.copy()
        if description:
            img.set_type(pyvips.GValue.gstr_type, "image-description", description)

        img.tiffsave(
            str(tmp_out),
            tile=True, tile_width=_TILE_SIZE, tile_height=_TILE_SIZE,
            pyramid=True, compression="jpeg", Q=_JPEG_QUALITY, bigtiff=True,
        )

        # Release libvips' handle on src_path before the TemporaryDirectory
        # cleanup below tries to delete it -- on Windows, a file still open
        # via pyvips' lazy/sequential access cannot be unlinked, and the
        # cleanup silently fails with PermissionError otherwise.
        del img
        gc.collect()

    tmp_out.replace(dst_path)


def _read_image_description(path: Path) -> Optional[str]:
    """Best-effort: carry the original's ImageDescription tag (vendor
    metadata -- MPP, scanner info, etc.) forward into the normalized copy."""
    try:
        import tifffile

        with tifffile.TiffFile(str(path)) as tf:
            tag = tf.pages[0].tags.get("ImageDescription")
            return tag.value if tag else None
    except Exception:
        return None
