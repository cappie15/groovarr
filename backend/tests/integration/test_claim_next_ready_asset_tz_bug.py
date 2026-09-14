"""Regression test for a real bug found while building Phase 7: SQLite
silently drops tzinfo across a DateTime(timezone=True) round-trip, so a
freshly-reloaded `DownloadAttempt.next_retry_at` came back naive and blew up
`claim_next_ready_asset`'s comparison against a UTC-aware `now` with
`TypeError: can't compare offset-naive and offset-aware datetimes` — the
existing test suite had only ever exercised this path with `next_retry_at`
manually nulled out, never a real reloaded future/past value. Fixed via
app.core.db_time.as_aware_utc; this test exercises the real reload path
(commit + refresh) that the bug actually depended on, not just the fixed
helper function in isolation (see tests/unit/test_db_time.py for that).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.acquisition import AttemptStatus, DownloadAttempt
from app.db.models.media import MediaAsset, MediaState
from app.db.models.spotify import Track
from app.services.acquisition import claim_next_ready_asset


async def _make_asset_with_attempt(session, *, next_retry_at) -> MediaAsset:
    track = Track(
        spotify_track_id=f"tz-bug-track-{next_retry_at.isoformat()}",
        canonical_artist="A",
        canonical_title="B",
        duration_ms=1000,
    )
    session.add(track)
    await session.flush()

    asset = MediaAsset(track_id=track.id, state=MediaState.QUEUED)
    session.add(asset)
    await session.flush()

    attempt = DownloadAttempt(
        media_asset_id=asset.id,
        attempt_number=1,
        status=AttemptStatus.FAILED,
        started_at=datetime.now(UTC),
        next_retry_at=next_retry_at,
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)  # forces the real SQLite round-trip that stripped tzinfo
    assert attempt.next_retry_at.tzinfo is None  # confirms the round-trip really is naive here

    await session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_claim_does_not_raise_when_backoff_is_still_in_the_future(db_session):
    await _make_asset_with_attempt(db_session, next_retry_at=datetime.now(UTC) + timedelta(hours=1))

    claimed = await claim_next_ready_asset(db_session)

    assert claimed is None  # not due yet — and critically, no TypeError raised


@pytest.mark.asyncio
async def test_claim_succeeds_when_backoff_has_elapsed(db_session):
    asset = await _make_asset_with_attempt(db_session, next_retry_at=datetime.now(UTC) - timedelta(minutes=1))

    claimed = await claim_next_ready_asset(db_session)

    assert claimed is not None
    assert claimed.id == asset.id
    assert claimed.state == MediaState.DOWNLOADING


@pytest.mark.asyncio
async def test_claim_with_multiple_candidates_skips_not_due_and_claims_due_one(db_session):
    not_due = await _make_asset_with_attempt(db_session, next_retry_at=datetime.now(UTC) + timedelta(hours=1))
    due = await _make_asset_with_attempt(db_session, next_retry_at=datetime.now(UTC) - timedelta(minutes=1))

    claimed = await claim_next_ready_asset(db_session)

    assert claimed is not None
    assert claimed.id == due.id

    remaining = await db_session.scalar(select(MediaAsset).where(MediaAsset.id == not_due.id))
    assert remaining.state == MediaState.QUEUED  # untouched — its backoff hasn't elapsed
