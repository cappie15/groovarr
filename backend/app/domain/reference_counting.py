"""Reference-counting eligibility for physical media deletion (§13/§14/§70).

A `MediaAsset` may be deleted only when nothing still needs it. "Needs it"
is tracked purely via `PlaylistMediaReference` rows, independent of whether
the owning playlist is still connected or has been finalized/disconnected
(§10: disconnecting a playlist must NOT make its media garbage-collectable —
finalizing only stops future Spotify-driven changes, it does not remove the
playlist's existing references).

This module also owns *keeping those reference rows correct* — the two
directions that must both stay wired up for reference-counting (and Phase
8's playlist reconstruction, which reads these same rows in
`app.services.external_playlists._resolve_ordered_items`) to mean anything:

- a `PlaylistEntry` appears for a track that already has a `MediaAsset`
  (`sync_references_for_track`, called once a `MediaAsset` becomes
  associated with a track — Phase 4/9's search/manual-selection paths, and
  Phase 3's existing-library scanner);
- a track newly enters a playlist whose *other* tracks may already have
  media (`ensure_reference`, called directly by `app.services.spotify_sync`
  for each track newly present in a re-synced playlist).

Phase 9 note: earlier phases' own tests exercised `is_eligible_for_deletion`
by inserting `PlaylistMediaReference` rows by hand, which is why it took
until Phase 9 (wiring *real* reference-counted deletion end-to-end) to
notice neither `app.services.search` nor `app.services.acquisition` had
ever actually created one for a normally-searched-and-downloaded track —
only `app.services.library_scan` had its own private copy of this logic.
Fixed here by making it the one shared implementation.

Scoping note: this phase implements only the reference-count portion of
§70's full deletion-safety checklist (item: "database reference count is
zero"). The remaining filesystem-safety checks (path is inside the
configured media root, is a regular file, is not a symlink-escape target,
etc.) belong with the actual delete routine (`app.services.deletion`) —
this predicate answers "is anything still using this asset", not "is it
safe to unlink this path".
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.media import PlaylistMediaReference
from app.db.models.spotify import PlaylistEntry


async def reference_count(session: AsyncSession, media_asset_id: int) -> int:
    """Count of live `PlaylistMediaReference` rows pointing at this asset,
    across every playlist regardless of connected/finalized state.
    """
    count = await session.scalar(
        select(func.count())
        .select_from(PlaylistMediaReference)
        .where(PlaylistMediaReference.media_asset_id == media_asset_id)
    )
    return count or 0


async def is_eligible_for_deletion(session: AsyncSession, media_asset_id: int) -> bool:
    """True only when zero playlists (connected or finalized) still
    reference this media asset. See module docstring for exactly what this
    does and does not check.
    """
    return await reference_count(session, media_asset_id) == 0


async def ensure_reference(session: AsyncSession, *, playlist_id: int, track_id: int, media_asset_id: int) -> None:
    """Idempotently ensure a `PlaylistMediaReference` row exists for this
    (playlist, track, media_asset) triple — "this playlist currently needs
    this media for this track". Safe to call repeatedly; a no-op if the row
    already exists.
    """
    existing = await session.scalar(
        select(PlaylistMediaReference).where(
            PlaylistMediaReference.playlist_id == playlist_id,
            PlaylistMediaReference.track_id == track_id,
            PlaylistMediaReference.media_asset_id == media_asset_id,
        )
    )
    if existing is None:
        session.add(
            PlaylistMediaReference(playlist_id=playlist_id, track_id=track_id, media_asset_id=media_asset_id)
        )
        await session.flush()


async def sync_references_for_track(session: AsyncSession, track_id: int, media_asset_id: int) -> None:
    """Whenever a track becomes associated with a media asset (a fresh
    Automatic/Manual Search match, a Manual Selection, or an
    existing-library-scan match), ensure *every* playlist that currently
    lists this track via `PlaylistEntry` gets a `PlaylistMediaReference`
    pointing at that asset. Without this, the asset is invisible to both
    reference-counted deletion and Phase 8's playlist reconstruction even
    though it's genuinely wanted.
    """
    playlist_ids = (
        await session.scalars(select(PlaylistEntry.playlist_id).where(PlaylistEntry.track_id == track_id).distinct())
    ).all()
    for playlist_id in playlist_ids:
        await ensure_reference(session, playlist_id=playlist_id, track_id=track_id, media_asset_id=media_asset_id)
