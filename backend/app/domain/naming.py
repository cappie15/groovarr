"""Filename naming template engine (§16-17).

The default output format is exactly:

    Artist - Song Title (ReleaseYear) [Quality].ext

with exactly one semantic hyphen (surrounded by spaces) between artist and
title, the release year in parentheses, and the quality label in square
brackets. The project owner corrected this format explicitly multiple times
during specification — in particular, `[Quality]` must never be silently
dropped, including when the quality is unknown (an explicit `[Unknown]`
placeholder is used instead of omitting the bracket group entirely).

Scoping note: §16 asks for "configurable" naming in the spirit of Sonarr/
Radarr naming templates. This phase implements the engine and its one
required default as a small set of composable parameters (separator,
whether year/quality are shown, sanitization rules) rather than a full
user-facing mini templating language — that refinement (e.g. a raw
`{artist} - {title}` string editable from Settings) is a natural follow-up
once the Settings UI exists (§84), and is a deliberate simplification here,
not an oversight.
"""

import re
from dataclasses import dataclass

# --- Sanitization ------------------------------------------------------------

# Characters illegal (or awkward) on common filesystems, plus control chars.
# Deliberately narrow: this strips structural/illegal characters only and
# never transliterates or drops meaningful Unicode (§16 — "do not silently
# destroy meaningful Unicode characters unless required by the filesystem").
_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows reserved device names — sanitized defensively even though Groovarr
# runs on Linux (§69: sanitize defensively, don't assume the runtime
# filesystem is the only one that will ever see these files, e.g. a Samba/
# SMB-exported media root mounted from a Windows client).
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# A conservative cap on one filename *component* (artist or title), leaving
# headroom for the rest of the template + extension under common filesystem
# limits (e.g. 255 bytes on ext4/NTFS for the whole filename). Applied to
# Unicode codepoints, not bytes — a hard byte cap would require the caller's
# filesystem encoding, which isn't knowable here.
_MAX_COMPONENT_LENGTH = 120

UNKNOWN_QUALITY_PLACEHOLDER = "Unknown"


def sanitize_filename_component(text: str, *, max_length: int = _MAX_COMPONENT_LENGTH) -> str:
    """Sanitize one piece of a filename (an artist or title string) for safe
    use on disk: strips illegal/control characters, path-traversal-relevant
    separators (`/`, `\\`), collapses the resulting whitespace, trims
    trailing dots/spaces (both illegal as a trailing character on Windows,
    sanitized defensively per §69), disambiguates an exact reserved device
    name, and caps length — all without touching any other Unicode
    character, so accented/non-Latin names are preserved as-is.
    """
    cleaned = _ILLEGAL_CHARS_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")

    if not cleaned:
        cleaned = "Unknown"

    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"{cleaned}_"

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(". ")

    return cleaned


# --- Default naming template -------------------------------------------------


@dataclass(frozen=True)
class NamingFields:
    """Everything the default template needs. `quality` is a pre-computed
    human-readable label (e.g. "1080p") derived elsewhere (from an actual
    ffprobe'd stream once Phase 5/6 exist) — this module only formats it."""

    artist: str
    title: str
    year: int | None
    quality: str | None
    ext: str


def render_filename(fields: NamingFields) -> str:
    """Render the default `Artist - Song Title (ReleaseYear) [Quality].ext`
    filename. Individual components are sanitized before assembly; the
    assembled result is filesystem-safe on its own (no residual path
    separators, since sanitize_filename_component already strips `/`/`\\`).
    """
    artist = sanitize_filename_component(fields.artist)
    title = sanitize_filename_component(fields.title)
    quality = sanitize_filename_component(fields.quality) if fields.quality else UNKNOWN_QUALITY_PLACEHOLDER
    ext = fields.ext.lstrip(".").lower() or "mp4"

    base = f"{artist} - {title}"
    if fields.year is not None:
        base += f" ({fields.year})"
    base += f" [{quality}]"

    return f"{base}.{ext}"


def disambiguate_filename(candidate: str, existing_filenames: set[str]) -> str:
    """If `candidate` collides with something in `existing_filenames` (two
    tracks that render to an identical name — e.g. two different remixes
    that normalize the same way), append " (2)", " (3)", ... before the
    extension until unique. Returns `candidate` unchanged if there's no
    collision.
    """
    if candidate not in existing_filenames:
        return candidate

    if "." in candidate:
        stem, _, ext = candidate.rpartition(".")
    else:
        stem, ext = candidate, ""

    suffix = 2
    while True:
        attempt = f"{stem} ({suffix})" + (f".{ext}" if ext else "")
        if attempt not in existing_filenames:
            return attempt
        suffix += 1
