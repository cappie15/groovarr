"""Integration test for a frontend-wiring-pass fix to `GET /api/queue`:
DOWNLOAD_FAILED is now included in the listing (previously only the truly
"active" states were), because the Activity/Queue page's Retry action needs
somewhere to actually show a failed asset — a manual Retry endpoint with
nothing in the UI ever routed to it is effectively dead. Calls the route
function directly against `db_session`, matching test_media_api.py's
convention.
"""

from app.api.pagination import Pagination
from app.api.queue import list_queue
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import Track


async def test_list_queue_includes_download_failed_assets(db_session) -> None:
    track = Track(
        spotify_track_id="t-queue-failed", canonical_artist="A", canonical_title="B", duration_ms=1000, explicit=False
    )
    db_session.add(track)
    await db_session.flush()

    failed = MediaAsset(track_id=track.id, state=MediaState.DOWNLOAD_FAILED)
    available = MediaAsset(track_id=track.id, state=MediaState.AVAILABLE)
    downloading = MediaAsset(track_id=track.id, state=MediaState.DOWNLOADING)
    db_session.add_all([failed, available, downloading])
    await db_session.commit()

    rows = await list_queue(page=Pagination(limit=50, offset=0), session=db_session)
    states_by_id = {row.media_asset_id: row.state for row in rows}

    assert states_by_id[failed.id] == MediaState.DOWNLOAD_FAILED
    assert states_by_id[downloading.id] == MediaState.DOWNLOADING
    # AVAILABLE is neither "active" nor "failed" — never belongs in the queue.
    assert available.id not in states_by_id
