"""
tests/test_slide_session_service.py
Unit tests for SlideSessionService.
All external I/O is mocked — no GCS, no pathology API calls.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import Settings
from models.domain import Slide, WSISession
from services.slide_session_service import (
    SlideSessionService,
    SessionNotAvailableError,
    _filename_from_url,
    _parse_slides,
    _make_key,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

def _settings(configured: bool = True) -> Settings:
    s = MagicMock(spec=Settings)
    s.pathology_api_configured = configured
    s.wsi_url_refresh_minutes = 10
    s.session_ttl_minutes = 30
    return s


SAMPLE_PAYLOAD = {
    "selected_slide_id": "B1",
    "blocks": [
        {
            "block_id": "A1",
            "slides": [
                {
                    "slide_id": "B1",
                    "slide_url": "https://storage.googleapis.com/bucket/1087-25.svs",
                    "thumbnail_url": "https://storage.googleapis.com/bucket/1087-25_thumbnail.png",
                    "tca_url": "https://storage.googleapis.com/bucket/1087-25_tca.zip",
                    "status": "FINISHED",
                    "meta_data": {
                        "width": "112000",
                        "height": "86000",
                        "mpp": "0.2527",
                        "objective_power": "40",
                        "vendor": "aperio",
                    },
                },
                {
                    "slide_id": "C1",
                    "slide_url": "https://storage.googleapis.com/bucket/1087-26.svs",
                    "thumbnail_url": None,
                    "tca_url": None,
                    "status": "FINISHED",
                    "meta_data": {"width": "50000", "height": "40000", "mpp": None},
                },
                # Should be ignored — no slide_url
                {
                    "slide_id": "D1",
                    "slide_url": "",
                    "status": "PROCESSING",
                    "meta_data": {},
                },
            ],
        }
    ],
}


# ── _parse_slides ──────────────────────────────────────────────────────────

def test_parse_slides_filters_empty_url():
    slides = _parse_slides(SAMPLE_PAYLOAD)
    assert len(slides) == 2
    ids = {s.slide_id for s in slides}
    assert "D1" not in ids


def test_parse_slides_extracts_metadata():
    slides = _parse_slides(SAMPLE_PAYLOAD)
    b1 = next(s for s in slides if s.slide_id == "B1")
    assert b1.meta_data.width == 112000
    assert b1.meta_data.mpp == pytest.approx(0.2527)
    assert b1.meta_data.vendor == "aperio"


def test_parse_slides_derives_filename():
    slides = _parse_slides(SAMPLE_PAYLOAD)
    b1 = next(s for s in slides if s.slide_id == "B1")
    assert b1.filename == "1087-25.svs"


def test_parse_slides_tolerates_null_mpp():
    slides = _parse_slides(SAMPLE_PAYLOAD)
    c1 = next(s for s in slides if s.slide_id == "C1")
    assert c1.meta_data.mpp is None


# ── _filename_from_url ─────────────────────────────────────────────────────

def test_filename_from_url():
    url = "https://storage.googleapis.com/bucket/path/to/1087-25.svs"
    assert _filename_from_url(url) == "1087-25.svs"


# ── SlideSessionService ────────────────────────────────────────────────────

def _make_service(configured: bool = True) -> SlideSessionService:
    mock_client = AsyncMock()
    mock_client.fetch_slides = AsyncMock(return_value=SAMPLE_PAYLOAD)
    svc = SlideSessionService(settings=_settings(configured), api_client=mock_client)
    return svc


@pytest.mark.asyncio
async def test_get_or_create_raises_if_not_configured():
    svc = _make_service(configured=False)
    with pytest.raises(SessionNotAvailableError):
        await svc.get_or_create_session("p1", "e1", "B1")


@pytest.mark.asyncio
async def test_get_or_create_builds_session():
    svc = _make_service()
    session = await svc.get_or_create_session("p1", "e1", "B1")

    assert isinstance(session, WSISession)
    assert len(session.slides) == 2
    assert session.default_slide.slide_id == "B1"
    assert session.default_slide.filename == "1087-25.svs"


@pytest.mark.asyncio
async def test_get_or_create_reuses_fresh_session():
    svc = _make_service()
    s1 = await svc.get_or_create_session("p1", "e1", "B1")
    s2 = await svc.get_or_create_session("p1", "e1", "B1")
    # Should be the same object — no second API call
    assert s1 is s2
    svc._api_client.fetch_slides.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_refreshes_stale_session():
    svc = _make_service()
    s1 = await svc.get_or_create_session("p1", "e1", "B1")

    # Wind back the refreshed_at timestamp to simulate a stale session
    key = _make_key("p1", "e1", "B1")
    svc._store[key].refreshed_at = datetime.utcnow() - timedelta(minutes=15)

    s2 = await svc.get_or_create_session("p1", "e1", "B1")
    # Should be a new object, but created_at preserved
    assert s2 is not s1
    assert s2.created_at == s1.created_at
    assert svc._api_client.fetch_slides.call_count == 2


@pytest.mark.asyncio
async def test_selected_slide_is_first():
    svc = _make_service()
    session = await svc.get_or_create_session("p1", "e1", "B1")
    assert session.slides[0].slide_id == "B1"


@pytest.mark.asyncio
async def test_slide_url_by_filename():
    svc = _make_service()
    session = await svc.get_or_create_session("p1", "e1", "B1")
    url = session.slide_url_by_filename("1087-25.svs")
    assert "1087-25.svs" in url


@pytest.mark.asyncio
async def test_invalidate_forces_rebuild():
    svc = _make_service()
    await svc.get_or_create_session("p1", "e1", "B1")
    svc.invalidate("p1", "e1", "B1")
    await svc.get_or_create_session("p1", "e1", "B1")
    assert svc._api_client.fetch_slides.call_count == 2
