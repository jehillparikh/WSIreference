"""
tests/test_slide_normalization_service.py
Unit + fast integration tests for SlideNormalizationService.

The pure classification logic is tested directly (no real files needed).
The conversion path is exercised end-to-end against a tiny synthetic
pyramidal TIFF generated on the fly — real pyvips/tifffile, but small
enough to run in well under a second, unlike a real multi-GB slide.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from services.gcs_stream_service import path_to_local_url
from services.slide_normalization_service import (
    SlideNormalizationService,
    _classify,
    _convert_sync,
    _decide,
)

pytest.importorskip("pyvips")
pytest.importorskip("tifffile")


# ── _decide (pure logic) ────────────────────────────────────────────────────

def test_decide_skips_healthy_allowlisted_format():
    assert _decide("svs", is_tiled=True, num_levels=3, tile_dim=240) == "skip"


def test_decide_normalizes_non_allowlisted_vendor():
    assert _decide("philips", is_tiled=True, num_levels=10, tile_dim=256) == "normalize"


def test_decide_normalizes_allowlisted_but_unhealthy():
    # Single-level "svs" (no real pyramid) -- still gets normalized as a
    # defensive fallback, even though the vendor is allow-listed.
    assert _decide("svs", is_tiled=True, num_levels=1, tile_dim=240) == "normalize"


def test_decide_normalizes_oversized_tiles():
    assert _decide("svs", is_tiled=True, num_levels=3, tile_dim=1024) == "normalize"


def test_decide_normalizes_untiled():
    assert _decide("svs", is_tiled=False, num_levels=3, tile_dim=240) == "normalize"


def test_decide_is_case_insensitive():
    assert _decide("SVS", is_tiled=True, num_levels=3, tile_dim=240) == "skip"


# ── _cache_path ──────────────────────────────────────────────────────────────

def _settings(tmp_path: Path) -> Settings:
    s = Settings.__new__(Settings)  # bypass __init__/env loading
    s.normalized_slide_cache_dir = str(tmp_path / "normalized")
    return s


def test_cache_path_is_stable_and_url_specific(tmp_path):
    svc = SlideNormalizationService(settings=_settings(tmp_path))
    p1 = svc._cache_path("local:///a/slide1.tif")
    p2 = svc._cache_path("local:///a/slide1.tif")
    p3 = svc._cache_path("local:///a/slide2.tif")
    assert p1 == p2
    assert p1 != p3


# ── Fixture: a tiny synthetic pyramidal TIFF ────────────────────────────────

@pytest.fixture
def tiny_pyramid_tiff(tmp_path) -> Path:
    import numpy as np
    import tifffile

    path = tmp_path / "tiny.tiff"
    base = np.random.randint(0, 255, size=(256, 256, 3), dtype=np.uint8)
    levels = [base, base[::2, ::2], base[::4, ::4]]

    with tifffile.TiffWriter(str(path), bigtiff=False) as tif:
        opts = dict(tile=(64, 64), photometric="rgb", compression="deflate")
        tif.write(levels[0], subfiletype=0, **opts)
        for level in levels[1:]:
            tif.write(level, subfiletype=1, **opts)

    return path


# ── Classification against a real (tiny) file ───────────────────────────────

def test_classify_generic_tiled_pyramid_is_normalized(tiny_pyramid_tiff):
    # Not "svs" kind -- normalized regardless of how healthy it looks,
    # matching the conservative default (only Aperio is allow-listed).
    url = path_to_local_url(tiny_pyramid_tiff)
    assert _classify(url) == "normalize"


# ── End-to-end conversion ───────────────────────────────────────────────────

def test_convert_sync_produces_clean_pyramid(tiny_pyramid_tiff, tmp_path):
    import tifffile

    url = path_to_local_url(tiny_pyramid_tiff)
    dst = tmp_path / "out.tif"

    _convert_sync(url, dst)

    assert dst.exists()
    tf = tifffile.TiffFile(str(dst))
    assert len(tf.series) == 1
    page0 = tf.pages[0]
    assert page0.is_tiled
    assert page0.tilewidth == 256
    assert page0.tilelength == 256


def test_convert_sync_preserves_image_description(tmp_path):
    import numpy as np
    import tifffile

    src = tmp_path / "with_desc.tiff"
    base = np.random.randint(0, 255, size=(256, 256, 3), dtype=np.uint8)
    with tifffile.TiffWriter(str(src), bigtiff=False) as tif:
        tif.write(
            base, subfiletype=0, tile=(64, 64), photometric="rgb",
            compression="deflate", description="hello from the original scanner",
        )
        tif.write(base[::2, ::2], subfiletype=1, tile=(64, 64),
                   photometric="rgb", compression="deflate")

    dst = tmp_path / "out.tif"
    _convert_sync(path_to_local_url(src), dst)

    tf = tifffile.TiffFile(str(dst))
    tag = tf.pages[0].tags.get("ImageDescription")
    assert tag is not None
    assert "hello from the original scanner" in tag.value


# ── resolve_serving_url orchestration ───────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_serving_url_returns_original_for_skip_kind(tmp_path, monkeypatch):
    import services.slide_normalization_service as mod

    monkeypatch.setattr(mod, "_classify", lambda url: "skip")
    svc = SlideNormalizationService(settings=_settings(tmp_path))
    await svc.open()

    result = await svc.resolve_serving_url("gs://bucket/healthy.svs")
    assert result == "gs://bucket/healthy.svs"
    assert svc._classification["gs://bucket/healthy.svs"] == "skip"


@pytest.mark.asyncio
async def test_resolve_serving_url_starts_background_conversion(tmp_path, monkeypatch):
    import asyncio
    import services.slide_normalization_service as mod

    started = asyncio.Event()

    async def fake_convert_and_cache(self, slide_url):
        started.set()

    monkeypatch.setattr(mod, "_classify", lambda url: "normalize")
    monkeypatch.setattr(SlideNormalizationService, "_convert_and_cache", fake_convert_and_cache)

    svc = SlideNormalizationService(settings=_settings(tmp_path))
    await svc.open()

    result = await svc.resolve_serving_url("gs://bucket/unhealthy.tif")
    # Falls back to the original immediately -- never blocks on conversion.
    assert result == "gs://bucket/unhealthy.tif"

    await asyncio.wait_for(started.wait(), timeout=2)


@pytest.mark.asyncio
async def test_resolve_serving_url_uses_cached_normalized_copy(tmp_path):
    svc = SlideNormalizationService(settings=_settings(tmp_path))
    await svc.open()

    cache_path = svc._cache_path("gs://bucket/already-done.tif")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"fake tiff bytes")

    result = await svc.resolve_serving_url("gs://bucket/already-done.tif")
    assert result == path_to_local_url(cache_path)
