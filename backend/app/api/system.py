"""System-capability introspection — distinct from `/health` (a cheap
liveness probe): this exposes what Groovarr detected about the HOST
(hardware transcode acceleration) and about its own operational state
(YouTube Data API quota usage, yt-dlp version currency), so System/Status
can show an operator real numbers instead of leaving them to guess (§88).
"""

from datetime import datetime

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_http_client
from app.db.session import get_session
from app.integrations.acquisition.hwaccel import get_hardware_acceleration_status
from app.integrations.acquisition.ytdlp_version import get_ytdlp_version_status
from app.services.settings_service import get_youtube_search_quota_status

router = APIRouter(prefix="/api/system", tags=["system"])


class HardwareAccelStatusOut(BaseModel):
    nvenc_available: bool
    qsv_available: bool
    vaapi_available: bool
    best: str | None


@router.get("/hardware-acceleration", response_model=HardwareAccelStatusOut)
async def read_hardware_acceleration_status() -> HardwareAccelStatusOut:
    status = await get_hardware_acceleration_status()
    return HardwareAccelStatusOut(
        nvenc_available=status.nvenc_available,
        qsv_available=status.qsv_available,
        vaapi_available=status.vaapi_available,
        best=status.best(),
    )


class YouTubeQuotaStatusOut(BaseModel):
    date: str
    search_calls_today: int
    quota_units_used_today: int
    quota_units_default_daily: int
    estimated_daily_search_limit: int


@router.get("/youtube-quota", response_model=YouTubeQuotaStatusOut)
async def read_youtube_quota_status(session: AsyncSession = Depends(get_session)) -> YouTubeQuotaStatusOut:
    """How much of the YouTube Data API v3 daily quota Groovarr itself has
    used today (§88/§2-G) — an estimate Groovarr derives from its own count
    of `search.list` calls, since Google's API has no "remaining quota"
    endpoint to read this from directly. Only the real Data API discovery
    path increments the counter; the `ytsearch:` fallback has no quota cost
    and is never counted.
    """
    status = await get_youtube_search_quota_status(session)
    return YouTubeQuotaStatusOut(
        date=status.date,
        search_calls_today=status.search_calls_today,
        quota_units_used_today=status.quota_units_used_today,
        quota_units_default_daily=status.quota_units_default_daily,
        estimated_daily_search_limit=status.estimated_daily_search_limit,
    )


class YtdlpVersionStatusOut(BaseModel):
    installed_version: str
    latest_version: str | None
    update_available: bool
    checked_at: datetime | None
    check_error: str | None


@router.get("/ytdlp-version", response_model=YtdlpVersionStatusOut)
async def read_ytdlp_version_status(http: httpx.AsyncClient = Depends(get_http_client)) -> YtdlpVersionStatusOut:
    """Compares the installed yt-dlp build against the latest GitHub release
    (§88 — a gap the frontend System page previously called out by name).
    Purely informational: never attempts to update anything. The GitHub
    lookup is cached in-process for 24h (see ytdlp_version.CACHE_TTL), so
    this is typically a cache hit, not a live network call.
    """
    status = await get_ytdlp_version_status(http)
    return YtdlpVersionStatusOut(
        installed_version=status.installed_version,
        latest_version=status.latest_version,
        update_available=status.update_available,
        checked_at=status.checked_at,
        check_error=status.check_error,
    )
