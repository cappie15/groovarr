"""MP4 (iTunes-atom) tag writing via `mutagen` — the project's chosen
tagging library (§4/§10). Source of truth for every field is Spotify data
already in the DB (`Track`), never `VideoCandidate`'s YouTube title/channel
text, which is noisy presentation text (§42).

Tagging is explicitly best-effort (§48's "errors must not fail the
pipeline" principle, applied here too): a broken tag write must never cost
the user an otherwise-successfully-acquired video. `write_mp4_tags` raises
`TaggingError` on any failure; `apply_tags_best_effort` is what callers
should actually use — it catches everything and reports a `TaggingResult`
instead, logging clearly rather than silently doing nothing.
"""

from dataclasses import dataclass
from pathlib import Path

import structlog
from mutagen.mp4 import MP4, MP4Cover

logger = structlog.get_logger(__name__)

# MP4 `rtng` (content advisory) atom values as actually produced/consumed in
# the wild (§3 research — tool disagreement means 1 *and* 4 both appear for
# "explicit"; this project writes the more common `1`).
_RTNG_EXPLICIT = 1
_RTNG_CLEAN = 2

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TaggingError(Exception):
    """Raised by `write_mp4_tags` on any failure — missing/unreadable file,
    a mutagen error, an unrecognized artwork format, etc. Callers that must
    not let a tagging failure abort acquisition should use
    `apply_tags_best_effort` instead of calling this directly.
    """


@dataclass(frozen=True)
class TagFields:
    title: str
    artist: str  # Already includes featured artists per `format_artist_tag`.
    year: int | None
    artwork_path: Path | None
    explicit: bool = False
    # Optional plain-text lyrics — Phase 7 (LRCLIB) is what actually
    # populates this; the plumbing exists now so Phase 7 needs no changes
    # here. `.lrc` sidecar remains the *primary* interoperable format
    # regardless (§2-C/§3 — Plex ignores embedded lyrics entirely, VLC has
    # no LRC engine, and Jellyfin's lyrics feature is Audio-only); this is
    # strictly a harmless best-effort bonus tag.
    lyrics: str | None = None


@dataclass(frozen=True)
class TaggingResult:
    success: bool
    error: str | None = None


def format_artist_tag(canonical_artist: str, featured_artists: list[str]) -> str:
    """§7/§42: artist tag includes featured artists rather than dropping
    them, joined in the conventional "A feat. B & C" style. A single
    combined string is used because MP4's `©ART` atom is a single text
    field, not a structured list — there is no per-artist atom to split
    across.
    """
    if not featured_artists:
        return canonical_artist
    if len(featured_artists) == 1:
        return f"{canonical_artist} feat. {featured_artists[0]}"
    return f"{canonical_artist} feat. {', '.join(featured_artists[:-1])} & {featured_artists[-1]}"


def _sniff_cover_format(data: bytes) -> int:
    if data.startswith(_JPEG_MAGIC):
        return MP4Cover.FORMAT_JPEG
    if data.startswith(_PNG_MAGIC):
        return MP4Cover.FORMAT_PNG
    raise TaggingError("Artwork file is neither a recognizable JPEG nor PNG")


def write_mp4_tags(path: Path, fields: TagFields) -> None:
    """Write `fields` into `path`'s MP4 atoms. Raises `TaggingError` on any
    failure. Never re-encodes/touches the video or audio streams — mutagen
    only rewrites the `moov/udta/meta/ilst` atom, per §3 research.
    """
    if not path.is_file():
        raise TaggingError(f"Cannot tag: {path} does not exist")

    try:
        mp4 = MP4(str(path))
    except Exception as exc:  # mutagen raises its own exception hierarchy
        raise TaggingError(f"mutagen could not open {path}: {exc}") from exc

    mp4["\xa9nam"] = [fields.title]
    mp4["\xa9ART"] = [fields.artist]
    if fields.year is not None:
        mp4["\xa9day"] = [str(fields.year)]
    if fields.lyrics:
        mp4["\xa9lyr"] = [fields.lyrics]
    # Best-effort per the architecture doc's own flagged uncertainty about
    # mutagen's `rtng` support — confirmed working via a direct round-trip
    # test against mutagen>=1.47, so it's written unconditionally rather
    # than skipped; if a future mutagen version ever rejects it, the
    # surrounding `apply_tags_best_effort` catch still keeps this
    # non-fatal.
    mp4["rtng"] = [_RTNG_EXPLICIT if fields.explicit else _RTNG_CLEAN]

    if fields.artwork_path is not None:
        if not fields.artwork_path.is_file():
            logger.warning("tagging.artwork_missing", path=str(fields.artwork_path))
        else:
            data = fields.artwork_path.read_bytes()
            cover_format = _sniff_cover_format(data)
            mp4["covr"] = [MP4Cover(data, imageformat=cover_format)]

    try:
        mp4.save()
    except Exception as exc:
        raise TaggingError(f"mutagen could not save tags to {path}: {exc}") from exc


def apply_tags_best_effort(path: Path, fields: TagFields) -> TaggingResult:
    """The function the acquisition pipeline actually calls: tagging must
    never fail an otherwise-successful import (§48 applied to tagging).
    Catches every exception, logs clearly, and reports a `TaggingResult`
    instead of raising.
    """
    try:
        write_mp4_tags(path, fields)
    except Exception as exc:  # deliberately broad — see module docstring
        logger.warning("tagging.failed", path=str(path), error=str(exc))
        return TaggingResult(success=False, error=str(exc))
    return TaggingResult(success=True)
