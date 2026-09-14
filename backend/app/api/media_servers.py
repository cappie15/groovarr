"""Connectivity/introspection endpoints for Jellyfin and Plex (§53/§54:
connection test, Jellyfin user list for the required "target user" Settings
field, Plex library-section list for the "no Music Videos type, pick a Music
or Other Videos section" Settings picker). Config get/set lives in
app/api/settings.py; this router is read-only probing against whatever is
currently configured.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_http_client
from app.db.session import get_session
from app.integrations.jellyfin.client import JellyfinClient
from app.integrations.jellyfin.errors import JellyfinError
from app.integrations.plex.client import PlexClient
from app.integrations.plex.errors import PlexError
from app.services.settings_service import get_jellyfin_connection, get_plex_credentials

jellyfin_router = APIRouter(prefix="/api/jellyfin", tags=["jellyfin"])
plex_router = APIRouter(prefix="/api/plex", tags=["plex"])


class JellyfinUserOut(BaseModel):
    id: str
    name: str


@jellyfin_router.post("/test")
async def test_jellyfin_connection(
    session: AsyncSession = Depends(get_session), http: httpx.AsyncClient = Depends(get_http_client)
) -> dict[str, bool]:
    try:
        conn = await get_jellyfin_connection(session)
        client = JellyfinClient(http, conn.url, conn.api_key)
        await client.test_connection()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JellyfinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@jellyfin_router.get("/users", response_model=list[JellyfinUserOut])
async def list_jellyfin_users(
    session: AsyncSession = Depends(get_session), http: httpx.AsyncClient = Depends(get_http_client)
) -> list[JellyfinUserOut]:
    """Populates the "Jellyfin user" picker in Settings — required for
    playlist mutations (§2 row F). Only needs url + api key to be configured,
    not a user id yet (that's what this endpoint is for).
    """
    try:
        conn = await get_jellyfin_connection(session)
        client = JellyfinClient(http, conn.url, conn.api_key)
        users = await client.list_users()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JellyfinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [JellyfinUserOut(id=u.id, name=u.name) for u in users]


class PlexLibrarySectionOut(BaseModel):
    key: str
    title: str
    type: str


@plex_router.post("/test")
async def test_plex_connection(
    session: AsyncSession = Depends(get_session), http: httpx.AsyncClient = Depends(get_http_client)
) -> dict[str, bool]:
    try:
        creds = await get_plex_credentials(session)
        client = PlexClient(http, creds.url, creds.token)
        await client.test_connection()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@plex_router.get("/library-sections", response_model=list[PlexLibrarySectionOut])
async def list_plex_library_sections(
    session: AsyncSession = Depends(get_session), http: httpx.AsyncClient = Depends(get_http_client)
) -> list[PlexLibrarySectionOut]:
    try:
        creds = await get_plex_credentials(session)
        client = PlexClient(http, creds.url, creds.token)
        sections = await client.list_library_sections()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [PlexLibrarySectionOut(key=s.key, title=s.title, type=s.type) for s in sections]
