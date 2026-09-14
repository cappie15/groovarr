"""Pure LRC-format parsing and lyrics-candidate plausibility checks (§46/§48).

No I/O, no DB, no HTTP — everything here is a plain function over strings/
numbers so it's directly unit-testable, mirroring app/domain/naming.py and
app/domain/text_normalize.py. app/integrations/lyrics/lrclib.py is the only
caller.

Duration-tolerance philosophy (from the beets architecture this project
deliberately mirrors, per the research in
docs/00-research-and-architecture-review.md §3): a lyrics candidate whose
*metadata* duration is far from the track's real duration is probably the
wrong song entirely, so it's rejected outright rather than scored. A synced
candidate gets one additional check: its own LRC content's last timestamp
must not exceed the track's real duration (with the same tolerance) — LRC
lyrics naturally end a little *before* the track does (there's usually an
outro), so "ends noticeably past the end of the song" is the implausible
direction, not "ends a bit early".
"""

import re

# Same 5% tolerance beets uses for lyrics-duration validation — deliberately
# tighter than the 15% used for YouTube-candidate duration scoring (§29),
# since LRCLIB entries are (allegedly) tied to the exact same track rather
# than an independently-produced video.
DURATION_TOLERANCE = 0.05

_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:\.\d+)?)\]")


def parse_last_timestamp_seconds(lrc_text: str) -> float | None:
    """Return the largest `[mm:ss.xx]` timestamp found in `lrc_text`, in
    seconds, or `None` if the text contains no LRC timestamps at all (e.g.
    it's actually plain, unsynced text despite being passed in here).
    """
    matches = _TIMESTAMP_RE.findall(lrc_text)
    if not matches:
        return None
    return max(int(minutes) * 60 + float(seconds) for minutes, seconds in matches)


def is_duration_plausible(candidate_duration_s: float | None, expected_duration_s: float) -> bool:
    """§46/§48: reject a lyrics candidate whose reported duration is far off
    the track's real duration. A candidate with no reported duration at all
    is *not* rejected here — LRCLIB's fuzzy `/search` results don't always
    carry one, and "unknown" isn't evidence of a wrong match.
    """
    if candidate_duration_s is None or candidate_duration_s <= 0 or expected_duration_s <= 0:
        return True
    ratio = abs(candidate_duration_s - expected_duration_s) / expected_duration_s
    return ratio <= DURATION_TOLERANCE


def is_synced_lyrics_plausible(lrc_text: str, expected_duration_s: float) -> bool:
    """§46/§48: additionally reject synced lyrics whose own last timestamp
    exceeds the track's real duration by more than the tolerance — content
    that claims to still be singing well past the end of the actual track is
    a strong signal of a mismatched candidate, independent of whatever
    duration metadata it reported (or didn't).
    """
    last_timestamp = parse_last_timestamp_seconds(lrc_text)
    if last_timestamp is None or expected_duration_s <= 0:
        return True
    return last_timestamp <= expected_duration_s * (1 + DURATION_TOLERANCE)
