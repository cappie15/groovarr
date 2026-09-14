"""Lyrics orchestration (§44-48, Phase 7): resolves the persisted `Lyrics`
row for one `Track`, fetching from LRCLIB only when there isn't already an
answer (a positive hit, or a still-valid negative-cache miss). Called from
the acquisition pipeline (app/services/acquisition.py) as a best-effort step
that must never fail or block a video import.
"""

from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_time import as_aware_utc
from app.db.models.lyrics import Lyrics, LyricsKind
from app.db.models.spotify import Track
from app.integrations.lyrics.lrclib import LRCLIBBackend

logger = structlog.get_logger(__name__)

# §48: how long a confirmed "not found" is trusted before LRCLIB is queried
# again for the same track — long enough that a routine Spotify re-sync
# never re-triggers it needlessly, short enough that a song LRCLIB later
# adds gets picked up within a reasonable time without any manual action.
NEGATIVE_CACHE_DAYS = 7


async def get_or_fetch_lyrics(session: AsyncSession, http: httpx.AsyncClient, track: Track) -> Lyrics | None:
    """Return the `Lyrics` row for `track`, fetching from LRCLIB if there
    isn't a still-valid answer already. Never raises: any backend failure
    (network error, unexpected response, etc.) is caught and recorded as a
    "missing" result with a negative-cache window, exactly like an honest
    not-found, so a flaky LRCLIB never becomes a hard failure for the caller
    and never gets hammered repeatedly for it either (§48).
    """
    now = datetime.now(UTC)
    existing = await session.scalar(select(Lyrics).where(Lyrics.track_id == track.id))

    if existing is not None:
        if existing.kind != LyricsKind.MISSING:
            return existing
        cache_until = as_aware_utc(existing.negative_cache_until)
        if cache_until is not None and cache_until > now:
            logger.info("lyrics.negative_cache_hit", track_id=track.id)
            return existing

    try:
        backend = LRCLIBBackend(http)
        result = await backend.fetch(
            artist=track.canonical_artist,
            title=track.canonical_title,
            album=None,
            duration_s=track.duration_ms / 1000,
        )
    except Exception as exc:  # deliberately broad — lyrics must never fail acquisition (§48)
        logger.warning("lyrics.fetch_failed", track_id=track.id, error=str(exc))
        result = None

    if existing is None:
        existing = Lyrics(track_id=track.id, kind=LyricsKind.MISSING, fetched_at=now)
        session.add(existing)

    existing.provider = "lrclib"
    existing.fetched_at = now
    if result is None:
        existing.kind = LyricsKind.MISSING
        existing.content = None
        existing.negative_cache_until = now + timedelta(days=NEGATIVE_CACHE_DAYS)
        logger.info("lyrics.not_found", track_id=track.id)
    else:
        existing.kind = LyricsKind.SYNCED if result.kind == "synced" else LyricsKind.PLAIN
        existing.content = result.content
        existing.negative_cache_until = None
        logger.info("lyrics.found", track_id=track.id, kind=existing.kind.value)

    await session.commit()
    await session.refresh(existing)
    return existing
