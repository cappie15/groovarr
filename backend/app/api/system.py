"""System-capability introspection — distinct from `/health` (a cheap
liveness probe): this exposes what Groovarr detected about the HOST
(hardware transcode acceleration), so the Settings UI can show an operator
what's actually available next to the `hardware_acceleration` choice
instead of leaving them to guess.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.integrations.acquisition.hwaccel import get_hardware_acceleration_status

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
