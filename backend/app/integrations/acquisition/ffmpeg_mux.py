"""FFmpeg remux/transcode into the project's output container.

Default policy (`ContainerPolicy.ALWAYS_MP4`, the project owner's original
finalized decision, §2/§3 of the architecture doc): always finish in MP4,
transcoding when needed, because consistent metadata/artwork/lyrics-tag
visibility across VLC/Jellyfin/Plex is worth more than avoiding a transcode.
This is now a Settings-overridable choice (`AppSettings.container_policy`),
not hardcoded — see `mux_media` below and `app/db/models/settings.py`'s
`ContainerPolicy` docstring for what the alternative
(`PREFER_MP4_ALLOW_MKV`) trades away: falling back to MKV via pure
stream-copy instead of transcoding, at the cost of Plex/Jellyfin no longer
reliably reading the file's embedded metadata (only the `.lrc` sidecar and
Groovarr's own UI remain reliable for a file produced that way).

Stream copy (`-c copy`) is still attempted first, per stream, whenever the
source codec is already MP4-compatible — a transcode only happens for the
stream(s) that actually need it, and only under `ALWAYS_MP4`. Never
trims/crops/alters duration (§30): no `-ss`/`-t` is ever passed, the full
source duration is always encoded/copied.
"""

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.db.models.settings import ContainerPolicy, HardwareAccelPolicy
from app.integrations.acquisition.errors import MuxError
from app.integrations.acquisition.hwaccel import get_hardware_acceleration_status
from app.integrations.acquisition.probe import probe_media

logger = structlog.get_logger(__name__)

#: Per-encoder ffmpeg invocation pieces for the video leg of a transcode.
#: Each entry is (pre-input global args, video-encode args) — VAAPI needs a
#: device declared before `-i` and a format/hwupload filter on the video
#: stream (its encoder only accepts VAAPI-surface frames, not the plain
#: software frames a software-decoded VP9/AV1 source produces); NVENC and
#: QSV accept software frames directly for encode-only (no hwaccel decode)
#: use, which is what Groovarr needs here — the source is already fully
#: decoded software frames by the time ffmpeg reads it, only the ENCODE
#: side needs to be hardware, so there's no benefit to a hwaccel decode
#: path and real extra failure surface (pixel-format/device mismatches) in
#: adding one. See ffmpeg.org/ffmpeg-codecs.html §VAAPI and the ffmpeg wiki
#: Hardware/QuickSync and Hardware/VAAPI pages for these exact flag shapes.
_HW_VIDEO_ARGS: dict[str, tuple[list[str], list[str]]] = {
    "nvenc": ([], ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "19"]),
    "qsv": ([], ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "19"]),
    "vaapi": (
        ["-vaapi_device", "/dev/dri/renderD128"],
        ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", "19"],
    ),
}

# Codecs the MP4 (ISO-BMFF) muxer carries with broad real-world player
# support (§3 research) — anything else gets transcoded rather than muxed
# as-is, even though ffmpeg's mov muxer can technically write a few other
# codec/container combinations, because the point of "always MP4" is
# consistent playback/metadata behavior across VLC/Jellyfin/Plex, not just
# a technically-valid file.
MP4_COMPATIBLE_VIDEO_CODECS = {"h264", "hevc", "mpeg4", "av1"}
MP4_COMPATIBLE_AUDIO_CODECS = {"aac", "mp3", "alac"}


@dataclass(frozen=True)
class MuxResult:
    output_path: Path
    container: str  # "mp4" | "mkv" — the container actually produced.
    transcoded_video: bool
    transcoded_audio: bool


async def _ffmpeg_encoder_available(name: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-encoders",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return name.encode() in stdout


async def _h264_encoder() -> str:
    """Prefer `libopenh264` to keep FFmpeg's LGPL license posture where the
    deployed ffmpeg build supports it (§10 of the architecture doc) — fall
    back to whatever H.264 encoder is actually available rather than
    crashing, logging the choice clearly so the license tradeoff is visible
    in the running container's own logs, not just in project docs.
    """
    if await _ffmpeg_encoder_available("libopenh264"):
        return "libopenh264"
    logger.warning(
        "acquisition.libopenh264_unavailable_using_libx264",
        note="This ffmpeg build has no libopenh264 encoder; falling back to libx264, which "
        "flips this ffmpeg component's effective license to GPL v2+ for any transcoded output "
        "produced with it. See docs/00-research-and-architecture-review.md §10.",
    )
    return "libx264"


async def _resolve_hw_video_encoders(policy: HardwareAccelPolicy) -> list[str]:
    """Which hardware encoder key(s) (into `_HW_VIDEO_ARGS`) to attempt for
    this transcode, in priority order, or `[]` for software-only. `AUTO`
    defers to `hwaccel.py`'s full detected candidate list — not just its
    top choice — so that if the best-ranked encoder fails at runtime (a
    real, live-observed case: QSV detected as available via a genuine
    Intel device, but failing with an MFX session error from a missing
    userspace runtime component, while VAAPI on the same device worked),
    the next genuinely-detected candidate is tried before giving up to
    software. Behaviorally identical to `DISABLED` when nothing is
    genuinely usable, which is what makes AUTO safe as the default (see
    `HardwareAccelPolicy` docstring). A forced specific choice
    (`NVENC`/`QSV`/`VAAPI`) is attempted alone even if detection didn't
    confirm it, since an operator may know their setup works better than
    a generic heuristic — the runtime software fallback below still
    protects against it being wrong; it is not cascaded to the *other*
    hardware vendors, since the operator explicitly picked one.
    """
    if policy == HardwareAccelPolicy.DISABLED:
        return []
    if policy == HardwareAccelPolicy.AUTO:
        status = await get_hardware_acceleration_status()
        return status.ordered_candidates()
    return [policy.value]  # NVENC/QSV/VAAPI forced explicitly


async def _run_ffmpeg(args: list[str], dest_path: Path) -> tuple[bool, str]:
    """Run one ffmpeg invocation. Returns (succeeded, stderr_tail) — never
    raises, so callers can decide whether to retry with a different encoder
    rather than immediately failing the whole download on a hardware
    encoder that turns out not to actually work at runtime.
    """
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        return False, stderr.decode(errors="replace")[-2000:]
    return True, ""


async def mux_to_mp4(
    video_path: Path,
    audio_path: Path,
    dest_path: Path,
    hardware_policy: HardwareAccelPolicy = HardwareAccelPolicy.DISABLED,
) -> MuxResult:
    """Mux `video_path`'s video stream and `audio_path`'s audio stream into
    a single MP4 at `dest_path`. Raises `MuxError` (retryable) if ffmpeg
    fails.

    When a video transcode is actually needed (source not MP4-compatible),
    `hardware_policy` controls whether a hardware encoder is tried first —
    see `_resolve_hw_video_encoders`. A hardware attempt that fails at
    runtime for ANY reason falls back to the existing software path
    (`libopenh264`/`libx264`) rather than failing the whole mux: hardware
    acceleration must never turn a working software fallback into a hard
    failure.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    video_probe = await probe_media(video_path)
    audio_probe = await probe_media(audio_path)

    video_ok = (video_probe.video_codec or "").lower() in MP4_COMPATIBLE_VIDEO_CODECS
    audio_ok = (audio_probe.audio_codec or "").lower() in MP4_COMPATIBLE_AUDIO_CODECS

    base_args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    audio_args = ["-c:a", "copy"] if audio_ok else ["-c:a", "aac", "-b:a", "256k"]

    def build(pre_input: list[str], video_args: list[str]) -> list[str]:
        return (
            base_args
            + pre_input
            + ["-i", str(video_path), "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"]
            + video_args
            + audio_args
            + ["-movflags", "+faststart", str(dest_path)]
        )

    hw_candidates: list[str] = []
    if not video_ok:
        hw_candidates = await _resolve_hw_video_encoders(hardware_policy)

    for hw_key in hw_candidates:
        pre_input, video_args = _HW_VIDEO_ARGS[hw_key]
        ok, stderr_tail = await _run_ffmpeg(build(pre_input, video_args), dest_path)
        if ok:
            logger.info(
                "acquisition.mux_completed",
                dest=str(dest_path),
                container="mp4",
                transcoded_video=True,
                transcoded_audio=not audio_ok,
                hardware_encoder=hw_key,
            )
            return MuxResult(
                output_path=dest_path, container="mp4", transcoded_video=True, transcoded_audio=not audio_ok
            )
        logger.warning(
            "acquisition.hw_encode_failed_trying_next_candidate",
            hardware_encoder=hw_key,
            remaining_candidates=hw_candidates[hw_candidates.index(hw_key) + 1 :],
            stderr_tail=stderr_tail,
        )

    if hw_candidates:
        logger.warning("acquisition.all_hw_encoders_failed_falling_back_to_software", attempted=hw_candidates)

    if video_ok:
        video_args = ["-c:v", "copy"]
    else:
        encoder = await _h264_encoder()
        video_args = ["-c:v", encoder, "-preset", "medium", "-crf", "18"]

    ok, stderr_tail = await _run_ffmpeg(build([], video_args), dest_path)
    if not ok:
        raise MuxError(f"ffmpeg failed: {stderr_tail}")

    logger.info(
        "acquisition.mux_completed",
        dest=str(dest_path),
        container="mp4",
        transcoded_video=not video_ok,
        transcoded_audio=not audio_ok,
    )
    return MuxResult(
        output_path=dest_path, container="mp4", transcoded_video=not video_ok, transcoded_audio=not audio_ok
    )


async def mux_to_mkv(video_path: Path, audio_path: Path, dest_path: Path) -> MuxResult:
    """Pure stream-copy into MKV — never transcodes. Only used by
    `mux_media` under `ContainerPolicy.PREFER_MP4_ALLOW_MKV`, and only when
    the source codec pair isn't MP4-compatible (that's the whole point of
    this path: avoid the transcode `mux_to_mp4` would otherwise perform).
    Raises `MuxError` (retryable) if ffmpeg fails, same as `mux_to_mp4`.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        str(dest_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        raise MuxError(f"ffmpeg exited {proc.returncode}: {stderr.decode(errors='replace')[-2000:]}")

    logger.info("acquisition.mux_completed", dest=str(dest_path), container="mkv")
    return MuxResult(output_path=dest_path, container="mkv", transcoded_video=False, transcoded_audio=False)


async def mux_media(
    video_path: Path,
    audio_path: Path,
    work_dir: Path,
    policy: ContainerPolicy,
    hardware_policy: HardwareAccelPolicy = HardwareAccelPolicy.DISABLED,
) -> MuxResult:
    """Dispatch to the right muxing strategy for `policy`
    (`AppSettings.container_policy`, §2 row D — now Settings-overridable,
    default unchanged). Always tries a stream-copy into MP4 first regardless
    of policy; `policy` only controls what happens when the source codec
    pair ISN'T natively MP4-compatible: `ALWAYS_MP4` transcodes (today's
    original behavior, unchanged), `PREFER_MP4_ALLOW_MKV` falls back to a
    no-transcode MKV stream-copy instead — `hardware_policy` is therefore
    only ever relevant under `ALWAYS_MP4`, since the MKV path never
    transcodes at all.
    """
    if policy == ContainerPolicy.ALWAYS_MP4:
        return await mux_to_mp4(video_path, audio_path, work_dir / "muxed.mp4", hardware_policy)

    video_probe = await probe_media(video_path)
    audio_probe = await probe_media(audio_path)
    video_ok = (video_probe.video_codec or "").lower() in MP4_COMPATIBLE_VIDEO_CODECS
    audio_ok = (audio_probe.audio_codec or "").lower() in MP4_COMPATIBLE_AUDIO_CODECS
    if video_ok and audio_ok:
        return await mux_to_mp4(video_path, audio_path, work_dir / "muxed.mp4")
    return await mux_to_mkv(video_path, audio_path, work_dir / "muxed.mkv")


def cleanup_paths(*paths: Path) -> None:
    """Best-effort cleanup of working-area artifacts (§51) — never raises,
    since a cleanup failure must not mask the real outcome of a download
    attempt.
    """
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort only
            logger.warning("acquisition.cleanup_failed", path=str(path))
