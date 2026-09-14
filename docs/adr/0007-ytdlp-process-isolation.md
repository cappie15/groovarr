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

## Update: yt-dlp PO-Token Provider (live-testing addition)

Live testing hit a real `HTTP Error 403: Forbidden` from YouTube — consistent anti-bot behavior,
not a Groovarr bug. yt-dlp's own documented, non-evasive mitigation is its PO Token Provider
Framework; `bgutil-ytdlp-pot-provider` (GPL-3.0,
[github.com/Brainicism/bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider))
is the community-maintained implementation it points to. This reintroduces a real subprocess (a
small Node.js HTTP server, `docker/entrypoint.sh`), but it does not reopen the injection risk
this ADR avoids: its argv is fixed constants (`node build/main.js --host 127.0.0.1 --port 4416`)
— no Spotify/YouTube-derived text ever reaches it. The Python side needs zero code changes: the
`bgutil-ytdlp-pot-provider` PyPI package is a yt-dlp plugin, auto-discovered once importable, and
its default target (`http://127.0.0.1:4416`) is exactly where the bundled server listens — this
is confirmed directly from the plugin's own source
(`getpot_bgutil_http.py`'s `DEFAULT_BASE_URL`), not assumed. If the server isn't running or
isn't reachable for any reason, the plugin catches the transport error, logs one warning, and
raises `PoTokenProviderRejectedRequest` — yt-dlp's provider framework treats that exactly like
"no provider available," which is already yt-dlp's default (pre-existing, unaffected) behavior:
it drops PO-token-gated formats and proceeds with whatever remains extractable, never a hard
failure. Bound to `127.0.0.1` only, per the upstream project's own security notice — the server
has no authentication of its own.
