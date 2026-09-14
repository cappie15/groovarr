"""Connect / notification-connection CRUD (Sonarr/Radarr-style): list,
create, update, delete, and test a `NotificationConnection` — see
`app/db/models/notification.py` for the schema and full rationale and
`app/services/notifications.py` for the framework this is a thin HTTP layer
over. Config get/set for the underlying Jellyfin/Plex *servers* themselves
still lives in app/api/settings.py — a connection here just says "notify
this already-configured server on these events," it doesn't configure the
server.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_http_client
from app.db.models.notification import NotificationConnection, NotificationProvider
from app.db.session import get_session
from app.integrations.jellyfin.errors import JellyfinError
from app.integrations.plex.errors import PlexError
from app.services import notifications as notifications_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationEventTypeOut(BaseModel):
    event_type: str
    label: str


class NotificationConnectionOut(BaseModel):
    id: int
    name: str
    provider: NotificationProvider
    enabled: bool
    event_types: list[str]

    @classmethod
    def from_model(cls, row: NotificationConnection) -> "NotificationConnectionOut":
        return cls(
            id=row.id,
            name=row.name,
            provider=row.provider,
            enabled=row.enabled,
            event_types=[e.event_type for e in row.events],
        )


class NotificationConnectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider: NotificationProvider
    enabled: bool = True
    event_types: list[str] = Field(default_factory=list)


class NotificationConnectionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    event_types: list[str] | None = None


@router.get("/event-types", response_model=list[NotificationEventTypeOut])
async def list_event_types() -> list[NotificationEventTypeOut]:
    """Every subscribable event type — the real vocabulary already used by
    `app.services.history.record_event`, not a parallel taxonomy. Powers the
    Connect page's per-connection event checkboxes."""
    return [
        NotificationEventTypeOut(event_type=event_type, label=label)
        for event_type, label in notifications_service.NOTIFICATION_EVENT_TYPES.items()
    ]


@router.get("", response_model=list[NotificationConnectionOut])
async def list_connections(session: AsyncSession = Depends(get_session)) -> list[NotificationConnectionOut]:
    rows = await notifications_service.list_connections(session)
    return [NotificationConnectionOut.from_model(r) for r in rows]


@router.post("", response_model=NotificationConnectionOut, status_code=201)
async def create_connection(
    body: NotificationConnectionCreateRequest, session: AsyncSession = Depends(get_session)
) -> NotificationConnectionOut:
    try:
        row = await notifications_service.create_connection(
            session,
            name=body.name,
            provider=body.provider,
            enabled=body.enabled,
            event_types=body.event_types,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NotificationConnectionOut.from_model(row)


@router.put("/{connection_id}", response_model=NotificationConnectionOut)
async def update_connection(
    connection_id: int,
    body: NotificationConnectionUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> NotificationConnectionOut:
    try:
        row = await notifications_service.update_connection(
            session,
            connection_id,
            name=body.name,
            enabled=body.enabled,
            event_types=body.event_types,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NotificationConnectionOut.from_model(row)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(connection_id: int, session: AsyncSession = Depends(get_session)) -> None:
    try:
        await notifications_service.delete_connection(session, connection_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{connection_id}/test")
async def test_connection(
    connection_id: int,
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, bool]:
    try:
        await notifications_service.test_connection(session, http, connection_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (JellyfinError, PlexError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}
