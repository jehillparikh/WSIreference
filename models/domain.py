"""
models/domain.py
Pure-Python dataclasses that represent core domain objects.
No DB, no network — just typed shapes that services exchange.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SlideMetadata:
    width: int
    height: int
    mpp: Optional[float]           # microns-per-pixel
    objective_power: Optional[int]
    vendor: Optional[str]


@dataclass
class Slide:
    slide_id: str                  # e.g. "B1"
    block_id: str                  # e.g. "A1"
    slide_url: str                 # signed or public GCS HTTPS URL
    thumbnail_url: Optional[str]
    tca_url: Optional[str]
    status: str                    # "FINISHED" | "PROCESSING" | …
    meta_data: Optional[SlideMetadata]

    # Derived — set by the viewer when it maps URL → local filename key
    filename: Optional[str] = None   # e.g. "1087-25.svs"


@dataclass
class WSISession:
    """
    In-memory session produced by SlideSessionService.
    Key:  pid_<patient_id>_<event_id>_<selected_slide_id>
    """
    patient_id: str
    event_id: str
    selected_slide_id: str

    slides: list[Slide] = field(default_factory=list)
    default_slide: Optional[Slide] = None  # the slide matching selected_slide_id

    created_at: datetime = field(default_factory=datetime.utcnow)
    refreshed_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def cache_key(self) -> str:
        return f"pid_{self.patient_id}_{self.event_id}_{self.selected_slide_id}"

    def slide_by_filename(self, filename: str) -> Optional[Slide]:
        return next((s for s in self.slides if s.filename == filename), None)

    def slide_url_by_filename(self, filename: str) -> Optional[str]:
        slide = self.slide_by_filename(filename)
        return slide.slide_url if slide else None


# ── Annotation models ─────────────────────────────────────────────────────
# Coordinates are in OpenSeadragon viewport space (0.0–1.0 on both axes).
# The frontend redraws the SVG overlay on every zoom/pan, so storage is
# purely positional — no pixel math lives here.

@dataclass
class ViewportPoint:
    x: float   # 0.0–1.0
    y: float   # 0.0–1.0


@dataclass
class PolygonAnnotation:
    id: str
    vertices: list[ViewportPoint]           # min 3, closed implicitly
    label: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class LabelAnnotation:
    id: str
    anchor: ViewportPoint                   # where the leader line points
    offset: ViewportPoint                   # where the text box sits
    text: str
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class MeasureAnnotation:
    id: str
    start: ViewportPoint
    end: ViewportPoint
    # Distance in µm when mpp is known; None means pixels were used instead
    distance_um: Optional[float] = None
    distance_px: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SlideAnnotations:
    """All annotations for a single slide (keyed by filename stem)."""
    slide_key: str                          # filename stem, e.g. "1087-25"
    polygons: list[PolygonAnnotation] = field(default_factory=list)
    labels: list[LabelAnnotation] = field(default_factory=list)
    measures: list[MeasureAnnotation] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.polygons or self.labels or self.measures)


@dataclass
class OverlayConfig:
    available: bool
    density_image_path: Optional[str] = None   # absolute local filesystem path
    metadata_path: Optional[str] = None
    grid_path: Optional[str] = None
    slide_metadata: Optional[SlideMetadata] = None
