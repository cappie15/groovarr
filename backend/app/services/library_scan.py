"""Existing-library import (§15): scan the configured media directory for
files that may already satisfy a known `Track`, without ever automatically
renaming/moving/deleting/overwriting an unknown file.

Matching uses two deterministic signals — normalized artist/title text and
duration proximity (±15% tolerance, mirroring the acquisition-time tolerance
in §29 even though this isn't the full YouTube-candidate scoring engine from
§21/§8, which is Phase 4's job). A file that scores confidently against
exactly one Track is reused as-is (state=AVAILABLE, no reorganization); an
uncertain file is recorded for Manual Review with a human-readable reason;
a file with no plausible signal at all is left alone entirely (it may simply
not correspond to anything in a connected playlist).
"""

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import Track
from app.domain.reference_counting import sync_references_for_track
from app.domain.text_normalize import normalize_for_comparison

logger = structlog.get_logger(__name__)

# Video containers Groovarr is expected to encounter in an existing library.
# Groovarr's own downloads always default to MP4 (§2-D, project owner's
# decision), but a pre-existing library commonly predates that policy.
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".webm", ".mov", ".avi"}

# Scoring weights — simple, deterministic, and explained via `reasons`
# (§21's spirit: never an unexplained number), but intentionally not the
# full multi-signal scoring engine §21 describes for YouTube candidates.
_ARTIST_EXACT_SCORE = 45
_ARTIST_PARTIAL_SCORE = 20
_TITLE_EXACT_SCORE = 40
_TITLE_PARTIAL_SCORE = 20
_DURATION_MATCH_SCORE = 15
_DURATION_TOLERANCE = 0.15  # ±15%, same tolerance concept as §29

HIGH_CONFIDENCE_SCORE = 85
MANUAL_REVIEW_MIN_SCORE = 30


@dataclass
class LibraryScanResult:
    files_scanned: int = 0
    matched_available: int = 0
    manual_review_created: int = 0
    skipped_no_signal: int = 0
    skipped_already_known: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class _FileProbe:
    path: Path
    duration_s: float | None
    artist: str | None
    title: str | None


def _iter_media_files(media_dir: Path):
    for path in sorted(media_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def _parse_filename_stem(stem: str) -> tuple[str | None, str | None]:
    """Best-effort "Artist - Title" split of a filename stem (ignoring an
    optional trailing "(Year)"/"[Quality]" the file may already carry, e.g.
    from a previous Groovarr-organized name). Returns (None, None) if the
    stem doesn't contain a recognizable separator.
    """
    # Strip a trailing "(YYYY)"/"[...]" group or two before splitting, so an
    # already-organized "Artist - Title (2024) [1080p]" still splits cleanly.
    trimmed = stem
    for _ in range(2):
        stripped = trimmed.rstrip()
        if stripped.endswith(")") and "(" in stripped:
            trimmed = stripped[: stripped.rindex("(")].rstrip()
        elif stripped.endswith("]") and "[" in stripped:
            trimmed = stripped[: stripped.rindex("[")].rstrip()
        else:
            break

    if " - " not in trimmed:
        return None, None
    artist, _, title = trimmed.partition(" - ")
    artist, title = artist.strip(), title.strip()
    return (artist or None, title or None)


def _probe_file(path: Path) -> _FileProbe:
    duration_s: float | None = None
    tag_artist: str | None = None
    tag_title: str | None = None

    with contextlib.suppress(Exception):
        # mutagen.File auto-detects container/tag format; some containers
        # (e.g. certain .avi) it simply won't recognize, and that's fine —
        # we fall back to filename heuristics below.
        audio = mutagen.File(path, easy=True)
        if audio is not None:
            if audio.info is not None and getattr(audio.info, "length", None):
                duration_s = float(audio.info.length)
            if audio.tags:
                tag_artist = _first_tag(audio.tags, "artist")
                tag_title = _first_tag(audio.tags, "title")

    filename_artist, filename_title = _parse_filename_stem(path.stem)

    return _FileProbe(
        path=path,
        duration_s=duration_s,
        artist=tag_artist or filename_artist,
        title=tag_title or filename_title,
    )


def _first_tag(tags, key: str) -> str | None:
    value = tags.get(key)
    if not value:
        return None
    # easy-mode mutagen tags are lists of strings.
    return str(value[0]) if value[0] else None


def _score_candidate(probe: _FileProbe, track: Track) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0

    norm_track_artist = normalize_for_comparison(track.canonical_artist)
    norm_track_title = normalize_for_comparison(track.canonical_title)
    norm_probe_artist = normalize_for_comparison(probe.artist) if probe.artist else ""
    norm_probe_title = normalize_for_comparison(probe.title) if probe.title else ""

    if norm_probe_artist and norm_probe_artist == norm_track_artist:
        score += _ARTIST_EXACT_SCORE
        reasons.append("exact artist match")
    elif norm_probe_artist and (norm_probe_artist in norm_track_artist or norm_track_artist in norm_probe_artist):
        score += _ARTIST_PARTIAL_SCORE
        reasons.append("partial artist match")

    if norm_probe_title and norm_probe_title == norm_track_title:
        score += _TITLE_EXACT_SCORE
        reasons.append("exact title match")
    elif norm_probe_title and (norm_probe_title in norm_track_title or norm_track_title in norm_probe_title):
        score += _TITLE_PARTIAL_SCORE
        reasons.append("partial title match")

    if probe.duration_s is not None and track.duration_ms:
        track_duration_s = track.duration_ms / 1000
        diff_ratio = abs(probe.duration_s - track_duration_s) / track_duration_s
        if diff_ratio <= _DURATION_TOLERANCE:
            score += _DURATION_MATCH_SCORE
            reasons.append(f"duration within tolerance ({diff_ratio:.0%} difference)")
        else:
            reasons.append(f"duration differs by {diff_ratio:.0%} (red flag, not a hard exclusion)")

    return score, reasons


def _best_match(probe: _FileProbe, tracks: list[Track]) -> tuple[Track | None, int, list[str]]:
    best_track: Track | None = None
    best_score = -1
    best_reasons: list[str] = []
    for track in tracks:
        score, reasons = _score_candidate(probe, track)
        if score > best_score:
            best_track, best_score, best_reasons = track, score, reasons
    return best_track, max(best_score, 0), best_reasons


async def scan_library(session: AsyncSession) -> LibraryScanResult:
    """Scan MEDIA_DIR once. Idempotent: a file whose path is already recorded
    on some MediaAsset is skipped (re-scanning doesn't create duplicates or
    re-evaluate a file a human may have already resolved via Manual Review).
    """
    result = LibraryScanResult()
    media_dir = get_settings().media_dir

    if not media_dir.exists():
        logger.warning("library_scan.media_dir_missing", media_dir=str(media_dir))
        return result

    known_paths = set(
        (await session.scalars(select(MediaAsset.local_path).where(MediaAsset.local_path.is_not(None)))).all()
    )
    tracks = list((await session.scalars(select(Track))).all())

    for path in _iter_media_files(media_dir):
        path_str = str(path)
        if path_str in known_paths:
            result.skipped_already_known += 1
            continue

        result.files_scanned += 1
        try:
            probe = _probe_file(path)
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the whole scan
            logger.warning("library_scan.probe_failed", path=path_str, error=str(exc))
            result.errors.append(f"{path_str}: {exc}")
            continue

        best_track, score, reasons = _best_match(probe, tracks)

        if best_track is None or score < MANUAL_REVIEW_MIN_SCORE:
            result.skipped_no_signal += 1
            continue

        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = None

        if score >= HIGH_CONFIDENCE_SCORE:
            asset = MediaAsset(
                track_id=best_track.id,
                local_path=path_str,
                container=path.suffix.lstrip(".").lower(),
                duration_s=probe.duration_s,
                file_size=file_size,
                state=MediaState.AVAILABLE,
                source_reference="existing-library-scan",
            )
            session.add(asset)
            await session.flush()
            await sync_references_for_track(session, best_track.id, asset.id)
            result.matched_available += 1
            logger.info("library_scan.matched", path=path_str, track_id=best_track.id, score=score)
        else:
            asset = MediaAsset(
                candidate_track_id=best_track.id,
                local_path=path_str,
                container=path.suffix.lstrip(".").lower(),
                duration_s=probe.duration_s,
                file_size=file_size,
                state=MediaState.MANUAL_REVIEW_REQUIRED,
                review_reason=f"Uncertain match (score {score}/100): {'; '.join(reasons) or 'no strong signals'}",
                source_reference="existing-library-scan",
            )
            session.add(asset)
            result.manual_review_created += 1
            logger.info("library_scan.manual_review", path=path_str, candidate_track_id=best_track.id, score=score)

    await session.commit()
    return result
