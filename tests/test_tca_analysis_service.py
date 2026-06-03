"""
tests/test_tca_analysis_service.py
Unit tests for TCAAnalysisService.
All disk I/O is mocked — no GCS calls, no real filesystem.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from models.tca_domain import TCAAnalysisResult, TCAGridCell
from services.tca_analysis_service import (
    TCAAnalysisService,
    _compute_aggregates,
    _parse_density_bands,
    _parse_grid,
    _cell_from_dict,
)
from models.domain import OverlayConfig, SlideMetadata


# ── Fixtures ───────────────────────────────────────────────────────────────

SAMPLE_META = {
    "overall_tumour_pct": 62.5,
    "overall_stroma_pct": 27.3,
    "overall_necrosis_pct": 5.1,
    "hotspot_tumour_pct": 89.2,
    "hotspot_row": 2,
    "hotspot_col": 3,
    "density_bands": [
        {"label": "low",    "min_pct": 0.0,  "max_pct": 33.3, "cell_count": 120, "area_um2": 5000.0},
        {"label": "medium", "min_pct": 33.3, "max_pct": 66.6, "cell_count": 340, "area_um2": 12000.0},
        {"label": "high",   "min_pct": 66.6, "max_pct": 100.0,"cell_count": 85,  "area_um2": 3000.0},
    ],
}

SAMPLE_GRID = [
    [{"tumour_pct": 10.0, "stroma_pct": 80.0}, {"tumour_pct": 45.0, "stroma_pct": 50.0}],
    [{"tumour_pct": 70.0, "stroma_pct": 25.0}, {"tumour_pct": 89.2, "stroma_pct": 5.0, "necrosis_pct": 5.0}],
]

FLAT_GRID = [
    {"row": 0, "col": 0, "tumour_pct": 10.0, "stroma_pct": 80.0},
    {"row": 0, "col": 1, "tumour_pct": 45.0, "stroma_pct": 50.0},
    {"row": 1, "col": 0, "tumour_pct": 70.0, "stroma_pct": 25.0},
]


def _make_overlay_svc(meta: dict = None, grid=None, available: bool = True):
    """Build a mock TCAOverlayService that returns controlled assets."""
    svc = MagicMock()
    config = MagicMock()
    config.available = available

    if available:
        # Write fixture JSON to a temp-like MagicMock path
        meta_path = MagicMock(spec=Path)
        meta_path.read_text.return_value = json.dumps(meta or SAMPLE_META)
        config.metadata_path = str(meta_path)

        grid_path = None
        if grid is not None:
            grid_mock = MagicMock(spec=Path)
            grid_mock.read_text.return_value = json.dumps(grid)
            config.grid_path = str(grid_mock)
        else:
            config.grid_path = None
    else:
        config.metadata_path = None
        config.grid_path = None

    svc._build_config_from_dir.return_value = config
    svc._cache_dir = Path("/tmp/fake_cache")
    return svc


# ── _parse_density_bands ───────────────────────────────────────────────────

def test_parse_bands_list_format():
    bands = _parse_density_bands(SAMPLE_META)
    assert len(bands) == 3
    labels = [b.label for b in bands]
    assert "low" in labels and "high" in labels


def test_parse_bands_flat_format():
    flat_meta = {"low_count": 100, "medium_count": 200, "high_count": 50}
    bands = _parse_density_bands(flat_meta)
    assert len(bands) == 3
    low = next(b for b in bands if b.label == "low")
    assert low.cell_count == 100


def test_parse_bands_empty():
    bands = _parse_density_bands({})
    assert bands == []


def test_parse_bands_area():
    bands = _parse_density_bands(SAMPLE_META)
    high = next(b for b in bands if b.label == "high")
    assert high.area_um2 == pytest.approx(3000.0)


# ── _parse_grid ────────────────────────────────────────────────────────────

def test_parse_grid_2d_dict():
    cells = _parse_grid(SAMPLE_GRID)
    assert len(cells) == 4
    assert cells[0].row == 0 and cells[0].col == 0
    assert cells[0].tumour_pct == pytest.approx(10.0)


def test_parse_grid_flat_list():
    cells = _parse_grid(FLAT_GRID)
    assert len(cells) == 3
    assert cells[1].col == 1


def test_parse_grid_2d_float():
    grid = [[10.0, 45.0], [70.0, 89.2]]
    cells = _parse_grid(grid)
    assert len(cells) == 4
    assert cells[3].tumour_pct == pytest.approx(89.2)
    # stroma is computed as 100 - tumour
    assert cells[3].stroma_pct == pytest.approx(10.8)


def test_parse_grid_empty():
    assert _parse_grid([]) == []


def test_parse_grid_necrosis():
    cells = _parse_grid(SAMPLE_GRID)
    bottom_right = next(c for c in cells if c.row == 1 and c.col == 1)
    assert bottom_right.necrosis_pct == pytest.approx(5.0)


# ── _compute_aggregates ────────────────────────────────────────────────────

def test_aggregates_from_metadata():
    cells = _parse_grid(SAMPLE_GRID)
    agg = _compute_aggregates(SAMPLE_META, cells)
    assert agg.overall_tumour_pct == pytest.approx(62.5)
    assert agg.hotspot_tumour_pct == pytest.approx(89.2)
    assert agg.hotspot_row == 2
    assert agg.hotspot_col == 3


def test_aggregates_derived_from_grid():
    """When metadata has no aggregate fields, derive them from grid."""
    cells = _parse_grid(SAMPLE_GRID)
    agg = _compute_aggregates({}, cells)
    # Mean tumour across 4 cells: (10+45+70+89.2)/4
    expected = (10.0 + 45.0 + 70.0 + 89.2) / 4
    assert agg.overall_tumour_pct == pytest.approx(expected)
    assert agg.hotspot_tumour_pct == pytest.approx(89.2)


def test_aggregates_total_cells():
    cells = _parse_grid(SAMPLE_GRID)
    agg = _compute_aggregates({}, cells)
    assert agg.total_cells_analysed == 4


# ── TCAAnalysisService — integration ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_analysis_unavailable():
    svc = TCAAnalysisService()
    overlay = _make_overlay_svc(available=False)
    result = await svc.get_analysis("1087-25.svs", overlay)
    assert not result.available


@pytest.mark.asyncio
async def test_get_analysis_returns_result():
    svc = TCAAnalysisService()
    overlay = _make_overlay_svc(meta=SAMPLE_META)

    with patch("builtins.open"), \
         patch("pathlib.Path.read_text", return_value=json.dumps(SAMPLE_META)):
        # Patch the service's internal path reads
        overlay._build_config_from_dir.return_value = _make_overlay_svc(
            meta=SAMPLE_META
        )._build_config_from_dir.return_value

        result = await svc.get_analysis("1087-25.svs", overlay)

    assert result.available
    assert result.slide_key == "1087-25"
    assert result.aggregates is not None
    assert result.aggregates.overall_tumour_pct == pytest.approx(62.5)


@pytest.mark.asyncio
async def test_cache_hit():
    svc = TCAAnalysisService()
    overlay = _make_overlay_svc(available=False)

    # Manually seed the cache
    from models.tca_domain import TCAAnalysisResult
    cached = TCAAnalysisResult(slide_key="1087-25", available=True)
    svc._cache["1087-25"] = cached

    result = await svc.get_analysis("1087-25.svs", overlay)
    assert result is cached
    # overlay should NOT have been called
    overlay._build_config_from_dir.assert_not_called()


def test_invalidate():
    svc = TCAAnalysisService()
    from models.tca_domain import TCAAnalysisResult
    svc._cache["1087-25"] = TCAAnalysisResult(slide_key="1087-25", available=True)

    svc.invalidate("1087-25.svs")
    assert "1087-25" not in svc._cache


def test_list_analysed_slides():
    svc = TCAAnalysisService()
    from models.tca_domain import TCAAnalysisResult
    svc._cache["slide-a"] = TCAAnalysisResult(slide_key="slide-a", available=True)
    svc._cache["slide-b"] = TCAAnalysisResult(slide_key="slide-b", available=False)
    keys = svc.list_analysed_slides()
    assert "slide-a" in keys
    assert "slide-b" not in keys
