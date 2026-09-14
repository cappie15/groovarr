"""Periodic "Monitor for Better Versions" pass (§36/§37), off by default
(`AppSettings.monitor_better_versions_enabled`). Mirrors
`app.jobs.spotify_scheduler`'s deliberately simple single-asyncio-task
design (§76) — this is a coarse, infrequent background sweep, not the
bounded-concurrency acquisition worker pool.

Per tick, when enabled: re-search every `Incomplete` (visualizer-class)
asset — always eligible per §37 — and every `Available` asset that does
NOT have `manual_selection=True` (a human's explicit choice is never
silently touched by monitoring, §33, regardless of this toggle). A
re-search never mutates an already-downloaded asset directly
(`app.services.search.run_search_for_track`'s own guard, §9 finding) — it
only reports the winning candidate. This job is what decides whether that
winner is worth actually applying, and if so calls
`app.services.replacement.replace_track_media`, the one safe swap mechanism
(old file stays fully intact/serving until the new one is proven good).

"Search Again" (Phase 4's endpoint) remains callable manually at any time
regardless of this toggle (§37) — that code path is untouched by this job.
"""

import asyncio

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import Track
from app.db.session import get_sessionmaker
from app.services.replacement import ReplacementError, replace_track_media
from app.services.search import run_search_for_track
from app.services.settings_service import get_app_settings

logger = structlog.get_logger(__name__)

# Deliberately coarse — this is a background quality sweep, not a
# time-sensitive operation. Real deployments will have this tick far less
# often in practice than it's checked, since it only acts when the operator
# has explicitly opted in.
_TICK_INTERVAL_S = 3600


class UpgradeMonitorScheduler:
    """Owns the single background task. `start()`/`stop()` are idempotent,
    matching `SpotifySyncScheduler`.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="upgrade-monitor-scheduler")

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
                logger.exception("upgrade_monitor.tick_failed")
            await asyncio.sleep(_TICK_INTERVAL_S)

    async def _tick(self) -> None:
        session_factory = get_sessionmaker()

        async with session_factory() as session:
            app_settings = await get_app_settings(session)
            if not app_settings.monitor_better_versions_enabled:
                return
            asset_ids = list(
                (
                    await session.scalars(
                        select(MediaAsset.id).where(
                            MediaAsset.track_id.is_not(None),
                            (MediaAsset.state == MediaState.INCOMPLETE)
                            | (
                                (MediaAsset.state == MediaState.AVAILABLE)
                                & (MediaAsset.manual_selection.is_(False))
                            ),
                        )
                    )
                ).all()
            )

        if not asset_ids:
            return

        async with httpx.AsyncClient(timeout=15.0) as http:
            for asset_id in asset_ids:
                async with session_factory() as session:
                    try:
                        await _check_one_asset(session, http, asset_id)
                    except Exception:
                        logger.exception("upgrade_monitor.asset_check_failed", media_asset_id=asset_id)


async def _check_one_asset(session: AsyncSession, http: httpx.AsyncClient, asset_id: int) -> None:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None or asset.track_id is None or asset.local_path is None:
        return  # disappeared or never actually downloaded since the tick started

    track = await session.get(Track, asset.track_id)
    if track is None:  # pragma: no cover - defensive; FK integrity should prevent this
        return

    previous_source = asset.source_reference
    outcome = await run_search_for_track(session, http, track, reopen=True)
    winner = outcome.winning_candidate
    if winner is None:
        return  # no automatic-quality candidate found this round

    new_source = f"youtube:{winner.youtube_video_id}"
    if new_source == previous_source:
        return  # the current file already *is* the best candidate found

    try:
        await replace_track_media(session, http, track.id, winner.id)
        logger.info(
            "upgrade_monitor.replaced",
            track_id=track.id,
            media_asset_id=asset_id,
            new_video_id=winner.youtube_video_id,
        )
    except ReplacementError as exc:
        logger.warning("upgrade_monitor.replace_failed", track_id=track.id, media_asset_id=asset_id, error=str(exc))
