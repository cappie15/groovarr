# 0007 — yt-dlp process isolation: embedded library, not shelled out

Status: Accepted
Rationale: [architecture review §39, §69](../00-research-and-architecture-review.md)

## Context

yt-dlp's own README explicitly recommends embedding via `from yt_dlp import YoutubeDL` over
shelling out to the CLI and parsing stdout. Constructing shell commands from
Spotify/YouTube-derived text (artist names, titles) is also a direct command-injection risk if
done via string interpolation.

## Decision

yt-dlp is embedded as a Python library everywhere it's used — `app/integrations/youtube/ytdlp_client.py`
(discovery-fallback search + per-candidate technical enrichment via `extract_info`) and
`app/integrations/acquisition/ytdlp_download.py` (the actual download). No subprocess/shell
invocation of the `yt-dlp` CLI exists anywhere in the codebase. `YoutubeDL.extract_info` is a
blocking call; every call site runs it appropriately (e.g. via a thread executor) rather than
blocking the event loop.

## Consequences

- Zero shell-injection surface for this integration — there is no argv-array vs. shell-string
  distinction to get right here, because there's no subprocess at all.
- Structured `info_dict` access (rather than stdout parsing) is what makes hard-filtering
  Shorts/portrait content reliable: `media_type == 'short'` and per-format `width`/`height` are
  read directly as Python values, not regex'd out of CLI output.
- FFmpeg (a genuine subprocess, since there's no Python-native equivalent) is invoked with argv
  arrays only — see [ADR 0008](0008-ffmpeg-strategy.md) — the one place in acquisition where the
  injection risk this ADR avoids for yt-dlp still had to be defended against directly.
