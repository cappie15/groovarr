# 0005 — Spotify integration: Client Credentials default, PKCE optional

Status: Accepted
Rationale: [architecture review §2 row E, §3](../00-research-and-architecture-review.md)

## Context

MusicGrabber (the researched prior-art project) abandoned real Spotify OAuth entirely in favor of
scraping the logged-out web player, because Spotify had stopped approving new app registrations at
the time. Later research confirmed real OAuth app registration is still possible today. Separately,
the project owner explicitly decided public playlists shouldn't require any login step at all.

## Decision

`app/integrations/spotify/client.py` implements two independent auth modes behind one client:

- **Client Credentials (default, required only)**: app-only token via
  `SpotifyClient.get_app_access_token(client_id, client_secret)` — no user consent, no browser
  step, no long-lived refresh token to manage. Sufficient for any public/unlisted playlist by ID.
- **Authorization Code + PKCE (optional)**: `exchange_pkce_code`/`refresh_pkce_token`, only needed
  for private/collaborative playlists, surfaced as a distinct "Connect your Spotify account"
  action in Settings, separate from the default no-login path.

`app/services/spotify_sync.py`'s `connect_playlist` tries Client Credentials first and falls back
to PKCE only on an access-denied error.

## Consequences

- The common case (importing a public curated playlist) needs zero interactive login — just a
  free Spotify Developer Dashboard app's client ID/secret in Settings.
- The PKCE refresh token's real 6-month hard expiry (a 2026 Spotify policy change) only matters
  for users who opt into private-playlist mode; it's handled via `SpotifyReauthRequiredError`
  rather than crashing a routine sync.
- No code here reuses MusicGrabber's scraping approach — that was a deliberate divergence, not an
  oversight (see the architecture review's §2 row E for why).

## Addendum: Liked Songs (Saved Tracks)

Spotify's "Liked Songs" has no playlist ID and is not reachable via `/playlists/{id}` — it's the
Saved Tracks resource (`GET /me/tracks`, paginated, requiring the `user-library-read` scope).
Because it's inherently private, per-user data, it can **never** be read via Client Credentials
(app-only) auth, in any mode, unlike a regular playlist which at least attempts that path first.

Modeled as a single `SpotifyPlaylist` row with a sentinel `spotify_id` (`LIKED_SONGS_SPOTIFY_ID =
"__liked_songs__"`, in `app/db/models/spotify.py`) plus an explicit `is_liked_songs` boolean flag,
rather than a parallel model or a forked sync path — the existing unique constraint on
`spotify_id` is what guarantees at most one such row can ever exist. `app/services/spotify_sync.py`'s
`sync_playlist` branches only at the two points that genuinely differ (how the access token is
obtained, and which endpoint/response shape supplies raw track items); everything else — track
upsert, full-replace `PlaylistEntry` diffing, reference counting, Jellyfin/Plex sync — is the exact
same code every other playlist goes through.

`user-library-read` was added to `SPOTIFY_SCOPES` (`app/integrations/spotify/pkce.py`) alongside
the two scopes already requested. A refresh token obtained under the old scope list does not
retroactively gain the new scope — Spotify denies `/me/tracks` for it with a 403, surfaced as a
`SpotifyAccessDeniedError` telling the user to reconnect, not a crash.

Liked Songs has no `snapshot_id` (no cheap change-detection signal exists for `/me/tracks`) — the
`SpotifyPlaylist.snapshot_id` column simply stays `None` forever for this row, which naturally
makes `sync_playlist`'s existing `snapshot_id is not None` short-circuit check always false for it:
every sync of Liked Songs always re-fetches and re-diffs the full library. This is a deliberate
"no cheaper option exists" acceptance, not an oversight — a full re-fetch/diff is the only signal
available, and Saved Tracks libraries are not expected to be so large that this is a real cost.
A distinct endpoint, `POST /api/playlists/liked-songs`, connects it (there is no URL/ID to parse,
so the existing URL-based `POST /api/playlists` doesn't fit); it requires "Connect your Spotify
account" to already be enabled and completed, checked up front and reported as a clean 403 rather
than failing confusingly mid-sync.
