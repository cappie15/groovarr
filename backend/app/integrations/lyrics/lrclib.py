"""LRCLIB (lrclib.net) lyrics provider (§44-48). No API key, no auth — the
only courtesy the project asks for is a descriptive User-Agent (§3 research).

Two-step lookup: an exact-match `/api/get` (artist/title/album/duration)
first, falling back to the fuzzy `/api/search` if that misses, preferring a
synced result over a plain one among the fuzzy candidates. Every candidate
(from either endpoint) is validated with app.domain.lrc's duration/timestamp
plausibility checks before being accepted — LRCLIB itself does no such
validation, so a wrong-song match is entirely possible on the fuzzy path if
Groovarr doesn't check it.

Client-side courtesy rate limiting (a small minimum interval between
requests) and Retry-After-respecting backoff on 429/5xx are implemented
here rather than relying on LRCLIB enforcing anything itself — its read API
currently documents no rate limit, but hammering a free public service
anyway would be poor citizenship (§48).
"""

import asyncio
import time
from typing import Any

import httpx
import structlog

from app.domain.lrc import is_duration_plausible, is_synced_lyrics_plausible
from app.integrations.lyrics.base import LyricsResult

logger = structlog.get_logger(__name__)

BASE_URL = "https://lrclib.net/api"
USER_AGENT = "Groovarr/0.1 (+https://github.com/groovarr/groovarr)"

_MIN_REQUEST_INTERVAL_S = 0.25
# Capped short so a slow/rate-limited LRCLIB never turns "best-effort lyrics"
# into a long, request-blocking pause in the middle of the acquisition
# pipeline (§48 — lyrics must never meaningfully delay, let alone fail, the
# video import). Three attempts total: the request itself plus up to two
# retries.
_MAX_ATTEMPTS = 3
_MAX_BACKOFF_S = 3.0

_last_request_at = 0.0
_rate_limit_lock = asyncio.Lock()


async def _rate_limit_gate() -> None:
    """A simple monotonic-clock gate enforcing a minimum interval between
    outgoing requests, process-wide (§48's "respect provider rate limits"
    applied preemptively rather than only reactively on a 429).
    """
    global _last_request_at
    async with _rate_limit_lock:
        now = time.monotonic()
        wait = _last_request_at + _MIN_REQUEST_INTERVAL_S - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


class LRCLIBBackend:
    """Takes an `httpx.AsyncClient` so tests can inject a mocked transport
    instead of hitting the network, mirroring SpotifyClient (Phase 2).
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(
        self, *, artist: str, title: str, album: str | None, duration_s: float
    ) -> LyricsResult | None:
        exact = await self._fetch_exact(artist, title, album, duration_s)
        if exact is not None:
            return exact
        return await self._fetch_fuzzy(artist, title, duration_s)

    async def _fetch_exact(
        self, artist: str, title: str, album: str | None, duration_s: float
    ) -> LyricsResult | None:
        params: dict[str, Any] = {
            "artist_name": artist,
            "track_name": title,
            "duration": str(round(duration_s)),
        }
        if album:
            params["album_name"] = album
        response = await self._request("/get", params)
        if response is None or response.status_code == 404:
            return None
        if response.status_code != 200:
            return None
        return self._result_from_payload(response.json(), duration_s)

    async def _fetch_fuzzy(self, artist: str, title: str, duration_s: float) -> LyricsResult | None:
        response = await self._request("/search", {"artist_name": artist, "track_name": title})
        if response is None or response.status_code != 200:
            return None
        candidates = response.json()
        if not isinstance(candidates, list) or not candidates:
            return None
        # Prefer a candidate with synced lyrics over a plain-only one among
        # the fuzzy results (§46), before plausibility-filtering either kind.
        ranked = sorted(candidates, key=lambda c: 0 if c.get("syncedLyrics") else 1)
        for candidate in ranked:
            result = self._result_from_payload(candidate, duration_s)
            if result is not None:
                return result
        return None

    def _result_from_payload(self, data: dict[str, Any], expected_duration_s: float) -> LyricsResult | None:
        if data.get("instrumental"):
            return None
        if not is_duration_plausible(data.get("duration"), expected_duration_s):
            return None

        synced = data.get("syncedLyrics")
        if synced:
            if is_synced_lyrics_plausible(synced, expected_duration_s):
                return LyricsResult(kind="synced", content=synced)
            # Fall through: a metadata-plausible but timestamp-implausible
            # synced result doesn't get a second chance via its own
            # plainLyrics on the *same* record either — if the record itself
            # looks like the wrong song, prefer to keep looking (the fuzzy
            # ranking loop moves on to the next candidate) rather than
            # accepting a downgraded version of a rejected match.
            return None

        plain = data.get("plainLyrics")
        if plain:
            return LyricsResult(kind="plain", content=plain)
        return None

    async def _request(self, path: str, params: dict[str, Any]) -> httpx.Response | None:
        last_response: httpx.Response | None = None
        for attempt in range(_MAX_ATTEMPTS):
            await _rate_limit_gate()
            try:
                response = await self._http.get(
                    f"{BASE_URL}{path}", params=params, headers={"User-Agent": USER_AGENT}
                )
            except httpx.HTTPError as exc:
                logger.warning("lyrics.lrclib.request_error", path=path, attempt=attempt, error=str(exc))
                continue
            last_response = response
            if response.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_ATTEMPTS - 1:
                retry_after_header = response.headers.get("Retry-After")
                delay = min(float(retry_after_header), _MAX_BACKOFF_S) if retry_after_header else _MAX_BACKOFF_S
                logger.info(
                    "lyrics.lrclib.retrying", path=path, status=response.status_code, delay=delay, attempt=attempt
                )
                await asyncio.sleep(delay)
                continue
            return response
        return last_response
