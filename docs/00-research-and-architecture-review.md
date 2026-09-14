# Groovarr — Pre-Implementation Research & Architecture Review

Status: **first deliverable per master-prompt §106 — research/architecture review, not implementation.**
Date: 2026-09-14

---

## 1. Understanding of Groovarr

Groovarr is a self-hosted, single-user, Docker-first *Arr-style application that turns Spotify
playlists into a deduplicated, correctly-tagged local library of music videos, kept in sync with
Jellyfin and/or Plex playlists. It is explicitly **not** a one-shot downloader script: it is a
stateful library manager with the operational vocabulary of Sonarr/Radarr — monitored items,
wanted/missing, automatic vs. manual search, a queue, history, and explainable decisions.

The pipeline is: **Spotify (canonical playlist/track truth) → local library check → YouTube
discovery → deterministic weighted-scoring candidate selection → automatic acquisition or manual
review → yt-dlp (best video + best audio) → FFmpeg remux into MP4 (stream-copy where the source
codec pair allows it, transcode to MP4-compatible codecs where it doesn't) → validation → Spotify-
sourced metadata/artwork/lyrics enrichment → atomic import → Jellyfin/Plex library refresh →
playlist reconstruction in Spotify order.**

Three architectural commitments run through the entire spec and dominate every trade-off:

1. **Local media is authoritative once acquired.** A successful download does not get
   re-validated against YouTube's continued existence. Only an explicit, opt-in "monitor for
   better versions" setting (default OFF) revisits a completed acquisition, and it is described
   honestly as *may replace with a different video*, not merely "upgrade quality."
2. **Media identity ≠ playlist identity ≠ file identity.** A single physical file can be
   referenced by many playlists (reference-counted), a playlist can reference the same file at
   multiple positions (duplicate occurrences), and Spotify's identity for tracks/playlists is
   tracked by ID, never by name or filename.
3. **Precision beats recall for automation.** A wrong automatic download is strictly worse than
   an item sitting in Manual Review. Version/remix correctness outranks generic "official video"
   signals, which outrank popularity or resolution.

The rest of this document treats every explicit decision in the master prompt as final and only
proposes where the prompt leaves room (tech stack, exact schema, exact scoring weights).

---

## 2. Ambiguities, contradictions, and constraints surfaced by research

These are real conflicts between the spec's stated requirements and the actual behavior of the
external systems Groovarr depends on — discovered by primary-source research, not speculation.
Recommended resolutions are given; see §14 for the ones that still warrant your explicit sign-off.

| # | Spec says | Reality (researched) | Resolution |
|---|---|---|---|
| A | §53/59: configure a "target Music Videos library" in Plex | Plex has exactly five library types (Movie/Show/Music/Photo/Other Videos) — **there is no Music Videos library type**. Music videos live inside a Music (`artist`-type) library or an "Other Videos" library, surfaced as generic `clip` items. | Groovarr's Plex settings target a **Music library section** (or Other Videos), not a fictional "Music Videos" type. Document this plainly in the setup UI so it isn't confused with a Jellyfin-style dedicated content type. |
| B | §12/60: duplicate Spotify track occurrences must be reproduced in Plex/Jellyfin playlists "if their playlist APIs support this correctly" | **Jellyfin has no server-side dedup** — duplicates are preserved (confirmed unresolved-by-design via issues #1914/#4130). **Plex silently deduplicates repeated items when adding to a playlist** (community-confirmed, no override found). | Exactly as the spec's own caveat anticipates: fully preserve duplicates for Jellyfin; for Plex, generate the playlist with only one entry per duplicate occurrence and show a one-line UI note ("Plex does not support duplicate entries in one playlist — N repeated track(s) collapsed"). This is not a bug to route around, it's a documented platform limit. |
| C | §47: prefer embedding lyrics in the media file, sidecar only where "ecosystem compatibility requires it" | Research shows the sidecar **is** the only reliably interoperable path: Plex ignores embedded lyric tags entirely (any container); VLC has no LRC-aware engine at all; MP4's `©lyr` atom is confirmed used only for plain unsynced text by every major tagging tool, with no evidence any of the three target apps ever display it for a video file. Jellyfin's Lyrics feature *can* read a sidecar/synthetic-stream LRC convention, but — per source-level confirmation, §3 — that machinery is **hard-scoped to `Audio` items only** and is never invoked for `MusicVideo`, so for Groovarr's actual content type Jellyfin support is not available via either embedding or sidecar today. | Make `.lrc` sidecar the **default/primary** output; also write a plain-text lyric tag as a harmless best-effort bonus. This isn't a contradiction so much as the spec's own fallback clause resolving in the sidecar's favor for this entire ecosystem, not just "where required" — and it's the right call independent of Jellyfin's MusicVideo gap, since VLC/other tools and Groovarr's own UI all consume the sidecar directly. |
| D | §40: MP4 and MKV are both acceptable output containers, quality must not be sacrificed for container choice | Metadata/artwork reliability is starkly asymmetric: **Plex will not read embedded MKV title/artist tags at all** (long-standing, seemingly deliberate); Jellyfin's MKV support is partial/buggy; MP4's iTunes-style atom set is read consistently by VLC/Jellyfin, and by Plex when the library type is right. Conversely, forcing MP4 for a VP9+Opus source forces a real transcode (quality loss), whereas MKV accepts it via pure stream-copy. | **Decided (project owner's explicit call): the DEFAULT is always MP4**, even when that requires a real transcode — consistent metadata/artwork/lyrics-tag visibility across VLC/Jellyfin/Plex is prioritized over avoiding transcode cost by default. Groovarr still attempts `-c copy` (stream copy) into MP4 first whenever the source codec pair is already MP4-compatible; it transcodes (e.g. to H.264/AAC, preferring `libopenh264` over `libx264` to keep FFmpeg's LGPL status, §10) only when the source codec isn't natively MP4-compatible. **Now a Settings option, not purely hardcoded** (`AppSettings.container_policy`, per a later owner request): `always_mp4` (default, unchanged behavior) or `prefer_mp4_allow_mkv`, which avoids the transcode by falling back to a pure-stream-copy MKV instead — the Settings UI copy for this option explicitly states the metadata-reliability tradeoff (Plex won't read embedded MKV tags, Jellyfin only partially will; a file produced this way depends on the `.lrc` sidecar and Groovarr's own UI for metadata instead), so an operator who prefers avoiding transcodes can make that call knowingly rather than have MKV silently reintroduced as a default. |
| E | §8: research and reuse MusicGrabber's Spotify ingestion approach | MusicGrabber (canonical: `gitlab.com/g33kphr33k/musicgrabber`, mirrored on GitHub) **removed real Spotify OAuth entirely** in v1.5.2 ("Spotify has disabled new app creation") and now scrapes the logged-out `open.spotify.com` embed/web pages, with a headless-Chromium fallback for large playlists. Our own research into the current Spotify Web API originally concluded the **Client Credentials Flow** (app-only auth, no user login at all) was sufficient for reading any public or unlisted playlist by URL/ID. **UPDATE (2026-09-14, live-verified against a real Spotify app and 3 real public playlists, not just docs): this is no longer true.** Client Credentials can still read a playlist's *metadata* (name, `snapshot_id`, cover images), but the track-listing call now returns `403` for every playlist tested, public ones included — corroborated directly in Spotify's own July 2026 API changelog, which shows the legacy `/playlists/{id}/tracks` endpoint deprecated and its replacement (`/items`) requiring genuine user authentication (`401`). This is a real, current Spotify-side tightening beyond what the Feb 2026 docs said at the time of the original research, not a bug in Groovarr's implementation. | Reuse MusicGrabber's *architectural* ideas (polling/hash-diff sync model, scoring heuristics, LRCLIB fallback chain) but **do not reuse its Spotify access method**. **Revised (supersedes the original "Client Credentials is sufficient" decision): reading a playlist's tracks now requires the Authorization Code + PKCE "Connect your Spotify account" flow for every playlist, not only private/collaborative ones.** Client Credentials remains useful for the cheap metadata/snapshot_id check, but Groovarr must fall back to (in practice, effectively always use) the PKCE-authenticated call to actually fetch tracks — the code already does this fallback (Phase 2's crash on this exact path was found and fixed during live verification). The 6-month refresh-token re-authorization consideration therefore now applies to essentially every user, not just the private-playlist subset originally assumed — see §13/§14 for the resulting onboarding-flow implication. |
| F | §54: Jellyfin config listed as URL + API key + library + playlist toggle | Jellyfin's playlist-mutation endpoints throw when called with only an API key and no resolvable user context (`Guid.Empty`, confirmed open issue #12999) — every Create/AddItem/Move/Update call needs an explicit real `userId`. | Add a **required "Jellyfin user" field** to Settings (populated via `GET /Users` using the API key), beyond what §54 enumerates. This is an addition, not a contradiction — flagged because the spec's field list would otherwise be insufficient to implement playlists at all. |
| G | §87: "evaluate current practical options" for YouTube discovery, decoupled from yt-dlp acquisition | `ytsearch:` is implemented inside yt-dlp's own extractor, so using it for discovery re-couples the two systems the spec wants decoupled, and inherits the same 2025–2026 anti-bot escalation as playback. The official YouTube Data API v3 `search.list` needs no cookies/login (only a one-time, operator-provisioned API key) and is structurally independent of yt-dlp. | **Primary discovery = YouTube Data API v3** behind a small `Discovery` interface; `ytsearch:` kept only as an explicitly-labeled fallback if no API key is configured or daily quota (100 units/search, 10k/day default ⇒ ~100 searches/day) is exhausted. See §14 for the trade-off you should confirm. |

---

## 3. Research findings summary

Full findings (sources, exact API behaviors, version numbers, license text) were captured by ten
parallel research agents and are condensed into §2 and the sections below where they change a
decision. Headline facts not already covered in §2:

- **MusicGrabber**: Unlicense (public domain) — code can legally be copied verbatim into Groovarr
  regardless of Groovarr's own license. Reusable ideas: hash-based playlist diffing, a
  100-point-base scoring heuristic with named bonuses/penalties (official/VEVO/Topic-channel
  bonuses, live/cover/karaoke/fan penalties), and a two-stage lyrics fallback
  (exact-match → fuzzy search) against LRCLIB.
- **Spotify Web API (2026)**: **Default and only-required integration mode is the Client
  Credentials Flow** — app-only auth via `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET`, no user
  login, no browser/loopback redirect step, no refresh token to manage, no expiry to track; access
  tokens last 1h and are silently re-requested. This is sufficient for reading any **public or
  unlisted** playlist by URL/ID, covering the primary use case with zero setup friction beyond
  creating a free Spotify Developer Dashboard app. **Authorization Code + PKCE** user OAuth is
  retained as an **optional**, separately-toggled "Connect your Spotify account" feature in
  Settings, needed only for **private or collaborative** playlists (scopes
  `playlist-read-private` + `playlist-read-collaborative`); only that optional path is subject to
  the 2026 policy change where refresh tokens now expire after 6 months outright (no way to
  extend by refreshing) — Groovarr must detect `invalid_grant` and surface a "re-authorize
  Spotify" flag roughly twice a year, but only for users who opted into it. Track objects have no
  distinct remix/version field — it's embedded in free-text `name` and must be parsed.
  `snapshot_id` on every playlist response is the correct cheap change-detection signal for
  polling, and is available in both auth modes. Playlist cover URLs expire in <24h and must be
  fetched/cached promptly, not stored long-term.
- **yt-dlp**: Unlicense, CalVer releases (current 2026.08.19). Embed as a Python library
  (`YoutubeDL`) rather than shelling out — avoids the injection surface entirely and gives
  structured access to `media_type` (`'short'` flag) and per-format `width/height/aspect_ratio`
  for hard-excluding Shorts/portrait *before* download. Default separate-stream selection:
  `bv*+ba/b`. YouTube's PO-token/SABR restrictions are handled by yt-dlp gracefully (drops
  gated formats, degrades rather than crashes) using client profiles that don't currently require
  a token — no cookies needed for the common case.
- **FFmpeg**: 9.0.1, LGPL v2.1+ by default. **MP4 is the always-on target container** (decided,
  §2-D): attempt `-c copy` remux into MP4 first whenever the source codec pair is already
  MP4-compatible; transcode when it isn't (chiefly VP9/Opus sources). Building with
  `--enable-gpl` (needed for `libx264`) flips the whole FFmpeg component to GPL v2+ with
  source-availability obligations for the Docker image — since the always-MP4 default means the
  transcode path will be exercised routinely rather than as a rare fallback, use `libopenh264` by
  default for the H.264 transcode path to stay LGPL, and document the trade-off either way.
- **Lyrics**: LRCLIB (MIT, no auth, no documented read rate limit) is the right single provider
  for v1. Beets' `Backend.fetch(artist, title, album, length)` interface plus its
  duration-tolerance validation (reject if `|candidate.duration − track.duration| > 5%` **and**
  cross-check the LRC's own last timestamp against real duration) is a stronger, directly citable
  pattern than syncedlyrics' simpler fuzzy-only approach — adopt it.
- **Jellyfin**: Auth via `Authorization: MediaBrowser Token="…"` (not the legacy `X-Emby-Token`/
  `?api_key=`, which Jellyfin 10.11+ can disable server-side). `POST /Library/Refresh` is
  fire-and-forget — poll `GET /ScheduledTasks` for completion. No dedup on playlist add (good —
  matches spec). Known perf/regression issues on large flat folders and on 10.11.x realtime-
  monitor-triggered full rescans — budget extra retry/poll time rather than assuming near-instant
  discoverability.
- **Plex**: Single `X-Plex-Token`. Scans are `GET /library/sections/{id}/refresh[?path=]` — partial
  scan needs the *server-side absolute path*. Playlist order is controlled purely by the order of
  `ratingKey`s in a `uri=` parameter. No API-level existence check for playlist names — Groovarr
  must list-and-match by title itself before creating (same as Jellyfin).
- **MP4 vs MKV metadata in practice**: MP4's small, decades-old iTunes atom set (`©nam/©ART/©alb/
  covr`) is the most consistently read format across VLC/Jellyfin/(a correctly-typed) Plex, which
  is why MP4 is now Groovarr's always-on default output container regardless of transcode cost
  (§2-D) — MKV is manual-override-only. Jellyfin's dedicated Music Videos content type does
  **no** metadata scraping at all by its own documentation — filename/foldername is the title
  source there regardless of embedded tags, so correct **naming** (§16) matters independently of
  tagging quality.
- **Jellyfin lyrics support is Audio-only, not MusicVideo** (source-confirmed against Jellyfin
  tag `v10.11.11`, the current 10.11.x stable — high confidence, static source read plus current
  docs, not a live-server test): Jellyfin's entire Lyrics subsystem is hard-typed to `Audio` items
  at three independent layers — `LyricManager`'s public methods and its `GetSupportedProviders`
  explicitly `return []` for anything `is not Audio`; the "Download missing lyrics" scheduled task
  queries only `IncludeItemTypes = [Audio]`; and `LyricsController`'s REST endpoints do
  `GetItemById<Audio>` and 404 for any other item type. The video/MusicVideo prober
  (`FFProbeVideoInfo`) contains no lyric-handling code at all, and no `MediaStreamType.Lyric` is
  ever constructed on that path. The underlying sidecar-matching code is not architecturally
  MusicVideo-incompatible (there's a generic `Video`-typed overload it could in principle use),
  but in the current codebase it is simply never invoked that way. **Practical consequence**:
  Groovarr still writes the `.lrc` sidecar as its default, primary lyrics output — it's correct
  for VLC, other tools, and Groovarr's own UI regardless — but that sidecar should **not** be
  assumed to be picked up by Jellyfin's own Lyrics UI/API for a MusicVideo library item; this is a
  documented platform gap, not a Groovarr bug, and per §13 it should still be spot-checked once
  against a live Jellyfin server in Phase 8 in case an unreleased change has since altered it. (For
  Audio items specifically — not Groovarr's content type — the confirmed convention is: same
  folder as the media file, same base filename case-insensitively, optionally `.` + a
  language/flag token, extension `.lrc`/`.elrc`/`.txt`, e.g. `Song.mp3` → `Song.lrc` or
  `Song.eng.lrc`.)

---

## 4. Proposed technology stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend language/runtime | **Python 3.12+** | yt-dlp is Python-native — embedding it as a library (not a subprocess) is the single biggest safety and reliability win in this whole system, and it's only available that way in Python. `mutagen` (the most mature MP4/MKV tag library found in research) is also Python. This alignment removes an entire cross-language subprocess/IPC layer for the two most failure-prone integrations. |
| API framework | **FastAPI** | Async-native (fits yt-dlp/FFmpeg subprocess supervision + concurrent API calls to Spotify/Jellyfin/Plex), typed request/response models, and OpenAPI schema generation "for free" (§82 desirable). |
| Background jobs | **Custom SQLite-backed job table + bounded asyncio worker pool** (no Celery/RQ, no Redis) | §76 explicitly discourages an infra dependency like Redis purely to satisfy a queue library's default. A `jobs` table with `state`, `attempt`, `next_run_at`, `payload` gives idempotency, crash recovery, and retry-with-backoff (§77/§78/§50) without a second container. |
| Database / ORM | **SQLite** (mandated) via **SQLAlchemy 2.0 (async) + Alembic** | Alembic gives real, non-destructive migrations (§6 requirement) with negligible ceremony; SQLAlchemy's dialect abstraction means a future Postgres path isn't foreclosed, without building speculative abstraction now. |
| External processes | `yt-dlp` (embedded library), `ffmpeg`/`ffprobe` (subprocess, **argv arrays only**, pinned version) | Matches §39/§40's explicit engine choices; §69/§72 mandate argv-array invocation, never shell strings. |
| Frontend | **React + TypeScript + Vite** | Dense, information-first admin UI (§83) is React's strong suit; Vite gives fast builds for a small SPA bundled and served by the backend container — no second frontend service. |
| Frontend styling | Plain CSS modules or a lightweight utility layer (e.g. a small design-system, not a full component-kit) | §83 asks for information density and speed, not a consumer look — avoid a heavy component library's opinionated chrome. |
| Container discovery | YouTube **Data API v3** (`search.list`), `ytsearch:` (yt-dlp) as labeled fallback | Per §2-G — structurally decoupled, ToS-compliant, no login. |
| Lyrics | **LRCLIB** as sole v1 provider, `.lrc` sidecar as default output | Per §2-C and §3. |
| Tagging | **mutagen** (LGPL v2.1+) for MP4/M4V; a Matroska tag/attachment writer (e.g. driving `mkvpropedit`/direct EBML writes) kept only for the manual MKV override | mutagen is the most complete, most battle-tested library found for the primary container; MP4 is the sole default output container (§2-D — decided, not a preference), so the MKV writer exists purely to support the explicit manual override, never a default path. |
| Reverse-proxy / auth | None built-in beyond a single operator API key/session (§98) | Multi-user/RBAC explicitly out of scope; document that public exposure needs an external reverse proxy with auth, per §98. |

---

## 5. High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Groovarr container                       │
│                                                                   │
│  ┌────────────┐   ┌───────────────────────────────────────────┐ │
│  │  React SPA │──▶│              FastAPI app                  │ │
│  │ (built,    │   │  ┌────────────┐  ┌───────────────────────┐│ │
│  │ served     │   │  │  REST API  │  │  Job dispatcher /     ││ │
│  │ static)    │   │  │  (typed)   │  │  worker pool (asyncio)││ │
│  └────────────┘   │  └─────┬──────┘  └───────────┬───────────┘│ │
│                    │        │                     │            │ │
│                    │   ┌────▼─────────────────────▼─────────┐  │ │
│                    │   │        Domain / services layer      │  │ │
│                    │   │  (state machines, use-cases,        │  │ │
│                    │   │   reference counting, naming)       │  │ │
│                    │   └───┬──────┬──────┬──────┬──────┬─────┘  │ │
│                    │       │      │      │      │      │        │ │
│                    │  ┌────▼─┐┌───▼──┐┌──▼───┐┌─▼────┐┌▼──────┐ │ │
│                    │  │Spotify││Discov││Acquis││Meta/ ││Server │ │ │
│                    │  │client ││ery   ││ition ││Lyrics││sync   │ │ │
│                    │  │(CC,   ││(YT   ││(yt-  ││(muta-││(Jelly-│ │ │
│                    │  │PKCE   ││ Data ││dlp+  ││gen,  ││fin/   │ │ │
│                    │  │opt.)  ││ API) ││ffmpeg││LRCLIB││Plex)  │ │ │
│                    │  └───────┘└──────┘└──────┘└──────┘└───────┘ │ │
│                    └──────────────────────┬────────────────────┘ │
│                                            │                      │
│                              ┌─────────────▼──────────────┐       │
│                              │   SQLite (/config/groovarr  │       │
│                              │   .db) + Alembic migrations │       │
│                              └─────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
        │                              │                     │
   /downloads (tmp)              /music-videos          Jellyfin/Plex
   working area                  final library            (external,
                                  (bind mount)              user's own)
```

Single process, single container. Background jobs and the HTTP API share the same event loop and
the same SQLite connection pool (SQLite in WAL mode handles concurrent readers plus a single
writer queue fine at this scale — §94 targets hundreds/thousands of rows, not millions).

**Module boundaries** (why this shape): the "hard filters → weighted scoring → confidence
classification" pipeline (§19-21) is intentionally a pure, side-effect-free `matching/` module
that both Automatic Search and Manual Search call — so a candidate's score/explanation is
identical whichever path produced it, satisfying §21's explainability requirement without
duplicating logic. Acquisition, tagging, and server-sync are separate modules specifically so a
Plex/Jellyfin outage cannot roll back a successful media import (§61), and so yt-dlp's own
volatility (§87/§2-G) cannot silently break candidate discovery.

---

## 6. Proposed repository structure

```
groovarr/
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI routers (playlists, tracks, queue, settings, ...)
│   │   ├── core/                # config loading, logging, security/secrets helpers
│   │   ├── domain/               # entities, state machines, pure business rules
│   │   ├── db/
│   │   │   ├── models/           # SQLAlchemy models
│   │   │   └── migrations/       # Alembic
│   │   ├── integrations/
│   │   │   ├── spotify/          # Client Credentials auth (default) + optional PKCE user-OAuth,
│   │   │   │                     #   playlist/track fetch, snapshot diffing
│   │   │   ├── youtube/          # Discovery interface: Data API v3 + ytsearch fallback
│   │   │   ├── acquisition/      # yt-dlp wrapper, ffmpeg remux/transcode, ffprobe validation
│   │   │   ├── lyrics/           # Provider interface + LRCLIB implementation
│   │   │   ├── jellyfin/         # client + playlist/refresh logic
│   │   │   └── plex/             # client + playlist/refresh logic
│   │   ├── matching/              # hard filters, scoring, confidence classification
│   │   ├── jobs/                  # job table, dispatcher, worker, retry/backoff policy
│   │   └── services/               # use-cases orchestrating the above (ImportPlaylist, etc.)
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── fixtures/               # tiny legally-distributable media, mocked API payloads
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/                  # Dashboard, Playlists, Tracks, Wanted, Incomplete,
│   │   │                            # Search, Activity, History, Settings, System
│   │   ├── components/
│   │   └── api/                    # typed client generated from OpenAPI
│   └── package.json
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── entrypoint.sh                # handles signals, migrations-on-boot, non-root drop
├── docs/
│   ├── adr/                        # one file per decision in §101
│   ├── architecture.md
│   ├── security.md
│   └── THIRD_PARTY_NOTICES.md
├── .github/workflows/ci.yml
├── .env.example
├── .gitignore
└── README.md
```

---

## 7. Initial domain model

State machines first (§74), then entities.

**MediaAsset lifecycle**: `Wanted → Searching → (ManualReviewRequired | CandidateSelected) →
Queued → Downloading → Processing → Importing → Available` (or `Incomplete` if only a
visualizer-class candidate is available) `| DownloadFailed | Missing`.

**External sync state is separate and orthogonal** (§61/§74): each `MediaAsset` carries an
independent `JellyfinSyncStatus` and `PlexSyncStatus` ∈ `{NotConfigured, PendingSync, Synced,
FailedSync}` — a `FailedSync` never mutates the media's own lifecycle state.

Entities (fields trimmed to what's load-bearing; full column lists belong in the Alembic
migration, not this doc):

- **SpotifyPlaylist** — `spotify_id` (unique key, not name), `name`, `snapshot_id` (last-seen, for
  cheap diffing), `connected: bool`, `sync_interval_hours`, `last_synced_at`, `next_sync_at`,
  `finalized_at` (nullable — set on disconnect, never cleared), naming/destination overrides,
  `plex_enabled`, `jellyfin_enabled`. A playlist's *metadata* (name, artwork, `snapshot_id`) can be
  fetched via the no-login Client Credentials Flow purely from its Spotify ID/URL, but per the
  live-verified update to §2-E, actually fetching its *tracks* now requires the operator to have
  enabled the "Connect your Spotify account" (Authorization Code + PKCE) feature — this is no
  longer limited to private/collaborative playlists, it applies to public ones too.
- **Track** — `spotify_track_id`, `canonical_artist`, `featured_artists` (structured, not just
  folded into a display string), `canonical_title`, `parsed_version` (remix/edit info extracted
  from Spotify's free-text `name`), `release_year`, `duration_ms`, `explicit`, `album_artwork_ref`.
- **PlaylistEntry** — `(playlist_id, track_id, position, occurrence_index)` — `occurrence_index`
  is what makes duplicate occurrences of the same track in one playlist distinct rows (§12).
- **VideoCandidate** — `track_id`, `youtube_video_id`, `title`, `channel_id`, `channel_name`,
  `duration_s`, `orientation` (`landscape`/`portrait`/`short` — computed pre-download from
  yt-dlp's `media_type`/`aspect_ratio`, per §31/§3), `official_signals` (structured, not a single
  bool), `score`, `score_breakdown` (JSON, the human-readable "+/-" reasons from §21),
  `rejection_flags`.
- **MediaAsset** — `track_id` (nullable — a MediaAsset can briefly exist mid-acquisition before
  final track confirmation isn't needed since Track always precedes it), `local_path`, `container`,
  `video_codec`, `audio_codec`, `resolution_label` (derived from ffprobe post-mux, §80/§81),
  `duration_s`, `file_size`, `state` (lifecycle above), `manual_selection: bool`,
  `source_video_candidate_id` (provenance, §79 — not the identity key).
- **PlaylistMediaReference** — `(playlist_id, track_id, media_asset_id)` join used purely for
  reference counting (§13/§14) — a physical file is eligible for deletion only when this table has
  zero live rows referencing it **and** every referencing playlist that ever existed is either
  still absent-of-this-track or was never finalized-while-referencing (transactional check, §70).
- **DownloadAttempt** — `media_asset_id`, `attempt_number`, `status`, `started_at`, `finished_at`,
  `error_class`, `next_retry_at` (drives the exponential-backoff schedule in §50).
- **Lyrics** — `track_id`, `provider` (`lrclib`), `kind` (`synced`/`plain`/`missing`),
  `sidecar_path` (written next to the media file — the correct, portable output for VLC, other
  tools, and Groovarr's own UI; **note**: per §3, Jellyfin's Lyrics feature is Audio-item-only as
  of 10.11.x and does not currently surface sidecar lyrics for `MusicVideo` library items, so this
  field should not be relied on to produce in-Jellyfin lyrics display — pending a live-server
  spot-check in Phase 8), `fetched_at`, `negative_cache_until` (§48).
- **ExternalPlaylist** — `platform` (`jellyfin`/`plex`), `spotify_playlist_id` (FK — identity is by
  Spotify ID per §57, never by name), `external_id`, `desired_name` (override support, §58),
  `adopted: bool` (explicit ownership per §59), `sync_state`, `last_synced_snapshot`.
- **Settings** — a small typed table (or a single JSON blob with a Pydantic schema) covering
  exactly the fields enumerated in §84, nothing more. Includes a `spotify_user_oauth_enabled`
  toggle (default **off**) gating the Authorization Code + PKCE "Connect your Spotify account"
  feature (§2-E) — note this toggle now needs to be enabled for essentially every real playlist
  import, not just private ones, per the live-verified update; the setting itself and its stored
  refresh-token shape are unchanged, only the practical expectation of when a user needs to use it.

---

## 8. Proposed matching / scoring model

Per §19-21, discovery, scoring, and acquisition are separate stages. The scoring function is a
deterministic, explainable weighted sum — **not** ML/LLM-based (§2 core philosophy).

**Stage 0 — hard filters (reject before scoring, never shown as a "low score" candidate,
§31):**
- `orientation != 'landscape'` (Shorts or portrait) → hard-excluded.
- Candidate technically unplayable (no extractable formats at all) → hard-excluded.

**Stage 1 — signal extraction** per candidate (drawing on MusicGrabber's validated
100-point-base heuristic as an empirical starting point, restructured to honor the spec's
explicit priority order in §22-25):

| Signal | Direction | Notes |
|---|---|---|
| Requested version/remix match | large **+** | Highest-priority signal — §22 mandates this can outrank "official" status entirely. If Spotify's `parsed_version` says "Remix" and the candidate title matches that specific remix, this dominates the score. |
| Undesired-version markers present (live/cover/karaoke/instrumental/acoustic) when *not* requested | large **−** | Mirrors MusicGrabber's penalty set; these are near-disqualifying but not hard filters (spec keeps them as scoring signals, not exclusions, so an otherwise-empty result set can still surface them for Manual Review). |
| Exact artist match (incl. featured artists) | **+** | |
| Normalized title match | **+** | Normalization strips bracketed noise (official/HD/lyrics/etc.) the same way MusicGrabber's `clean_title()` does. |
| Official indicators (title phrasing, channel = VEVO/official/Topic) | **+**, tiered | "Official Video" phrasing > official artist channel > VEVO/Topic > generic. |
| Uploader/channel identity matches known artist channel | **+** | |
| Explicit/clean compatibility | **+/−** | Penalize a clean candidate when Spotify marks the track explicit (§23), never the reverse. |
| Duration difference vs. Spotify duration | **soft +/−**, red-flag at >15% | Never a hard exclusion (§29) — contributes proportionally, with a visible "red flag" annotation past the threshold. |
| Release year corroboration | small **+** | Absence is neutral, not penalized (Spotify data isn't always populated). |
| Fan-made/unofficial/tribute markers | **−** | |

**Stage 2 — confidence classification**: the weighted sum maps to `AutomaticMatch` (above a
tuned threshold) or `ManualReviewRequired` (below it). Per §86, the threshold is **not** picked by
intuition — Phase 4 builds the fixture corpus from §85 (remixes, covers, remasters, clean/explicit
pairs, live/lyric/visualizer variants, misleading titles, Unicode/alias cases) and tunes the
threshold against it, explicitly optimizing precision over recall (§86/§104).

Every candidate's `score_breakdown` is stored as an ordered list of `(signal, delta, explanation)`
tuples — this is what renders directly as the "+Exact artist / +Official video / −Duration
mismatch" UI in §21/§62, with no separate "explain" computation needed at render time.

---

## 9. Security / threat-model highlights

- **Process invocation**: yt-dlp embedded as a library (no shell at all for the primary path);
  FFmpeg/ffprobe invoked with argv arrays built from typed, already-validated fields — never
  interpolated Spotify/YouTube strings (§39/§69). yt-dlp's own `--exec`/output-template mechanism
  is not used with untrusted expansion fields.
- **Filesystem safety**: final paths are always resolved and checked to be descendants of the
  configured media root (reject `..`, absolute-path injection, null bytes, symlink escape) before
  any write. Deletion specifically requires the full checklist in §70 (managed-by-Groovarr, inside
  root, zero references, not a directory, not a symlink-escape target) — implemented as one
  reusable guarded-delete function, never inlined at each call site.
- **Filename sanitization**: strip filesystem-illegal characters and reserved names while
  preserving meaningful Unicode (§16) — sanitize, don't transliterate.
- **Secrets**: Spotify Client ID/Secret (the default Client Credentials Flow, no per-user token
  involved), a Spotify refresh token only if the optional user-OAuth feature is enabled, Jellyfin
  API key, Plex token — stored in SQLite under
  `/config`, encrypted at rest with a key derived from an operator-supplied secret (env var at
  container start) — documented plainly, per §71's own honesty requirement, as *not* equivalent to
  an external secrets manager since the key still lives on the same host. Never logged, never
  returned in API responses beyond a masked suffix, never placed in a URL.
- **SSRF posture**: Jellyfin/Plex URLs are operator-configured trusted endpoints (used as-is, with
  a connection test), never derived from YouTube/Spotify data. No server-side fetch is ever built
  from an untrusted string. Redirect-following is restricted on any outbound fetch; `file://`/
  other dangerous schemes are rejected outright.
- **YouTube auth boundary**: no cookies, no login, no `cookies.txt`, ever (§67). If content needs
  authenticated access, it's treated as unavailable and search continues — never escalated.
- **Resource bounds**: bounded worker pool (default 2 concurrent downloads, §49), bounded
  Discovery request rate (respecting YouTube Data API quota and LRCLIB's implicit courtesy
  rate), disk-space check before large batch imports, response-size caps on external fetches.
- **Container hardening**: non-root runtime user, minimal base image, dependency/secret scanning
  in CI (§5/§91), pinned `yt-dlp`/FFmpeg versions with a documented, deliberate upgrade cadence
  (exposed in System/Status per §88).

---

## 10. Third-party projects and licenses

| Project | License | Use |
|---|---|---|
| MusicGrabber (`gitlab.com/g33kphr33k/musicgrabber`) | Unlicense | Architectural reference (polling/diff model, scoring heuristic shape, LRCLIB fallback chain) — **not** its Spotify-scraping auth method (§2-E). Verbatim reuse is legally unrestricted given the license, but real OAuth is the better fit here regardless. |
| yt-dlp | Unlicense | Acquisition engine, embedded as a Python library. |
| FFmpeg | LGPL v2.1+ (GPL v2+ if built `--enable-gpl` for `libx264`) | Remux/transcode/ffprobe validation. Since MP4 is now the always-on default output container (§2-D), the H.264 transcode path is exercised routinely, not just as a rare fallback — build without `--enable-gpl` and use `libopenh264` for that path to stay LGPL in the shipped Docker image — document the choice in `THIRD_PARTY_NOTICES.md`. |
| mutagen | LGPL v2.1+ | MP4/M4V tag + artwork + lyrics-atom writing. |
| LRCLIB (server + API) | MIT | Sole v1 lyrics provider. |
| beets (`beetsplug/lyrics.py`, `_utils/requests.py`) | MIT | Architectural reference only — `Backend` interface shape, duration-tolerance validation, retry/backoff + rate-limit adapter pattern. |
| python-plexapi | MIT | Reference for exact Plex REST call shapes (playlist `uri=` construction, scan endpoints) — may be vendored directly if it simplifies the Plex integration, or reimplemented against the same documented calls. |
| FastAPI, SQLAlchemy, Alembic, React | MIT (all) | Application framework/tooling. |
| AtomicParsley (optional, MP4 cover-art edge cases) | GPL v2 | Only if invoked as an external subprocess for cases mutagen can't handle cleanly (embedding `covr` into a file that already has a video stream) — kept as a separate, optionally-installed binary, not statically linked, to scope its GPL obligations to itself. |

A `docs/THIRD_PARTY_NOTICES.md` tracking exact versions/licenses/attribution is a Phase 1
deliverable (§90).

---

## 11. Implementation phases

Following §102 exactly; each phase leaves the repo buildable and tested.

1. **Foundation** — repo scaffold, Docker/Compose, SQLite+Alembic, FastAPI+React shells, config
   loading, structured logging, `/health`, CI skeleton (§91).
2. **Spotify** — Client Credentials Flow (app-only auth, used for cheap metadata/`snapshot_id`
   checks); Authorization Code + PKCE user-OAuth flow (implemented as ordinary routes on
   Groovarr's own running server, not a throwaway local listener — it's already browser-reachable),
   separately toggled in Settings, now required in practice for fetching any playlist's tracks
   (live-verified update, §2-E — not limited to private/collaborative playlists as originally
   assumed); playlist/track fetch, `snapshot_id` diffing, periodic sync job, artwork fetch/cache
   (24h URL expiry).
3. **Domain/library** — Track/PlaylistEntry/PlaylistMediaReference models, existing-library
   scanner (§15), naming templates (§16-17).
4. **Search/matching** — YouTube Data API v3 discovery module (+ ytsearch fallback), hard
   filters, scoring engine + fixture corpus (§85), Automatic/Manual Search UI.
5. **Acquisition** — job queue, yt-dlp wrapper, FFmpeg remux/transcode decision logic (always
   targeting MP4, stream-copy when possible else transcode, §2-D), ffprobe validation,
   retry/backoff, atomic import.
6. **Metadata** — mutagen-based tag/artwork writing, ffprobe-derived quality label.
7. **Lyrics** — Provider interface, LRCLIB implementation, `.lrc` sidecar + best-effort tag
   (portable output for VLC/other tools/Groovarr's own UI; not assumed to surface inside
   Jellyfin's Lyrics UI for MusicVideo items, §3/§7).
8. **Media servers** — Jellyfin client (incl. required user-id field, §2-F; spot-check the
   MusicVideo-lyrics gap from §3/§7 against a live server), Plex client (incl. documented
   duplicate-collapse behavior, §2-B), refresh + playlist sync.
9. **Lifecycle** — reference counting/deletion, disconnect/finalize, Incomplete/upgrade
   monitoring, manual replacement workflow.
10. **Hardening** — crash-recovery tests, security review pass, performance/pagination pass, full
    test suite, documentation, release pipeline.

---

## 12. Test strategy

Per §92, with research-informed additions:

- **Unit**: title/artist normalization, remix/version parsing, explicit detection, duration
  scoring, official-signal scoring, filename sanitization, reference counting, naming templates,
  state-machine transitions.
- **Integration**: Spotify fixture → discovery (mocked Data API responses) → scoring → selection
  → mocked yt-dlp info-dicts → FFmpeg against tiny fixture streams → import → playlist generation.
- **Media tests**: real MP4 fixture files (the sole default output container, §2-D) covering both
  the stream-copy and the transcode path, plus a small MKV fixture set exercising only the
  explicit manual-override path — asserting mutagen/ffprobe round-trips actually produce the
  atoms documented in §3. This is the one area where "the spec says embed it" and "the player
  actually shows it" diverge, so these tests should assert against a small **matrix of known
  real-world quirks** found in research (e.g. a regression test asserting Groovarr never silently
  relies on Plex reading an MKV tag, since MKV is override-only anyway).
- **API contract tests**: Jellyfin/Plex clients tested against recorded fixtures of their actual
  (sometimes buggy) responses — e.g. a fixture reproducing issue #12999's `Guid.Empty` failure
  mode to lock in the "always pass explicit userId" defense; and a fixture/regression test
  asserting Groovarr correctly writes the `.lrc` sidecar for a MusicVideo item while **not**
  asserting Jellyfin's own Lyrics API/UI surfaces it — that gap is a documented Jellyfin
  limitation (§3/§7), not something to silently code around or assume away.
- **Failure tests**: network timeout, rate limiting (429/`Retry-After` from Spotify/YouTube/
  LRCLIB), corrupt download, non-zero yt-dlp/FFmpeg exit, full disk, media server offline, SQLite
  busy, container restart mid-job.

---

## 13. Explicit assumptions

- Groovarr's owner will run Spotify's app in Development Mode indefinitely (Extended Quota Mode's
  250k-MAU business requirement is unreachable for a self-hosted single-user tool). **Updated per
  the live-verified §2-E finding: this now has a real operational consequence for essentially
  every user, not just an opt-in subset** — since fetching a playlist's tracks requires the
  Authorization Code + PKCE "Connect your Spotify account" flow regardless of the playlist's
  visibility, the roughly twice-yearly manual re-authorization (6-month refresh-token hard expiry)
  is now a mainline expectation, not an edge case. The Settings UI should surface this clearly
  (a "Spotify needs re-authorization" flag/notice) rather than treating it as a rare path.
- The PKCE flow's redirect URI must be reachable and Spotify-acceptable (`https://` or
  `http://127.0.0.1`, per Spotify's current redirect-URI policy — a plain LAN IP like
  `http://10.0.0.31:8080/...` is rejected by the Spotify Dashboard) — for a self-hosted deployment
  reached over a bare LAN IP, this means the operator needs either an HTTPS reverse proxy in front
  of Groovarr, or to perform the one-time authorization step via a loopback tunnel (e.g. SSH port
  forward to `127.0.0.1`) even though day-to-day use of Groovarr itself doesn't require HTTPS.
  This is a real, now-mainline onboarding step, not an edge case for a small minority of users.
- The operator is willing to provision a free YouTube Data API v3 key (one Google Cloud Console
  step) for reliable discovery; if not, Groovarr degrades to `ytsearch:`-only discovery with
  reduced reliability. *(Flagged for confirmation — §14.)*
- Single Docker container/single process is acceptable per §4's stated preference against extra
  infra containers; no separate worker container is introduced.
- MP4 is the always-on default output container, decided (not merely flagged) per the project
  owner's explicit call in §2-D — transcoding is accepted when necessary in exchange for
  consistent metadata/artwork/lyrics-tag visibility. MKV remains available only as an explicit
  manual override in Settings, never a default.
- The exact `rtng` (MP4 explicit-flag) atom write path needs source-level (not docs-level)
  confirmation in mutagen before Phase 6 relies on it as a named field rather than raw atom
  manipulation.
- All per-topic "could not independently verify" notes from the ten research agents (wiki pages
  behind JS rendering, a few 403-gated Plex support articles, GitHub issue AI-summarization) are
  treated as high-confidence but not primary-source-verbatim; none of them changed an
  architectural decision above, but should be spot-checked once against a live server during
  Phase 8 integration testing rather than hard-coded blind.

---

## 14. Questions for explicit confirmation before Phase 1 begins

None of these block starting Phase 1 (Foundation) — they're called out because they're genuine
product/scope decisions, not because a technically-sound default doesn't exist. Recommended
defaults are stated; work will proceed on these defaults unless you say otherwise.

1. **Plex duplicate-track behavior (§2-B)**: Plex will silently collapse duplicate Spotify track
   occurrences into a single playlist entry (no known API override exists). *Default: accept this
   as a documented Plex limitation, surfaced as a UI note, rather than skipping Plex playlist
   generation for playlists that contain duplicates.*
2. **YouTube discovery mechanism (§2-G)**: recommended primary is the YouTube Data API v3, which
   requires you to create a free Google Cloud project and API key once. *Default: implement Data
   API v3 as primary with automatic `ytsearch:` fallback if no key is configured.*
3. **Jellyfin "acting user" (§2-F)**: Jellyfin's playlist API needs a specific Jellyfin user
   account selected (beyond just an API key) due to a confirmed Jellyfin bug with API-key-only
   calls. *Default: add a required "Jellyfin user" dropdown to Settings, populated via the API key
   itself at setup time — no separate credential needed from you.*

If any of these defaults are wrong for your setup, say so now; otherwise Phase 1 (Foundation)
begins on the plan above.
