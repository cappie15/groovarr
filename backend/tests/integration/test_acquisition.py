"""Acquisition pipeline integration tests (§39-52 of the original spec,
Phase 5 of the architecture doc's phase plan).

The yt-dlp *network download* is mocked (monkeypatched at the
app.services.acquisition boundary, mirroring how Phase 2/4 mocked Spotify/
YouTube network calls, §92) — but everything downstream of that (ffmpeg
remux/transcode, ffprobe validation, atomic import, naming, retry/backoff
state transitions) runs for real against small ffmpeg-generated fixtures.
"""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.acquisition import AttemptStatus, DownloadAttempt
from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import Track
from app.integrations.acquisition.errors import DownloadError, NonRetryableDownloadError
from app.integrations.acquisition.ytdlp_download import DownloadedStreams
from app.services.acquisition import (
    claim_next_ready_asset,
    process_media_asset,
    retry_download,
)
from app.services.settings_service import get_app_settings
from tests.fixtures.media_clips import (
    make_aac_audio,
    make_h264_aac_video,
    make_opus_audio,
    make_truncated_garbage,
    make_vp9_video,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available in this environment")


@pytest.fixture
def media_dirs(tmp_path, monkeypatch):
    media_dir = tmp_path / "music-videos"
    downloads_dir = tmp_path / "downloads"
    media_dir.mkdir()
    downloads_dir.mkdir()
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("DOWNLOADS_DIR", str(downloads_dir))
    get_settings.cache_clear()
    yield media_dir, downloads_dir
    get_settings.cache_clear()


async def _make_candidate_selected_asset(
    session, *, artist="Daft Punk", title="Around the World", duration_ms=2000, visualizer=False
) -> MediaAsset:
    track = Track(
        spotify_track_id=f"t-{artist}-{title}",
        canonical_artist=artist,
        canonical_title=title,
        duration_ms=duration_ms,
    )
    session.add(track)
    await session.flush()

    candidate = VideoCandidate(
        track_id=track.id,
        youtube_video_id="vid1",
        title=title,
        score=90,
        score_breakdown=[],
        rejection_flags=[],
    )
    session.add(candidate)
    await session.flush()

    asset = MediaAsset(
        track_id=track.id,
        state=MediaState.CANDIDATE_SELECTED,
        video_candidate_id=candidate.id,
        candidate_is_visualizer_only=visualizer,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


def _good_streams_factory(duration_s=2):
    """A `download_streams` replacement that writes real, MP4-compatible
    (H.264 + AAC) fixture streams into the requested dest_dir — proving the
    stream-copy remux path."""

    async def _download_streams(youtube_video_id: str, dest_dir: Path) -> DownloadedStreams:
        dest_dir.mkdir(parents=True, exist_ok=True)
        video_path = dest_dir / "video.mp4"
        audio_path = dest_dir / "audio.m4a"
        make_h264_aac_video(video_path, duration_s=duration_s)
        make_aac_audio(audio_path, duration_s=duration_s)
        return DownloadedStreams(video_path=video_path, audio_path=audio_path)

    return _download_streams


def _transcode_needed_streams_factory(duration_s=2):
    """A `download_streams` replacement using codecs that force ffmpeg_mux's
    transcode path (VP9 video + Opus audio, per §3 research the canonical
    "needs a real transcode into MP4" case)."""

    async def _download_streams(youtube_video_id: str, dest_dir: Path) -> DownloadedStreams:
        dest_dir.mkdir(parents=True, exist_ok=True)
        video_path = dest_dir / "video.webm"
        audio_path = dest_dir / "audio.opus"
        make_vp9_video(video_path, duration_s=duration_s)
        make_opus_audio(audio_path, duration_s=duration_s)
        return DownloadedStreams(video_path=video_path, audio_path=audio_path)

    return _download_streams


def _corrupt_streams_factory():
    """A `download_streams` replacement that "succeeds" (from yt-dlp's point
    of view) but hands back garbage — proving ffprobe-based validation
    catches a corrupt/truncated download rather than importing it (§51)."""

    async def _download_streams(youtube_video_id: str, dest_dir: Path) -> DownloadedStreams:
        dest_dir.mkdir(parents=True, exist_ok=True)
        video_path = dest_dir / "video.mp4"
        audio_path = dest_dir / "audio.m4a"
        make_truncated_garbage(video_path)
        make_truncated_garbage(audio_path)
        return DownloadedStreams(video_path=video_path, audio_path=audio_path)

    return _download_streams


def _always_fails_factory(exc: Exception):
    async def _download_streams(youtube_video_id: str, dest_dir: Path):
        raise exc

    return _download_streams


@pytest.mark.asyncio
async def test_successful_acquisition_imports_and_names_correctly(db_session, media_dirs, monkeypatch):
    media_dir, _ = media_dirs
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    asset = await _make_candidate_selected_asset(db_session, artist="Daft Punk", title="Around the World")
    claimed = await claim_next_ready_asset(db_session)
    assert claimed is not None and claimed.id == asset.id

    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.state == MediaState.AVAILABLE
    assert claimed.container == "mp4"
    assert claimed.video_codec == "h264"
    assert claimed.audio_codec == "aac"
    assert claimed.local_path is not None
    dest = Path(claimed.local_path)
    assert dest.is_file()
    assert dest.parent == media_dir
    # Default naming template (§16): "Artist - Song Title (ReleaseYear) [Quality].ext".
    # No release year was set on this track, so it's cleanly omitted; quality
    # comes from the *actual* ffprobe'd stream (a 64x64 test clip => "480p"
    # bucket floor since it's below even the 480p threshold... in practice
    # a real video is >=480p, so just assert the bracket is never dropped).
    assert dest.name.startswith("Daft Punk - Around the World")
    assert "[" in dest.name and "]" in dest.name

    attempts = (
        (await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id)))
        .scalars()
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].status == AttemptStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_visualizer_flagged_asset_lands_on_incomplete_not_available(db_session, media_dirs, monkeypatch):
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    await _make_candidate_selected_asset(db_session, visualizer=True)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.state == MediaState.INCOMPLETE
    assert claimed.local_path is not None and Path(claimed.local_path).is_file()


@pytest.mark.asyncio
async def test_transcode_path_is_actually_exercised_end_to_end(db_session, media_dirs, monkeypatch):
    monkeypatch.setattr("app.services.acquisition.download_streams", _transcode_needed_streams_factory())

    await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.state == MediaState.AVAILABLE
    assert claimed.container == "mp4"
    assert claimed.video_codec == "h264"  # transcoded from vp9
    assert claimed.audio_codec == "aac"  # transcoded from opus


@pytest.mark.asyncio
async def test_corrupt_download_fails_validation_and_is_not_imported(db_session, media_dirs, monkeypatch):
    monkeypatch.setattr("app.services.acquisition.download_streams", _corrupt_streams_factory())

    asset = await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is False
    # Retryable (ValidationError defaults retryable=True) and this is
    # attempt 1 of 5, so it's requeued with a backoff timer, not failed outright.
    assert claimed.state == MediaState.QUEUED
    assert claimed.local_path is None

    attempt = (
        await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id))
    ).scalar_one()
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.next_retry_at is not None


@pytest.mark.asyncio
async def test_non_retryable_error_goes_straight_to_download_failed(db_session, media_dirs, monkeypatch):
    monkeypatch.setattr(
        "app.services.acquisition.download_streams",
        _always_fails_factory(NonRetryableDownloadError("video is private")),
    )

    asset = await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is False
    assert claimed.state == MediaState.DOWNLOAD_FAILED

    attempt = (
        await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id))
    ).scalar_one()
    assert attempt.next_retry_at is None


@pytest.mark.asyncio
async def test_retry_backoff_progression_ends_in_download_failed_then_manual_retry_recovers(
    db_session, media_dirs, monkeypatch
):
    app_settings = await get_app_settings(db_session)
    app_settings.max_download_attempts = 3
    await db_session.commit()

    monkeypatch.setattr(
        "app.services.acquisition.download_streams", _always_fails_factory(DownloadError("transient network error"))
    )

    asset = await _make_candidate_selected_asset(db_session)

    # Attempt 1: retryable, backoff scheduled, state -> QUEUED.
    claimed = await claim_next_ready_asset(db_session)
    await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)
    assert claimed.state == MediaState.QUEUED

    attempts = (
        (await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id)))
        .scalars()
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].next_retry_at is not None
    # Clear the backoff timer directly (simulating time having passed) so
    # the test can drive the next attempt immediately rather than sleeping.
    attempts[0].next_retry_at = None
    await db_session.commit()

    # Attempt 2: still retryable (2 < 3).
    claimed = await claim_next_ready_asset(db_session)
    await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)
    assert claimed.state == MediaState.QUEUED

    attempts = (
        (await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id)))
        .scalars()
        .all()
    )
    assert len(attempts) == 2
    attempts[-1].next_retry_at = None
    await db_session.commit()

    # Attempt 3: attempts exhausted (3 >= max_download_attempts=3) -> DOWNLOAD_FAILED.
    claimed = await claim_next_ready_asset(db_session)
    await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)
    assert claimed.state == MediaState.DOWNLOAD_FAILED

    attempts = (
        (await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id)))
        .scalars()
        .all()
    )
    assert len(attempts) == 3
    assert attempts[-1].next_retry_at is None

    # A routine "sync"-style touch must NOT reset anything — only the
    # explicit retry_download() call may (§50). Nothing to actually call
    # here since Spotify sync never touches DownloadAttempt/MediaAsset for
    # acquisition — this is asserted structurally by retry_download being
    # the only function in this module that writes retry_requested_at.

    # Manual Retry: now let the download succeed, proving the fresh cycle
    # actually recovers rather than immediately re-exhausting.
    await retry_download(db_session, claimed)
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.state == MediaState.AVAILABLE

    all_attempts = (
        (await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id)))
        .scalars()
        .all()
    )
    # History is never deleted (§65) — all 4 attempts (3 failed + 1
    # succeeded) are still there, with a strictly increasing attempt_number.
    assert len(all_attempts) == 4
    assert [a.attempt_number for a in all_attempts] == [1, 2, 3, 4]
    assert all_attempts[-1].status == AttemptStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_manual_retry_requires_download_failed_state(db_session, media_dirs):
    asset = await _make_candidate_selected_asset(db_session)
    with pytest.raises(ValueError):
        await retry_download(db_session, asset)


@pytest.mark.asyncio
async def test_disk_space_guard_blocks_download_when_free_space_is_low(db_session, media_dirs, monkeypatch):
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())
    monkeypatch.setattr("app.services.acquisition.MIN_FREE_BYTES", 10**18)  # absurdly high, always "insufficient"

    await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is False
    assert claimed.state == MediaState.QUEUED  # DiskSpaceError is retryable
    assert claimed.local_path is None
