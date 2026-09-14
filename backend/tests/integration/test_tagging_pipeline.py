"""Phase 6 (Metadata) integration tests: tagging wired into the Phase 5
acquisition pipeline as a non-fatal stage between validation and import.
Reuses Phase 5's `_make_candidate_selected_asset` fixture-builder rather
than duplicating it.
"""

import shutil
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.db.models.media import MediaState
from app.db.models.spotify import Track
from app.integrations.tagging import mp4_tags
from app.services.acquisition import claim_next_ready_asset, process_media_asset
from tests.integration.test_acquisition import _good_streams_factory, _make_candidate_selected_asset

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


@pytest.mark.asyncio
async def test_successful_import_writes_real_tags_from_spotify_data_not_youtube_text(
    db_session, media_dirs, monkeypatch, tmp_path
):
    from mutagen.mp4 import MP4

    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    artwork = tmp_path / "cover.jpg"
    artwork.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)

    asset = await _make_candidate_selected_asset(db_session, artist="Calvin Harris", title="This Is What You Came For")
    track = await db_session.get(Track, asset.track_id)
    track.featured_artists = ["Rihanna"]
    track.release_year = 2016
    track.explicit = True
    track.album_artwork_path = str(artwork)
    await db_session.commit()

    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.tags_written is True

    tags = MP4(claimed.local_path)
    # Spotify-canonical data, never the VideoCandidate's own YouTube-style
    # title/channel text (which in this fixture is a plain placeholder
    # unrelated to these asserted values — see _make_candidate_selected_asset).
    assert tags["\xa9nam"] == ["This Is What You Came For"]
    assert tags["\xa9ART"] == ["Calvin Harris feat. Rihanna"]
    assert tags["\xa9day"] == ["2016"]
    assert tags["rtng"] == [1]  # explicit
    assert "covr" in tags


@pytest.mark.asyncio
async def test_successful_import_with_no_track_data_skips_tagging_gracefully(db_session, media_dirs, monkeypatch):
    """`track` can be `None` in principle (asset.track_id unset) — tagging
    must be skipped cleanly, not crash the import."""
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    asset = await _make_candidate_selected_asset(db_session)
    asset.track_id = None
    await db_session.commit()

    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.state == MediaState.AVAILABLE
    assert claimed.tags_written is False


@pytest.mark.asyncio
async def test_tagging_failure_does_not_fail_the_import(db_session, media_dirs, monkeypatch):
    """The core Phase 6 correctness requirement: a broken tag write must
    never cost the user an otherwise-successfully-acquired video (§48
    applied to tagging)."""
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    # `apply_tags_best_effort` itself never raises in real usage (it catches
    # everything internally) — this simulates the *reported-failure* path,
    # i.e. what the pipeline sees whenever mutagen throws underneath it.
    monkeypatch.setattr(
        "app.services.acquisition.apply_tags_best_effort",
        lambda path, fields: mp4_tags.TaggingResult(success=False, error="simulated mutagen failure"),
    )

    await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)

    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.state == MediaState.AVAILABLE
    assert claimed.tags_written is False
    assert claimed.local_path is not None and Path(claimed.local_path).is_file()


@pytest.mark.asyncio
async def test_tagging_never_corrupts_the_media_streams(db_session, media_dirs, monkeypatch):
    """Tag-writing only touches the moov/udta/meta/ilst atom — the video/
    audio streams themselves must be byte-identical in substance
    (same codecs/duration) after tagging."""
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory(duration_s=3))

    await _make_candidate_selected_asset(db_session, duration_ms=3000)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert claimed.video_codec == "h264"
    assert claimed.audio_codec == "aac"
    assert claimed.duration_s is not None
    assert abs(claimed.duration_s - 3.0) < 0.5
