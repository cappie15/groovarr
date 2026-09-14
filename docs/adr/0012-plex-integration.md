# 0012 — Plex integration and its platform limitations

Status: Accepted
Rationale: [architecture review §2 rows A and B](../00-research-and-architecture-review.md)

## Context

Two real Plex constraints surfaced by research directly shape this integration: Plex has no
"Music Videos" library type (only Movie/Show/Music/Photo/Other Videos), and Plex silently
deduplicates repeated items when added to a playlist — with no known API override for either.

## Decision

`app/integrations/plex/client.py` authenticates via a single `X-Plex-Token` header.
`list_library_sections` returns sections typed for a Music or Other-Videos library, never a
fictional Music-Videos type — Settings' library picker is restricted accordingly. Playlist
create/add-items goes through the `uri=server://<machineIdentifier>/.../metadata/<ratingKey,...>`
mechanism; item order is controlled purely by ratingKey order in that string.
`app/services/external_playlists.py` records how many duplicate occurrences Plex actually
collapsed (`duplicates_collapsed_count` on `ExternalPlaylist`) rather than pretending full
fidelity, and surfaces it in the frontend Playlists page.

## Consequences

- A Spotify playlist with duplicate track occurrences will show fewer items in its generated Plex
  playlist than in Jellyfin's — this is treated as a documented platform limit, not a bug to route
  around (see [ADR 0013](0013-jellyfin-integration.md) for the Jellyfin contrast).
- Name-collision detection (an existing unmanaged same-named playlist) is done client-side by
  listing and matching, since Plex has no dedicated existence-check endpoint — the same pattern
  used for Jellyfin.
- All of this is verified against faithfully-researched mocked Plex API behavior; no live Plex
  server was available during development (see `docs/00-research-and-architecture-review.md` §13).
