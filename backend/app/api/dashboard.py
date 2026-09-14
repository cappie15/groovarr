"""Dashboard summary endpoint.

Added alongside the frontend's first real data-wiring pass: the Dashboard
page (architecture doc §63) needs operationally-useful aggregate counts
(connected playlists, media by state, pending server syncs, queue size,
last/next Spotify sync), and no endpoint returns totals — every existing
list endpoint returns a plain paginated array with no count. Rather than
have the frontend page through everything client-side just to count rows,
this adds one small additive read-only endpoint that computes the same
aggregates server-side with `func.count()`/`group_by`, reusing the existing
models rather than introducing any new ones.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.media import MediaAsset, MediaState, SyncStatus
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist
from app.db.session import get_session

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# States that represent "currently moving through the acquisition pipeline"
# for the dashboard's "active queue items" figure.
_ACTIVE_QUEUE_STATES = (
    MediaState.QUEUED,
    MediaState.DOWNLOADING,
    MediaState.PROCESSING,
    MediaState.IMPORTING,
)


class DashboardSummary(BaseModel):
    connected_playlists: int
    finalized_playlists: int
    monitored_tracks: int
    media_by_state: dict[str, int]
    active_queue_count: int
    download_failed_count: int
    manual_review_required_count: int
    pending_jellyfin_sync: int
    pending_plex_sync: int
    last_spotify_sync_at: datetime | None
    next_spotify_sync_at: datetime | None


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(session: AsyncSession = Depends(get_session)) -> DashboardSummary:
    connected_playlists = (
        await session.execute(
            select(func.count()).select_from(SpotifyPlaylist).where(SpotifyPlaylist.connected.is_(True))
        )
    ).scalar_one()

    finalized_playlists = (
        await session.execute(
            select(func.count()).select_from(SpotifyPlaylist).where(SpotifyPlaylist.finalized_at.is_not(None))
        )
    ).scalar_one()

    # "Monitored tracks" = distinct tracks referenced by at least one
    # connected, non-finalized playlist's entries.
    monitored_tracks = (
        await session.execute(
            select(func.count(func.distinct(PlaylistEntry.track_id)))
            .select_from(PlaylistEntry)
            .join(SpotifyPlaylist, SpotifyPlaylist.id == PlaylistEntry.playlist_id)
            .where(SpotifyPlaylist.connected.is_(True), SpotifyPlaylist.finalized_at.is_(None))
        )
    ).scalar_one()

    state_rows = (
        await session.execute(select(MediaAsset.state, func.count()).group_by(MediaAsset.state))
    ).all()
    media_by_state = {state.value: count for state, count in state_rows}

    active_queue_count = sum(media_by_state.get(s.value, 0) for s in _ACTIVE_QUEUE_STATES)
    download_failed_count = media_by_state.get(MediaState.DOWNLOAD_FAILED.value, 0)
    manual_review_required_count = media_by_state.get(MediaState.MANUAL_REVIEW_REQUIRED.value, 0)

    pending_jellyfin_sync = (
        await session.execute(
            select(func.count())
            .select_from(MediaAsset)
            .where(MediaAsset.jellyfin_sync_status == SyncStatus.PENDING_SYNC)
        )
    ).scalar_one()
    pending_plex_sync = (
        await session.execute(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.plex_sync_status == SyncStatus.PENDING_SYNC)
        )
    ).scalar_one()

    sync_bounds = (
        await session.execute(
            select(func.max(SpotifyPlaylist.last_synced_at), func.min(SpotifyPlaylist.next_sync_at)).where(
                SpotifyPlaylist.connected.is_(True), SpotifyPlaylist.finalized_at.is_(None)
            )
        )
    ).one()
    last_spotify_sync_at, next_spotify_sync_at = sync_bounds

    return DashboardSummary(
        connected_playlists=connected_playlists,
        finalized_playlists=finalized_playlists,
        monitored_tracks=monitored_tracks,
        media_by_state=media_by_state,
        active_queue_count=active_queue_count,
        download_failed_count=download_failed_count,
        manual_review_required_count=manual_review_required_count,
        pending_jellyfin_sync=pending_jellyfin_sync,
        pending_plex_sync=pending_plex_sync,
        last_spotify_sync_at=last_spotify_sync_at,
        next_spotify_sync_at=next_spotify_sync_at,
    )
