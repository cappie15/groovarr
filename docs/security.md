# Security review (Phase 10 hardening audit)

This is an audit of the actual code as of Phase 10, not a restatement of intent — each
item below was checked against the real source, not assumed correct from the architecture
doc's own design intent (see `docs/00-research-and-architecture-review.md` §9/§66-71 for
that original intent).

## Process invocation

**Checked:** every external process call (`ffmpeg`/`ffprobe` in
`app/integrations/acquisition/{ffmpeg_mux,probe}.py`, yt-dlp in
`app/integrations/acquisition/ytdlp_download.py` and
`app/integrations/youtube/ytdlp_client.py`).

**Result: correct.** All process invocation goes through
`asyncio.create_subprocess_exec` with argument arrays — there is no
`shell=True`, `os.system`, or shell-string interpolation anywhere in the
codebase (verified by grep across `app/`). yt-dlp itself is embedded as a
Python library (`from yt_dlp import YoutubeDL`), not shelled out at all, per
the architecture doc's own recommendation — this removes an entire class of
injection surface rather than just mitigating it.

## Filesystem safety (path traversal / symlink escape / media-root containment)

**Checked:** every code path that produces or accepts a filesystem path
derived (even indirectly) from Spotify/YouTube text: the naming engine
(`app/domain/naming.py`), Organize/Rename (`app/services/organize.py`),
atomic import (`app/services/acquisition.py::_import_asset`), the manual
replacement workflow (`app/services/replacement.py`, which reuses
`run_pipeline_core`/`_import_asset` rather than reimplementing import), the
`.lrc` sidecar writer (`app/integrations/lyrics/sidecar.py`), and the delete
routine (`app/services/deletion.py`).

**Result: correct, with real defense in depth.** `app.domain.naming.render_filename`
sanitizes at the source (strips path separators, null bytes, reserved
device names, caps length, preserves meaningful Unicode). Every one of the
call sites above *also* independently re-resolves the final path and checks
it is a descendant of the configured `MEDIA_DIR`/`DOWNLOADS_DIR` root before
touching disk — `organize.py` and `deletion.py` both do this explicitly even
though `render_filename` should already make an escape unreachable, exactly
the "a destructive operation deserves more than one layer" posture §69/§70
ask for. `app.domain.atomic_move.atomic_move` is the single shared
move-with-fallback implementation used by both Organize/Rename and the
acquisition pipeline's import step, so this logic exists in one place, not
several that could drift apart.

## Secrets

**Checked:** Spotify client secret + optional PKCE refresh token
(`app/core/secrets.py`, used by `app/services/spotify_sync.py`), Jellyfin
API key and Plex token (same Fernet helper, used by
`app/services/settings_service.py`).

**Result: correct.**
- All four secret values are encrypted at rest with Fernet, keyed from the
  operator-supplied `GROOVARR_SECRET_KEY` env var — consistent with the
  architecture doc's own honest framing (§71/§9): this is *not* equivalent
  to an external secrets manager, since the key lives on the same host as
  the encrypted data, but it is real encryption-at-rest, not obfuscation.
- `app/api/settings.py`'s `SettingsOut` response model exposes only boolean
  `*_configured` flags (e.g. `spotify_client_configured`) for every secret —
  grep confirms no endpoint response model contains a raw `client_secret`,
  `api_key`, or `token` field. Write-only request bodies accept the raw
  value; nothing ever reflects it back.
- Grepped every `logger.*(...)` call in `app/` for secret-shaped field names
  (`token`, `secret`, `api_key`, `password`, `credential`): the only match is
  a log *event name* (`youtube.no_api_key_configured_using_ytsearch_fallback`)
  describing that a key is absent, not logging a key's value. No secret
  value is ever passed as a structlog field anywhere in the codebase.

## SSRF posture

**Checked:** every `httpx.AsyncClient` construction site (`app/api/deps.py`'s
shared request-scoped client, plus the handful of `async with
httpx.AsyncClient(timeout=...)` sites in `acquisition.py`, `replacement.py`,
`spotify_scheduler.py`, `upgrade_monitor.py`).

**Result: correct.**
- Every client construction site passes an explicit timeout (`10.0`–`15.0`s)
  — there is no unbounded-timeout client anywhere.
- `follow_redirects` is never set to `True` anywhere in the codebase (grep
  confirms zero matches) — httpx's own default is to *not* follow redirects,
  so every client in this app inherits that safer default rather than
  opting into redirect-following.
- Jellyfin/Plex URLs are operator-configured trusted endpoints (entered once
  in Settings, exercised via an explicit connection-test action) — no code
  path ever constructs a server-side fetch URL from Spotify- or
  YouTube-derived text. Discovery (YouTube Data API / yt-dlp) and lyrics
  (LRCLIB) each talk to one fixed, hardcoded host; nothing user-controlled
  ever becomes part of *which host* is contacted, only query parameters sent
  to it.

## Bounded resources

**Checked:** download concurrency (`app/jobs/download_queue.py`), LRCLIB
rate limiting (`app/integrations/lyrics/lrclib.py`), and whether the Phase 9
replacement workflow reuses Phase 5's disk-space guard rather than skipping
it.

**Result: correct.**
- `DownloadQueue` bounds concurrent downloads to
  `AppSettings.max_concurrent_downloads` (default 2) via a running-task-count
  check each poll tick — covered by a live-concurrency test
  (`tests/integration/test_download_queue.py`) that proves the limit is
  never exceeded, not just configured.
- The LRCLIB client applies a client-side minimum-interval rate gate and
  honors `Retry-After` on 429/5xx, per the beets-derived pattern the
  architecture doc specified.
- `app/services/replacement.py::replace_track_media` calls
  `app.services.acquisition.check_disk_space()` before starting the new
  download — the same guard the normal acquisition pipeline uses, not a
  separate or skipped check.

## What this audit does *not* cover

This was a static/code-level audit plus the existing automated test suite
(220 tests as of Phase 10) — it does not replace a live penetration test or
dependency-vulnerability scan (`docs/00-research-and-architecture-review.md`
§91 already calls for secret-scanning and dependency-scanning in CI; that CI
wiring is a `docker`/`.github` concern outside this backend-focused pass).
Jellyfin/Plex/Spotify/YouTube integration correctness against a *live*
server remains unverified beyond mocked-API tests, exactly as flagged in the
architecture doc's own §13 assumptions — nothing in this audit changes that.
