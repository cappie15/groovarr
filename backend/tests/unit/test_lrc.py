"""Pure unit tests for app.domain.lrc (§46/§48) — no I/O, no DB, no network."""

from app.domain.lrc import (
    is_duration_plausible,
    is_synced_lyrics_plausible,
    parse_last_timestamp_seconds,
)

SAMPLE_LRC = "[00:12.34]First line\n[01:02.50]Second line\n[03:45.00]Last line\n"


def test_parse_last_timestamp_seconds_finds_the_maximum():
    assert parse_last_timestamp_seconds(SAMPLE_LRC) == 3 * 60 + 45.0


def test_parse_last_timestamp_seconds_returns_none_for_plain_text():
    assert parse_last_timestamp_seconds("just some plain lyric text\nwith no timestamps at all") is None


def test_duration_plausible_within_tolerance():
    # 200s vs 205s is a 2.5% difference — within the 5% tolerance.
    assert is_duration_plausible(200.0, 205.0) is True


def test_duration_implausible_beyond_tolerance():
    # 200s vs 260s is a 30% difference — clearly a different recording.
    assert is_duration_plausible(200.0, 260.0) is False


def test_duration_plausible_when_candidate_has_no_duration():
    # LRCLIB's fuzzy /search results don't always carry a duration — absence
    # is not evidence of a wrong match.
    assert is_duration_plausible(None, 200.0) is True


def test_synced_lyrics_plausible_when_last_timestamp_within_tolerance():
    # Last line at 3:45 (225s), track is 230s — lyrics ending a little before
    # the track does is the normal, expected case.
    assert is_synced_lyrics_plausible(SAMPLE_LRC, expected_duration_s=230.0) is True


def test_synced_lyrics_implausible_when_last_timestamp_exceeds_duration():
    # Last line at 3:45 (225s) but the track is only 60s long — the LRC
    # content claims to run far past the end of the actual track.
    assert is_synced_lyrics_plausible(SAMPLE_LRC, expected_duration_s=60.0) is False


def test_synced_lyrics_plausible_when_lrc_has_no_timestamps():
    # Nothing to cross-check against — don't reject purely on that basis
    # (the metadata-duration check already covers the primary signal).
    assert is_synced_lyrics_plausible("plain text, no timestamps", expected_duration_s=200.0) is True
