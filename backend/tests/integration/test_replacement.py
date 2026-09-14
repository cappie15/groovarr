"""Manual replacement workflow tests (§34) — see app/services/replacement.py.

The yt-dlp *network download* is mocked (mirroring test_acquisition.py, §92)
but everything downstream (ffmpeg mux, ffprobe validation, atomic import,
the reference-count swap, and cleanup) runs for real, proving the exact
safety property §34 requires: the OLD file stays completely intact and
`Available`/referenced for the *entire* duration of the new download, and is
only ever removed after the new one is proven good and swapped in.
"""

import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.integrations.acquisition.ytdlp_download import DownloadedStreams
from app.services.replacement import ReplacementError, replace_track_media
from tests.fixtures.media_clips import make_aac_audio, make_h264_aac_video, make_truncated_garbage

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


def _good_streams_factory(duration_s=2):
    async def _download_streams(youtube_video_id: str, dest_dir: Path) -> DownloadedStreams:
        dest_dir.mkdir(parents=True, exist_ok=True)
        video_path = dest_dir / "video.mp4"
        audio_path = dest_dir / "audio.m4a"
        make_h264_aac_video(video_path, duration_s=duration_s)
        make_aac_audio(audio_path, duration_s=duration_s)
        return DownloadedStreams(video_path=video_path, audio_path=audio_path)

    return _download_streams


def _corrupt_streams_factory():
    async def _download_streams(youtube_video_id: str, dest_dir: Path) -> DownloadedStreams:
        dest_dir.mkdir(parents=True, exist_ok=True)
        video_path = dest_dir / "video.mp4"
        audio_path = dest_dir / "audio.m4a"
        make_truncated_garbage(video_path)
        make_truncated_garbage(audio_path)
        return DownloadedStreams(video_path=video_path, audio_path=audio_path)

    return _download_streams


async def _make_existing_available_track_and_asset(session, media_dir, *, state=MediaState.AVAILABLE):
    track = Track(
        spotify_track_id="t-replace", canonical_artist="Daft Punk", canonical_title="Around the World", duration_ms=2000
    )
    session.add(track)
    await session.flush()

    old_video = media_dir / "Daft Punk - Around the World (Unknown) [480p].mp4"
    old_video.write_bytes(b"old but perfectly fine video bytes")

    old_asset = MediaAsset(
        track_id=track.id,
        local_path=str(old_video),
        state=state,
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
    )
    session.add(old_asset)
    await session.flush()

    playlist = SpotifyPlaylist(spotify_id="p-replace", name="Playlist", connected=True)
    session.add(playlist)
    await session.flush()
    session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    session.add(
        PlaylistMediaReference(playlist_id=playlist.id, track_id=track.id, media_asset_id=old_asset.id)
    )
    await session.commit()
    return track, old_asset, old_video, playlist


async def _make_new_candidate(session, track_id: int, *, video_id="new-vid") -> VideoCandidate:
    candidate = VideoCandidate(
        track_id=track_id,
        youtube_video_id=video_id,
        title="New Official Video",
        score=95,
        score_breakdown=[],
        rejection_flags=[],
    )
    session.add(candidate)
    await session.commit()
    return candidate


@pytest.mark.asyncio
async def test_replacement_happy_path_swaps_atomically_and_cleans_up_old_file(db_session, media_dirs, monkeypatch):
    media_dir, _ = media_dirs
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    track, old_asset, old_video, playlist = await _make_existing_available_track_and_asset(db_session, media_dir)
    candidate = await _make_new_candidate(db_session, track.id)
    old_asset_id = old_asset.id

    async with httpx.AsyncClient() as http:
        new_asset = await replace_track_media(db_session, http, track.id, candidate.id)

    assert new_asset.state == MediaState.AVAILABLE
    assert new_asset.manual_selection is True
    assert new_asset.video_candidate_id == candidate.id
    assert new_asset.local_path is not None
    new_video = Path(new_asset.local_path)
    assert new_video.is_file()
    assert new_video.parent == media_dir

    # Old file and row are gone — but only *after* the new one landed.
    assert not old_video.exists()
    assert await db_session.get(MediaAsset, old_asset_id) is None

    # The playlist's reference now points at the new asset, not the old one.
    ref = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == playlist.id, PlaylistMediaReference.track_id == track.id
        )
    )
    assert ref is not None
    assert ref.media_asset_id == new_asset.id


@pytest.mark.asyncio
async def test_replacement_failure_leaves_the_original_file_completely_untouched(db_session, media_dirs, monkeypatch):
    media_dir, _ = media_dirs
    monkeypatch.setattr("app.services.acquisition.download_streams", _corrupt_streams_factory())

    track, old_asset, old_video, playlist = await _make_existing_available_track_and_asset(db_session, media_dir)
    candidate = await _make_new_candidate(db_session, track.id)
    original_bytes = old_video.read_bytes()

    async with httpx.AsyncClient() as http:
        with pytest.raises(ReplacementError):
            await replace_track_media(db_session, http, track.id, candidate.id)

    # Old asset is exactly as it was — same row, same path, same content,
    # same state, still referenced.
    await db_session.refresh(old_asset)
    assert old_asset.state == MediaState.AVAILABLE
    assert old_asset.local_path == str(old_video)
    assert old_video.read_bytes() == original_bytes

    ref = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == playlist.id, PlaylistMediaReference.track_id == track.id
        )
    )
    assert ref is not None and ref.media_asset_id == old_asset.id

    # The failed attempt's MediaAsset row was cleaned up, not left dangling
    # in DOWNLOADING forever.
    leftover = (
        await db_session.execute(select(MediaAsset).where(MediaAsset.state == MediaState.DOWNLOADING))
    ).scalars().all()
    assert leftover == []


@pytest.mark.asyncio
async def test_replacement_refuses_when_no_existing_media_to_replace(db_session, media_dirs):
    track = Track(spotify_track_id="t-none", canonical_artist="Artist", canonical_title="Title", duration_ms=1000)
    db_session.add(track)
    await db_session.commit()
    candidate = await _make_new_candidate(db_session, track.id)

    async with httpx.AsyncClient() as http:
        with pytest.raises(ReplacementError, match="no existing"):
            await replace_track_media(db_session, http, track.id, candidate.id)


@pytest.mark.asyncio
async def test_replacement_refuses_unknown_candidate(db_session, media_dirs):
    media_dir, _ = media_dirs
    track, _old_asset, _old_video, _playlist = await _make_existing_available_track_and_asset(db_session, media_dir)

    async with httpx.AsyncClient() as http:
        with pytest.raises(ReplacementError, match="No candidate"):
            await replace_track_media(db_session, http, track.id, 999999)


@pytest.mark.asyncio
async def test_replacement_works_for_an_incomplete_visualizer_asset_too(db_session, media_dirs, monkeypatch):
    """§26/§37: replacing an Incomplete (visualizer-only) asset with a real
    music video follows the exact same safe swap.
    """
    media_dir, _ = media_dirs
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    track, old_asset, old_video, _playlist = await _make_existing_available_track_and_asset(
        db_session, media_dir, state=MediaState.INCOMPLETE
    )
    candidate = await _make_new_candidate(db_session, track.id)

    async with httpx.AsyncClient() as http:
        new_asset = await replace_track_media(db_session, http, track.id, candidate.id)

    assert new_asset.state == MediaState.AVAILABLE  # a manual choice is never auto-downgraded (§33)
    assert not old_video.exists()
