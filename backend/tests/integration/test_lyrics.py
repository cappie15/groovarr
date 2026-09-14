"""Phase 7 (Lyrics) integration tests: LRCLIB client behavior (exact-match,
fuzzy fallback, duration/timestamp plausibility rejection, retry/backoff)
and the service-layer negative-caching short-circuit — all against a mocked
HTTP transport (§92), never the real network.
"""

import pytest

from app.db.models.lyrics import LyricsKind
from app.db.models.spotify import Track
from app.integrations.lyrics.lrclib import LRCLIBBackend
from app.services.lyrics import get_or_fetch_lyrics
from tests.fixtures.lrclib_backend import FakeLRCLIBBackend


async def _make_track(session, *, artist="Daft Punk", title="Around the World", duration_ms=200_000) -> Track:
    track = Track(
        spotify_track_id=f"t-{artist}-{title}-{duration_ms}",
        canonical_artist=artist,
        canonical_title=title,
        duration_ms=duration_ms,
    )
    session.add(track)
    await session.commit()
    await session.refresh(track)
    return track


# -- LRCLIBBackend (client-level) ---------------------------------------------


@pytest.mark.asyncio
async def test_exact_match_hit_returns_synced_lyrics():
    fake = FakeLRCLIBBackend()
    fake.set_get_found(syncedLyrics="[00:01.00]La la la", plainLyrics="La la la", duration=200, instrumental=False)

    async with fake.build_client() as http:
        result = await LRCLIBBackend(http).fetch(
            artist="Daft Punk", title="Around the World", album=None, duration_s=200
        )

    assert result is not None
    assert result.kind == "synced"
    assert result.content == "[00:01.00]La la la"
    assert fake.search_requests == 0  # exact match hit — no fuzzy fallback needed


@pytest.mark.asyncio
async def test_exact_match_miss_falls_back_to_fuzzy_search_finding_plain_only():
    fake = FakeLRCLIBBackend()
    fake.set_get_not_found()
    fake.set_search_results([{"plainLyrics": "Some plain lyrics", "duration": 200, "instrumental": False}])

    async with fake.build_client() as http:
        result = await LRCLIBBackend(http).fetch(
            artist="Daft Punk", title="Around the World", album=None, duration_s=200
        )

    assert result is not None
    assert result.kind == "plain"
    assert result.content == "Some plain lyrics"
    assert fake.search_requests == 1


@pytest.mark.asyncio
async def test_candidate_rejected_for_metadata_duration_mismatch():
    fake = FakeLRCLIBBackend()
    # LRCLIB says 260s, the real track is 200s — a 30% mismatch, clearly a
    # different recording, rejected before even looking at the lyrics text.
    fake.set_get_found(plainLyrics="Wrong song's lyrics", duration=260, instrumental=False)

    async with fake.build_client() as http:
        result = await LRCLIBBackend(http).fetch(
            artist="Daft Punk", title="Around the World", album=None, duration_s=200
        )

    assert result is None


@pytest.mark.asyncio
async def test_synced_candidate_rejected_for_implausible_last_timestamp():
    fake = FakeLRCLIBBackend()
    # Metadata duration matches (200s), but the LRC content's own last
    # timestamp (3:45 = 225s) is well past the track's real duration.
    fake.set_get_found(syncedLyrics="[03:45.00]Way too late", duration=200, instrumental=False)

    async with fake.build_client() as http:
        result = await LRCLIBBackend(http).fetch(
            artist="Daft Punk", title="Around the World", album=None, duration_s=200
        )

    assert result is None


@pytest.mark.asyncio
async def test_retry_honors_retry_after_then_succeeds():
    fake = FakeLRCLIBBackend()
    fake.queue_get_statuses([(429, 0.01), (429, 0.01)])
    fake.set_get_found(plainLyrics="Eventually found", duration=200, instrumental=False)

    async with fake.build_client() as http:
        result = await LRCLIBBackend(http).fetch(
            artist="Daft Punk", title="Around the World", album=None, duration_s=200
        )

    assert result is not None
    assert result.content == "Eventually found"
    assert fake.get_requests == 3  # two 429s, then the successful attempt


@pytest.mark.asyncio
async def test_instrumental_result_is_rejected():
    fake = FakeLRCLIBBackend()
    fake.set_get_found(plainLyrics="", instrumental=True, duration=200)

    async with fake.build_client() as http:
        result = await LRCLIBBackend(http).fetch(
            artist="Daft Punk", title="Around the World", album=None, duration_s=200
        )

    assert result is None


# -- get_or_fetch_lyrics (service-level negative caching) ---------------------


@pytest.mark.asyncio
async def test_not_found_creates_negative_cache_row(db_session, monkeypatch):
    fake = FakeLRCLIBBackend()
    fake.set_get_not_found()
    fake.set_search_results([])
    monkeypatch.setattr("app.integrations.lyrics.lrclib._rate_limit_gate", _noop_gate)

    track = await _make_track(db_session)
    async with fake.build_client() as http:
        lyrics = await get_or_fetch_lyrics(db_session, http, track)

    assert lyrics is not None
    assert lyrics.kind == LyricsKind.MISSING
    assert lyrics.negative_cache_until is not None
    assert fake.get_requests == 1
    assert fake.search_requests == 1


@pytest.mark.asyncio
async def test_second_lookup_within_negative_cache_window_makes_no_http_calls(db_session, monkeypatch):
    fake = FakeLRCLIBBackend()
    fake.set_get_not_found()
    fake.set_search_results([])
    monkeypatch.setattr("app.integrations.lyrics.lrclib._rate_limit_gate", _noop_gate)

    track = await _make_track(db_session)
    async with fake.build_client() as http:
        await get_or_fetch_lyrics(db_session, http, track)
        calls_after_first = fake.get_requests + fake.search_requests
        second = await get_or_fetch_lyrics(db_session, http, track)
        calls_after_second = fake.get_requests + fake.search_requests

    assert second.kind == LyricsKind.MISSING
    assert calls_after_second == calls_after_first  # no additional HTTP calls at all


@pytest.mark.asyncio
async def test_a_confirmed_hit_is_reused_without_a_second_http_call(db_session, monkeypatch):
    fake = FakeLRCLIBBackend()
    fake.set_get_found(plainLyrics="Found it", duration=200, instrumental=False)
    monkeypatch.setattr("app.integrations.lyrics.lrclib._rate_limit_gate", _noop_gate)

    track = await _make_track(db_session)
    async with fake.build_client() as http:
        first = await get_or_fetch_lyrics(db_session, http, track)
        calls_after_first = fake.get_requests
        second = await get_or_fetch_lyrics(db_session, http, track)
        calls_after_second = fake.get_requests

    assert first.kind == LyricsKind.PLAIN
    assert second.id == first.id
    assert calls_after_second == calls_after_first


@pytest.mark.asyncio
async def test_backend_exception_is_treated_as_a_miss_never_raised(db_session, monkeypatch):
    """§48: a lyrics-provider failure must never propagate as an exception
    out of get_or_fetch_lyrics — callers (the acquisition pipeline) rely on
    that to keep lyrics strictly best-effort.
    """

    class ExplodingBackend:
        async def fetch(self, **kwargs):
            raise RuntimeError("simulated LRCLIB outage")

    monkeypatch.setattr("app.services.lyrics.LRCLIBBackend", lambda http: ExplodingBackend())

    track = await _make_track(db_session)
    import httpx

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as http:
        lyrics = await get_or_fetch_lyrics(db_session, http, track)

    assert lyrics is not None
    assert lyrics.kind == LyricsKind.MISSING


async def _noop_gate() -> None:
    """Skip the real client-side rate-limit sleep in tests — the gate's own
    behavior isn't what these tests are exercising, and 0.25s per call adds
    up across a test file that calls the backend many times.
    """
