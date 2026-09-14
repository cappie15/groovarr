"""Phase 10 (Hardening) tests: the History log (§65), pagination (§94), and
crash-recovery beyond what Phase 5 already covers — specifically the
orphaned-replacement-attempt reconciliation this phase adds, including a
regression test for the "zero references doesn't always mean abandoned"
bug found and fixed while building it (see app/services/replacement.py's
module docstring).
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.media import list_media_assets
from app.api.pagination import Pagination
from app.db.models.acquisition import AttemptStatus, DownloadAttempt
from app.db.models.candidates import VideoCandidate
from app.db.models.history import HistoryEvent
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.services.acquisition import claim_next_ready_asset, process_media_asset, reconcile_interrupted_assets
from app.services.deletion import delete_media_asset_if_eligible
from app.services.replacement import reconcile_orphaned_replacement_attempts
from app.services.search import get_wanted_tracks, select_candidate


async def _make_wanted_track(session, *, spotify_id="t-hardening", playlist_id="p-hardening") -> Track:
    track = Track(spotify_track_id=spotify_id, canonical_artist="Artist", canonical_title="Title", duration_ms=200_000)
    session.add(track)
    await session.flush()
    playlist = SpotifyPlaylist(spotify_id=playlist_id, name="Playlist")
    session.add(playlist)
    await session.flush()
    session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    await session.commit()
    return track


# --------------------------------------------------------------------------
# History log (§65)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_selection_is_recorded_in_history(db_session):
    track = await _make_wanted_track(db_session)
    candidate = VideoCandidate(
        track_id=track.id,
        youtube_video_id="v1",
        title="Official Video",
        score=90,
        score_breakdown=[],
        rejection_flags=[],
    )
    db_session.add(candidate)
    await db_session.commit()

    await select_candidate(db_session, track.id, candidate.id)

    events = (await db_session.scalars(select(HistoryEvent).where(HistoryEvent.track_id == track.id))).all()
    types = [e.event_type for e in events]
    assert "search.manual_selection_made" in types


@pytest.mark.asyncio
async def test_deletion_is_recorded_in_history_even_after_the_row_is_gone(db_session, tmp_path, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        track = Track(spotify_track_id="t-del", canonical_artist="A", canonical_title="B", duration_ms=1000)
        db_session.add(track)
        await db_session.flush()
        video = tmp_path / "A - B (Unknown) [Unknown].mp4"
        video.write_bytes(b"bytes")
        asset = MediaAsset(track_id=track.id, local_path=str(video), state=MediaState.AVAILABLE)
        db_session.add(asset)
        await db_session.commit()
        asset_id = asset.id

        deleted = await delete_media_asset_if_eligible(db_session, asset)
        assert deleted is True
        assert await db_session.get(MediaAsset, asset_id) is None

        # The event survives even though the MediaAsset row it describes is gone
        # (§65: media_asset_id is deliberately not a cascading FK).
        event = await db_session.scalar(
            select(HistoryEvent).where(
                HistoryEvent.media_asset_id == asset_id, HistoryEvent.event_type == "deletion.removed_unreferenced"
            )
        )
        assert event is not None
        assert event.track_id == track.id
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_history_endpoint_filters_by_track_and_paginates(db_session):
    from app.api.history import list_history

    for i in range(3):
        db_session.add(
            HistoryEvent(
                track_id=1 if i < 2 else 2,
                event_type="test.event",
                detail=f"event {i}",
                occurred_at=datetime.now(UTC),
            )
        )
    await db_session.commit()

    all_events = await list_history(
        track_id=None, media_asset_id=None, page=Pagination(limit=2, offset=0), session=db_session
    )
    assert len(all_events) == 2  # limit respected even though 3 rows exist

    track_1_events = await list_history(
        track_id=1, media_asset_id=None, page=Pagination(limit=100, offset=0), session=db_session
    )
    assert len(track_1_events) == 2
    assert all(e.track_id == 1 for e in track_1_events)


# --------------------------------------------------------------------------
# Pagination (§94)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_wanted_tracks_is_paginated_by_default_but_unbounded_on_request(db_session):
    for i in range(5):
        await _make_wanted_track(db_session, spotify_id=f"t{i}", playlist_id=f"p{i}")

    page_1 = await get_wanted_tracks(db_session, limit=2, offset=0)
    page_2 = await get_wanted_tracks(db_session, limit=2, offset=2)
    assert len(page_1) == 2
    assert len(page_2) == 2
    assert {t.id for t in page_1}.isdisjoint({t.id for t in page_2})

    everything = await get_wanted_tracks(db_session, limit=None)
    assert len(everything) == 5


@pytest.mark.asyncio
async def test_media_asset_list_endpoint_respects_pagination(db_session):
    for _ in range(3):
        db_session.add(MediaAsset(state=MediaState.WANTED))
    await db_session.commit()

    page = await list_media_assets(state=None, page=Pagination(limit=1, offset=0), session=db_session)
    assert len(page) == 1


# --------------------------------------------------------------------------
# Crash recovery (§78) beyond what Phase 5 already covered
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_orphaned_replacement_attempt_cleans_up_but_leaves_real_zero_reference_assets_alone(
    db_session, tmp_path, monkeypatch
):
    """Regression test for a bug introduced and fixed while building this
    reconciliation: an early version inferred "abandoned replacement" purely
    from "track-linked with zero references", which incorrectly swept up
    ordinary assets that simply haven't been reference-synced (e.g. a fresh
    CANDIDATE_SELECTED row waiting in the queue). The fix uses an explicit
    `pending_reference_swap` marker instead — this test locks in both halves:
    the real orphan IS cleaned up, and a legitimate zero-reference asset is
    NOT touched.
    """
    from app.core.config import get_settings

    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        # A genuinely abandoned replacement: fully downloaded (has a real
        # file) but crashed before the reference-swap commit.
        orphan_track = Track(spotify_track_id="t-orphan", canonical_artist="A", canonical_title="B", duration_ms=1000)
        db_session.add(orphan_track)
        await db_session.flush()
        orphan_video = tmp_path / "orphan.mp4"
        orphan_video.write_bytes(b"orphaned download")
        orphan = MediaAsset(
            track_id=orphan_track.id,
            local_path=str(orphan_video),
            state=MediaState.AVAILABLE,
            manual_selection=True,
            pending_reference_swap=True,
        )
        db_session.add(orphan)

        # A perfectly ordinary, legitimate zero-reference asset (e.g. just
        # claimed off the queue, reference-sync not relevant to this test) —
        # must survive reconciliation untouched.
        legit_track = Track(spotify_track_id="t-legit", canonical_artist="C", canonical_title="D", duration_ms=1000)
        db_session.add(legit_track)
        await db_session.flush()
        legit = MediaAsset(track_id=legit_track.id, state=MediaState.CANDIDATE_SELECTED)
        db_session.add(legit)
        await db_session.commit()
        legit_id = legit.id
        orphan_id = orphan.id

        reconciled = await reconcile_orphaned_replacement_attempts(db_session)

        assert reconciled == 1
        assert await db_session.get(MediaAsset, orphan_id) is None
        assert not orphan_video.exists()

        still_there = await db_session.get(MediaAsset, legit_id)
        assert still_there is not None
        assert still_there.state == MediaState.CANDIDATE_SELECTED
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_interrupted_processing_reconciles_and_a_fresh_attempt_still_succeeds(db_session, tmp_path, monkeypatch):
    """Simulates a crash while an asset was mid-PROCESSING (i.e. sometime
    during mux/tag/lyrics — those steps run without an intermediate commit,
    so an interruption anywhere in that span leaves the asset in PROCESSING
    with a RUNNING DownloadAttempt). Confirms `reconcile_interrupted_assets`
    requeues it cleanly and a subsequent fresh attempt completes normally,
    with honest attempt bookkeeping (one failed/interrupted + one succeeded).
    """
    import shutil

    from app.core.config import get_settings
    from app.integrations.acquisition.ytdlp_download import DownloadedStreams
    from tests.fixtures.media_clips import make_aac_audio, make_h264_aac_video

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available in this environment")

    media_dir = tmp_path / "media"
    downloads_dir = tmp_path / "downloads"
    media_dir.mkdir()
    downloads_dir.mkdir()
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("DOWNLOADS_DIR", str(downloads_dir))
    get_settings.cache_clear()
    try:
        track = Track(spotify_track_id="t-interrupt", canonical_artist="A", canonical_title="B", duration_ms=2000)
        db_session.add(track)
        await db_session.flush()
        candidate = VideoCandidate(
            track_id=track.id,
            youtube_video_id="v-interrupt",
            title="Video",
            score=90,
            score_breakdown=[],
            rejection_flags=[],
        )
        db_session.add(candidate)
        await db_session.flush()

        # Simulate: this asset was claimed and got as far as PROCESSING, with
        # a RUNNING attempt row, when the process died.
        asset = MediaAsset(track_id=track.id, video_candidate_id=candidate.id, state=MediaState.PROCESSING)
        db_session.add(asset)
        await db_session.flush()
        stuck_attempt = DownloadAttempt(
            media_asset_id=asset.id, attempt_number=1, status=AttemptStatus.RUNNING, started_at=datetime.now(UTC)
        )
        db_session.add(stuck_attempt)
        await db_session.commit()
        asset_id = asset.id

        reconciled = await reconcile_interrupted_assets(db_session)
        assert reconciled == 1

        await db_session.refresh(asset)
        assert asset.state == MediaState.QUEUED
        await db_session.refresh(stuck_attempt)
        assert stuck_attempt.status == AttemptStatus.FAILED
        assert stuck_attempt.error_class == "Interrupted"

        # A fresh attempt (as the normal queue poller would trigger) now
        # succeeds cleanly — no double-processing, no leftover confusion.
        async def _good_streams(youtube_video_id, dest_dir):
            dest_dir.mkdir(parents=True, exist_ok=True)
            video_path = dest_dir / "video.mp4"
            audio_path = dest_dir / "audio.m4a"
            make_h264_aac_video(video_path, duration_s=2)
            make_aac_audio(audio_path, duration_s=2)
            return DownloadedStreams(video_path=video_path, audio_path=audio_path)

        monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams)

        claimed = await claim_next_ready_asset(db_session)
        assert claimed is not None and claimed.id == asset_id

        # Lyrics/media-server sync are both real, best-effort, non-fatal
        # calls here (exactly as in test_acquisition.py's own tests) — their
        # success or failure is irrelevant to what this test is proving.
        result = await process_media_asset(db_session, claimed)

        assert result.succeeded is True
        assert result.final_state == MediaState.AVAILABLE

        attempts = (
            await db_session.scalars(
                select(DownloadAttempt)
                .where(DownloadAttempt.media_asset_id == asset_id)
                .order_by(DownloadAttempt.attempt_number)
            )
        ).all()
        assert [a.status for a in attempts] == [AttemptStatus.FAILED, AttemptStatus.SUCCEEDED]
    finally:
        get_settings.cache_clear()
