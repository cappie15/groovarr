# 0015 — Secrets: Fernet encryption at rest

Status: Accepted
Rationale: [architecture review §9, §71](../00-research-and-architecture-review.md)

## Context

Groovarr stores several real secrets in its SQLite database: the Spotify PKCE refresh token, the
Jellyfin API key, and the Plex token. The spec requires being honest that encryption whose key sits
beside the encrypted database is not equivalent to an external secrets manager.

## Decision

`app/core/secrets.py` derives a Fernet key deterministically from a single operator-supplied
`GROOVARR_SECRET_KEY` (SHA-256 → urlsafe base64, so the same env var always decrypts what it
previously encrypted) and exposes `encrypt_secret`/`decrypt_secret`. Every credential field
(Spotify client secret, PKCE refresh token, Jellyfin API key, Plex token) is encrypted through
this helper before being persisted, and the module's own docstring states plainly that this only
raises the bar above plaintext-in-SQLite — it does not claim to be an external secrets manager.

## Consequences

- Secrets never appear unmasked in API responses or structlog output — confirmed by grepping every
  logger call site during the Phase 10 security audit (`docs/security.md`), not just assumed.
- Losing `GROOVARR_SECRET_KEY` (e.g. a fresh container without the same env var) makes all
  previously-encrypted secrets unrecoverable — an operator responsibility to persist that value
  alongside the `/config` volume, documented in `.env.example`.
