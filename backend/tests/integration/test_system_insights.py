"""Integration tests for the two System/Status insights added on top of
app/api/system.py's existing hardware-acceleration pattern (§88):

1. YouTube Data API v3 `search.list` daily quota visibility.
2. yt-dlp installed-vs-latest version check.

Calls route functions directly against `db_session`, matching
test_settings_api.py/test_dashboard.py's convention.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.api.system import read_youtube_quota_status, read_ytdlp_version_status
from app.integrations.acquisition import ytdlp_version
from app.integrations.youtube.data_api import SEARCH_LIST_QUOTA_COST_UNITS, YOUTUBE_DEFAULT_DAILY_QUOTA_UNITS
from app.services.settings_service import get_youtube_search_quota_status, record_youtube_search_call

# --- YouTube quota counter (service layer) ----------------------------------


async def test_quota_status_starts_at_zero(db_session) -> None:
    status = await get_youtube_search_quota_status(db_session)
    assert status.search_calls_today == 0
    assert status.quota_units_used_today == 0
    assert status.quota_units_default_daily == YOUTUBE_DEFAULT_DAILY_QUOTA_UNITS
    assert status.estimated_daily_search_limit == YOUTUBE_DEFAULT_DAILY_QUOTA_UNITS // SEARCH_LIST_QUOTA_COST_UNITS


async def test_record_search_call_increments_and_persists(db_session) -> None:
    await record_youtube_search_call(db_session)
    await record_youtube_search_call(db_session)
    row = await record_youtube_search_call(db_session)

    assert row.youtube_quota_search_calls == 3
    status = await get_youtube_search_quota_status(db_session)
    assert status.search_calls_today == 3
    assert status.quota_units_used_today == 3 * SEARCH_LIST_QUOTA_COST_UNITS


async def test_quota_resets_on_first_call_of_a_new_utc_day(db_session) -> None:
    await record_youtube_search_call(db_session)
    await record_youtube_search_call(db_session)
    row = await record_youtube_search_call(db_session)
    assert row.youtube_quota_search_calls == 3

    # Simulate the stored counter belonging to yesterday.
    row.youtube_quota_date = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    await db_session.commit()

    # A read (no new call yet) must report 0 for today, not yesterday's 3 —
    # and must not mutate the stored row itself (a pure read shouldn't have
    # side effects on data it isn't the source of truth for touching).
    status = await get_youtube_search_quota_status(db_session)
    assert status.search_calls_today == 0

    # The next actual call rolls the counter over and starts counting today.
    row_after = await record_youtube_search_call(db_session)
    assert row_after.youtube_quota_search_calls == 1
    assert row_after.youtube_quota_date == datetime.now(UTC).date().isoformat()


async def test_ytsearch_fallback_never_increments_quota(db_session, monkeypatch) -> None:
    """§88's explicit requirement: only the real Data API path counts —
    proven here at the discovery-orchestration boundary (with no API key
    configured, so the ytsearch: fallback is what actually runs), not just
    by checking the counter function exists in isolation. yt-dlp's own
    ytsearch: is itself an external network call, so it's monkeypatched out
    here the same way app.services.search's own tests stub discovery/
    enrichment — this test is about the quota counter, not ytsearch itself.
    """
    from app.db.models.spotify import Track
    from app.integrations.youtube import discovery

    monkeypatch.setattr(discovery, "ytsearch_discover", lambda query, max_results=10: _empty_list())

    track = Track(
        spotify_track_id="t-quota-1",
        canonical_artist="Artist",
        canonical_title="Title",
        duration_ms=200_000,
        explicit=False,
    )
    db_session.add(track)
    await db_session.flush()

    async with httpx.AsyncClient() as http:
        # No YOUTUBE_API_KEY configured in the test environment (app/core/
        # config loads real env/`.env`, which has none for this suite) —
        # so this necessarily takes the ytsearch: fallback path.
        await discovery.discover_candidates(db_session, http, track)

    status = await get_youtube_search_quota_status(db_session)
    assert status.search_calls_today == 0


async def _empty_list():
    return []


# --- /api/system/youtube-quota -----------------------------------------------


async def test_youtube_quota_endpoint_reflects_recorded_calls(db_session) -> None:
    await record_youtube_search_call(db_session)
    await record_youtube_search_call(db_session)

    out = await read_youtube_quota_status(session=db_session)
    assert out.search_calls_today == 2
    assert out.quota_units_used_today == 2 * SEARCH_LIST_QUOTA_COST_UNITS
    assert out.estimated_daily_search_limit == 100


# --- /api/system/ytdlp-version -----------------------------------------------


@pytest.fixture(autouse=True)
def _clear_ytdlp_version_cache():
    ytdlp_version.reset_ytdlp_version_cache()
    yield
    ytdlp_version.reset_ytdlp_version_cache()


async def test_ytdlp_version_endpoint_flags_update_available(monkeypatch) -> None:
    monkeypatch.setattr(ytdlp_version, "installed_ytdlp_version", lambda: "2024.08.19")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tag_name": "2024.09.01"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        out = await read_ytdlp_version_status(http=http)

    assert out.installed_version == "2024.08.19"
    assert out.latest_version == "2024.09.01"
    assert out.update_available is True
    assert out.check_error is None


async def test_ytdlp_version_endpoint_degrades_cleanly_on_github_failure(monkeypatch) -> None:
    monkeypatch.setattr(ytdlp_version, "installed_ytdlp_version", lambda: "2024.08.19")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        out = await read_ytdlp_version_status(http=http)

    assert out.update_available is False
    assert out.latest_version is None
    assert out.check_error is not None
