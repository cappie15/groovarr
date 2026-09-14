"""Top-level API router.

Phase 1 (Foundation) added `/health`. Phase 2 added Spotify playlist
connect/sync/disconnect, Settings, and the optional PKCE OAuth callback.
Phase 3 added the existing-library scan, MediaAsset listing, and
Organize/Rename endpoints. Phase 4 added Automatic/Manual Search and Manual
Selection. Phase 5 added the download queue (list/retry/cancel). Phase 6
added Spotify-canonical metadata/artwork tagging (no new endpoints — it's
wired directly into the acquisition pipeline). Phase 7 added the lyrics
status endpoint. Phase 8 adds Jellyfin/Plex settings, connectivity
introspection, and per-playlist external-playlist sync/collision-resolution.
"""

import structlog
from fastapi import APIRouter

from app import __version__
from app.api.dashboard import router as dashboard_router
from app.api.history import router as history_router
from app.api.lyrics import router as lyrics_router
from app.api.media import router as media_router
from app.api.media_servers import jellyfin_router, plex_router
from app.api.playlists import router as playlists_router
from app.api.queue import router as queue_router
from app.api.search import router as search_router
from app.api.settings import router as settings_router
from app.api.spotify_oauth import router as spotify_oauth_router
from app.db.session import check_db_connection

logger = structlog.get_logger(__name__)

router = APIRouter()
router.include_router(playlists_router)
router.include_router(media_router)
router.include_router(search_router)
router.include_router(queue_router)
router.include_router(settings_router)
router.include_router(spotify_oauth_router)
router.include_router(lyrics_router)
router.include_router(jellyfin_router)
router.include_router(plex_router)
router.include_router(history_router)
router.include_router(dashboard_router)


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness/readiness probe. Actually checks DB connectivity rather than
    hardcoding "ok" — a `db: error` response with an otherwise-200 status
    lets callers distinguish "process is up" from "process is fully healthy".
    """
    db_ok = await check_db_connection()
    if not db_ok:
        logger.warning("health.db_check_failed")
    return {
        "status": "ok",
        "version": __version__,
        "db": "ok" if db_ok else "error",
    }
