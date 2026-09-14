"""Bounded-concurrency download queue (§76: SQLite-backed, no Redis/Celery;
§49: bounded concurrency, default 2 simultaneous downloads).

A single background poller task claims QUEUED `MediaAsset` rows and hands
each to its own worker coroutine, bounded by `AppSettings.
max_concurrent_downloads` via a simple running-task-count check rather than
a fixed-size `asyncio.Semaphore`, since the limit is a runtime-configurable
Settings value, not a constant. `app.services.acquisition.process_media_asset`
does the actual work per asset; this module only owns claiming/scheduling
and crash-recovery reconciliation on startup (§78).
"""

import asyncio

import structlog

from app.db.models.media import MediaAsset
from app.db.session import get_sessionmaker
from app.services.acquisition import claim_next_ready_asset, process_media_asset, reconcile_interrupted_assets
from app.services.replacement import reconcile_orphaned_replacement_attempts
from app.services.settings_service import get_app_settings

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_S = 5


class DownloadQueue:
    """Owns the poller task and the set of currently-running per-asset
    worker tasks. `start()`/`stop()` are idempotent so the FastAPI lifespan
    can call them without extra bookkeeping — mirrors SpotifySyncScheduler.
    """

    def __init__(self, *, poll_interval_s: float = _POLL_INTERVAL_S) -> None:
        self._poller_task: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()
        self._poll_interval_s = poll_interval_s

    def start(self) -> None:
        if self._poller_task is None:
            self._poller_task = asyncio.create_task(self._run(), name="download-queue-poller")

    async def stop(self) -> None:
        if self._poller_task is not None:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
            self._poller_task = None
        if self._workers:
            for task in list(self._workers):
                task.cancel()
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()

    async def _run(self) -> None:
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            await reconcile_interrupted_assets(session)
            # Phase 10 hardening: also clean up any replacement attempt left
            # dangling by a crash between "new file imported" and
            # "references swapped" (§78) — see
            # app.services.replacement's module docstring.
            await reconcile_orphaned_replacement_attempts(session)

        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("download_queue.tick_failed")
            await asyncio.sleep(self._poll_interval_s)

    async def _tick(self) -> None:
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            max_concurrent = (await get_app_settings(session)).max_concurrent_downloads

        self._workers = {t for t in self._workers if not t.done()}
        slots_free = max_concurrent - len(self._workers)

        for _ in range(max(slots_free, 0)):
            async with session_factory() as session:
                asset = await claim_next_ready_asset(session)
                asset_id = asset.id if asset is not None else None
            if asset_id is None:
                break
            task = asyncio.create_task(self._run_one(asset_id), name=f"download-{asset_id}")
            self._workers.add(task)

    async def _run_one(self, media_asset_id: int) -> None:
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            asset = await session.get(MediaAsset, media_asset_id)
            if asset is None:  # pragma: no cover - defensive; FK integrity should prevent this
                return
            try:
                await process_media_asset(session, asset)
            except Exception:
                logger.exception("download_queue.worker_crashed", media_asset_id=media_asset_id)
