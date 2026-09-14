# 0011 — Lyrics providers: LRCLIB + `.lrc` sidecar as primary artifact

Status: Accepted
Rationale: [architecture review §2 row C, §3](../00-research-and-architecture-review.md)

## Context

Research found Plex ignores embedded lyric tags entirely (any container), VLC has no LRC-aware
engine, and — critically — Jellyfin's Lyrics feature is hard-scoped to `Audio` items at three
independent code layers (scan dispatch, `LyricManager`, the REST API) and is never invoked for
`MusicVideo`, so it's unreachable for Groovarr's actual content type via either embedding or
sidecar today, regardless of what Groovarr writes.

## Decision

`app/integrations/lyrics/lrclib.py`'s `LRCLIBBackend` is the sole v1 provider — no API key, exact
match first then fuzzy search, validated against `app/domain/lrc.py`'s duration/timestamp
plausibility checks (LRCLIB itself enforces none of this). `app/integrations/lyrics/sidecar.py`'s
`write_lrc_sidecar` writes the `.lrc` file at the exact same basename as the video
(`sidecar_path_for`) as the DEFAULT/authoritative artifact; the plain-text `©lyr` embedded tag
(ADR 0010) is a harmless best-effort bonus, not the primary channel.

## Consequences

- A lyrics failure (network error, not found, rate-limited) never blocks or fails video import —
  `app/services/lyrics.py`'s `get_or_fetch_lyrics` never raises, and negative-caches a genuine
  "not found" so repeated syncs don't hammer LRCLIB for a song it doesn't have.
- The Jellyfin-MusicVideo gap is a known, documented platform limitation, not something Groovarr's
  own code can work around — the `.lrc` sidecar is still written for VLC, other tools, and
  Groovarr's own UI, which is why sidecar-as-default was the right call independent of that gap.
- Phase 9's `replace_track_media` moves the `.lrc` sidecar alongside the video whenever a track is
  replaced or renamed, so it never gets silently orphaned.
