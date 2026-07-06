"""
main.py
FastAPI application factory.

Service wiring
──────────────
Services are created once at startup and stored on app.state.
Route handlers pull them out via Depends() + request.app.state.

This pattern means:
  • Services are singletons within a process (correct for in-memory stores).
  • Tests can swap them by replacing app.state before the test client runs.
  • No global variables — everything flows through the ASGI app object.

Service inventory
──────────────────
  gcs_service          GCSStreamService      GCS Range-request proxy
  overlay_service      TCAOverlayService     TCA zip download + asset cache
  tca_analysis_service TCAAnalysisService    TCA metadata parsing + aggregates
  session_service      SlideSessionService   WSI session cache + refresh loop
  annotation_service   AnnotationService     Per-slide annotation CRUD
  worklist_service     WorklistService       Pathology case review queue
  thumbnail_service    ThumbnailService      Generated overview PNG + disk cache
  normalization_service SlideNormalizationService  Background re-encode of unsupported slide formats
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from config.settings import get_settings
from routes.annotation_routes import router as annotation_router
from routes.tca_routes import router as tca_router
from routes.worklist_routes import router as worklist_router
from routes.wsi_routes import router
from services.annotation_service import AnnotationService
from services.gcs_stream_service import GCSStreamService
from services.slide_normalization_service import SlideNormalizationService
from services.slide_session_service import SlideSessionService
from services.tca_analysis_service import TCAAnalysisService
from services.tca_overlay_service import TCAOverlayService
from services.thumbnail_service import ThumbnailService
from services.worklist_service import WorklistService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start services → yield → graceful shutdown."""
    settings = get_settings()

    # ── Startup ────────────────────────────────────────────────────────────
    logger.info("WSI Viewer: starting services")

    gcs_service = GCSStreamService(project_id=settings.gcs_project_id)
    await gcs_service.open()

    overlay_service = TCAOverlayService(settings=settings)
    await overlay_service.open()

    tca_analysis_service = TCAAnalysisService()

    session_service = SlideSessionService(settings=settings)
    await session_service.start()

    annotation_service = AnnotationService()
    worklist_service = WorklistService()

    thumbnail_service = ThumbnailService(settings=settings)
    await thumbnail_service.open()

    normalization_service = SlideNormalizationService(settings=settings)
    await normalization_service.open()

    app.state.gcs_service = gcs_service
    app.state.overlay_service = overlay_service
    app.state.tca_analysis_service = tca_analysis_service
    app.state.session_service = session_service
    app.state.annotation_service = annotation_service
    app.state.worklist_service = worklist_service
    app.state.thumbnail_service = thumbnail_service
    app.state.normalization_service = normalization_service

    logger.info(
        "WSI Viewer ready  root_path=%r  port=%d  api_configured=%s",
        settings.root_path,
        settings.port,
        settings.pathology_api_configured,
    )

    yield  # ← application is running

    # ── Shutdown ───────────────────────────────────────────────────────────
    logger.info("WSI Viewer: shutting down")
    await session_service.stop()
    await overlay_service.close()
    await thumbnail_service.close()
    await normalization_service.close()
    await gcs_service.close()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="WSI Viewer Backend",
        description=(
            "Whole-slide image viewer — tile streaming, session management, "
            "TCA analysis, and pathology review worklist."
        ),
        version="1.0.0",
        root_path=settings.root_path,
        lifespan=lifespan,
    )

    # Allow the browser to make Range requests from any origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Range", "Content-Type", "Authorization"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
    )

    # ── API routers ────────────────────────────────────────────────────────
    app.include_router(router)
    app.include_router(annotation_router)
    app.include_router(tca_router)
    app.include_router(worklist_router)

    # ── Frontend static files (SPA fallback) ───────────────────────────────
    vite_dist = Path(__file__).parent / "frontend" / "dist"
    frontend_root = Path(__file__).parent / "frontend"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # 1. Check Vite build output first
        if vite_dist.exists():
            target_file = vite_dist / full_path
            if full_path and target_file.is_file():
                return FileResponse(target_file)
            return FileResponse(vite_dist / "index.html")
        
        # 2. Fallback to raw frontend/ directory (during dev before build)
        elif frontend_root.exists():
            target_file = frontend_root / full_path
            if full_path and target_file.is_file():
                return FileResponse(target_file)
            fallback = frontend_root / "index.html"
            if fallback.is_file():
                return FileResponse(fallback)
                
        # 3. Not found
        return JSONResponse(status_code=404, content={"detail": "Frontend UI not found. Run 'npm run build' in frontend/ directory."})

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=get_settings().port,
        reload=False,
    )
