"""Pure parsing/normalization helpers for Spotify data — deliberately free of
any network/DB access so they're trivially unit-testable (§92).

Spotify's Track object has no distinct remix/version field (confirmed by
research, §3) — any such information is embedded in the free-text `name`
string and must be parsed out here. Likewise, featured artists are not a
separate flag; Spotify lists every contributing artist in the `artists`
array, conventionally with the primary artist first.
"""

import re

# --- Playlist ID parsing --------------------------------------------------

# Spotify playlist IDs are 22-character base62 strings. Accept a bare ID, an
# open.spotify.com URL (with or without a query string), or a spotify: URI.
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_PLAYLIST_URL_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]{22})")
_PLAYLIST_URI_RE = re.compile(r"spotify:playlist:([A-Za-z0-9]{22})")


def parse_playlist_id(url_or_id: str) -> str:
    """Extract a bare Spotify playlist ID from a URL, URI, or already-bare ID.

    Raises `ValueError` with a message safe to surface directly to a user if
    nothing recognizable is found.
    """
    candidate = url_or_id.strip()

    if match := _PLAYLIST_URL_RE.search(candidate):
        return match.group(1)
    if match := _PLAYLIST_URI_RE.search(candidate):
        return match.group(1)
    if _PLAYLIST_ID_RE.match(candidate):
        return candidate

    raise ValueError(
        "Could not recognize a Spotify playlist URL, URI, or ID in "
        f"{url_or_id!r}. Expected e.g. "
        "https://open.spotify.com/playlist/<id>, spotify:playlist:<id>, or a bare 22-character ID."
    )


# --- Version / remix parsing ----------------------------------------------

# Keywords that mark a segment of the title as version/remix information
# rather than just the song title. Checked case-insensitively.
_VERSION_KEYWORDS = re.compile(
    r"\b("
    r"remix|rmx|re-?mix|"
    r"extended\s+(mix|edit|version)?|"
    r"radio\s+(edit|mix)|"
    r"club\s+mix|"
    r"vip\s+mix|"
    r"dub\s+mix|"
    r"original\s+mix|"
    r"instrumental|"
    r"acoustic(\s+version)?|"
    r"live(\s+(version|at\s+.+))?|"
    r"session\s+version|"
    r"rework|"
    r"bootleg|"
    r"edit|"
    r"anniversary\s+edition|"
    r"remaster(ed)?(\s+\d{4})?|"
    r"deluxe(\s+edition)?"
    r")\b",
    re.IGNORECASE,
)

# Segments in parentheses or square brackets, e.g. "(Radio Edit)", "[Extended Mix]".
_BRACKETED_RE = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")

# A trailing " - <version text>" suffix, e.g. "Song Name - Artist Remix".
_DASH_SUFFIX_RE = re.compile(r"\s-\s([^-]+)$")


def parse_version(track_name: str) -> str | None:
    """Extract remix/edit/version text from a Spotify track's free-text name.

    Checks bracketed segments first (the common case), then a trailing
    " - ..." suffix. Returns the matched segment's own text (trimmed), or
    None if nothing resembling a version marker is found — an unversioned
    track's `name` is just its title and should not produce a false match.
    """
    for match in _BRACKETED_RE.finditer(track_name):
        segment = match.group(1).strip()
        if _VERSION_KEYWORDS.search(segment):
            return segment

    if match := _DASH_SUFFIX_RE.search(track_name):
        segment = match.group(1).strip()
        if _VERSION_KEYWORDS.search(segment):
            return segment

    return None


def strip_version_and_feature_text(track_name: str) -> str:
    """Return a clean title with bracketed version markers and inline
    "feat./ft./featuring ..." clauses removed — that information lives
    structurally in `Track.parsed_version` / `Track.featured_artists`
    instead of being folded into the display title (§7).
    """
    cleaned = track_name

    # Remove only bracketed segments that are version markers or feature
    # clauses — an unrelated bracketed segment (rare, but possible) is left
    # alone rather than stripped blindly.
    def _replace_bracket(match: re.Match[str]) -> str:
        segment = match.group(1)
        if _VERSION_KEYWORDS.search(segment) or _FEATURE_CLAUSE_RE.search(segment):
            return ""
        return match.group(0)

    cleaned = _BRACKETED_RE.sub(_replace_bracket, cleaned)

    # Strip an inline (non-bracketed) "feat./ft./featuring ..." clause that
    # runs to the end of the string.
    cleaned = _FEATURE_CLAUSE_INLINE_RE.sub("", cleaned)

    # Collapse whitespace left behind by removed segments.
    return re.sub(r"\s{2,}", " ", cleaned).strip(" -")


# --- Featured-artist parsing -----------------------------------------------

_FEATURE_CLAUSE_RE = re.compile(r"\b(feat\.?|ft\.?|featuring)\b", re.IGNORECASE)
_FEATURE_CLAUSE_INLINE_RE = re.compile(
    r"\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\b[^)\]]*[\)\]]?\s*$", re.IGNORECASE
)


def split_artists(artists: list[str]) -> tuple[str, list[str]]:
    """Split a Spotify track's `artists` list (name strings, in API order)
    into (primary_artist, featured_artists). Spotify lists every
    contributing artist with the primary artist first by convention — this
    is the authoritative, structured source for featured artists (§7/§8),
    preferred over parsing "feat. ..." text out of the free-text name.
    """
    if not artists:
        raise ValueError("A track must have at least one artist")
    return artists[0], list(artists[1:])


# --- Release year -----------------------------------------------------------


def parse_release_year(release_date: str | None) -> int | None:
    """Derive a release year from Spotify's `album.release_date`, which may
    be precision-qualified as "YYYY", "YYYY-MM", or "YYYY-MM-DD" (§3/§7) —
    there is no separate year field to read instead.
    """
    if not release_date:
        return None
    year_part = release_date[:4]
    if year_part.isdigit():
        return int(year_part)
    return None
