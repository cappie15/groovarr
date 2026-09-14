"""Deterministic, explainable candidate scoring (§8/§19-25/§29/§31 of the
architecture doc). Not ML-based: every point added or subtracted is a named,
regex/comparison-based signal recorded in `ScoreResult.breakdown`, so a
future UI can render exactly why a candidate scored the way it did — the
same "+Exact artist / -Duration mismatch" style called for in §21.

Two independent stages live here:

- `hard_filter`: Shorts/portrait exclusion (§31) — applied BEFORE scoring,
  never scored, never persisted as a low-score candidate.
- `score_candidate`: the weighted signal sum, tuned (see the module-level
  comment above `AUTOMATIC_MATCH_THRESHOLD_DEFAULT`) against
  tests/unit/test_scoring.py's fixture corpus rather than by feel, and
  explicitly biased toward precision over recall (§86/§104): a wrong
  automatic pick is worse than one sitting in Manual Review.
"""

import re
from dataclasses import dataclass, field

from app.db.models.spotify import Track
from app.domain.text_normalize import normalize_for_comparison
from app.integrations.youtube.data_api import RawCandidate
from app.integrations.youtube.ytdlp_client import EnrichedInfo

# --- Hard filters (§31) -------------------------------------------------------


def hard_filter(enriched: EnrichedInfo) -> str | None:
    """Returns a short reason string if `enriched` must be hard-excluded
    (never scored, never persisted), or None if it passes through to
    scoring. Shorts and portrait-oriented videos are the only hard
    exclusions — everything else (live, cover, visualizer, ...) is a scored
    signal, not a filter, per §22-25/§26/§29.
    """
    if enriched.is_short:
        return "excluded: YouTube Short"
    if enriched.is_portrait:
        return "excluded: portrait orientation"
    return None


# --- Scoring weights -----------------------------------------------------------
#
# Tuned against tests/unit/test_scoring.py's fixture corpus (§85-equivalent):
# same song by multiple artists, requested remix vs. plain official video,
# covers, remasters, clean/explicit pairs, live, lyric video, visualizer, fan
# video, official-without-the-word-"official", VEVO, featured artists,
# punctuation/Unicode/alias differences, misleading titles. The threshold
# (AppSettings.automatic_match_threshold, default below) is deliberately
# conservative: precision over recall (§86/§104).

VERSION_EXACT_PHRASE_BONUS = 90
VERSION_CATEGORY_MATCH_BONUS = 60
VERSION_WRONG_VARIANT_PENALTY = -25  # requested *a* remix, candidate is a *different* one
VERSION_UNWANTED_PENALTY = -40  # no specific version requested, candidate is clearly a remix/edit
PLAIN_ORIGINAL_WHEN_VERSION_REQUESTED_PENALTY = -50  # a version was requested, candidate looks like the plain original

ARTIST_EXACT_BONUS = 25
ARTIST_PARTIAL_BONUS = 10
TITLE_CONTAINS_BONUS = 25
TITLE_FUZZY_BONUS = 10

OFFICIAL_VIDEO_PHRASE_BONUS = 25
OFFICIAL_ARTIST_CHANNEL_BONUS = 20
VEVO_OR_TOPIC_CHANNEL_BONUS = 18
GENERIC_OFFICIAL_WORD_BONUS = 8

EXPLICIT_BUT_CLEAN_PENALTY = -20

DURATION_CLOSE_BONUS = 15  # within 5%
DURATION_OK_BONUS = 8  # within 15% (the §29 tolerance) but beyond 5%
DURATION_FAR_PENALTY = -10  # beyond 15% — a red flag, never a hard exclusion (§29)
DURATION_RED_FLAG_RATIO = 0.15

RELEASE_YEAR_MATCH_BONUS = 5

# Undesired-version-marker penalties (§22-25's fallback hierarchy, coarsely
# encoded as penalty magnitude — more negative ranks lower):
FAN_MADE_PENALTY = -15
LYRIC_VIDEO_PENALTY = -25
COVER_KARAOKE_TRIBUTE_PENALTY = -30
LIVE_PENALTY = -35
INSTRUMENTAL_ACOUSTIC_PENALTY = -20
VISUALIZER_PENALTY = -45

AUTOMATIC_MATCH_THRESHOLD_DEFAULT = 70

# --- Regexes -------------------------------------------------------------------

_OFFICIAL_VIDEO_PHRASE_RE = re.compile(r"official\s+(music\s+)?video|official\s+audio", re.IGNORECASE)
_GENERIC_OFFICIAL_WORD_RE = re.compile(r"\bofficial\b", re.IGNORECASE)
_VEVO_RE = re.compile(r"vevo", re.IGNORECASE)
_TOPIC_CHANNEL_RE = re.compile(r"-\s*topic\s*$", re.IGNORECASE)
_FAN_UNOFFICIAL_TRIBUTE_RE = re.compile(r"\b(fan\s*made|fan\s*video|unofficial)\b", re.IGNORECASE)
_TRIBUTE_RE = re.compile(r"\btribute\b", re.IGNORECASE)
_LYRIC_VIDEO_RE = re.compile(r"\blyrics?\s+video\b", re.IGNORECASE)
_LIVE_RE = re.compile(r"\b(live|concert|unplugged|tour)\b", re.IGNORECASE)
_COVER_KARAOKE_RE = re.compile(r"\b(cover|karaoke)\b", re.IGNORECASE)
_INSTRUMENTAL_ACOUSTIC_RE = re.compile(r"\b(instrumental|acoustic)\b", re.IGNORECASE)
_VISUALIZER_RE = re.compile(r"\bvisuali[sz]er\b", re.IGNORECASE)
_CLEAN_MARKER_RE = re.compile(r"\b(clean|radio\s+edit|edited\s+version)\b", re.IGNORECASE)

# The same category keywords Track.parsed_version can carry (mirrors
# app/integrations/spotify/utils.py's vocabulary, kept independent — see
# module docstring rationale in app/matching/scoring.py's design notes).
_VERSION_CATEGORY_RE = re.compile(
    r"\b(remix|rmx|re-?mix|extended(\s+(mix|edit|version))?|radio\s+(edit|mix)|club\s+mix|"
    r"vip\s+mix|dub\s+mix|instrumental|acoustic|live|rework|bootleg|edit|"
    r"remaster(ed)?|deluxe)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScoreSignal:
    signal: str
    delta: int
    explanation: str


@dataclass
class ScoreResult:
    total: int
    breakdown: list[ScoreSignal] = field(default_factory=list)
    rejection_flags: list[str] = field(default_factory=list)
    official_signals: dict[str, bool] = field(default_factory=dict)
    is_visualizer_only: bool = False


def score_candidate(track: Track, candidate: RawCandidate, enriched: EnrichedInfo) -> ScoreResult:
    """Score one candidate against `track`. Assumes `hard_filter` has already
    passed (never call this on a Short/portrait candidate).
    """
    signals: list[ScoreSignal] = []
    flags: list[str] = []
    official: dict[str, bool] = {}

    norm_title = normalize_for_comparison(candidate.title)
    norm_channel = normalize_for_comparison(candidate.channel_name or "")
    norm_track_artist = normalize_for_comparison(track.canonical_artist)
    norm_track_title = normalize_for_comparison(track.canonical_title)

    # --- Version/remix correctness (§22 — dominates generic "official"). ---
    if track.parsed_version:
        norm_requested = normalize_for_comparison(track.parsed_version)
        if norm_requested and norm_requested in norm_title:
            signals.append(
                ScoreSignal(
                    "requested_version_exact",
                    VERSION_EXACT_PHRASE_BONUS,
                    f"Matches requested version {track.parsed_version!r}",
                )
            )
        else:
            requested_category = _VERSION_CATEGORY_RE.search(track.parsed_version)
            candidate_category = _VERSION_CATEGORY_RE.search(candidate.title)
            same_category = (
                requested_category
                and candidate_category
                and requested_category.group(1).lower() == candidate_category.group(1).lower()
            )
            if same_category:
                # Same category keyword (e.g. both "Remix") — but the
                # requested version may additionally name a *specific*
                # variant (e.g. "John Doe Remix"). If it does, and that
                # qualifier doesn't appear in this candidate's title, this
                # is the WRONG remix, not a generic category match — the
                # bare category word alone is not enough to call it correct.
                qualifier = norm_requested.replace(requested_category.group(1).lower(), "").strip()
                if qualifier and qualifier not in norm_title:
                    signals.append(
                        ScoreSignal(
                            "wrong_version_variant",
                            VERSION_WRONG_VARIANT_PENALTY,
                            f"Requested the {qualifier!r} {requested_category.group(1)!r} but this candidate "
                            "is a different one",
                        )
                    )
                else:
                    signals.append(
                        ScoreSignal(
                            "requested_version_category",
                            VERSION_CATEGORY_MATCH_BONUS,
                            f"Candidate is also a {requested_category.group(1)!r}-type version",
                        )
                    )
            elif requested_category and candidate_category:
                signals.append(
                    ScoreSignal(
                        "wrong_version_variant",
                        VERSION_WRONG_VARIANT_PENALTY,
                        f"Requested {requested_category.group(1)!r} but candidate is a different variant "
                        f"({candidate_category.group(1)!r})",
                    )
                )
            elif not candidate_category:
                signals.append(
                    ScoreSignal(
                        "plain_original_but_version_requested",
                        PLAIN_ORIGINAL_WHEN_VERSION_REQUESTED_PENALTY,
                        f"A specific version ({track.parsed_version!r}) was requested but this candidate "
                        "shows no matching version marker",
                    )
                )
    else:
        # No specific version requested — the plain/original track is
        # wanted. A candidate that is clearly a remix/edit is the wrong
        # version, symmetric to §22's "never upgrade a requested remix into
        # the original".
        if _VERSION_CATEGORY_RE.search(candidate.title):
            signals.append(
                ScoreSignal(
                    "unwanted_version_variant",
                    VERSION_UNWANTED_PENALTY,
                    "The original/plain track was requested but this candidate appears to be a remix/edit",
                )
            )

    requested_marker_categories = (
        {m.group(1).lower() for m in _VERSION_CATEGORY_RE.finditer(track.parsed_version)}
        if track.parsed_version
        else set()
    )

    # --- Artist / title match (incl. featured artists, §8). ---
    if norm_track_artist and (norm_track_artist in norm_title or norm_track_artist in norm_channel):
        signals.append(ScoreSignal("artist_match", ARTIST_EXACT_BONUS, "Exact artist match"))
    elif norm_track_artist and any(tok in norm_title for tok in norm_track_artist.split() if len(tok) > 2):
        signals.append(ScoreSignal("artist_partial_match", ARTIST_PARTIAL_BONUS, "Partial artist match"))

    for featured in track.featured_artists:
        norm_featured = normalize_for_comparison(featured)
        if norm_featured and (norm_featured in norm_title or norm_featured in norm_channel):
            signals.append(
                ScoreSignal("featured_artist_match", ARTIST_PARTIAL_BONUS, f"Featured artist {featured!r} found")
            )

    if norm_track_title and norm_track_title in norm_title:
        signals.append(ScoreSignal("title_match", TITLE_CONTAINS_BONUS, "Normalized title found in candidate title"))
    elif norm_track_title:
        track_tokens = {t for t in norm_track_title.split() if len(t) > 2}
        title_tokens = set(norm_title.split())
        if track_tokens and len(track_tokens & title_tokens) / len(track_tokens) >= 0.6:
            signals.append(ScoreSignal("title_fuzzy_match", TITLE_FUZZY_BONUS, "Most title words present"))

    # --- Official indicators (tiered, additive — §24/§25). ---
    if _OFFICIAL_VIDEO_PHRASE_RE.search(candidate.title):
        signals.append(
            ScoreSignal(
                "official_video_phrase",
                OFFICIAL_VIDEO_PHRASE_BONUS,
                "Title contains an 'Official Video/Audio'-style phrase",
            )
        )
        official["official_video_phrase"] = True
    elif _GENERIC_OFFICIAL_WORD_RE.search(candidate.title):
        signals.append(
            ScoreSignal("generic_official_word", GENERIC_OFFICIAL_WORD_BONUS, "Title contains the word 'official'")
        )
        official["generic_official_word"] = True

    is_fan_or_tribute = bool(_FAN_UNOFFICIAL_TRIBUTE_RE.search(candidate.title) or _TRIBUTE_RE.search(candidate.title))
    if (
        norm_track_artist
        and norm_track_artist in norm_channel
        and not is_fan_or_tribute
        and not _FAN_UNOFFICIAL_TRIBUTE_RE.search(candidate.channel_name or "")
    ):
        signals.append(
            ScoreSignal(
                "official_artist_channel",
                OFFICIAL_ARTIST_CHANNEL_BONUS,
                "Uploaded by a channel matching the artist's name",
            )
        )
        official["official_artist_channel"] = True

    if _VEVO_RE.search(candidate.channel_name or "") or _TOPIC_CHANNEL_RE.search(candidate.channel_name or ""):
        signals.append(
            ScoreSignal(
                "vevo_or_topic_channel", VEVO_OR_TOPIC_CHANNEL_BONUS, "VEVO or auto-generated '- Topic' channel"
            )
        )
        official["vevo_or_topic_channel"] = True

    # --- Explicit/clean compatibility (§23). ---
    if track.explicit and _CLEAN_MARKER_RE.search(candidate.title) and "radio" not in requested_marker_categories:
        signals.append(
            ScoreSignal(
                "clean_but_explicit_requested",
                EXPLICIT_BUT_CLEAN_PENALTY,
                "Track is explicit but candidate looks like a clean/radio edit",
            )
        )

    # --- Duration (§29 — soft signal, red flag past 15%, never a hard exclusion). ---
    if enriched.duration_s is not None and track.duration_ms:
        track_duration_s = track.duration_ms / 1000
        diff_ratio = abs(enriched.duration_s - track_duration_s) / track_duration_s
        if diff_ratio <= 0.05:
            signals.append(ScoreSignal("duration_close", DURATION_CLOSE_BONUS, f"Duration within {diff_ratio:.0%}"))
        elif diff_ratio <= DURATION_RED_FLAG_RATIO:
            signals.append(
                ScoreSignal("duration_ok", DURATION_OK_BONUS, f"Duration within tolerance ({diff_ratio:.0%})")
            )
        else:
            signals.append(
                ScoreSignal("duration_mismatch", DURATION_FAR_PENALTY, f"Duration differs by {diff_ratio:.0%}")
            )
            flags.append(f"Duration red flag: differs by {diff_ratio:.0%} (not a hard exclusion, §29)")

    # --- Release year (small positive, neutral if unavailable). ---
    # Candidate publish-date-derived year corroboration is intentionally not
    # implemented in this phase: a YouTube upload date is frequently years
    # after a track's actual release and would be a misleading signal far
    # more often than a useful one (§3 research note on this exact risk) —
    # deferred rather than guessed at.

    # --- Undesired-version markers (only penalized if NOT what was requested). ---
    is_visualizer = bool(_VISUALIZER_RE.search(candidate.title))
    if is_visualizer:
        signals.append(
            ScoreSignal("visualizer", VISUALIZER_PENALTY, "Title indicates a visualizer, not a real music video")
        )

    if is_fan_or_tribute:
        signals.append(ScoreSignal("fan_made", FAN_MADE_PENALTY, "Title indicates a fan-made/unofficial upload"))
    elif _TRIBUTE_RE.search(candidate.title) and "official_artist_channel" not in official:
        signals.append(ScoreSignal("tribute", FAN_MADE_PENALTY, "Title indicates a tribute upload"))

    if _LYRIC_VIDEO_RE.search(candidate.title):
        signals.append(
            ScoreSignal("lyric_video", LYRIC_VIDEO_PENALTY, "Title indicates a lyric video, not a full music video")
        )

    if _COVER_KARAOKE_RE.search(candidate.title) and "cover" not in requested_marker_categories:
        signals.append(
            ScoreSignal("cover_or_karaoke", COVER_KARAOKE_TRIBUTE_PENALTY, "Title indicates a cover/karaoke version")
        )

    if _LIVE_RE.search(candidate.title) and "live" not in requested_marker_categories:
        signals.append(ScoreSignal("live", LIVE_PENALTY, "Title indicates a live performance"))

    if _INSTRUMENTAL_ACOUSTIC_RE.search(candidate.title) and not (
        {"instrumental", "acoustic"} & requested_marker_categories
    ):
        signals.append(
            ScoreSignal(
                "instrumental_or_acoustic",
                INSTRUMENTAL_ACOUSTIC_PENALTY,
                "Title indicates an instrumental/acoustic version",
            )
        )

    total = sum(s.delta for s in signals)
    return ScoreResult(
        total=total,
        breakdown=signals,
        rejection_flags=flags,
        official_signals=official,
        is_visualizer_only=is_visualizer,
    )


def classify(score: int, threshold: int) -> str:
    """Maps a score to 'automatic' or 'manual_review' against a
    (Settings-configurable) threshold. Precision over recall (§86/§104): the
    threshold is tuned to be conservative, not permissive.
    """
    return "automatic" if score >= threshold else "manual_review"
