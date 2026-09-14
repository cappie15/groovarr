"""Unit tests for `app.integrations.tagging.mp4_tags` — pure logic
(artist-tag formatting, cover-format sniffing) plus real mutagen/ffprobe
round-trips against tiny ffmpeg-generated fixtures (Phase 6, §42/§43).
"""

import shutil
from pathlib import Path

import pytest

from app.integrations.tagging.mp4_tags import (
    TagFields,
    TaggingError,
    apply_tags_best_effort,
    format_artist_tag,
    write_mp4_tags,
)
from tests.fixtures.media_clips import make_muxed_mp4

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available in this environment")


def test_format_artist_tag_no_featured_artists():
    assert format_artist_tag("Daft Punk", []) == "Daft Punk"


def test_format_artist_tag_single_featured_artist():
    assert format_artist_tag("Calvin Harris", ["Rihanna"]) == "Calvin Harris feat. Rihanna"


def test_format_artist_tag_multiple_featured_artists():
    assert (
        format_artist_tag("DJ Khaled", ["Justin Bieber", "Quavo", "Chance the Rapper"])
        == "DJ Khaled feat. Justin Bieber, Quavo & Chance the Rapper"
    )


@pytest.fixture
def real_mp4(tmp_path) -> Path:
    path = tmp_path / "video.mp4"
    make_muxed_mp4(path, duration_s=2)
    return path


def test_write_mp4_tags_round_trips_title_artist_year(real_mp4):
    from mutagen.mp4 import MP4

    write_mp4_tags(
        real_mp4,
        TagFields(title="Around the World", artist="Daft Punk", year=1997, artwork_path=None, explicit=False),
    )

    tags = MP4(str(real_mp4))
    assert tags["\xa9nam"] == ["Around the World"]
    assert tags["\xa9ART"] == ["Daft Punk"]
    assert tags["\xa9day"] == ["1997"]
    assert tags["rtng"] == [2]  # clean, since explicit=False


def test_write_mp4_tags_explicit_flag_maps_to_rtng_1(real_mp4):
    from mutagen.mp4 import MP4

    write_mp4_tags(
        real_mp4, TagFields(title="Song", artist="Artist", year=None, artwork_path=None, explicit=True)
    )
    assert MP4(str(real_mp4))["rtng"] == [1]


def test_write_mp4_tags_omits_year_when_absent(real_mp4):
    from mutagen.mp4 import MP4

    write_mp4_tags(real_mp4, TagFields(title="Song", artist="Artist", year=None, artwork_path=None))
    assert "\xa9day" not in MP4(str(real_mp4))


def test_write_mp4_tags_writes_lyrics_when_present(real_mp4):
    from mutagen.mp4 import MP4

    write_mp4_tags(
        real_mp4,
        TagFields(title="Song", artist="Artist", year=None, artwork_path=None, lyrics="La la la\nLa la la"),
    )
    assert MP4(str(real_mp4))["\xa9lyr"] == ["La la la\nLa la la"]


def test_write_mp4_tags_embeds_jpeg_artwork(real_mp4, tmp_path):
    from mutagen.mp4 import MP4, MP4Cover

    artwork = tmp_path / "cover.jpg"
    # Minimal-but-valid JPEG magic-byte prefix is enough for our own sniffing
    # logic; mutagen doesn't itself decode the image, just stores the bytes.
    artwork.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)

    write_mp4_tags(real_mp4, TagFields(title="Song", artist="Artist", year=None, artwork_path=artwork))

    covers = MP4(str(real_mp4))["covr"]
    assert len(covers) == 1
    assert covers[0].imageformat == MP4Cover.FORMAT_JPEG


def test_write_mp4_tags_missing_artwork_file_is_skipped_not_fatal(real_mp4, tmp_path):
    from mutagen.mp4 import MP4

    missing = tmp_path / "does-not-exist.jpg"
    write_mp4_tags(real_mp4, TagFields(title="Song", artist="Artist", year=None, artwork_path=missing))
    assert "covr" not in MP4(str(real_mp4))


def test_write_mp4_tags_rejects_unrecognized_artwork_format(real_mp4, tmp_path):
    bogus = tmp_path / "cover.bin"
    bogus.write_bytes(b"not an image at all")
    with pytest.raises(TaggingError):
        write_mp4_tags(real_mp4, TagFields(title="Song", artist="Artist", year=None, artwork_path=bogus))


def test_write_mp4_tags_missing_media_file_raises(tmp_path):
    with pytest.raises(TaggingError):
        write_mp4_tags(
            tmp_path / "nope.mp4", TagFields(title="Song", artist="Artist", year=None, artwork_path=None)
        )


def test_apply_tags_best_effort_succeeds_on_valid_input(real_mp4):
    result = apply_tags_best_effort(
        real_mp4, TagFields(title="Song", artist="Artist", year=2020, artwork_path=None)
    )
    assert result.success is True
    assert result.error is None


def test_apply_tags_best_effort_never_raises_on_failure(tmp_path):
    """The whole point of this wrapper (§48 applied to tagging): a broken
    tag write must be reported, never raised, so it can never abort an
    otherwise-successful import."""
    result = apply_tags_best_effort(
        tmp_path / "does-not-exist.mp4", TagFields(title="Song", artist="Artist", year=None, artwork_path=None)
    )
    assert result.success is False
    assert result.error is not None
