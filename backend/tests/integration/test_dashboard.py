"""Dashboard summary aggregates (architecture doc §63), added alongside the
frontend's first real data-wiring pass. Calls the route function directly
against `db_session`, the same way other integration tests exercise
service/API-layer logic without spinning up an ASGI transport.
"""

import pytest

from app.api.dashboard import get_dashboard_summary
from app.db.models.media import MediaAsset, MediaState, SyncStatus
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track


async def _track(session, **overrides):
    defaults = dict(canonical_artist="Artist", canonical_title="Title", duration_ms=200_000, explicit=False)
    defaults.update(overrides)
    t = Track(**defaults)
    session.add(t)
    await session.flush()
    return t


@pytest.mark.asyncio
async def test_summary_on_empty_db_is_all_zero(db_session) -> None:
    summary = await get_dashboard_summary(session=db_session)
    assert summary.connected_playlists == 0
    assert summary.monitored_tracks == 0
    assert summary.media_by_state == {}
    assert summary.active_queue_count == 0
    assert summary.last_spotify_sync_at is None
    assert summary.next_spotify_sync_at is None


@pytest.mark.asyncio
async def test_summary_counts_reflect_real_rows(db_session) -> None:
    connected = SpotifyPlaylist(spotify_id="p-connected", name="Connected", connected=True)
    finalized = SpotifyPlaylist(spotify_id="p-final", name="Finalized", connected=False)
    db_session.add_all([connected, finalized])
    await db_session.flush()
    from datetime import UTC, datetime

    finalized.finalized_at = datetime.now(UTC)

    t1 = await _track(db_session, spotify_track_id="t1")
    t2 = await _track(db_session, spotify_track_id="t2")
    db_session.add(PlaylistEntry(playlist_id=connected.id, track_id=t1.id, position=0, occurrence_index=0))
    db_session.add(PlaylistEntry(playlist_id=connected.id, track_id=t2.id, position=1, occurrence_index=0))
    # Same track wanted twice in one playlist must not double-count as two
    # monitored tracks.
    db_session.add(PlaylistEntry(playlist_id=connected.id, track_id=t2.id, position=2, occurrence_index=1))

    db_session.add(
        MediaAsset(track_id=t1.id, state=MediaState.AVAILABLE, plex_sync_status=SyncStatus.PENDING_SYNC)
    )
    db_session.add(
        MediaAsset(track_id=t2.id, state=MediaState.DOWNLOAD_FAILED, jellyfin_sync_status=SyncStatus.PENDING_SYNC)
    )
    db_session.add(MediaAsset(state=MediaState.QUEUED))
    db_session.add(MediaAsset(state=MediaState.MANUAL_REVIEW_REQUIRED))
    await db_session.commit()

    summary = await get_dashboard_summary(session=db_session)

    assert summary.connected_playlists == 1
    assert summary.finalized_playlists == 1
    assert summary.monitored_tracks == 2  # t1, t2 - not 3, despite the duplicate occurrence
    assert summary.media_by_state[MediaState.AVAILABLE.value] == 1
    assert summary.media_by_state[MediaState.DOWNLOAD_FAILED.value] == 1
    assert summary.media_by_state[MediaState.QUEUED.value] == 1
    assert summary.active_queue_count == 1  # only the QUEUED one
    assert summary.download_failed_count == 1
    assert summary.manual_review_required_count == 1
    assert summary.pending_plex_sync == 1
    assert summary.pending_jellyfin_sync == 1
