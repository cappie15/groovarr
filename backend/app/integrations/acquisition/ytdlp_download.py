"""yt-dlp acquisition: download the best available video-only and
audio-only streams SEPARATELY (never yt-dlp's own `+`-combinator auto-merge)
so app.integrations.acquisition.ffmpeg_mux has full control over the
container/codec decisions per the project's finalized "always MP4" policy
(§2/§3 of the architecture doc; original spec §40 — download best video,
download best audio, mux with FFmpeg ourselves).

Embedded as a library, never shelled out (§39/§69) — there is no subprocess
argument-construction surface here at all, only structured yt-dlp options.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.integrations.acquisition.errors import DownloadError, NonRetryableDownloadError

_COMMON_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "retries": 3,
}

# A small, explicit set of phrases that indicate a *permanent* failure (the
# video is gone/blocked, not a transient network/extraction hiccup). yt-dlp
# raises one exception type (DownloadError) for both cases with no separate
# subclass, so message text is the only signal available without parsing
# yt-dlp's internal error taxonomy (§3 research). Everything else defaults
# to retryable, matching yt-dlp's own stance of degrading/retrying rather
# than hard-failing wherever possible.
_PERMANENT_FAILURE_MARKERS = (
    "video unavailable",
    "private video",
    "removed by the uploader",
    "account associated with this video",
    "this video is no longer available",
)


@dataclass(frozen=True)
class DownloadedStreams:
    video_path: Path
    audio_path: Path


def _download_format_sync(video_id: str, fmt: str, dest_dir: Path, label: str) -> Path:
    outtmpl = str(dest_dir / f"{label}.%(ext)s")
    opts = {**_COMMON_OPTS, "format": fmt, "outtmpl": outtmpl}
    url = f"https://www.youtube.com/watch?v={video_id}"
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
    path = Path(filename)
    if not path.is_file() or path.stat().st_size == 0:
        raise DownloadError(f"yt-dlp reported success but no usable file exists at {path}")
    return path


async def download_streams(youtube_video_id: str, dest_dir: Path) -> DownloadedStreams:
    """Download best-video and best-audio streams for `youtube_video_id`
    into `dest_dir` (a per-attempt scratch directory under DOWNLOADS_DIR —
    never MEDIA_DIR directly, §52). `bestvideo/best` and `bestaudio/best`
    each fall back to a combined progressive format if no video-only/
    audio-only format exists for this particular video — ffmpeg_mux's
    two-input mapping still produces the correct result even if both
    fallbacks resolve to the very same underlying combined format.

    Raises `DownloadError` (retryable) or `NonRetryableDownloadError`.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        video_path = await asyncio.to_thread(
            _download_format_sync, youtube_video_id, "bestvideo/best", dest_dir, "video"
        )
        audio_path = await asyncio.to_thread(
            _download_format_sync, youtube_video_id, "bestaudio/best", dest_dir, "audio"
        )
    except YtDlpDownloadError as exc:
        message = str(exc)
        if any(marker in message.lower() for marker in _PERMANENT_FAILURE_MARKERS):
            raise NonRetryableDownloadError(message) from exc
        raise DownloadError(message) from exc
    except (DownloadError, NonRetryableDownloadError):
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many distinct exception types
        raise DownloadError(f"yt-dlp download failed: {exc}") from exc

    return DownloadedStreams(video_path=video_path, audio_path=audio_path)
