# Groovarr backend architecture (developer map)

This is a concise, code-facing map of what's actually built, for a developer
orienting themselves in the repo. For *why* each decision was made — the
research, the alternatives considered, the platform quirks that shaped it —
see `docs/00-research-and-architecture-review.md`, which this file
deliberately does not duplicate.

## Module map

```
app/
├── api/            FastAPI routers — thin: parse request, call a service, shape the response.
│   ├── history.py, media.py, playlists.py, queue.py, search.py, settings.py,
│   ├── spotify_oauth.py, media_servers.py (Jellyfin/Plex settings + connection tests)
│   └── pagination.py   shared limit/offset dependency (§94)
├── core/           config.py (pydantic-settings), logging.py (structlog JSON), secrets.py (Fernet)
├── db/
│   ├── models/     one file per domain area — see "Data model" below
│   └── migrations/ Alembic; every schema change is a migration, see §6/§90
├── domain/         pure logic, no I/O: naming.py, text_normalize.py, reference_counting.py,
│   │               atomic_move.py, lrc.py
│   └── (this is where invariants live — e.g. reference-counting eligibility is a pure
│        predicate over the DB, deletion.py owns turning "eligible" into an actual delete)
├── integrations/   one package per external system, each hiding its own quirks behind a
│   │               small interface so the rest of the app never sees vendor-specific detail:
│   ├── spotify/    Client Credentials (default) + optional PKCE, playlist/track fetch
│   ├── youtube/    Data API v3 (primary) + yt-dlp ytsearch (fallback), yt-dlp enrichment
│   ├── acquisition/ yt-dlp download, ffmpeg mux/transcode (always MP4), ffprobe validation
│   ├── tagging/    mutagen-based MP4 tag writing
│   ├── lyrics/     LRCLIB client + `.lrc` sidecar writer
│   ├── jellyfin/   MediaBrowser-token client (refresh polling, playlists, users)
│   └── plex/       X-Plex-Token client (scans, playlists via ratingKey ordering)
├── matching/       scoring.py — the deterministic, explainable candidate scorer (§8/§21)
├── jobs/           background schedulers: download_queue.py (bounded-concurrency worker
│   │               pool), spotify_scheduler.py (periodic playlist sync),
│   └── upgrade_monitor.py (opt-in "monitor for better versions", §36/§37)
└── services/       orchestration — the layer that actually calls across integrations/domain/db:
    ├── spotify_sync.py   connect/sync/disconnect, reference reconciliation on entry changes
    ├── search.py         Automatic/Manual Search, Manual Selection
    ├── acquisition.py    the download→mux→validate→tag→lyrics→import pipeline + retry/backoff
    ├── library_scan.py   existing-library import (§15)
    ├── organize.py       explicit, user-triggered rename-to-template action
    ├── deletion.py       the one safe, reference-counted physical delete routine
    ├── replacement.py    the one safe "swap in a different file" workflow (§34) +
    │                     crash-recovery reconciliation for an interrupted swap
    ├── external_playlists.py   Jellyfin/Plex library refresh + playlist reconstruction
    ├── lyrics.py         LRCLIB lookup with negative caching, called from acquisition.py
    ├── history.py        `record_event(...)` — the one place History rows get written
    └── settings_service.py     typed reads/writes over the single AppSettings row
```

## Data model (one line each — see the research doc §7 for full rationale)

| Model | Purpose |
|---|---|
| `SpotifyPlaylist` | A connected/finalized Spotify playlist; identity by `spotify_id`, never name. |
| `Track` | Canonical Spotify track metadata, normalized (artist/title/version split). |
| `PlaylistEntry` | One row per (playlist, track, occurrence) — preserves duplicate occurrences. |
| `MediaAsset` | A physical file and its lifecycle (`MediaState`) + independent server sync status. |
| `PlaylistMediaReference` | The reference-counting join: "this playlist currently needs this file". |
| `VideoCandidate` | A scored YouTube search result, with a full `score_breakdown` for explainability. |
| `DownloadAttempt` | One row per acquisition attempt — backoff/retry bookkeeping, never deleted. |
| `Lyrics` | One row per track; `.lrc` sidecar path + negative-cache expiry. |
| `ExternalPlaylist` | The Jellyfin/Plex-side reconstruction of one Spotify playlist. |
| `HistoryEvent` | Append-only "what happened" log (§65) — not FK-cascaded to `MediaAsset`, so it outlives a deleted asset. |
| `AppSettings` | Single-row typed settings (Spotify/Jellyfin/Plex config, thresholds, toggles). |

## Key data flows

**Import a playlist:** `POST /api/playlists` → `spotify_sync.connect_playlist` (Client
Credentials by default, PKCE fallback on 403) → `sync_playlist` fetches + diffs
`PlaylistEntry` rows → `_reconcile_references` keeps `PlaylistMediaReference` correct →
best-effort `sync_external_playlist` for Jellyfin/Plex if enabled.

**Acquire a track:** a `MediaAsset` reaches `CANDIDATE_SELECTED` (via
`search.run_search_for_track` or `select_candidate`) → `download_queue` claims it →
`acquisition.process_media_asset` → `run_pipeline_core` (download → mux → ffprobe validate →
lyrics fetch → tag → import → sidecar) → on success, best-effort Jellyfin/Plex sync.
Every step from mux onward runs inside `PROCESSING`/`IMPORTING` with no intermediate commit,
so a crash anywhere in that span is recovered uniformly by
`acquisition.reconcile_interrupted_assets` on restart.

**Replace a track's file:** `POST /api/search/tracks/{id}/replace` →
`replacement.replace_track_media` downloads a whole new `MediaAsset` (old one untouched,
`pending_reference_swap=True`) via the *same* `run_pipeline_core` → only once that succeeds
does it swap `PlaylistMediaReference` rows and delete the old file via `deletion.
delete_media_asset_if_eligible`. `replacement.reconcile_orphaned_replacement_attempts`
(startup) cleans up an interrupted swap using the `pending_reference_swap` marker — a
precise flag, not an inferred heuristic (see that module's docstring for why the inferred
version was tried first and found unsafe).

**Delete when unreferenced:** any place a `PlaylistMediaReference` row disappears
(sync removing a track, a replacement retiring the old file) re-checks
`domain.reference_counting.is_eligible_for_deletion` and, if zero references remain,
calls `deletion.delete_media_asset_if_eligible` — which re-verifies eligibility inside the
same transaction as the actual unlink (closing the TOCTOU gap) and removes the `.lrc`
sidecar alongside the video.

## Background jobs (no Redis/Celery — SQLite + asyncio, per §76)

Three long-running tasks, started/stopped from `main.py`'s FastAPI lifespan:
`SpotifySyncScheduler` (periodic playlist re-sync), `DownloadQueue` (bounded-concurrency
acquisition worker pool + startup crash-recovery), `UpgradeMonitorScheduler` (opt-in
periodic re-search for `Incomplete`/non-manually-selected assets).

## Tests

235 tests as of the post-hardening/frontend-wiring pass (`pytest -q`), organized under
`tests/unit/` (pure logic — naming, normalization, scoring fixtures) and `tests/integration/`
(real SQLite + real ffmpeg/mutagen where the pipeline touches them; only genuinely external
services — Spotify, YouTube, LRCLIB, Jellyfin, Plex — are mocked, mirroring the architecture
doc's own §92 test strategy).

## Decision records

Short, per-decision records (what was decided, what it implies for the code) live in
[`docs/adr/`](adr/) — one file per major architectural choice, each linking back to the relevant
section of `00-research-and-architecture-review.md` for the full rationale.
