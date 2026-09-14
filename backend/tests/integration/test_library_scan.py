"""Existing-library scan integration tests (§15) — see app/services/library_scan.py.

Uses tiny (2-second, black/silent) real MP4 fixtures generated via ffmpeg at
test time, tagged via mutagen where a test needs embedded artist/title tags
— this proves the scanner against real container/tag parsing rather than
mocked mutagen calls.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.mp4 import MP4
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track
from app.services.library_scan import scan_library

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available in this environment")


def _make_clip(path: Path, *, duration_s: int = 2) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={duration_s}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(duration_s),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _tag(path: Path, *, artist: str, title: str) -> None:
    mp4 = MP4(str(path))
    mp4["\xa9ART"] = [artist]
    mp4["\xa9nam"] = [title]
    mp4.save()


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    directory = tmp_path / "music-videos"
    directory.mkdir()
    monkeypatch.setenv("MEDIA_DIR", str(directory))
    get_settings.cache_clear()
    yield directory
    get_settings.cache_clear()


async def _make_track(session, *, spotify_track_id, artist, title, duration_ms) -> Track:
    track = Track(
        spotify_track_id=spotify_track_id, canonical_artist=artist, canonical_title=title, duration_ms=duration_ms
    )
    session.add(track)
    await session.flush()
    return track


@pytest.mark.asyncio
async def test_tagged_file_is_matched_with_high_confidence(db_session, media_dir):
    track = await _make_track(
        db_session, spotify_track_id="t1", artist="Daft Punk", title="Around the World", duration_ms=2000
    )
    playlist = SpotifyPlaylist(spotify_id="p1", name="Playlist")
    db_session.add(playlist)
    await db_session.flush()
    db_session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track.id, position=0, occurrence_index=0))
    await db_session.commit()

    clip = media_dir / "known_match.mp4"
    _make_clip(clip)
    _tag(clip, artist="Daft Punk", title="Around the World")

    result = await scan_library(db_session)

    assert result.files_scanned == 1
    assert result.matched_available == 1
    assert result.manual_review_created == 0

    asset = await db_session.scalar(select(MediaAsset).where(MediaAsset.local_path == str(clip)))
    assert asset is not None
    assert asset.state == MediaState.AVAILABLE
    assert asset.track_id == track.id

    # It must be reused, not redownloaded: linked into the playlist via
    # PlaylistMediaReference (§13/§15) rather than left dangling.
    reference = await db_session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == playlist.id,
            PlaylistMediaReference.track_id == track.id,
            PlaylistMediaReference.media_asset_id == asset.id,
        )
    )
    assert reference is not None


@pytest.mark.asyncio
async def test_uncertain_match_goes_to_manual_review_not_guessed(db_session, media_dir):
    # Exact artist match via filename heuristic, but a title that shares no
    # real signal and a duration wildly different from the track's — a
    # partial-confidence case that must NOT be silently auto-matched.
    await _make_track(
        db_session, spotify_track_id="t2", artist="Test Artist", title="Somewhat Different Title", duration_ms=300_000
    )

    clip = media_dir / "Test Artist - Totally Unrelated Wording.mp4"
    _make_clip(clip, duration_s=2)  # ~2s vs the track's 300s — nowhere near ±15%, and untagged (filename-only)

    result = await scan_library(db_session)

    assert result.files_scanned == 1
    assert result.matched_available == 0
    assert result.manual_review_created == 1

    asset = await db_session.scalar(select(MediaAsset).where(MediaAsset.local_path == str(clip)))
    assert asset is not None
    assert asset.state == MediaState.MANUAL_REVIEW_REQUIRED
    assert asset.track_id is None  # never guessed/confirmed automatically
    assert asset.candidate_track_id is not None
    assert asset.review_reason  # a human-readable explanation must be present


@pytest.mark.asyncio
async def test_file_with_no_plausible_signal_is_left_alone(db_session, media_dir):
    await _make_track(db_session, spotify_track_id="t3", artist="Some Artist", title="Some Song", duration_ms=200_000)

    clip = media_dir / "unrelated_clip.mp4"
    _make_clip(clip, duration_s=2)

    result = await scan_library(db_session)

    assert result.files_scanned == 1
    assert result.matched_available == 0
    assert result.manual_review_created == 0
    assert result.skipped_no_signal == 1

    asset = await db_session.scalar(select(MediaAsset).where(MediaAsset.local_path == str(clip)))
    assert asset is None  # no row at all — never renamed/moved/deleted per §15


@pytest.mark.asyncio
async def test_rescanning_skips_already_known_files(db_session, media_dir):
    track = await _make_track(
        db_session, spotify_track_id="t4", artist="Daft Punk", title="Around the World", duration_ms=2000
    )
    clip = media_dir / "known.mp4"
    _make_clip(clip)
    _tag(clip, artist="Daft Punk", title="Around the World")

    first = await scan_library(db_session)
    assert first.matched_available == 1

    second = await scan_library(db_session)
    assert second.files_scanned == 0
    assert second.skipped_already_known == 1
    assert second.matched_available == 0

    # Still exactly one MediaAsset for this file — no duplicate created.
    ids = (
        await db_session.scalars(
            select(MediaAsset.id).where(MediaAsset.local_path == str(clip)).where(MediaAsset.track_id == track.id)
        )
    ).all()
    assert len(ids) == 1
