# 0014 — Filesystem safety: media-root validation, guarded deletion

Status: Accepted
Rationale: [architecture review §9, §69-70](../00-research-and-architecture-review.md)

## Context

Groovarr writes files to user-mounted storage based partly on Spotify/YouTube-derived text
(artist/title strings via the naming template) and performs physical deletion driven by
reference-counting logic — a bug here has real "delete the user's media library" blast radius.

## Decision

Every file-writing/deleting code path independently resolves the target path and validates it
stays a descendant of the configured media root before touching the filesystem — not just the one
module that first implemented the pattern. Confirmed present in: `app/domain/naming.py`
(sanitization: illegal characters, reserved Windows device names, no `../` traversal, Unicode
preserved), `app/domain/atomic_move.py` (`atomic_move`, used by both import and organize/rename),
`app/services/deletion.py` (`delete_media_asset_if_eligible` — re-verifies the media-root boundary
*and* re-checks zero-references inside the same transaction as the delete, closing a TOCTOU gap),
and the lyrics sidecar writer. `app/domain/reference_counting.py` is the pure predicate deletion
is gated on: a `MediaAsset` is only eligible when no live `PlaylistMediaReference` row references
it — critically, a finalized/disconnected playlist's references are never removed by that
disconnect action, so its media stays protected indefinitely (verified by a dedicated regression
test, not just asserted in docs).

## Consequences

- A reference-counting bug found in Phase 9 (normally-acquired tracks never got a
  `PlaylistMediaReference` row at all — only the existing-library scanner created them) was caught
  specifically because deletion eligibility and playlist-sync tests both depend on this invariant;
  fixing it required touching `app/services/search.py` and `app/services/spotify_sync.py`, not
  just `deletion.py`.
- Every destructive deletion is logged explicitly (`app/services/deletion.py`), per §70's "log
  destructive actions clearly."
