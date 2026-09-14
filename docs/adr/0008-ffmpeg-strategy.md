# 0008 — FFmpeg strategy: always-MP4 output, stream-copy first

Status: Accepted
Rationale: [architecture review §2 row D](../00-research-and-architecture-review.md)

## Context

Research found MP4/MKV metadata reliability across Plex/Jellyfin/VLC is starkly asymmetric in
MP4's favor (Plex won't read embedded MKV tags at all; Jellyfin's MKV support is partial). The
project owner explicitly decided output should always be MP4, even when that requires a real
transcode, prioritizing consistent metadata/artwork/lyrics-tag visibility over avoiding transcode
cost.

## Decision

`app/integrations/acquisition/ffmpeg_mux.py`'s `mux_to_mp4` always targets an MP4 container.
Per-stream, it attempts `-c:v copy`/`-c:a copy` first whenever the source codec is already
MP4-compatible, and only transcodes the incompatible stream(s) otherwise — never both
unconditionally. The H.264 encoder choice prefers `libopenh264` over `libx264`
(`_h264_encoder()`) to keep FFmpeg's LGPL license posture where possible; if the ffmpeg build only
has `libx264` available (true of this project's own CI/dev environment, which uses Debian's
packaged ffmpeg), it logs a clear warning and falls back rather than failing the transcode — see
[docs/THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the resulting honest license status
of what's actually shipped.

## Consequences

- MKV is no longer a default output anywhere in the pipeline; it remains only a theoretical manual
  override, not implemented as a user-facing toggle in v1.
- The Docker image installs Debian's `ffmpeg` package (`apt-get install ffmpeg`), which ships
  `libx264` and is GPL v2+ as distributed — a deliberate, documented departure from the
  originally-envisioned LGPL-only build, not an oversight.
- ffprobe (not the requested format string) is the source of truth for the final `resolution_label`
  and codec fields written onto `MediaAsset` — see `app/integrations/acquisition/probe.py`.
