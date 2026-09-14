"""Unit tests for the yt-dlp version-check helper
(app/integrations/acquisition/ytdlp_version.py). The GitHub releases API is
a genuinely external service (§92) — mocked via httpx.MockTransport, same
approach as this project's other external-API tests (LRCLIB, Jellyfin,
Plex, Spotify), never hit over the real network.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.integrations.acquisition import ytdlp_version


@pytest.fixture(autouse=True)
def _clear_cache():
    ytdlp_version.reset_ytdlp_version_cache()
    yield
    ytdlp_version.reset_ytdlp_version_cache()


def _github_client(tag_name: str = "2024.08.19", status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ytdlp_version.GITHUB_LATEST_RELEASE_URL
        if status != 200:
            return httpx.Response(status, json={"message": "error"})
        return httpx.Response(200, json={"tag_name": tag_name})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_version_handles_calver_and_patch_component():
    assert ytdlp_version._parse_version("2024.08.19") == (2024, 8, 19)
    assert ytdlp_version._parse_version("2024.08.19.1") == (2024, 8, 19, 1)
    assert ytdlp_version._parse_version("not-a-version") is None


def test_is_update_available_compares_calver_numerically_not_lexically():
    # A naive string comparison would get this right by luck (both zero-
    # padded), but the numeric-tuple comparison is what actually protects
    # against a single-digit month/day ever breaking this.
    assert ytdlp_version.is_update_available("2024.08.19", "2024.09.01") is True
    assert ytdlp_version.is_update_available("2024.09.01", "2024.08.19") is False
    assert ytdlp_version.is_update_available("2024.08.19", "2024.08.19") is False


def test_is_update_available_falls_back_to_string_inequality_for_unparseable_versions():
    assert ytdlp_version.is_update_available("nightly-abc", "nightly-def") is True
    assert ytdlp_version.is_update_available("nightly-abc", "nightly-abc") is False


def test_installed_version_reads_the_real_installed_package():
    # Not mocked — proves this reads the actual installed yt-dlp build
    # (pyproject.toml only pins a floor, `>=2024.8.0`; this is the real
    # version actually resolved into the venv).
    version = ytdlp_version.installed_ytdlp_version()
    assert ytdlp_version._parse_version(version) is not None


@pytest.mark.asyncio
async def test_fetch_latest_strips_leading_v_prefix():
    async with _github_client(tag_name="v2024.08.19") as http:
        latest = await ytdlp_version.fetch_latest_ytdlp_version(http)
    assert latest == "2024.08.19"


@pytest.mark.asyncio
async def test_status_reports_update_available_when_latest_is_newer(monkeypatch):
    monkeypatch.setattr(ytdlp_version, "installed_ytdlp_version", lambda: "2024.08.19")
    async with _github_client(tag_name="2024.09.01") as http:
        status = await ytdlp_version.get_ytdlp_version_status(http)

    assert status.installed_version == "2024.08.19"
    assert status.latest_version == "2024.09.01"
    assert status.update_available is True
    assert status.check_error is None
    assert status.checked_at is not None


@pytest.mark.asyncio
async def test_status_reports_no_update_when_already_current(monkeypatch):
    monkeypatch.setattr(ytdlp_version, "installed_ytdlp_version", lambda: "2024.09.01")
    async with _github_client(tag_name="2024.09.01") as http:
        status = await ytdlp_version.get_ytdlp_version_status(http)

    assert status.update_available is False


@pytest.mark.asyncio
async def test_github_failure_is_degraded_not_raised(monkeypatch):
    monkeypatch.setattr(ytdlp_version, "installed_ytdlp_version", lambda: "2024.08.19")
    async with _github_client(status=503) as http:
        status = await ytdlp_version.get_ytdlp_version_status(http)

    assert status.latest_version is None
    assert status.update_available is False
    assert status.check_error is not None


@pytest.mark.asyncio
async def test_result_is_cached_and_not_rechecked_within_ttl():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"tag_name": "2024.08.19"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await ytdlp_version.get_ytdlp_version_status(http)
        await ytdlp_version.get_ytdlp_version_status(http)
        await ytdlp_version.get_ytdlp_version_status(http)

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cache_is_rechecked_once_ttl_has_elapsed():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"tag_name": "2024.08.19"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await ytdlp_version.get_ytdlp_version_status(http)
        # Force the cached entry to look 25h old, past the 24h TTL.
        ytdlp_version._cache.checked_at = datetime.now(UTC) - timedelta(hours=25)
        await ytdlp_version.get_ytdlp_version_status(http)

    assert calls["n"] == 2
