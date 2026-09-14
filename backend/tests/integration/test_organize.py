"""Organize/Rename integration tests (§15) — see app/services/organize.py.

Proves the action is safe (stays under the media root, refuses to overwrite
a different existing file, is a no-op if already correctly named) and that
it never runs implicitly — only via an explicit call.
"""

import pytest

from app.core.config import get_settings
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import Track
from app.services.organize import OrganizeError, organize_media_asset


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    directory = tmp_path / "music-videos"
    directory.mkdir()
    monkeypatch.setenv("MEDIA_DIR", str(directory))
    get_settings.cache_clear()
    yield directory
    get_settings.cache_clear()


async def _make_track(session, *, artist="Daft Punk", title="Around the World", year=1997) -> Track:
    track = Track(
        spotify_track_id="t1",
        canonical_artist=artist,
        canonical_title=title,
        release_year=year,
        duration_ms=200_000,
    )
    session.add(track)
    await session.flush()
    return track


@pytest.mark.asyncio
async def test_organize_renames_to_default_template(db_session, media_dir):
    track = await _make_track(db_session)
    source = media_dir / "weird_original_filename.mp4"
    source.write_bytes(b"fake mp4 bytes")

    asset = MediaAsset(
        track_id=track.id, local_path=str(source), resolution_label="1080p", state=MediaState.AVAILABLE
    )
    db_session.add(asset)
    await db_session.commit()

    updated = await organize_media_asset(db_session, asset)

    expected = media_dir / "Daft Punk - Around the World (1997) [1080p].mp4"
    assert updated.local_path == str(expected)
    assert expected.exists()
    assert not source.exists()


@pytest.mark.asyncio
async def test_organize_is_a_noop_if_already_named_correctly(db_session, media_dir):
    track = await _make_track(db_session)
    already_correct = media_dir / "Daft Punk - Around the World (1997) [1080p].mp4"
    already_correct.write_bytes(b"fake mp4 bytes")

    asset = MediaAsset(
        track_id=track.id, local_path=str(already_correct), resolution_label="1080p", state=MediaState.AVAILABLE
    )
    db_session.add(asset)
    await db_session.commit()

    updated = await organize_media_asset(db_session, asset)
    assert updated.local_path == str(already_correct)
    assert already_correct.exists()


@pytest.mark.asyncio
async def test_organize_refuses_to_overwrite_a_different_existing_file(db_session, media_dir):
    track = await _make_track(db_session)
    source = media_dir / "source.mp4"
    source.write_bytes(b"source bytes")
    collision = media_dir / "Daft Punk - Around the World (1997) [1080p].mp4"
    collision.write_bytes(b"a completely different file that happens to already be there")

    asset = MediaAsset(
        track_id=track.id, local_path=str(source), resolution_label="1080p", state=MediaState.AVAILABLE
    )
    db_session.add(asset)
    await db_session.commit()

    with pytest.raises(OrganizeError, match="already exists"):
        await organize_media_asset(db_session, asset)

    # Neither file was touched.
    assert source.read_bytes() == b"source bytes"
    assert collision.read_bytes() == b"a completely different file that happens to already be there"


@pytest.mark.asyncio
async def test_organize_refuses_manual_review_assets_without_confirmed_track(db_session, media_dir):
    source = media_dir / "candidate.mp4"
    source.write_bytes(b"bytes")

    asset = MediaAsset(
        candidate_track_id=None,
        track_id=None,
        local_path=str(source),
        state=MediaState.MANUAL_REVIEW_REQUIRED,
        review_reason="test",
    )
    db_session.add(asset)
    await db_session.commit()

    with pytest.raises(OrganizeError, match="Manual Review"):
        await organize_media_asset(db_session, asset)

    assert source.exists()  # untouched


@pytest.mark.asyncio
async def test_organize_refuses_path_outside_media_root(db_session, media_dir, tmp_path):
    track = await _make_track(db_session)
    outside = tmp_path / "outside-media-root.mp4"
    outside.write_bytes(b"bytes")

    asset = MediaAsset(track_id=track.id, local_path=str(outside), state=MediaState.AVAILABLE)
    db_session.add(asset)
    await db_session.commit()

    with pytest.raises(OrganizeError, match="media root"):
        await organize_media_asset(db_session, asset)

    assert outside.exists()  # untouched
