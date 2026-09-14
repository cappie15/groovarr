"""ffprobe wrapper — the only source of truth for a media file's actual
codecs/resolution/duration (§80/§81: never trust the requested format
string or the source stream's own claims, always measure the real output).
"""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app.integrations.acquisition.errors import ValidationError


@dataclass(frozen=True)
class ProbeResult:
    duration_s: float | None
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None


async def probe_media(path: Path) -> ProbeResult:
    """Raises `ValidationError` if the file is missing/empty or ffprobe
    can't parse it at all — both are validation failures, never silently
    swallowed (§51/§92)."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ValidationError(f"File missing or empty: {path}")

    args = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ValidationError(f"ffprobe could not parse {path}: {stderr.decode(errors='replace')[-500:]}")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"ffprobe returned unparseable output for {path}") from exc

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = data.get("format", {}).get("duration")

    return ProbeResult(
        duration_s=float(duration) if duration is not None else None,
        video_codec=video_stream.get("codec_name") if video_stream else None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        width=video_stream.get("width") if video_stream else None,
        height=video_stream.get("height") if video_stream else None,
    )


def resolution_label(height: int | None) -> str | None:
    """Human-readable quality label derived from an actually-measured
    height (§80) — never from the requested/expected format string. `None`
    when unknown; app.domain.naming renders an explicit "[Unknown]"
    placeholder rather than omitting the bracket.
    """
    if not height:
        return None
    for threshold, label in ((2160, "2160p"), (1440, "1440p"), (1080, "1080p"), (720, "720p"), (480, "480p")):
        if height >= threshold:
            return label
    return f"{height}p"
