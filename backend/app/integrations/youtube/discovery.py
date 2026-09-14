"""Discovery: turns a `Track` into a list of candidate YouTube videos.

Primary = YouTube Data API v3 `search.list` (§2-G) using an operator-
provided API key — no login, structurally independent of yt-dlp. Fallback =
yt-dlp's own `ytsearch:` pseudo-search, used only when no API key is
configured or the Data API call fails/quota-exhausts, and always logged as
a degraded path (never silent) so a permanent fallback doesn't go unnoticed.
Kept behind this one function so either discovery mechanism can be swapped
without touching app/matching/scoring.py.
"""

import httpx
import structlog

from app.core.config import get_settings
from app.db.models.spotify import Track
from app.integrations.youtube.data_api import RawCandidate, YouTubeDataApiClient
from app.integrations.youtube.errors import YouTubeError
from app.integrations.youtube.ytdlp_client import ytsearch_discover

logger = structlog.get_logger(__name__)

MAX_CANDIDATES = 10


def build_search_query(track: Track) -> str:
    """Build a search query from Spotify metadata — never just "artist +
    title" (§18): includes featured artists and any parsed remix/version
    text so the query itself steers toward the right version.
    """
    parts = [track.canonical_artist]
    if track.featured_artists:
        parts.append(" ".join(track.featured_artists))
    parts.append(track.canonical_title)
    if track.parsed_version:
        parts.append(track.parsed_version)
    return " ".join(p for p in parts if p)


async def discover_candidates(http: httpx.AsyncClient, track: Track) -> list[RawCandidate]:
    query = build_search_query(track)
    settings = get_settings()

    if settings.youtube_api_key:
        try:
            client = YouTubeDataApiClient(http, settings.youtube_api_key)
            results = await client.search(query, max_results=MAX_CANDIDATES)
            if results:
                return results
            logger.info("youtube.discovery_empty_via_data_api", query=query)
            return results
        except YouTubeError as exc:
            logger.warning("youtube.data_api_failed_falling_back_to_ytsearch", query=query, error=str(exc))
    else:
        logger.info("youtube.no_api_key_configured_using_ytsearch_fallback", query=query)

    try:
        return await ytsearch_discover(query, max_results=MAX_CANDIDATES)
    except YouTubeError as exc:
        logger.warning("youtube.ytsearch_fallback_failed", query=query, error=str(exc))
        return []
