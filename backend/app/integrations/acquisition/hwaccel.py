"""Hardware-accelerated transcode encoder detection.

Groovarr's software transcode path (`ffmpeg_mux.py`'s `_h264_encoder`)
prefers `libopenh264` over `libx264` but is still CPU-bound either way —
confirmed slow in live testing (minutes per track on a host with no
hardware encoder). This module detects which of the three common
hardware H.264 encoders ffmpeg can actually use RIGHT NOW on this host:

- NVENC (Nvidia): needs both the `h264_nvenc` encoder compiled into ffmpeg
  AND a reachable Nvidia GPU. `nvidia-smi` succeeding is the standard way
  to confirm a GPU + driver are actually present and working (not just
  that the ffmpeg binary knows the encoder's name) — see NVIDIA's own
  Video Codec SDK docs, which use `nvidia-smi` as the standard sanity
  check before attempting NVENC.
- VAAPI (generic Linux hwaccel — Intel and AMD both implement it): needs
  `h264_vaapi` compiled into ffmpeg AND a DRM render node
  (`/dev/dri/renderD1*`) that's actually readable/writable — this is
  ffmpeg's own documented VAAPI prerequisite (ffmpeg.org/ffmpeg-codecs.html
  §VAAPI, and the vaapi hwaccel guide on the ffmpeg wiki), independent of
  GPU vendor.
- QSV (Intel Quick Sync): a special case of the above — QSV also goes
  through a `/dev/dri` render node on Linux (via Intel's media driver), but
  unlike generic VAAPI it ONLY works on an actual Intel GPU. The correct
  way to tell an Intel render node apart from an AMD/other one without
  needing `lspci` or a GPU-specific userspace tool is to read the PCI
  vendor ID ffmpeg/DRM already expose in sysfs at
  `/sys/class/drm/<node>/device/vendor` — Intel's PCI vendor ID is the
  well-known constant `0x8086`. This is the same signal tools like
  `vainfo`/`intel_gpu_top` rely on being present before they even try to
  talk to the device.

Detection results are cached for the process lifetime (`lru_cache`) — this
intentionally does not re-probe on every transcode; hardware presence
doesn't change while the container is running, and probing shells out to
`ffmpeg`/`nvidia-smi` and touches `/dev`, which is unnecessary work to
repeat per-file.
"""

import asyncio
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

INTEL_PCI_VENDOR_ID = "0x8086"
_DRI_DIR = Path("/dev/dri")


@dataclass(frozen=True)
class HardwareAccelStatus:
    nvenc_available: bool
    qsv_available: bool
    vaapi_available: bool

    @property
    def any_available(self) -> bool:
        return self.nvenc_available or self.qsv_available or self.vaapi_available

    def best(self) -> str | None:
        """Auto-selection priority when the operator has chosen "auto":
        NVENC first (unambiguous once `nvidia-smi` succeeds — no vendor
        guessing needed), then QSV (Intel-specific, confirmed via PCI vendor
        ID), then generic VAAPI last (works for AMD too, but gives the
        least specific guarantee about encode quality/speed since "some
        VAAPI-capable device exists" is a weaker signal than confirming a
        specific vendor's encoder).
        """
        if self.nvenc_available:
            return "nvenc"
        if self.qsv_available:
            return "qsv"
        if self.vaapi_available:
            return "vaapi"
        return None


async def _ffmpeg_has_encoder(name: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-encoders",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except FileNotFoundError:  # pragma: no cover - ffmpeg always present in prod image
        return False
    return name.encode() in stdout


async def _nvidia_gpu_reachable() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        returncode = await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (FileNotFoundError, TimeoutError):
        return False
    return returncode == 0


def _render_nodes() -> list[Path]:
    if not _DRI_DIR.is_dir():
        return []
    return sorted(p for p in _DRI_DIR.glob("renderD*") if os.access(p, os.R_OK | os.W_OK))


def _render_node_is_intel(node: Path) -> bool:
    vendor_file = Path(f"/sys/class/drm/{node.name}/device/vendor")
    try:
        return vendor_file.read_text().strip().lower() == INTEL_PCI_VENDOR_ID
    except OSError:
        return False


async def detect_hardware_acceleration() -> HardwareAccelStatus:
    """Uncached probe — use `get_hardware_acceleration_status()` for the
    cached, call-site-facing version. Exposed separately so tests can call
    this directly without fighting `lru_cache`.
    """
    render_nodes = _render_nodes()
    has_render_node = bool(render_nodes)
    has_intel_render_node = any(_render_node_is_intel(n) for n in render_nodes)

    nvenc_encoder, qsv_encoder, vaapi_encoder, nvidia_reachable = await asyncio.gather(
        _ffmpeg_has_encoder("h264_nvenc"),
        _ffmpeg_has_encoder("h264_qsv"),
        _ffmpeg_has_encoder("h264_vaapi"),
        _nvidia_gpu_reachable(),
    )

    status = HardwareAccelStatus(
        nvenc_available=nvenc_encoder and nvidia_reachable,
        qsv_available=qsv_encoder and has_render_node and has_intel_render_node,
        vaapi_available=vaapi_encoder and has_render_node,
    )
    logger.info(
        "hwaccel.detected",
        nvenc=status.nvenc_available,
        qsv=status.qsv_available,
        vaapi=status.vaapi_available,
        render_nodes=[str(n) for n in render_nodes],
    )
    return status


@lru_cache(maxsize=1)
def _cached_status_holder() -> list[HardwareAccelStatus | None]:
    # A one-element mutable box so `get_hardware_acceleration_status` can
    # populate it lazily on first (async) call while still benefiting from
    # `lru_cache`'s "compute once" semantics for the box itself.
    return [None]


async def get_hardware_acceleration_status() -> HardwareAccelStatus:
    box = _cached_status_holder()
    if box[0] is None:
        box[0] = await detect_hardware_acceleration()
    return box[0]


def reset_hardware_acceleration_cache() -> None:
    """Test-only: clear the cached detection result."""
    _cached_status_holder.cache_clear()
