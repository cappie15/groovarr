"""yt-dlp version-check — purely informational (§88 gap the frontend System
page previously called out explicitly: "Pinned yt-dlp/FFmpeg dependency
versions are not yet exposed"). Compares the currently-installed yt-dlp
version (`yt_dlp.version.__version__` — the actual installed build, not the
`>=` floor pinned in pyproject.toml) against the latest tagged release on
GitHub, so an operator can see "an update is available" at a glance. This
module never attempts to update anything itself — bumping the pyproject.toml
pin is a deliberate maintainer action, not a button in this app.

GitHub's public releases endpoint needs no auth for a read this
low-volume (one System/Status page's worth of checks, well under the
unauthenticated 60 requests/hour/IP rate limit), but the result is still
cached in-process for CACHE_TTL so a page load never has to wait on GitHub
and Groovarr isn't polling it needlessly. Mirrors hwaccel.py's "cache
detection in a module-level holder, expose a test-only reset" shape, except
with a real TTL rather than "forever" since, unlike host hardware, a new
yt-dlp release can legitimately land at any time.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import structlog
import yt_dlp.version

logger = structlog.get_logger(__name__)

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
CACHE_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class YtdlpVersionStatus:
    installed_version: str
    #: None only when a latest-release lookup has never succeeded (e.g. the
    #: very first check failed and GitHub is unreachable) — the frontend
    #: should render this as "unknown", never as "up to date".
    latest_version: str | None
    checked_at: datetime | None
    update_available: bool
    #: Set when the most recent GitHub check failed, so the UI can show
    #: *why* latest_version might be missing/stale instead of going silent.
    check_error: str | None = None


def installed_ytdlp_version() -> str:
    return yt_dlp.version.__version__


def _parse_version(value: str) -> tuple[int, ...] | None:
    """yt-dlp's stable releases are CalVer `YYYY.MM.DD` (optionally a 4th
    "patch of the day" component, e.g. `2024.08.19.1`) — parse into an int
    tuple so `(2024, 9, 1) > (2024, 8, 19)` compares correctly, unlike a
    plain string comparison once month/day aren't all double-digit-padded
    the same way (e.g. a hypothetical single-digit-day release). Returns
    None for anything that doesn't fit (a dev/nightly build suffix, etc.) so
    the caller can fall back to a plain inequality check.
    """
    parts = value.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def is_update_available(installed: str, latest: str) -> bool:
    parsed_installed = _parse_version(installed)
    parsed_latest = _parse_version(latest)
    if parsed_installed is not None and parsed_latest is not None:
        return parsed_latest > parsed_installed
    # Neither side parsed as clean CalVer (e.g. a dev build) — the only
    # honest signal left is "are these the same string at all".
    return installed != latest


async def fetch_latest_ytdlp_version(http: httpx.AsyncClient) -> str:
    """Raises `httpx.HTTPError` on failure — callers decide how to degrade
    (see `get_ytdlp_version_status`, which never lets this propagate to an
    API caller: this is informational, not something that should ever 5xx
    the System/Status page)."""
    response = await http.get(
        GITHUB_LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json"},
    )
    response.raise_for_status()
    body = response.json()
    tag: str = body.get("tag_name") or ""
    return tag.removeprefix("v")


@dataclass
class _Cache:
    latest_version: str | None = None
    checked_at: datetime | None = None
    error: str | None = None


_cache = _Cache()


async def get_ytdlp_version_status(http: httpx.AsyncClient) -> YtdlpVersionStatus:
    installed = installed_ytdlp_version()
    now = datetime.now(UTC)

    stale = _cache.checked_at is None or (now - _cache.checked_at) >= CACHE_TTL
    if stale:
        try:
            _cache.latest_version = await fetch_latest_ytdlp_version(http)
            _cache.error = None
        except httpx.HTTPError as exc:
            # Keep whatever latest_version we had (possibly None, possibly a
            # still-useful stale value) rather than discarding it on a
            # transient failure; just surface the error alongside it.
            logger.warning("ytdlp_version.github_check_failed", error=str(exc))
            _cache.error = str(exc)
        _cache.checked_at = now

    latest = _cache.latest_version
    return YtdlpVersionStatus(
        installed_version=installed,
        latest_version=latest,
        checked_at=_cache.checked_at,
        update_available=latest is not None and is_update_available(installed, latest),
        check_error=_cache.error,
    )


def reset_ytdlp_version_cache() -> None:
    """Test-only: clear the cached GitHub check result."""
    global _cache
    _cache = _Cache()
