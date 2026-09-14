"""Unit tests for pure Spotify parsing/normalization helpers (§92)."""

import pytest

from app.integrations.spotify.utils import (
    parse_playlist_id,
    parse_release_year,
    parse_version,
    split_artists,
    strip_version_and_feature_text,
)


class TestParsePlaylistId:
    def test_bare_id(self) -> None:
        assert parse_playlist_id("37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"

    def test_open_spotify_url(self) -> None:
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123def456"
        assert parse_playlist_id(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_open_spotify_url_no_query(self) -> None:
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_playlist_id(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_intl_locale_url(self) -> None:
        url = "https://open.spotify.com/intl-nl/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_playlist_id(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_spotify_uri(self) -> None:
        assert parse_playlist_id("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"

    def test_garbage_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Could not recognize"):
            parse_playlist_id("not a playlist at all")

    def test_too_short_id_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_playlist_id("short")


class TestParseVersion:
    @pytest.mark.parametrize(
        "track_name,expected",
        [
            ("Strobe (Extended Mix)", "Extended Mix"),
            ("Levels (Radio Edit)", "Radio Edit"),
            ("One More Time - Remix", "Remix"),
            ("Song Title (Club Mix)", "Club Mix"),
            ("Song Title (Acoustic Version)", "Acoustic Version"),
            ("Song Title (John Doe Remix)", "John Doe Remix"),
            ("Song Title [Instrumental]", "Instrumental"),
            ("Song Title - Remastered 2011", "Remastered 2011"),
        ],
    )
    def test_detects_version_markers(self, track_name: str, expected: str) -> None:
        assert parse_version(track_name) == expected

    @pytest.mark.parametrize(
        "track_name",
        [
            "Around the World",
            "Song Title (feat. Someone)",
            "Just a Normal Song Name",
        ],
    )
    def test_no_false_positive_on_plain_titles(self, track_name: str) -> None:
        assert parse_version(track_name) is None


class TestStripVersionAndFeatureText:
    def test_strips_bracketed_version(self) -> None:
        assert strip_version_and_feature_text("Strobe (Extended Mix)") == "Strobe"

    def test_strips_inline_feature_clause(self) -> None:
        assert strip_version_and_feature_text("Song Title (feat. Other Artist)") == "Song Title"

    def test_strips_ft_variant(self) -> None:
        assert strip_version_and_feature_text("Song Title (ft. Other Artist)") == "Song Title"

    def test_leaves_plain_title_untouched(self) -> None:
        assert strip_version_and_feature_text("Around the World") == "Around the World"

    def test_leaves_unrelated_bracketed_text_alone(self) -> None:
        # A bracketed segment that isn't a version/feature marker should survive.
        assert strip_version_and_feature_text("Song Title (Unplugged)") == "Song Title (Unplugged)"


class TestSplitArtists:
    def test_single_artist(self) -> None:
        primary, featured = split_artists(["Daft Punk"])
        assert primary == "Daft Punk"
        assert featured == []

    def test_primary_plus_featured(self) -> None:
        primary, featured = split_artists(["Calvin Harris", "Rihanna"])
        assert primary == "Calvin Harris"
        assert featured == ["Rihanna"]

    def test_multiple_featured(self) -> None:
        primary, featured = split_artists(["A", "B", "C"])
        assert primary == "A"
        assert featured == ["B", "C"]

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            split_artists([])


class TestParseReleaseYear:
    @pytest.mark.parametrize(
        "release_date,expected",
        [
            ("1997", 1997),
            ("1997-01", 1997),
            ("1997-01-14", 1997),
            (None, None),
            ("", None),
        ],
    )
    def test_derives_year_at_any_precision(self, release_date: str | None, expected: int | None) -> None:
        assert parse_release_year(release_date) == expected
