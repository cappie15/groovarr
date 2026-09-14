""""Monitor for Better Versions" periodic pass (§36/§37) — see
app/jobs/upgrade_monitor.py. Proves: off by default and a no-op when
disabled; only touches Incomplete assets and non-manually-selected
Available assets; never touches a manually-selected asset; applies a
genuinely different winning candidate via the safe replace_track_media swap
(old file only removed after the new one lands); and leaves an asset alone
when the same video is still the best candidate found.

Uses one `scheduler._tick()` call directly (no polling loop/timing) — the
scheduler's own internal session-per-tick is the same underlying SQLite
file the test's `db_session` fixture writes to (see tests/conftest.py), so
data committed by the test setup is visible to it.
"""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.integrations.acquisition.ytdlp_download import DownloadedStreams
from app.integrations.youtube.data_api import RawCandidate
from app.integrations.youtube.ytdlp_client import EnrichedInfo
from app.jobs.upgrade_monitor import UpgradeMonitorScheduler
from app.services.settings_service import get_app_settings, set_monitor_better_versions_enabled
from tests.fixtures.media_clips import make_aac_audio, make_h264_aac_video

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


def _good_streams_factory():
    async def _download_streams(youtube_video_id: str, dest_dir: Path) -> DownloadedStreams:
        dest_dir.mkdir(parents=True, exist_ok=True)
        video_path = dest_dir / "video.mp4"
        audio_path = dest_dir / "audio.m4a"
        make_h264_aac_video(video_path, duration_s=2)
        make_aac_audio(audio_path, duration_s=2)
        return DownloadedStreams(video_path=video_path, audio_path=audio_path)

    return _download_streams


async def _make_asset(session, media_dir, *, state, manual_selection=False, old_video_id="old-vid"):
    track = Track(
        spotify_track_id=f"t-{old_video_id}", canonical_artist="Daft Punk", canonical_title="Around the World",
        duration_ms=2000,
    )
    session.add(track)
    await session.flush()
    playlist = SpotifyPlaylist(spotify_id=f"p-{old_video_id}", name="Playlist", connected=True)
    session.add(playlist)
    await session.flush()
    session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))

    old_candidate = VideoCandidate(
        track_id=track.id, youtube_video_id=old_video_id, title="Old", score=90, score_breakdown=[], rejection_flags=[]
    )
    session.add(old_candidate)
    await session.flush()

    video = media_dir / f"Daft Punk - Around the World ({old_video_id}) [480p].mp4"
    video.write_bytes(b"old file bytes")
    asset = MediaAsset(
        track_id=track.id,
        local_path=str(video),
        state=state,
        manual_selection=manual_selection,
        video_candidate_id=old_candidate.id,
        source_reference=f"youtube:{old_video_id}",
    )
    session.add(asset)
    await session.flush()
    from app.db.models.media import PlaylistMediaReference

    session.add(PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=asset.id))
    await session.commit()
    return track, asset, video


def _mock_discovery(monkeypatch, new_video_id: str):
    candidate = RawCandidate(
        youtube_video_id=new_video_id,
        title="Daft Punk - Around the World (Official Music Video)",
        channel_id="c1",
        channel_name="Daft Punk",
    )

    async def _discover(http, track):
        return [candidate]

    async def _enrich(video_id):
        return EnrichedInfo(duration_s=2.0, media_type="video", width=1920, height=1080)

    monkeypatch.setattr("app.services.search.discover_candidates", _discover)
    monkeypatch.setattr("app.services.search.enrich_candidate", _enrich)


@pytest.mark.asyncio
async def test_disabled_by_default_and_is_a_noop(db_session, media_dirs, monkeypatch):
    media_dir, _ = media_dirs
    _track, asset, video = await _make_asset(db_session, media_dir, state=MediaState.INCOMPLETE)
    _mock_discovery(monkeypatch, "new-and-better")

    settings_row = await get_app_settings(db_session)
    assert settings_row.monitor_better_versions_enabled is False  # default off (§36)

    await UpgradeMonitorScheduler()._tick()

    db_session.expire_all()
    await db_session.refresh(asset)
    assert asset.local_path == str(video)
    assert asset.source_reference == "youtube:old-vid"
    assert video.exists()


@pytest.mark.asyncio
async def test_replaces_an_incomplete_visualizer_with_a_better_candidate(db_session, media_dirs, monkeypatch):
    media_dir, _ = media_dirs
    await set_monitor_better_versions_enabled(db_session, True)
    _track, asset, video = await _make_asset(db_session, media_dir, state=MediaState.INCOMPLETE)
    _mock_discovery(monkeypatch, "new-and-better")
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    old_asset_id = asset.id
    await UpgradeMonitorScheduler()._tick()
    db_session.expire_all()

    # The OLD MediaAsset row is gone (replaced, not reused) and its file
    # deleted; a NEW asset now exists with the new source, manually flagged
    # since it came from an explicit replacement decision.
    assert await db_session.get(MediaAsset, old_asset_id) is None
    assert not video.exists()
    new_asset = (
        await db_session.execute(select(MediaAsset).where(MediaAsset.source_reference == "youtube:new-and-better"))
    ).scalar_one()
    assert new_asset.state == MediaState.AVAILABLE
    assert new_asset.manual_selection is True
    assert Path(new_asset.local_path).is_file()


@pytest.mark.asyncio
async def test_never_touches_a_manually_selected_asset(db_session, media_dirs, monkeypatch):
    media_dir, _ = media_dirs
    await set_monitor_better_versions_enabled(db_session, True)
    _track, asset, video = await _make_asset(
        db_session, media_dir, state=MediaState.AVAILABLE, manual_selection=True
    )
    _mock_discovery(monkeypatch, "new-and-better")
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    await UpgradeMonitorScheduler()._tick()

    db_session.expire_all()
    await db_session.refresh(asset)
    assert asset.local_path == str(video)
    assert asset.manual_selection is True
    assert video.exists()


@pytest.mark.asyncio
async def test_same_winning_candidate_triggers_no_replacement(db_session, media_dirs, monkeypatch):
    media_dir, _ = media_dirs
    await set_monitor_better_versions_enabled(db_session, True)
    _track, asset, video = await _make_asset(
        db_session, media_dir, state=MediaState.AVAILABLE, old_video_id="already-best"
    )
    _mock_discovery(monkeypatch, "already-best")  # the "new" discovery finds the same video already in place
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    await UpgradeMonitorScheduler()._tick()

    db_session.expire_all()
    await db_session.refresh(asset)
    assert asset.local_path == str(video)  # untouched — nothing to replace
    assert video.exists()
