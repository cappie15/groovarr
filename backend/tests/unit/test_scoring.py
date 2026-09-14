"""Fixture-corpus classification tests for the scoring engine (§8/§85-
equivalent, §86/§104: tuned for precision over recall). Each scenario below
mirrors a case from the architecture doc's own list of difficult matches —
same song by multiple artists, requested remix vs. plain official video,
covers, remasters, clean/explicit pairs, live, lyric video, visualizer, fan
video, official-without-the-literal-word-"official", VEVO, featured
artists, and Unicode/accent differences.
"""

import pytest

from app.db.models.spotify import Track
from app.integrations.youtube.data_api import RawCandidate
from app.integrations.youtube.ytdlp_client import EnrichedInfo
from app.matching.scoring import AUTOMATIC_MATCH_THRESHOLD_DEFAULT, classify, hard_filter, score_candidate


def _track(
    *,
    artist="Daft Punk",
    featured=None,
    title="Around the World",
    version=None,
    duration_ms=210_000,
    explicit=False,
) -> Track:
    return Track(
        spotify_track_id="t1",
        canonical_artist=artist,
        featured_artists=featured or [],
        canonical_title=title,
        parsed_version=version,
        duration_ms=duration_ms,
        explicit=explicit,
    )


def _candidate(*, title, channel="Daft Punk") -> RawCandidate:
    return RawCandidate(youtube_video_id="vid", title=title, channel_id="c1", channel_name=channel)


def _enriched(*, duration_s=210.0, width=1920, height=1080, media_type="video") -> EnrichedInfo:
    return EnrichedInfo(duration_s=duration_s, media_type=media_type, width=width, height=height)


def _classify(track, candidate, enriched, threshold=AUTOMATIC_MATCH_THRESHOLD_DEFAULT):
    result = score_candidate(track, candidate, enriched)
    return result, classify(result.total, threshold)


# --- Hard filters (§31) -------------------------------------------------------


def test_short_is_hard_excluded():
    assert hard_filter(_enriched(media_type="short")) is not None


def test_portrait_is_hard_excluded():
    assert hard_filter(_enriched(width=1080, height=1920)) is not None


def test_landscape_video_passes_hard_filter():
    assert hard_filter(_enriched(width=1920, height=1080)) is None


# --- The official/high-confidence happy path ----------------------------------


def test_official_video_with_exact_artist_and_title_is_automatic():
    track = _track()
    candidate = _candidate(title="Daft Punk - Around the World (Official Music Video)", channel="Daft Punk")
    result, decision = _classify(track, candidate, _enriched(duration_s=209))
    assert decision == "automatic"
    assert result.total >= AUTOMATIC_MATCH_THRESHOLD_DEFAULT


def test_official_upload_without_the_literal_word_official_still_scores_well():
    # No "official" anywhere in the title — official-ness comes from the
    # VEVO channel and exact artist/title match instead (§24-25).
    track = _track()
    candidate = _candidate(title="Daft Punk - Around the World", channel="DaftPunkVEVO")
    result, decision = _classify(track, candidate, _enriched(duration_s=210))
    assert decision == "automatic"
    assert result.official_signals.get("vevo_or_topic_channel") is True


def test_topic_channel_counts_as_official_signal():
    track = _track()
    candidate = _candidate(title="Around the World", channel="Daft Punk - Topic")
    result, _ = _classify(track, candidate, _enriched(duration_s=210))
    assert result.official_signals.get("vevo_or_topic_channel") is True


# --- Same song, wrong artist / cover / tribute ---------------------------------


def test_same_title_different_artist_is_not_automatic():
    track = _track(artist="Daft Punk")
    candidate = _candidate(
        title="Some Cover Band - Around the World (Cover)", channel="Some Cover Band Tribute Channel"
    )
    result, decision = _classify(track, candidate, _enriched(duration_s=240))
    assert decision == "manual_review"


def test_cover_karaoke_is_penalized_when_not_requested():
    track = _track(version=None)
    candidate = _candidate(title="Daft Punk - Around the World (Karaoke Version)")
    result, _ = _classify(track, candidate, _enriched(duration_s=210))
    assert any(s.signal == "cover_or_karaoke" and s.delta < 0 for s in result.breakdown)


def test_fan_made_is_penalized():
    track = _track()
    candidate = _candidate(title="Daft Punk - Around the World (Fan Made)", channel="A Random Fan Channel")
    result, decision = _classify(track, candidate, _enriched(duration_s=210))
    assert any(s.signal == "fan_made" for s in result.breakdown)
    assert decision == "manual_review"


# --- Version/remix correctness (§22 — the most important behavior) ------------


def test_requested_remix_outranks_plain_official_video():
    track = _track(version="Extended Mix", duration_ms=360_000)

    plain_official = _candidate(title="Daft Punk - Around the World (Official Music Video)", channel="Daft Punk")
    plain_result, _ = _classify(track, plain_official, _enriched(duration_s=210))  # short version's real duration

    remix_candidate = _candidate(title="Daft Punk - Around the World (Extended Mix)", channel="Some Uploader")
    remix_result, remix_decision = _classify(track, remix_candidate, _enriched(duration_s=358))

    assert remix_result.total > plain_result.total
    assert remix_decision == "automatic"
    assert any(s.signal == "requested_version_exact" for s in remix_result.breakdown)
    assert any(s.signal == "plain_original_but_version_requested" and s.delta < 0 for s in plain_result.breakdown)


def test_requested_remix_but_candidate_is_a_different_remix_is_penalized():
    track = _track(version="John Doe Remix")
    wrong_remix = _candidate(title="Daft Punk - Around the World (Jane Smith Remix)")
    result, _ = _classify(track, wrong_remix, _enriched(duration_s=210))
    assert any(s.signal == "wrong_version_variant" and s.delta < 0 for s in result.breakdown)


def test_plain_original_requested_but_candidate_is_a_remix_is_penalized():
    track = _track(version=None)
    remix_candidate = _candidate(title="Daft Punk - Around the World (Club Mix)")
    result, decision = _classify(track, remix_candidate, _enriched(duration_s=210))
    assert any(s.signal == "unwanted_version_variant" and s.delta < 0 for s in result.breakdown)
    assert decision == "manual_review"


def test_remaster_requested_and_found_is_rewarded():
    track = _track(version="Remastered 2011")
    candidate = _candidate(title="Daft Punk - Around the World (Remastered 2011)")
    result, decision = _classify(track, candidate, _enriched(duration_s=210))
    assert any(s.signal == "requested_version_exact" for s in result.breakdown)
    assert decision == "automatic"


# --- Explicit / clean -----------------------------------------------------------


def test_explicit_track_penalizes_clean_candidate():
    track = _track(explicit=True)
    candidate = _candidate(title="Daft Punk - Around the World (Clean Version)")
    result, _ = _classify(track, candidate, _enriched(duration_s=210))
    assert any(s.signal == "clean_but_explicit_requested" and s.delta < 0 for s in result.breakdown)


def test_non_explicit_track_does_not_penalize_clean_candidate():
    track = _track(explicit=False)
    candidate = _candidate(title="Daft Punk - Around the World (Clean Version)")
    result, _ = _classify(track, candidate, _enriched(duration_s=210))
    assert not any(s.signal == "clean_but_explicit_requested" for s in result.breakdown)


# --- Duration: soft signal, red flag, never a hard exclusion (§29) -------------


def test_duration_far_off_is_a_red_flag_not_an_exclusion():
    track = _track(duration_ms=210_000)
    candidate = _candidate(title="Daft Punk - Around the World (Official Music Video)")
    result, decision = _classify(track, candidate, _enriched(duration_s=400))  # ~90% off
    assert result.rejection_flags  # flagged...
    assert decision in ("automatic", "manual_review")  # ...but still classified, never dropped


def test_missing_duration_is_neutral_not_penalized():
    track = _track()
    candidate = _candidate(title="Daft Punk - Around the World (Official Music Video)")
    result, _ = _classify(track, candidate, _enriched(duration_s=None))
    assert not any(s.signal.startswith("duration") for s in result.breakdown)


# --- Live / lyric video / visualizer (fallback hierarchy, §25) -----------------


def test_live_version_penalized_when_not_requested():
    track = _track(version=None)
    candidate = _candidate(title="Daft Punk - Around the World (Live at Coachella)")
    result, decision = _classify(track, candidate, _enriched(duration_s=280))
    assert any(s.signal == "live" and s.delta < 0 for s in result.breakdown)
    assert decision == "manual_review"


def test_live_version_not_penalized_when_explicitly_requested():
    track = _track(version="Live at Wembley")
    candidate = _candidate(title="Daft Punk - Around the World (Live at Wembley)")
    result, _ = _classify(track, candidate, _enriched(duration_s=280))
    assert not any(s.signal == "live" for s in result.breakdown)


def test_lyric_video_is_penalized_and_ranks_below_official():
    track = _track()
    official = _candidate(title="Daft Punk - Around the World (Official Music Video)")
    lyric = _candidate(title="Daft Punk - Around the World (Lyrics Video)")
    official_result, _ = _classify(track, official, _enriched(duration_s=210))
    lyric_result, _ = _classify(track, lyric, _enriched(duration_s=210))
    assert lyric_result.total < official_result.total
    assert any(s.signal == "lyric_video" for s in lyric_result.breakdown)


def test_visualizer_is_flagged_and_penalized():
    track = _track()
    candidate = _candidate(title="Daft Punk - Around the World (Visualizer)")
    result, _ = _classify(track, candidate, _enriched(duration_s=210))
    assert result.is_visualizer_only is True
    assert any(s.signal == "visualizer" and s.delta < 0 for s in result.breakdown)


def test_visualizer_that_still_clears_threshold_is_marked_visualizer_only():
    # A visualizer can still be the best available match (exact artist/title/
    # duration/version, no competing real video) — the *service* layer
    # (app/services/search.py) is what turns this into an Incomplete state
    # rather than a plain automatic pass-through; here we only assert the
    # scoring engine correctly flags it so that downstream logic can act.
    track = _track(version="Extended Mix", duration_ms=360_000)
    candidate = _candidate(title="Daft Punk - Around the World (Extended Mix Visualizer)")
    result, decision = _classify(track, candidate, _enriched(duration_s=359))
    assert result.is_visualizer_only is True
    assert decision == "automatic"


# --- Featured artists (§8) ------------------------------------------------------


def test_featured_artist_mentioned_in_title_is_rewarded():
    track = _track(artist="Daft Punk", featured=["Pharrell Williams"], title="Get Lucky")
    candidate = _candidate(title="Daft Punk - Get Lucky ft. Pharrell Williams (Official Music Video)")
    result, decision = _classify(track, candidate, _enriched(duration_s=248))
    assert any(s.signal == "featured_artist_match" for s in result.breakdown)
    assert decision == "automatic"


# --- Unicode / accent / punctuation differences --------------------------------


def test_accented_artist_name_matches_unaccented_candidate_title():
    track = _track(artist="Beyoncé", title="Halo")
    candidate = _candidate(title="Beyonce - Halo (Official Video)", channel="BeyonceVEVO")
    result, decision = _classify(track, candidate, _enriched(duration_s=261))
    assert any(s.signal == "artist_match" for s in result.breakdown)
    assert decision == "automatic"


@pytest.mark.parametrize(
    "threshold",
    [50, 70, 90],
)
def test_classify_respects_configured_threshold(threshold):
    assert classify(threshold, threshold) == "automatic"
    assert classify(threshold - 1, threshold) == "manual_review"
