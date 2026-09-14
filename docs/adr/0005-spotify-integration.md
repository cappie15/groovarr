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
