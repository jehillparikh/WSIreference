"""
models/tca_domain.py
Domain models for Tumour Content Analysis (TCA) results.

Separation of concerns
──────────────────────
TCAOverlayService  → downloads the TCA zip, extracts PNG/JSON assets to disk.
TCAAnalysisService → parses those JSON assets into the typed models below.

Coordinate / units convention
──────────────────────────────
• Percentages are 0.0–100.0 (not 0.0–1.0).
• Areas are in µm² when mpp is available, None otherwise.
• Grid rows/cols are zero-indexed from the top-left of the slide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TCADensityBand:
    """A named density stratum within the slide (e.g. low / medium / high)."""
    label: str               # e.g. "low", "medium", "high"
    min_pct: float           # lower bound of tumour-density band (%)
    max_pct: float           # upper bound (%)
    cell_count: int          # number of cells in this band
    area_um2: Optional[float] = None   # total area covered (µm²), None if no mpp


@dataclass
class TCAGridCell:
    """
    Single spatial grid cell from the TCA heatmap.
    Percentages sum to ≤ 100; the remainder is background / artefact.
    """
    row: int
    col: int
    tumour_pct: float                  # 0.0–100.0
    stroma_pct: float                  # 0.0–100.0
    necrosis_pct: Optional[float] = None
    inflammation_pct: Optional[float] = None
    other_pct: Optional[float] = None

    @property
    def tissue_pct(self) -> float:
        """Sum of all labelled tissue classes."""
        return self.tumour_pct + self.stroma_pct + (self.necrosis_pct or 0.0)


@dataclass
class TCAAggregates:
    """Slide-level aggregate statistics derived from the grid / cell data."""
    overall_tumour_pct: Optional[float]   # mean tumour % across tissue grid cells
    overall_stroma_pct: Optional[float]
    overall_necrosis_pct: Optional[float]
    hotspot_tumour_pct: Optional[float]   # highest single-cell tumour %
    hotspot_row: Optional[int]            # grid position of the hotspot cell
    hotspot_col: Optional[int]
    total_cells_analysed: int = 0
    tissue_cells: int = 0                 # cells with > 0 tissue content


@dataclass
class TCAAnalysisResult:
    """
    Complete TCA analysis for a single slide.

    ``available`` is False when:
    • No TCA zip has been downloaded yet (TCAOverlayService returns available=False).
    • The metadata JSON is missing or unparseable.

    When available=True all Optional fields are populated where the source
    data permits; fields the pipeline did not produce remain None.
    """
    slide_key: str
    available: bool

    aggregates: Optional[TCAAggregates] = None
    density_bands: list[TCADensityBand] = field(default_factory=list)
    grid_cells: list[TCAGridCell] = field(default_factory=list)

    analysed_at: Optional[datetime] = None
    raw_metadata: Optional[dict] = None    # verbatim _metadata.json for pass-through

    @property
    def hotspot_tumour_pct(self) -> Optional[float]:
        return self.aggregates.hotspot_tumour_pct if self.aggregates else None

    @property
    def overall_tumour_pct(self) -> Optional[float]:
        return self.aggregates.overall_tumour_pct if self.aggregates else None
