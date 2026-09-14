"""History endpoint (§65, Phase 10): paginated, optionally filtered by
track or media asset. Read-only — events are written only by
`app.services.history.record_event` from other services' own lifecycle
points, never from this API.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pagination import Pagination, paginate, pagination_params
from app.db.models.history import HistoryEvent
from app.db.session import get_session

router = APIRouter(prefix="/api/history", tags=["history"])


class HistoryEventOut(BaseModel):
    id: int
    track_id: int | None
    media_asset_id: int | None
    event_type: str
    detail: str | None
    occurred_at: datetime

    @classmethod
    def from_model(cls, event: HistoryEvent) -> "HistoryEventOut":
        return cls(
            id=event.id,
            track_id=event.track_id,
            media_asset_id=event.media_asset_id,
            event_type=event.event_type,
            detail=event.detail,
            occurred_at=event.occurred_at,
        )


@router.get("", response_model=list[HistoryEventOut])
async def list_history(
    track_id: int | None = None,
    media_asset_id: int | None = None,
    page: Pagination = Depends(pagination_params),
    session: AsyncSession = Depends(get_session),
) -> list[HistoryEventOut]:
    query = select(HistoryEvent).order_by(HistoryEvent.occurred_at.desc(), HistoryEvent.id.desc())
    if track_id is not None:
        query = query.where(HistoryEvent.track_id == track_id)
    if media_asset_id is not None:
        query = query.where(HistoryEvent.media_asset_id == media_asset_id)
    result = await session.execute(paginate(query, page))
    return [HistoryEventOut.from_model(e) for e in result.scalars().all()]
