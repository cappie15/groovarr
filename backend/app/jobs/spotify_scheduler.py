"""Minimal periodic scheduler for Spotify playlist sync.

This is deliberately simple: one asyncio background task, started/stopped
with the FastAPI app lifespan, that wakes up on a coarse tick and syncs
whatever playlists are due. It is *not* the bounded-concurrency job/worker
system described for acquisition (§76) — that's built in Phase 5, once there
are actual downloads to queue. Spotify syncs are infrequent (default 24h)
and cheap enough that a simple sequential loop is the right amount of
engineering for this phase.
"""

import asyncio
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy import select

from app.db.models.spotify import SpotifyPlaylist
from app.db.session import get_sessionmaker
from app.services.spotify_sync import sync_playlist

logger = structlog.get_logger(__name__)

# Coarse enough not to hammer anything; fine enough that even the shortest
# configurable sync interval is honored within minutes of coming due.
_TICK_INTERVAL_S = 300


class SpotifySyncScheduler:
    """Owns the single background task. `start()`/`stop()` are idempotent so
    the FastAPI lifespan can call them without extra bookkeeping.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="spotify-sync-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("spotify_scheduler.tick_failed")
            await asyncio.sleep(_TICK_INTERVAL_S)

    async def _tick(self) -> None:
        session_factory = get_sessionmaker()

        async with session_factory() as session:
            now = datetime.now(UTC)
            result = await session.execute(
                select(SpotifyPlaylist.id).where(
                    SpotifyPlaylist.connected.is_(True),
                    SpotifyPlaylist.finalized_at.is_(None),
                    (SpotifyPlaylist.next_sync_at.is_(None)) | (SpotifyPlaylist.next_sync_at <= now),
                )
            )
            due_playlist_ids = list(result.scalars().all())

        if not due_playlist_ids:
            return

        async with httpx.AsyncClient(timeout=15.0) as http:
            for playlist_id in due_playlist_ids:
                async with session_factory() as session:
                    playlist = await session.get(SpotifyPlaylist, playlist_id)
                    if playlist is None or not playlist.connected or playlist.finalized_at is not None:
                        continue
                    try:
                        await sync_playlist(session, http, playlist)
                    except Exception:
                        logger.exception(
                            "spotify_scheduler.playlist_sync_failed",
                            spotify_id=playlist.spotify_id,
                        )
