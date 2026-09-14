"""Tiny real media fixtures generated via ffmpeg at test time — used by the
acquisition pipeline tests to exercise real ffmpeg remux/transcode/ffprobe
logic (never faked), while the yt-dlp *download* itself stays mocked (no
network access, §92). Not a test module itself — no `test_` prefix.
"""

import subprocess
from pathlib import Path


def make_h264_aac_video(path: Path, *, duration_s: int = 2) -> None:
    """An MP4-compatible video-only-plus-audio clip? No — this makes a
    single self-contained clip with H.264 video + AAC copy-compatible
    streams, used as the "video" half of a mux test where stream-copy
    should be chosen (no transcode needed).
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={duration_s}",
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_vp9_video(path: Path, *, duration_s: int = 2) -> None:
    """A video-only clip in a codec the MP4 muxer does not treat as
    natively compatible (§3 research) — forces the transcode path."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=red:s=64x64:d={duration_s}",
            "-an",
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_aac_audio(path: Path, *, duration_s: int = 2) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(duration_s),
            "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_opus_audio(path: Path, *, duration_s: int = 2) -> None:
    """A codec the MP4 muxer does not treat as natively compatible — forces
    the audio transcode path."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", str(duration_s),
            "-c:a", "libopus",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_muxed_mp4(path: Path, *, duration_s: int = 2) -> None:
    """A single, already-muxed, MP4-compatible (H.264 + AAC) file — used by
    the Phase 6 tagging tests, which don't need to exercise the mux step
    itself, just a real file to write/read real mutagen tags against.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=green:s=64x64:d={duration_s}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(duration_s),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_truncated_garbage(path: Path) -> None:
    """Not valid media at all — used to prove ffprobe-based validation
    rejects a corrupt/truncated download rather than importing it (§51)."""
    path.write_bytes(b"not a real media file" * 10)
