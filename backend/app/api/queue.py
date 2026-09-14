"""Download queue endpoints (§64/§76 of the architecture doc): list the
active queue/attempt history, manual Retry for a DOWNLOAD_FAILED asset, and
safe Cancel for a QUEUED (not-yet-started) asset. The actual pipeline lives
in app/services/acquisition.py; app/jobs/download_queue.py is what actually
runs it in the background.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pagination import Pagination, paginate, pagination_params
from app.db.models.acquisition import AttemptStatus, DownloadAttempt
from app.db.models.media import MediaAsset, MediaState
from app.db.session import get_session
from app.services.acquisition import cancel_queued, enqueue_for_download, retry_download

router = APIRouter(prefix="/api/queue", tags=["queue"])

_ACTIVE_STATES = (
    MediaState.CANDIDATE_SELECTED,
    MediaState.QUEUED,
    MediaState.DOWNLOADING,
    MediaState.PROCESSING,
    MediaState.IMPORTING,
)

# DOWNLOAD_FAILED is deliberately included in the *listing* (unlike the
# worker's own polling, which only ever claims QUEUED rows) — this is the
# Activity/Queue page's one place to see and act on a failed download via
# the Retry endpoint below; excluding it would leave Retry with nothing to
# ever be called on.
_LISTED_STATES = (*_ACTIVE_STATES, MediaState.DOWNLOAD_FAILED)


class DownloadAttemptOut(BaseModel):
    id: int
    attempt_number: int
    status: AttemptStatus
    error_class: str | None
    error_message: str | None
    next_retry_at: str | None

    @classmethod
    def from_model(cls, attempt: DownloadAttempt) -> "DownloadAttemptOut":
        return cls(
            id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            error_class=attempt.error_class,
            error_message=attempt.error_message,
            next_retry_at=attempt.next_retry_at.isoformat() if attempt.next_retry_at else None,
        )


class QueueItemOut(BaseModel):
    media_asset_id: int
    track_id: int | None
    state: MediaState
    attempts: list[DownloadAttemptOut]

    @classmethod
    async def from_model(cls, session: AsyncSession, asset: MediaAsset) -> "QueueItemOut":
        result = await session.execute(
            select(DownloadAttempt)
            .where(DownloadAttempt.media_asset_id == asset.id)
            .order_by(DownloadAttempt.attempt_number)
        )
        attempts = [DownloadAttemptOut.from_model(a) for a in result.scalars().all()]
        return cls(media_asset_id=asset.id, track_id=asset.track_id, state=asset.state, attempts=attempts)


@router.get("", response_model=list[QueueItemOut])
async def list_queue(
    page: Pagination = Depends(pagination_params), session: AsyncSession = Depends(get_session)
) -> list[QueueItemOut]:
    query = select(MediaAsset).where(MediaAsset.state.in_(_LISTED_STATES)).order_by(MediaAsset.id)
    result = await session.execute(paginate(query, page))
    return [await QueueItemOut.from_model(session, asset) for asset in result.scalars().all()]


async def _get_asset_or_404(session: AsyncSession, asset_id: int) -> MediaAsset:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"No media asset with id {asset_id}")
    return asset


@router.post("/{asset_id}/enqueue", response_model=QueueItemOut)
async def enqueue(asset_id: int, session: AsyncSession = Depends(get_session)) -> QueueItemOut:
    asset = await _get_asset_or_404(session, asset_id)
    try:
        await enqueue_for_download(session, asset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await QueueItemOut.from_model(session, asset)


@router.post("/{asset_id}/retry", response_model=QueueItemOut)
async def retry(asset_id: int, session: AsyncSession = Depends(get_session)) -> QueueItemOut:
    asset = await _get_asset_or_404(session, asset_id)
    try:
        await retry_download(session, asset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await QueueItemOut.from_model(session, asset)


@router.post("/{asset_id}/cancel", response_model=QueueItemOut)
async def cancel(asset_id: int, session: AsyncSession = Depends(get_session)) -> QueueItemOut:
    asset = await _get_asset_or_404(session, asset_id)
    try:
        await cancel_queued(session, asset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await QueueItemOut.from_model(session, asset)
