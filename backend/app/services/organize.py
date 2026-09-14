"""Explicit "Organize / Rename" action (§15): renames/moves one
`MediaAsset`'s file to match the configured naming template. This never
runs automatically — it exists only as a deliberate, user-triggered action
for people who imported an existing library and now want it reorganized to
Groovarr's naming convention.
"""

from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.lyrics import Lyrics
from app.db.models.media import MediaAsset
from app.db.models.spotify import Track
from app.domain.atomic_move import AtomicMoveError, atomic_move
from app.domain.naming import NamingFields, render_filename
from app.integrations.lyrics.sidecar import sidecar_path_for

logger = structlog.get_logger(__name__)


class OrganizeError(ValueError):
    """Raised for any reason organizing must not proceed. Messages are
    written to be safe to surface directly in an API error response.
    """


async def organize_media_asset(session: AsyncSession, asset: MediaAsset) -> MediaAsset:
    if asset.local_path is None:
        raise OrganizeError("This media asset has no local file to organize.")
    if asset.track_id is None:
        raise OrganizeError(
            "This media asset is not yet confirmed to a track (it's still in Manual Review) "
            "— resolve that first."
        )

    settings = get_settings()
    media_root = settings.media_dir.resolve()
    current_path = Path(asset.local_path).resolve()

    if media_root != current_path and media_root not in current_path.parents:
        raise OrganizeError("Refusing to organize a file outside the configured media root.")
    if not current_path.is_file():
        raise OrganizeError(f"File not found on disk: {current_path}")

    track = await session.get(Track, asset.track_id)
    if track is None:  # pragma: no cover - defensive; FK integrity should prevent this
        raise OrganizeError("The associated track no longer exists.")

    filename = render_filename(
        NamingFields(
            artist=track.canonical_artist,
            title=track.canonical_title,
            year=track.release_year,
            quality=asset.resolution_label,
            ext=current_path.suffix,
        )
    )
    dest_path = (media_root / filename).resolve()

    if media_root != dest_path and media_root not in dest_path.parents:
        # Defense in depth: render_filename already strips path separators,
        # so this should be unreachable, but a destructive move deserves an
        # extra check rather than trusting a single layer (§69/§70).
        raise OrganizeError("Computed destination escapes the configured media root.")

    if dest_path == current_path:
        return asset  # already organized, nothing to do

    if dest_path.exists():
        raise OrganizeError(f"A different file already exists at the destination: {dest_path}")

    try:
        atomic_move(current_path, dest_path)
    except AtomicMoveError as exc:
        raise OrganizeError(str(exc)) from exc

    asset.local_path = str(dest_path)

    # Phase 7 (Lyrics): the `.lrc` sidecar's own name/location is derived
    # purely from the video's basename (§2-C/§3) — renaming the video without
    # also moving its sidecar would silently orphan it (the sidecar would
    # sit next to a now-nonexistent old filename and no longer be picked up
    # by anything looking for "same basename as the video"). Best-effort:
    # a failure here must not undo the video rename that already succeeded.
    old_sidecar = sidecar_path_for(current_path)
    if old_sidecar.is_file():
        new_sidecar = sidecar_path_for(dest_path)
        try:
            atomic_move(old_sidecar, new_sidecar)
            lyrics = await session.scalar(select(Lyrics).where(Lyrics.track_id == asset.track_id))
            if lyrics is not None:
                lyrics.sidecar_path = str(new_sidecar)
        except AtomicMoveError as exc:
            logger.warning("organize.sidecar_move_failed", asset_id=asset.id, error=str(exc))

    await session.commit()
    return asset
