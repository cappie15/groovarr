"""Groovarr FastAPI application entrypoint.

Run directly with `python -m app.main` for local development, or via
`uvicorn app.main:app` (what the production container does). Either way,
uvicorn's default signal handling installs SIGTERM/SIGINT handlers that
trigger the ASGI lifespan's shutdown phase below, so shutdown is graceful
(in-flight requests finish, the DB engine is disposed) without any extra
signal-handling code here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.router import router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine, get_engine
from app.jobs.download_queue import DownloadQueue
from app.jobs.spotify_scheduler import SpotifySyncScheduler
from app.jobs.upgrade_monitor import UpgradeMonitorScheduler

logger = structlog.get_logger(__name__)

spotify_scheduler = SpotifySyncScheduler()
download_queue = DownloadQueue()
upgrade_monitor = UpgradeMonitorScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "groovarr.startup",
        version=__version__,
        port=settings.port,
        config_dir=str(settings.config_dir),
        media_dir=str(settings.media_dir),
        downloads_dir=str(settings.downloads_dir),
    )
    # Touch the engine now (creates CONFIG_DIR if missing) so a broken DB
    # path fails fast at startup rather than on the first request.
    get_engine()
    spotify_scheduler.start()
    download_queue.start()
    upgrade_monitor.start()
    try:
        yield
    finally:
        logger.info("groovarr.shutdown")
        await upgrade_monitor.stop()
        await download_queue.stop()
        await spotify_scheduler.stop()
        await dispose_engine()


def _mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Serve the built React SPA from this same process (single-process,
    single-container deployment model, §4/§5).

    Registered AFTER `router` (all `/api/*` and `/health` routes) so the
    catch-all path below can never shadow a real API route — Starlette
    matches routes in registration order and returns the first match.

    If `frontend/dist` doesn't exist (e.g. a backend-only dev environment),
    logs a warning and leaves the app API-only rather than crashing.
    """
    if not dist_dir.is_dir():
        logger.warning("groovarr.frontend_dist_missing", path=str(dist_dir))
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    index_file = dist_dir / "index.html"
    resolved_dist_dir = dist_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> Response:
        # Any real static file at the root of dist (favicon.ico, etc.) is
        # served directly; everything else — including react-router deep
        # links like /playlists that don't correspond to a file on disk —
        # falls back to index.html so the client-side router can handle it.
        candidate = (dist_dir / full_path).resolve()
        try:
            candidate.relative_to(resolved_dist_dir)
        except ValueError:
            # Path traversal attempt (e.g. `..`) — never escape dist_dir.
            return FileResponse(index_file)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Groovarr", version=__version__, lifespan=lifespan)
    app.include_router(router)
    _mount_frontend(app, (settings or get_settings()).frontend_dist_dir)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port)
