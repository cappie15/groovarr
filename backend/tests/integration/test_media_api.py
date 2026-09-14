"""Integration tests for GET /api/media's Phase-"frontend wiring" additions:
denormalized track_artist/track_title/track_release_year (so the Tracks page
isn't forced into an N+1 fetch per row, §94), jellyfin_sync_status/
plex_sync_status, and a server-computed needs_organize flag (whether the
Organize/Rename action, §15, would actually change anything). Calls the
route functions directly against `db_session`, the same way other
integration tests exercise API-layer logic (see test_dashboard.py) — no ASGI
transport/app lifespan needed for these, and going through the full app
lifespan across multiple tests in one file causes SQLite lock contention
with the background schedulers it starts.
"""

from app.api.media import list_media_assets
from app.api.pagination import Pagination
from app.db.models.media import MediaAsset, MediaState, SyncStatus
from app.db.models.spotify import Track


async def test_list_media_assets_denormalizes_track_and_computes_needs_organize(db_session) -> None:
    track = Track(
        spotify_track_id="t-organize",
        canonical_artist="Daft Punk",
        canonical_title="Around the World",
        duration_ms=210_000,
        explicit=False,
        release_year=1997,
    )
    db_session.add(track)
    await db_session.flush()

    # Correctly-named file — needs_organize should be False.
    organized = MediaAsset(
        track_id=track.id,
        local_path="/music-videos/Daft Punk - Around the World (1997) [1080p].mp4",
        container="mp4",
        resolution_label="1080p",
        state=MediaState.AVAILABLE,
        jellyfin_sync_status=SyncStatus.SYNCED,
        plex_sync_status=SyncStatus.PENDING_SYNC,
    )
    # Same track, but the file on disk still has its pre-import/legacy name.
    needs_organize = MediaAsset(
        track_id=track.id,
        local_path="/music-videos/daft_punk_around_the_world_RAW.mp4",
        container="mp4",
        resolution_label="1080p",
        state=MediaState.AVAILABLE,
    )
    db_session.add_all([organized, needs_organize])
    await db_session.commit()

    rows = await list_media_assets(state=None, page=Pagination(limit=50, offset=0), session=db_session)
    by_path = {row.local_path: row for row in rows}

    good = by_path["/music-videos/Daft Punk - Around the World (1997) [1080p].mp4"]
    assert good.track_artist == "Daft Punk"
    assert good.track_title == "Around the World"
    assert good.track_release_year == 1997
    assert good.needs_organize is False
    assert good.jellyfin_sync_status == SyncStatus.SYNCED
    assert good.plex_sync_status == SyncStatus.PENDING_SYNC

    stale = by_path["/music-videos/daft_punk_around_the_world_RAW.mp4"]
    assert stale.needs_organize is True


async def test_list_media_assets_denormalizes_candidate_track_for_manual_review(db_session) -> None:
    candidate_track = Track(
        spotify_track_id="t-candidate",
        canonical_artist="Justice",
        canonical_title="D.A.N.C.E.",
        duration_ms=200_000,
        explicit=False,
    )
    db_session.add(candidate_track)
    await db_session.flush()

    review_asset = MediaAsset(
        track_id=None,
        candidate_track_id=candidate_track.id,
        local_path="/music-videos/unsorted/justice_dance_maybe.mp4",
        state=MediaState.MANUAL_REVIEW_REQUIRED,
        review_reason="Filename-only match, low confidence.",
    )
    db_session.add(review_asset)
    await db_session.commit()

    rows = await list_media_assets(
        state=MediaState.MANUAL_REVIEW_REQUIRED, page=Pagination(limit=50, offset=0), session=db_session
    )
    [row] = rows
    assert row.track_artist == "Justice"
    assert row.track_title == "D.A.N.C.E."
    # track_id is still unconfirmed (None) even though we show a candidate name.
    assert row.track_id is None
    assert row.candidate_track_id == candidate_track.id
    # No confirmed track_id -> nothing to compute an expected filename from.
    assert row.needs_organize is False


async def test_list_media_assets_with_no_track_association_has_null_denormalized_fields(db_session) -> None:
    orphan = MediaAsset(local_path=None, state=MediaState.WANTED)
    db_session.add(orphan)
    await db_session.commit()

    rows = await list_media_assets(state=MediaState.WANTED, page=Pagination(limit=50, offset=0), session=db_session)
    [row] = rows
    assert row.track_artist is None
    assert row.track_title is None
    assert row.track_release_year is None
    assert row.needs_organize is False
    assert row.jellyfin_sync_status == SyncStatus.NOT_CONFIGURED
    assert row.plex_sync_status == SyncStatus.NOT_CONFIGURED
