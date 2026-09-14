"""Bounded-concurrency + crash-recovery tests for app/jobs/download_queue.py
(§49/§76/§78). `process_media_asset` is replaced with a controllable fake
here — this module tests scheduling/concurrency/reconciliation, not the
acquisition pipeline itself (see test_acquisition.py for that).
"""

import asyncio

import pytest
from sqlalchemy import select

from app.db.models.acquisition import AttemptStatus, DownloadAttempt
from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import Track
from app.jobs.download_queue import DownloadQueue
from app.services.acquisition import reconcile_interrupted_assets
from app.services.settings_service import get_app_settings


async def _make_candidate_selected_asset(session, *, label: str) -> MediaAsset:
    track = Track(spotify_track_id=f"t-{label}", canonical_artist=label, canonical_title=label, duration_ms=2000)
    session.add(track)
    await session.flush()
    candidate = VideoCandidate(
        track_id=track.id, youtube_video_id=f"v-{label}", title=label, score=90, score_breakdown=[], rejection_flags=[]
    )
    session.add(candidate)
    await session.flush()
    asset = MediaAsset(track_id=track.id, state=MediaState.CANDIDATE_SELECTED, video_candidate_id=candidate.id)
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_bounded_concurrency_never_exceeds_configured_limit(db_session, monkeypatch):
    app_settings = await get_app_settings(db_session)
    app_settings.max_concurrent_downloads = 2
    await db_session.commit()

    assets = [await _make_candidate_selected_asset(db_session, label=f"track{i}") for i in range(5)]

    current = 0
    max_seen = 0
    completed: list[int] = []
    lock = asyncio.Lock()

    async def fake_process(session, asset):
        nonlocal current, max_seen
        async with lock:
            current += 1
            max_seen = max(max_seen, current)
        await asyncio.sleep(0.1)
        asset.state = MediaState.AVAILABLE
        await session.commit()
        async with lock:
            current -= 1
            completed.append(asset.id)

    monkeypatch.setattr("app.jobs.download_queue.process_media_asset", fake_process)

    queue = DownloadQueue(poll_interval_s=0.05)
    queue.start()
    try:
        # 5 assets, 2 at a time, ~0.1s each => needs a handful of poll ticks
        # to drain; give it generous headroom well short of a flaky test.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if len(completed) == len(assets):
                break
    finally:
        await queue.stop()

    assert max_seen <= 2, f"observed {max_seen} concurrent downloads, expected at most 2"
    assert len(completed) == len(assets), "not all assets were eventually processed"

    for asset in assets:
        await db_session.refresh(asset)
        assert asset.state == MediaState.AVAILABLE


@pytest.mark.asyncio
async def test_reconcile_interrupted_assets_requeues_stuck_media_and_fails_the_dangling_attempt(db_session):
    """Simulates a container restart mid-download (§78): a MediaAsset left
    in DOWNLOADING with a RUNNING DownloadAttempt row must be requeued
    cleanly on the next startup, with the dangling attempt marked failed
    rather than left RUNNING forever.
    """
    from datetime import UTC, datetime

    asset = await _make_candidate_selected_asset(db_session, label="stuck")
    asset.state = MediaState.DOWNLOADING
    await db_session.commit()

    attempt = DownloadAttempt(
        media_asset_id=asset.id, attempt_number=1, status=AttemptStatus.RUNNING, started_at=datetime.now(UTC)
    )
    db_session.add(attempt)
    await db_session.commit()

    reconciled_count = await reconcile_interrupted_assets(db_session)
    assert reconciled_count == 1

    await db_session.refresh(asset)
    assert asset.state == MediaState.QUEUED

    refreshed_attempt = (
        await db_session.execute(select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id))
    ).scalar_one()
    assert refreshed_attempt.status == AttemptStatus.FAILED
    assert refreshed_attempt.error_class == "Interrupted"


@pytest.mark.asyncio
async def test_reconcile_is_idempotent_when_nothing_is_stuck(db_session):
    await _make_candidate_selected_asset(db_session, label="fine")
    assert await reconcile_interrupted_assets(db_session) == 0
