"""yt-dlp as a library (never shelled out — §39/§69: argument arrays only,
and embedding avoids any subprocess/shell surface at all) for two distinct
jobs in this phase:

1. Technical enrichment (`enrich_candidate`): duration, `media_type`
   ('short'/'video'/'livestream'), and best-format width/height for every
   discovered candidate — this is what populates the hard-filter check
   (§31) and VideoCandidate.duration_s/orientation. Used regardless of
   which discovery path found the candidate.
2. Fallback discovery (`ytsearch_discover`): only used when the YouTube
   Data API has no key configured or its call fails/quota-exhausts (§2-G) —
   see app/integrations/youtube/discovery.py.

`YoutubeDL.extract_info` is a blocking call; every entry point here runs it
via `asyncio.to_thread` so it never blocks the event loop.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
from yt_dlp import YoutubeDL

from app.integrations.youtube.data_api import RawCandidate
from app.integrations.youtube.errors import YouTubeError

logger = structlog.get_logger(__name__)

_COMMON_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
}


@dataclass(frozen=True)
class EnrichedInfo:
    """Technical details yt-dlp can determine without downloading anything."""

    duration_s: float | None
    media_type: str  # 'short' | 'video' | 'livestream'
    width: int | None
    height: int | None

    @property
    def is_portrait(self) -> bool:
        return bool(self.width and self.height and self.height > self.width)

    @property
    def is_short(self) -> bool:
        return self.media_type == "short"

    @property
    def orientation(self) -> str:
        if self.is_short:
            return "short"
        if self.is_portrait:
            return "portrait"
        return "landscape"


def _extract_info_sync(video_id: str) -> dict[str, Any]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    with YoutubeDL({**_COMMON_OPTS}) as ydl:
        info: dict[str, Any] = ydl.extract_info(url, download=False)
        return info


async def enrich_candidate(video_id: str) -> EnrichedInfo:
    """Fetch duration/orientation for one candidate. Raises `YouTubeError`
    on failure — callers (app/services/search.py) should treat a single
    candidate's enrichment failure as "drop this one candidate", never as a
    reason to fail the whole search (yt-dlp's own philosophy per §3
    research: degrade gracefully rather than crash).
    """
    try:
        info = await asyncio.to_thread(_extract_info_sync, video_id)
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many distinct exception types
        raise YouTubeError(f"yt-dlp could not fetch technical info for video {video_id!r}: {exc}") from exc

    media_type = info.get("media_type") or ("short" if _looks_like_short_url(info) else "video")
    duration = info.get("duration")
    dimensions = _best_format_dimensions(info)

    return EnrichedInfo(
        duration_s=float(duration) if duration is not None else None,
        media_type=media_type,
        width=dimensions[0] if dimensions else info.get("width"),
        height=dimensions[1] if dimensions else info.get("height"),
    )


def _looks_like_short_url(info: dict[str, Any]) -> bool:
    # Cross-check per §3 research, for cases where `media_type` isn't
    # populated by the extractor version in use.
    return "/shorts/" in (info.get("webpage_url") or "")


def _best_format_dimensions(info: dict[str, Any]) -> tuple[int, int] | None:
    formats = info.get("formats") or []
    video_formats = [
        f for f in formats if f.get("vcodec") not in (None, "none") and f.get("width") and f.get("height")
    ]
    if not video_formats:
        return None
    best = max(video_formats, key=lambda f: (f.get("height") or 0) * (f.get("width") or 0))
    return best["width"], best["height"]


def _search_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    opts = {**_COMMON_OPTS, "extract_flat": True}
    with YoutubeDL(opts) as ydl:
        result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    return list(result.get("entries") or []) if result else []


async def ytsearch_discover(query: str, *, max_results: int = 10) -> list[RawCandidate]:
    """Fallback discovery via yt-dlp's own `ytsearch:` pseudo-search — never
    the default (§2-G), only used when the YouTube Data API is unavailable.
    """
    try:
        entries = await asyncio.to_thread(_search_sync, query, max_results)
    except Exception as exc:  # noqa: BLE001
        raise YouTubeError(f"yt-dlp ytsearch fallback failed for query {query!r}: {exc}") from exc

    candidates: list[RawCandidate] = []
    for entry in entries:
        video_id = entry.get("id")
        if not video_id:
            continue
        candidates.append(
            RawCandidate(
                youtube_video_id=video_id,
                title=entry.get("title", ""),
                channel_id=entry.get("channel_id"),
                channel_name=entry.get("channel") or entry.get("uploader"),
            )
        )
    return candidates
