"""Writes the `.lrc` sidecar file next to an imported video — the DEFAULT/
authoritative lyrics artifact (§2-C/§3): same folder, same basename as the
video, `.lrc` extension, regardless of whether the content is truly
timestamp-synced or plain text (a `.lrc` reader that finds no `[mm:ss]`
markers in a line simply treats it as plain text — this is standard,
tolerant `.lrc`/`.elrc` reader behavior, not a Groovarr-specific hack) —
this is the one convention actually confirmed to be read by real tools
(VLC via a manual LRC->SRT conversion path aside, Jellyfin's Audio-only
Lyrics feature, and any external player/editor a user points at the media
folder), unlike embedding, which none of Groovarr's three target consumers
(Plex, VLC, Jellyfin-for-MusicVideo) reliably surface.
"""

from pathlib import Path


def sidecar_path_for(video_path: Path) -> Path:
    """The `.lrc` path Groovarr always uses for a given video path: same
    directory, same basename, `.lrc` extension — e.g.
    `Artist - Title (2020) [1080p].mp4` -> `Artist - Title (2020) [1080p].lrc`.
    """
    return video_path.with_suffix(".lrc")


def write_lrc_sidecar(video_path: Path, content: str) -> Path:
    """Write `content` to the sidecar path for `video_path` and return it.
    Raises on any filesystem error — callers in the acquisition pipeline
    must catch this themselves (a sidecar-write failure must never fail an
    otherwise-successful video import, §48 applied here too).
    """
    path = sidecar_path_for(video_path)
    path.write_text(content, encoding="utf-8")
    return path
