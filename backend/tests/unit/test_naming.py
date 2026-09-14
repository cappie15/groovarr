"""Naming template engine tests (§16-17) — see app/domain/naming.py."""

from app.domain.naming import NamingFields, disambiguate_filename, render_filename, sanitize_filename_component


def test_default_format_exact() -> None:
    fields = NamingFields(artist="Daft Punk", title="Around the World", year=1997, quality="1080p", ext="mkv")
    assert render_filename(fields) == "Daft Punk - Around the World (1997) [1080p].mkv"


def test_missing_release_year_omits_parens_entirely() -> None:
    fields = NamingFields(artist="Example Artist", title="Example Song", year=None, quality="2160p", ext="mp4")
    assert render_filename(fields) == "Example Artist - Example Song [2160p].mp4"


def test_missing_quality_uses_explicit_placeholder_not_omission() -> None:
    fields = NamingFields(artist="Artist", title="Title", year=2020, quality=None, ext="mp4")
    result = render_filename(fields)
    assert result == "Artist - Title (2020) [Unknown].mp4"
    assert "[" in result and "]" in result  # the bracket group must never be silently dropped


def test_remix_version_suffixed_title_passes_through() -> None:
    fields = NamingFields(
        artist="Artist", title="Song Title (John Doe Remix)", year=2021, quality="1080p", ext="mp4"
    )
    assert render_filename(fields) == "Artist - Song Title (John Doe Remix) (2021) [1080p].mp4"


def test_unicode_artist_and_title_preserved() -> None:
    fields = NamingFields(artist="Beyoncé", title="Déjà Vu", year=2006, quality="720p", ext="mp4")
    assert render_filename(fields) == "Beyoncé - Déjà Vu (2006) [720p].mp4"


def test_non_latin_script_preserved() -> None:
    fields = NamingFields(artist="宇多田ヒカル", title="First Love", year=1999, quality="480p", ext="mp4")
    assert render_filename(fields) == "宇多田ヒカル - First Love (1999) [480p].mp4"


def test_very_long_names_are_capped_not_left_unbounded() -> None:
    long_artist = "A" * 500
    long_title = "B" * 500
    fields = NamingFields(artist=long_artist, title=long_title, year=2020, quality="1080p", ext="mp4")
    result = render_filename(fields)
    assert len(result) < 400
    assert result.startswith("A" * 50)


def test_illegal_characters_stripped() -> None:
    component = sanitize_filename_component('Artist: "Weird" / Name?')
    assert "/" not in component
    assert ":" not in component
    assert '"' not in component
    assert "?" not in component


def test_path_traversal_separators_never_survive() -> None:
    # The sanitizer's job is stripping the separators that would let this
    # string act as a *path* rather than a single filename component; a bare
    # run of dots (no slashes) can't traverse anything on its own. The
    # stronger guarantee — that a computed destination can never resolve
    # outside the configured media root even if a component were somehow
    # exactly ".." — is organize.py's job (it re-resolves and checks
    # containment independently; see test_organize.py).
    component = sanitize_filename_component("../../etc/passwd")
    assert "/" not in component
    assert "\\" not in component
    assert component not in (".", "..")


def test_reserved_windows_device_name_is_disambiguated() -> None:
    assert sanitize_filename_component("CON") != "CON"
    assert sanitize_filename_component("con") not in ("con", "CON")
    assert sanitize_filename_component("NUL") != "NUL"
    # An ordinary name that merely contains a reserved word is left alone.
    assert sanitize_filename_component("Console Wars") == "Console Wars"


def test_trailing_dots_and_spaces_trimmed() -> None:
    assert sanitize_filename_component("Trailing dot.") == "Trailing dot"
    assert sanitize_filename_component("Trailing space ") == "Trailing space"


def test_empty_component_gets_placeholder() -> None:
    assert sanitize_filename_component("") == "Unknown"
    assert sanitize_filename_component("   ") == "Unknown"


def test_disambiguate_no_collision_returns_unchanged() -> None:
    assert disambiguate_filename("Artist - Title (2020) [1080p].mp4", set()) == "Artist - Title (2020) [1080p].mp4"


def test_disambiguate_collision_appends_counter() -> None:
    name = "Artist - Title (2020) [1080p].mp4"
    existing = {name}
    result = disambiguate_filename(name, existing)
    assert result == "Artist - Title (2020) [1080p] (2).mp4"

    existing.add(result)
    result2 = disambiguate_filename(name, existing)
    assert result2 == "Artist - Title (2020) [1080p] (3).mp4"
