"""
tests/test_annotation_service.py
Unit tests for AnnotationService.  Pure in-process — no I/O.
"""
from __future__ import annotations

import math
import pytest

from services.annotation_service import (
    AnnotationNotFoundError,
    AnnotationService,
    DuplicateAnnotationError,
    InvalidAnnotationError,
    _compute_distance,
)
from models.domain import SlideAnnotations


SLIDE = "1087-25"

# Three viewport-space points forming a right triangle
TRI = [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.1}, {"x": 0.1, "y": 0.2}]


# ── get_slide_annotations ─────────────────────────────────────────────────

def test_get_creates_empty_set():
    svc = AnnotationService()
    sa = svc.get_slide_annotations(SLIDE)
    assert isinstance(sa, SlideAnnotations)
    assert sa.is_empty()


def test_get_returns_same_object():
    svc = AnnotationService()
    assert svc.get_slide_annotations(SLIDE) is svc.get_slide_annotations(SLIDE)


# ── Polygon ───────────────────────────────────────────────────────────────

def test_add_polygon_basic():
    svc = AnnotationService()
    poly = svc.add_polygon(SLIDE, TRI)
    assert poly.id
    assert len(poly.vertices) == 3
    assert poly.label is None


def test_add_polygon_with_label_and_id():
    svc = AnnotationService()
    poly = svc.add_polygon(SLIDE, TRI, label="ROI", annotation_id="abc-123")
    assert poly.id == "abc-123"
    assert poly.label == "ROI"


def test_add_polygon_too_few_vertices():
    svc = AnnotationService()
    with pytest.raises(InvalidAnnotationError):
        svc.add_polygon(SLIDE, [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}])


def test_add_polygon_duplicate_id():
    svc = AnnotationService()
    svc.add_polygon(SLIDE, TRI, annotation_id="dup")
    with pytest.raises(DuplicateAnnotationError):
        svc.add_polygon(SLIDE, TRI, annotation_id="dup")


def test_update_polygon_vertices():
    svc = AnnotationService()
    poly = svc.add_polygon(SLIDE, TRI, annotation_id="p1")
    new_verts = [{"x": 0.3, "y": 0.3}, {"x": 0.4, "y": 0.3}, {"x": 0.35, "y": 0.4}]
    updated = svc.update_polygon(SLIDE, "p1", vertices=new_verts)
    assert updated.vertices[0].x == pytest.approx(0.3)


def test_update_polygon_label_only():
    svc = AnnotationService()
    svc.add_polygon(SLIDE, TRI, annotation_id="p1")
    updated = svc.update_polygon(SLIDE, "p1", label="Tumour")
    assert updated.label == "Tumour"
    assert len(updated.vertices) == 3  # unchanged


def test_update_polygon_not_found():
    svc = AnnotationService()
    with pytest.raises(AnnotationNotFoundError):
        svc.update_polygon(SLIDE, "ghost", label="x")


def test_delete_polygon():
    svc = AnnotationService()
    svc.add_polygon(SLIDE, TRI, annotation_id="del-me")
    svc.delete_polygon(SLIDE, "del-me")
    sa = svc.get_slide_annotations(SLIDE)
    assert not sa.polygons


def test_delete_polygon_not_found():
    svc = AnnotationService()
    with pytest.raises(AnnotationNotFoundError):
        svc.delete_polygon(SLIDE, "missing")


# ── Label ─────────────────────────────────────────────────────────────────

ANCHOR = {"x": 0.5, "y": 0.5}
OFFSET = {"x": 0.55, "y": 0.45}


def test_add_label_basic():
    svc = AnnotationService()
    lbl = svc.add_label(SLIDE, ANCHOR, OFFSET, "Mitosis")
    assert lbl.text == "Mitosis"
    assert lbl.anchor.x == pytest.approx(0.5)


def test_add_label_empty_text():
    svc = AnnotationService()
    with pytest.raises(InvalidAnnotationError):
        svc.add_label(SLIDE, ANCHOR, OFFSET, "   ")


def test_add_label_strips_whitespace():
    svc = AnnotationService()
    lbl = svc.add_label(SLIDE, ANCHOR, OFFSET, "  Necrosis  ")
    assert lbl.text == "Necrosis"


def test_update_label_text():
    svc = AnnotationService()
    svc.add_label(SLIDE, ANCHOR, OFFSET, "Old", annotation_id="l1")
    updated = svc.update_label(SLIDE, "l1", text="New")
    assert updated.text == "New"


def test_update_label_offset():
    svc = AnnotationService()
    svc.add_label(SLIDE, ANCHOR, OFFSET, "X", annotation_id="l1")
    new_offset = {"x": 0.7, "y": 0.3}
    updated = svc.update_label(SLIDE, "l1", offset=new_offset)
    assert updated.offset.x == pytest.approx(0.7)


def test_delete_label():
    svc = AnnotationService()
    svc.add_label(SLIDE, ANCHOR, OFFSET, "Delete me", annotation_id="l-del")
    svc.delete_label(SLIDE, "l-del")
    assert not svc.get_slide_annotations(SLIDE).labels


def test_delete_label_not_found():
    svc = AnnotationService()
    with pytest.raises(AnnotationNotFoundError):
        svc.delete_label(SLIDE, "ghost")


# ── Measure ───────────────────────────────────────────────────────────────

def test_add_measure_with_mpp():
    svc = AnnotationService()
    m = svc.add_measure(
        SLIDE,
        start={"x": 0.0, "y": 0.0},
        end={"x": 1.0, "y": 0.0},
        slide_width_px=10000,
        slide_height_px=8000,
        mpp=0.25,
    )
    assert m.distance_px == pytest.approx(10000.0)
    assert m.distance_um == pytest.approx(2500.0)


def test_add_measure_without_mpp():
    svc = AnnotationService()
    m = svc.add_measure(
        SLIDE,
        start={"x": 0.0, "y": 0.0},
        end={"x": 0.0, "y": 1.0},
        slide_width_px=10000,
        slide_height_px=8000,
    )
    assert m.distance_px == pytest.approx(8000.0)
    assert m.distance_um is None


def test_add_measure_diagonal():
    svc = AnnotationService()
    m = svc.add_measure(
        SLIDE,
        start={"x": 0.0, "y": 0.0},
        end={"x": 1.0, "y": 1.0},
        slide_width_px=100,
        slide_height_px=100,
        mpp=1.0,
    )
    expected_px = math.sqrt(100**2 + 100**2)
    assert m.distance_px == pytest.approx(expected_px)
    assert m.distance_um == pytest.approx(expected_px)


def test_delete_measure():
    svc = AnnotationService()
    m = svc.add_measure(SLIDE, {"x": 0.0, "y": 0.0}, {"x": 0.1, "y": 0.1})
    svc.delete_measure(SLIDE, m.id)
    assert not svc.get_slide_annotations(SLIDE).measures


def test_delete_measure_not_found():
    svc = AnnotationService()
    with pytest.raises(AnnotationNotFoundError):
        svc.delete_measure(SLIDE, "missing")


# ── Bulk / counts ─────────────────────────────────────────────────────────

def test_clear_slide():
    svc = AnnotationService()
    svc.add_polygon(SLIDE, TRI)
    svc.add_label(SLIDE, ANCHOR, OFFSET, "hi")
    svc.clear_slide(SLIDE)
    assert svc.get_slide_annotations(SLIDE).is_empty()


def test_clear_slide_idempotent():
    svc = AnnotationService()
    svc.clear_slide("nonexistent")  # should not raise


def test_annotation_counts():
    svc = AnnotationService()
    svc.add_polygon(SLIDE, TRI)
    svc.add_polygon(SLIDE, TRI)
    svc.add_label(SLIDE, ANCHOR, OFFSET, "hi")
    counts = svc.annotation_counts(SLIDE)
    assert counts == {"polygons": 2, "labels": 1, "measures": 0, "total": 3}


def test_list_slide_keys_excludes_empty():
    svc = AnnotationService()
    svc.get_slide_annotations("empty-slide")       # creates empty set
    svc.add_polygon("filled-slide", TRI)
    keys = svc.list_slide_keys()
    assert "filled-slide" in keys
    assert "empty-slide" not in keys


def test_per_slide_isolation():
    svc = AnnotationService()
    svc.add_polygon("slide-A", TRI, annotation_id="shared-id")
    # Same ID on a different slide is fine — no collision
    svc.add_polygon("slide-B", TRI, annotation_id="shared-id")
    assert len(svc.get_slide_annotations("slide-A").polygons) == 1
    assert len(svc.get_slide_annotations("slide-B").polygons) == 1


# ── _compute_distance helper ──────────────────────────────────────────────

def test_compute_distance_no_dims():
    from models.domain import ViewportPoint
    p0 = ViewportPoint(0.0, 0.0)
    p1 = ViewportPoint(0.3, 0.4)
    px, um = _compute_distance(p0, p1, None, None, None)
    assert px == pytest.approx(0.5)
    assert um is None
