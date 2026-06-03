"""
services/annotation_service.py
In-process annotation store for the WSI viewer.

Spec (Section 5)
────────────────
• Polygons, labels, and distance measures are browser-local — they are NOT
  persisted to the pathology API and are lost when the server process
  restarts.  This is intentional per the spec; the service interface is
  deliberately designed so a future Redis/DB backend can be dropped in
  without changing any caller.

• Annotations are stored per slide key (filename stem).  The frontend
  hides/shows per-slide sets on slide switch; the backend mirrors that
  boundary.

• Coordinates are in OpenSeadragon viewport space (0.0–1.0).

• Distance measures are stored in µm when mpp is available, pixels
  otherwise.  The service computes the distance from raw viewport
  coordinates + the slide's mpp — callers pass those values in; no
  slide-metadata lookup happens here.

• Only one annotation of each type shares an ID — IDs are opaque
  strings (UUIDs from the client).  Duplicate IDs on add are rejected.

• A clear_slide() wipes all annotations for a slide, matching the
  "Clear all" toolbar button.

Thread safety: asyncio single-event-loop — asyncio.Lock per slide key.
"""
from __future__ import annotations

import math
import uuid
import logging
from typing import Optional

from models.domain import (
    LabelAnnotation,
    MeasureAnnotation,
    PolygonAnnotation,
    SlideAnnotations,
    ViewportPoint,
)

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────


class AnnotationNotFoundError(KeyError):
    pass


class DuplicateAnnotationError(ValueError):
    pass


class InvalidAnnotationError(ValueError):
    pass


# ── Service ───────────────────────────────────────────────────────────────


class AnnotationService:
    """
    CRUD store for per-slide annotations.

    All mutating methods return the updated SlideAnnotations so callers
    can serialise + push to the client in one step.
    """

    def __init__(self) -> None:
        # slide_key → SlideAnnotations
        self._store: dict[str, SlideAnnotations] = {}

    # ── Read ──────────────────────────────────────────────────────────────

    def get_slide_annotations(self, slide_key: str) -> SlideAnnotations:
        """Return annotations for *slide_key*, creating an empty set if absent."""
        if slide_key not in self._store:
            self._store[slide_key] = SlideAnnotations(slide_key=slide_key)
        return self._store[slide_key]

    def list_slide_keys(self) -> list[str]:
        """Return all slide keys that have at least one annotation."""
        return [k for k, v in self._store.items() if not v.is_empty()]

    # ── Polygon ───────────────────────────────────────────────────────────

    def add_polygon(
        self,
        slide_key: str,
        vertices: list[dict],
        label: Optional[str] = None,
        annotation_id: Optional[str] = None,
    ) -> PolygonAnnotation:
        """
        Add a polygon annotation.

        Parameters
        ──────────
        vertices    List of {"x": float, "y": float} dicts in viewport space.
        label       Optional text label attached to the polygon.
        annotation_id  Client-supplied UUID; auto-generated if omitted.

        Raises InvalidAnnotationError if fewer than 3 vertices are provided.
        Raises DuplicateAnnotationError if the ID already exists.
        """
        _require_min_vertices(vertices, 3)
        ann_id = annotation_id or str(uuid.uuid4())
        sa = self.get_slide_annotations(slide_key)
        _check_no_duplicate(ann_id, [p.id for p in sa.polygons], "polygon")

        poly = PolygonAnnotation(
            id=ann_id,
            vertices=[ViewportPoint(x=v["x"], y=v["y"]) for v in vertices],
            label=label or None,
        )
        sa.polygons.append(poly)
        logger.debug("add_polygon slide=%s id=%s vertices=%d", slide_key, ann_id, len(vertices))
        return poly

    def update_polygon(
        self,
        slide_key: str,
        annotation_id: str,
        vertices: Optional[list[dict]] = None,
        label: Optional[str] = None,
    ) -> PolygonAnnotation:
        """Update vertices and/or label of an existing polygon."""
        poly = self._find_polygon(slide_key, annotation_id)
        if vertices is not None:
            _require_min_vertices(vertices, 3)
            poly.vertices = [ViewportPoint(x=v["x"], y=v["y"]) for v in vertices]
        if label is not None:
            poly.label = label or None
        return poly

    def delete_polygon(self, slide_key: str, annotation_id: str) -> None:
        sa = self.get_slide_annotations(slide_key)
        before = len(sa.polygons)
        sa.polygons = [p for p in sa.polygons if p.id != annotation_id]
        if len(sa.polygons) == before:
            raise AnnotationNotFoundError(annotation_id)

    # ── Label ─────────────────────────────────────────────────────────────

    def add_label(
        self,
        slide_key: str,
        anchor: dict,
        offset: dict,
        text: str,
        annotation_id: Optional[str] = None,
    ) -> LabelAnnotation:
        """
        Add a text label annotation.

        Parameters
        ──────────
        anchor  {"x": float, "y": float} — where the leader line points.
        offset  {"x": float, "y": float} — where the text box is placed.
        text    Label text (non-empty).
        """
        if not text or not text.strip():
            raise InvalidAnnotationError("Label text must not be empty")
        ann_id = annotation_id or str(uuid.uuid4())
        sa = self.get_slide_annotations(slide_key)
        _check_no_duplicate(ann_id, [l.id for l in sa.labels], "label")

        lbl = LabelAnnotation(
            id=ann_id,
            anchor=ViewportPoint(x=anchor["x"], y=anchor["y"]),
            offset=ViewportPoint(x=offset["x"], y=offset["y"]),
            text=text.strip(),
        )
        sa.labels.append(lbl)
        logger.debug("add_label slide=%s id=%s text=%r", slide_key, ann_id, lbl.text)
        return lbl

    def update_label(
        self,
        slide_key: str,
        annotation_id: str,
        text: Optional[str] = None,
        offset: Optional[dict] = None,
    ) -> LabelAnnotation:
        lbl = self._find_label(slide_key, annotation_id)
        if text is not None:
            if not text.strip():
                raise InvalidAnnotationError("Label text must not be empty")
            lbl.text = text.strip()
        if offset is not None:
            lbl.offset = ViewportPoint(x=offset["x"], y=offset["y"])
        return lbl

    def delete_label(self, slide_key: str, annotation_id: str) -> None:
        sa = self.get_slide_annotations(slide_key)
        before = len(sa.labels)
        sa.labels = [l for l in sa.labels if l.id != annotation_id]
        if len(sa.labels) == before:
            raise AnnotationNotFoundError(annotation_id)

    # ── Measure ───────────────────────────────────────────────────────────

    def add_measure(
        self,
        slide_key: str,
        start: dict,
        end: dict,
        slide_width_px: Optional[int] = None,
        slide_height_px: Optional[int] = None,
        mpp: Optional[float] = None,
        annotation_id: Optional[str] = None,
    ) -> MeasureAnnotation:
        """
        Add a distance-measure annotation.

        Parameters
        ──────────
        start / end     {"x": float, "y": float} in viewport (0–1) space.
        slide_width_px  Full-resolution slide width in pixels (from meta_data).
        slide_height_px Full-resolution slide height in pixels.
        mpp             Microns-per-pixel.  When supplied, distance_um is
                        computed; otherwise only distance_px is set.

        The viewport→pixel conversion uses the slide's full-resolution
        dimensions so measurements are consistent regardless of zoom level.
        """
        ann_id = annotation_id or str(uuid.uuid4())
        sa = self.get_slide_annotations(slide_key)
        _check_no_duplicate(ann_id, [m.id for m in sa.measures], "measure")

        p0 = ViewportPoint(x=start["x"], y=start["y"])
        p1 = ViewportPoint(x=end["x"], y=end["y"])

        distance_px, distance_um = _compute_distance(
            p0, p1, slide_width_px, slide_height_px, mpp
        )

        m = MeasureAnnotation(
            id=ann_id,
            start=p0,
            end=p1,
            distance_um=distance_um,
            distance_px=distance_px,
        )
        sa.measures.append(m)
        logger.debug(
            "add_measure slide=%s id=%s px=%.1f µm=%s",
            slide_key, ann_id, distance_px or 0, f"{distance_um:.2f}" if distance_um else "n/a",
        )
        return m

    def delete_measure(self, slide_key: str, annotation_id: str) -> None:
        sa = self.get_slide_annotations(slide_key)
        before = len(sa.measures)
        sa.measures = [m for m in sa.measures if m.id != annotation_id]
        if len(sa.measures) == before:
            raise AnnotationNotFoundError(annotation_id)

    # ── Bulk operations ───────────────────────────────────────────────────

    def clear_slide(self, slide_key: str) -> None:
        """Remove all annotations for *slide_key* (matches "Clear all" button)."""
        self._store.pop(slide_key, None)
        logger.debug("clear_slide slide=%s", slide_key)

    def clear_all(self) -> None:
        """Wipe the entire in-process store.  Called on session eviction."""
        self._store.clear()

    # ── Snapshot metadata ─────────────────────────────────────────────────

    def annotation_counts(self, slide_key: str) -> dict:
        """
        Return a lightweight summary dict used by the snapshot route to
        embed metadata in the exported PNG filename / headers.
        """
        sa = self.get_slide_annotations(slide_key)
        return {
            "polygons": len(sa.polygons),
            "labels": len(sa.labels),
            "measures": len(sa.measures),
            "total": len(sa.polygons) + len(sa.labels) + len(sa.measures),
        }

    # ── Internal finders ──────────────────────────────────────────────────

    def _find_polygon(self, slide_key: str, ann_id: str) -> PolygonAnnotation:
        sa = self.get_slide_annotations(slide_key)
        for p in sa.polygons:
            if p.id == ann_id:
                return p
        raise AnnotationNotFoundError(ann_id)

    def _find_label(self, slide_key: str, ann_id: str) -> LabelAnnotation:
        sa = self.get_slide_annotations(slide_key)
        for l in sa.labels:
            if l.id == ann_id:
                return l
        raise AnnotationNotFoundError(ann_id)

    def _find_measure(self, slide_key: str, ann_id: str) -> MeasureAnnotation:
        sa = self.get_slide_annotations(slide_key)
        for m in sa.measures:
            if m.id == ann_id:
                return m
        raise AnnotationNotFoundError(ann_id)


# ── Module helpers ────────────────────────────────────────────────────────


def _require_min_vertices(vertices: list, minimum: int) -> None:
    if len(vertices) < minimum:
        raise InvalidAnnotationError(
            f"Polygon requires at least {minimum} vertices, got {len(vertices)}"
        )


def _check_no_duplicate(ann_id: str, existing_ids: list[str], kind: str) -> None:
    if ann_id in existing_ids:
        raise DuplicateAnnotationError(f"{kind} id={ann_id!r} already exists")


def _compute_distance(
    p0: ViewportPoint,
    p1: ViewportPoint,
    width_px: Optional[int],
    height_px: Optional[int],
    mpp: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """
    Convert two viewport-space points to a pixel distance, then to µm.

    Viewport space is normalised to [0, 1] on the longest axis by
    OpenSeadragon.  We recover pixel distances by scaling back with the
    full-resolution slide dimensions.  If dimensions are unavailable we
    fall back to a unit-space Euclidean distance (dimensionless).
    """
    if width_px and height_px:
        dx = (p1.x - p0.x) * width_px
        dy = (p1.y - p0.y) * height_px
    else:
        dx = p1.x - p0.x
        dy = p1.y - p0.y

    distance_px = math.sqrt(dx * dx + dy * dy)
    distance_um = (distance_px * mpp) if mpp else None
    return distance_px, distance_um
