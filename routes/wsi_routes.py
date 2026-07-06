"""
routes/wsi_routes.py
FastAPI route handlers for the WSI viewer backend.

All routes require the three query parameters:
    patient_id, event_id, selected_slide_id

The route layer is intentionally thin:
    1. Extract + validate query params.
    2. Delegate to the appropriate service.
    3. Build an HTTP response from the service result.

No business logic lives here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from config.settings import Settings, get_settings
from models.domain import WSISession
from services.gcs_stream_service import GCSStreamService
from services.slide_normalization_service import SlideNormalizationService
from services.slide_session_service import (
    SessionNotAvailableError,
    SlideSessionService,
)
from services.tca_overlay_service import TCAOverlayService, _slide_stem
from services.thumbnail_service import ThumbnailService

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Shared query-parameter dependency ─────────────────────────────────────

class WSIParams:
    def __init__(
        self,
        patient_id: str = Query(..., description="Patient UUID"),
        event_id: str = Query(..., description="Event / encounter ID"),
        selected_slide_id: str = Query(..., description="Slide block ID"),
    ) -> None:
        self.patient_id = patient_id
        self.event_id = event_id
        self.selected_slide_id = selected_slide_id


# ── Service singletons injected via app.state ──────────────────────────────

def _session_svc(request: Request) -> SlideSessionService:
    return request.app.state.session_service


def _gcs_svc(request: Request) -> GCSStreamService:
    return request.app.state.gcs_service


def _overlay_svc(request: Request) -> TCAOverlayService:
    return request.app.state.overlay_service


def _thumbnail_svc(request: Request) -> ThumbnailService:
    return request.app.state.thumbnail_service


def _normalization_svc(request: Request) -> SlideNormalizationService:
    return request.app.state.normalization_service


def _settings(request: Request) -> Settings:
    return get_settings()


# ── Helper to resolve session ──────────────────────────────────────────────

async def _get_session(
    params: WSIParams,
    svc: SlideSessionService,
) -> WSISession:
    try:
        return await svc.get_or_create_session(
            params.patient_id, params.event_id, params.selected_slide_id
        )
    except SessionNotAvailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to build WSI session: %s", exc)
        raise HTTPException(status_code=502, detail="Could not contact pathology API")


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/api/slides")
async def get_slides(
    params: WSIParams = Depends(),
    svc: SlideSessionService = Depends(_session_svc),
):
    """
    Return slide list and the default slide for the viewer UI.
    """
    session = await _get_session(params, svc)

    slides_out = [
        {
            "slide_id": s.slide_id,
            "block_id": s.block_id,
            "filename": s.filename,
            "status": s.status,
            "has_tca": bool(s.tca_url),
            "meta_data": (
                {
                    "width": s.meta_data.width,
                    "height": s.meta_data.height,
                    "mpp": s.meta_data.mpp,
                    "objective_power": s.meta_data.objective_power,
                    "vendor": s.meta_data.vendor,
                }
                if s.meta_data
                else None
            ),
        }
        for s in session.slides
    ]

    return JSONResponse(
        {
            "slides": slides_out,
            "default_slide": session.default_slide.filename if session.default_slide else None,
        }
    )


@router.get("/api/thumbnail/{slide_name:path}")
async def get_thumbnail(
    slide_name: str,
    params: WSIParams = Depends(),
    svc: SlideSessionService = Depends(_session_svc),
    thumbnails: ThumbnailService = Depends(_thumbnail_svc),
):
    """
    Return a low-res overview PNG for a slide, generated directly from its
    own pyramid (see ThumbnailService) — not proxied from whatever
    thumbnail_url the pathology API happens to report. This works
    uniformly for any slide reachable via slide_url: local mock files and
    real GCS-hosted slides alike, both today and for future sources.
    """
    session = await _get_session(params, svc)
    slide = next(
        (s for s in session.slides if s.slide_id == slide_name or s.filename == slide_name),
        None,
    )
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    png = await thumbnails.get_thumbnail_png(slide.slide_url)
    if png is None:
        raise HTTPException(status_code=404, detail="Thumbnail not available for this slide")

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/api/raw_slides/{filename:path}")
@router.head("/api/raw_slides/{filename:path}")
async def raw_slide(
    filename: str,
    request: Request,
    params: WSIParams = Depends(),
    svc: SlideSessionService = Depends(_session_svc),
    gcs: GCSStreamService = Depends(_gcs_svc),
    normalization: SlideNormalizationService = Depends(_normalization_svc),
):
    """
    Proxy Range requests to GCS for tile streaming.

    HEAD returns Content-Length so GeoTIFFTileSource can plan tile offsets.
    GET with Range header returns 206 Partial Content.

    Hot path: a single zoom/pan step fires dozens of these concurrently, so
    this resolves the slide_url via the FAST, lock-free, non-refreshing
    SlideSessionService.get_session() lookup (the session was already built
    by the /api/slides call that loaded the viewer). Falling back to the
    full get_or_create_session() — which awaits a shared asyncio.Lock and a
    staleness check — only on a cache miss avoids serializing every tile
    request in the process behind that lock.
    """
    slide_url = _resolve_slide_url_fast(params, svc, filename)
    if slide_url is None:
        session = await _get_session(params, svc)
        slide_url = session.slide_url_by_filename(filename)
    if not slide_url:
        raise HTTPException(status_code=404, detail=f"Slide '{filename}' not in session")

    # Transparently swap in a normalized copy once one's ready (see
    # SlideNormalizationService); otherwise this is a no-op that returns
    # slide_url unchanged and, at most, kicks off background re-encoding.
    slide_url = await normalization.resolve_serving_url(slide_url)

    if request.method == "HEAD":
        content_length = await gcs.head_content_length(slide_url)
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(content_length or 0),
                "Accept-Ranges": "bytes",
                "Access-Control-Allow-Origin": "*",
            },
        )

    range_header = request.headers.get("Range")
    status, resp_headers, body = await gcs.stream_range(slide_url, range_header)

    if status >= 400:
        raise HTTPException(status_code=status, detail="GCS tile fetch failed")

    # Tile bytes at a given byte range never change — let the browser's own
    # HTTP cache serve re-visited tiles (panning back over an area) instead
    # of re-issuing the Range request.
    resp_headers = {**resp_headers, "Cache-Control": "public, max-age=86400, immutable"}

    return Response(
        content=body,
        status_code=status,
        headers=resp_headers,
    )


def _resolve_slide_url_fast(
    params: WSIParams,
    svc: SlideSessionService,
    filename: str,
) -> Optional[str]:
    """Lock-free session lookup for the tile-streaming hot path (see raw_slide)."""
    session = svc.get_session(params.patient_id, params.event_id, params.selected_slide_id)
    return session.slide_url_by_filename(filename) if session else None


@router.get("/api/slide-direct-url/{filename:path}")
async def slide_direct_url(
    filename: str,
    params: WSIParams = Depends(),
    svc: SlideSessionService = Depends(_session_svc),
    gcs: GCSStreamService = Depends(_gcs_svc),
    settings: Settings = Depends(_settings),
):
    """
    Optional: return a signed GCS URL so the browser reads GCS directly.
    Falls back to the proxy URL if signing isn't possible.
    """
    session = await _get_session(params, svc)
    slide_url = session.slide_url_by_filename(filename)
    if not slide_url:
        raise HTTPException(status_code=404, detail="Slide not found")

    signed = await gcs.generate_signed_url(
        slide_url,
        ttl_seconds=settings.gcs_signed_url_ttl_seconds,
        service_account_email=settings.gcs_service_account,
    )

    # Build the proxy fallback URL
    base = str(params.patient_id)  # just used for clarity; real proxy URL built client-side
    return JSONResponse({"url": signed or None, "available": signed is not None})


@router.get("/api/overlay-config/{slide_name:path}")
async def overlay_config(
    slide_name: str,
    params: WSIParams = Depends(),
    svc: SlideSessionService = Depends(_session_svc),
    overlay: TCAOverlayService = Depends(_overlay_svc),
    request: Request = None,
):
    """
    Return TCA overlay config for a slide.
    The density_image and metadata URLs point back through /api/overlay-file/…
    """
    session = await _get_session(params, svc)
    slide = next(
        (s for s in session.slides if s.slide_id == slide_name or s.filename == slide_name),
        None,
    )

    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found in session")

    config = await overlay.get_overlay_config(
        slide_name=slide.filename or slide.slide_id,
        tca_url=slide.tca_url,
        slide_metadata=slide.meta_data,
    )

    if not config.available:
        return JSONResponse({"available": False})

    # Build URLs that go back through the viewer proxy
    qs = f"patient_id={params.patient_id}&event_id={params.event_id}&selected_slide_id={params.selected_slide_id}"
    stem = _slide_stem(slide.filename or slide.slide_id)

    density_filename = Path(config.density_image_path).name if config.density_image_path else None
    metadata_filename = Path(config.metadata_path).name if config.metadata_path else None

    resp: dict = {
        "available": True,
        "density_image": f"/api/overlay-file/{density_filename}?{qs}" if density_filename else None,
        "metadata": f"/api/overlay-file/{metadata_filename}?{qs}" if metadata_filename else None,
        "slide_metadata": (
            {
                "width": config.slide_metadata.width,
                "height": config.slide_metadata.height,
                "mpp": config.slide_metadata.mpp,
                "objective_power": config.slide_metadata.objective_power,
                "vendor": config.slide_metadata.vendor,
            }
            if config.slide_metadata
            else None
        ),
    }
    if config.grid_path:
        grid_filename = Path(config.grid_path).name
        resp["grid"] = f"/api/overlay-file/{grid_filename}?{qs}"

    return JSONResponse(resp)


@router.get("/api/overlay-file/{filename:path}")
async def overlay_file(
    filename: str,
    params: WSIParams = Depends(),
    overlay: TCAOverlayService = Depends(_overlay_svc),
):
    """
    Serve an extracted TCA overlay asset (PNG or JSON) from local cache.
    The browser never touches tca_url directly.
    """
    path = overlay.resolve_asset_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail=f"Overlay file '{filename}' not in cache")

    media_type = "application/json" if filename.endswith(".json") else "image/png"
    return FileResponse(str(path), media_type=media_type)
