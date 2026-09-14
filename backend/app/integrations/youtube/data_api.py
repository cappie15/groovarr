"""YouTube Data API v3 client — the PRIMARY discovery mechanism (§2-G of the
architecture doc): an operator-provided API key, no user login, structurally
independent of yt-dlp so a yt-dlp breakage never silently breaks discovery
(and vice versa). Only `search.list` is used; enrichment (duration/
orientation) is yt-dlp's job (app/integrations/youtube/ytdlp_client.py),
since the Data API doesn't expose orientation and its duration format needs
a second per-video call the research didn't find a compelling reason to add
when yt-dlp's enrichment step already needs to run anyway.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.integrations.youtube.errors import YouTubeError

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

#: A `search.list` call costs 100 quota units against Google's own default
#: project quota of 10,000 units/day — i.e. ~100 searches/day on an unmodified
#: project. Both are Google-documented constants (YouTube Data API v3 quota
#: costs table / default project quota), not something the API exposes for
#: Groovarr to read back at runtime — there is no "remaining quota" endpoint.
#: Used by app/services/settings_service.py to turn Groovarr's own call
#: counter into an operator-facing estimate on System/Status (§88).
SEARCH_LIST_QUOTA_COST_UNITS = 100
YOUTUBE_DEFAULT_DAILY_QUOTA_UNITS = 10_000


@dataclass(frozen=True)
class RawCandidate:
    """One discovery-stage result, before yt-dlp enrichment/hard filters."""

    youtube_video_id: str
    title: str
    channel_id: str | None
    channel_name: str | None


class YouTubeDataApiClient:
    """Thin async wrapper around `search.list`. Takes an `httpx.AsyncClient`
    so tests can inject a mocked transport instead of hitting the network,
    mirroring `SpotifyClient`.
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def search(self, query: str, *, max_results: int = 10) -> list[RawCandidate]:
        response = await self._http.get(
            SEARCH_URL,
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "videoEmbeddable": "true",
                "maxResults": max_results,
                "key": self._api_key,
            },
        )
        if response.status_code == 403:
            # Covers both an invalid key and daily-quota exhaustion — the
            # Data API doesn't distinguish these with a different status
            # code, and callers (Discovery, below) treat both the same way:
            # fall back to yt-dlp's ytsearch: rather than failing the search.
            raise YouTubeError(
                "YouTube Data API request was rejected (HTTP 403) — invalid API key or quota exhausted: "
                f"{response.text[:200]}"
            )
        if response.status_code != 200:
            raise YouTubeError(f"YouTube Data API request failed (HTTP {response.status_code}): {response.text[:200]}")

        body: dict[str, Any] = response.json()
        results: list[RawCandidate] = []
        for item in body.get("items", []):
            video_id = (item.get("id") or {}).get("videoId")
            snippet = item.get("snippet") or {}
            if not video_id:
                continue
            results.append(
                RawCandidate(
                    youtube_video_id=video_id,
                    title=snippet.get("title", ""),
                    channel_id=snippet.get("channelId"),
                    channel_name=snippet.get("channelTitle"),
                )
            )
        return results
