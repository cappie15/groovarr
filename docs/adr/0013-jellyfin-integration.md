# 0013 — Jellyfin integration and its platform limitations

Status: Accepted
Rationale: [architecture review §2 rows B, C, F](../00-research-and-architecture-review.md)

## Context

Research found Jellyfin's playlist-mutation endpoints throw a `Guid.Empty` error (confirmed
GitHub issue #12999) when called with only an API key and no resolvable user context — every
playlist call needs an explicit real Jellyfin `userId`. This wasn't in the original settings field
list and had to be added. Jellyfin has no server-side playlist item dedup (unlike Plex), so
duplicate Spotify occurrences are fully preserved here.

## Decision

`app/integrations/jellyfin/client.py` authenticates via
`Authorization: MediaBrowser Token="<key>", Client="Groovarr", ...` (not the legacy
`X-Emby-Token`/`?api_key=` forms, which newer servers can disable). Every playlist-mutation method
requires an explicit `user_id` parameter; the client refuses locally (zero HTTP calls) rather than
triggering the real Guid.Empty bug, via `JellyfinMissingUserError`. Settings' Jellyfin section adds
a "Jellyfin user" picker populated via `GET /Users` using the configured API key — no separate
credential needed from the operator. A path-remap setting (`jellyfin_media_path`, mirroring the
equivalent Plex field) handles the case where Jellyfin's mount path for the shared media volume
differs from Groovarr's own.

## Consequences

- Fresh/Docker Jellyfin installs can throw `ArgumentException: parentFolder` on the very first
  playlist creation (issue #14025) if the server's `Playlists` directory doesn't exist yet — this
  is translated into a distinct, actionable error rather than an unhandled crash; it's a
  remote-server quirk Groovarr's client cannot fix itself.
- Jellyfin's Lyrics feature is confirmed (source-level, not assumed) to be hard-scoped to `Audio`
  items and never reachable for `MusicVideo` — see [ADR 0011](0011-lyrics-providers.md).
- Verified against faithfully-researched mocked Jellyfin API behavior (including the real
  Guid.Empty and parentFolder failure modes); no live Jellyfin server was available during
  development.
