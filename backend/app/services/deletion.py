"""Real, reference-counted physical deletion (§13/§14/§70).

`app.domain.reference_counting.is_eligible_for_deletion` answers "is
anything still using this asset" (a pure DB-only predicate). This module
owns turning "yes, eligible" into an actual, safe filesystem deletion:
the remaining §70 checklist items (path is inside the configured media
root, target is a regular file, not a symlink-escape target) plus
re-verifying zero-references inside the same commit that performs the
delete, closing the TOCTOU gap between "we decided to delete" and "we
actually deleted" (§70: "never infer deletion solely from a single Spotify
sync event" — the re-check here is what makes that true even under
concurrent access).

Deletes the video file AND its `.lrc` sidecar (Phase 7) if present, then
the `MediaAsset` row itself. Every destructive delete is logged clearly
(§70's own requirement), at `warning` level since it's an irreversible
action worth a human noticing in the logs.
"""

from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.media import MediaAsset
from app.domain.reference_counting import is_eligible_for_deletion
from app.integrations.lyrics.sidecar import sidecar_path_for
from app.services.history import record_event

logger = structlog.get_logger(__name__)


class DeletionRefusedError(ValueError):
    """Raised when a delete is refused for a §70 filesystem-safety reason.
    Never indicates a bug in the caller — a defensive guard, safe to
    surface directly as a 4xx API error.
    """


async def delete_media_asset_if_eligible(session: AsyncSession, asset: MediaAsset) -> bool:
    """Delete `asset`'s physical file (+ `.lrc` sidecar) and its own DB row,
    but only if it is still genuinely unreferenced at the moment of
    deletion. Returns True if a deletion happened, False if the asset
    simply wasn't (or was no longer) eligible — that is the ordinary,
    expected outcome for "still wanted elsewhere", not an error.
    """
    if not await is_eligible_for_deletion(session, asset.id):
        return False

    if asset.local_path is None:
        # Nothing to unlink (e.g. a MANUAL_REVIEW_REQUIRED/SEARCHING asset
        # that never reached a downloaded file) — just drop the now
        # thoroughly-unreferenced row.
        record_event(
            session,
            track_id=asset.track_id,
            media_asset_id=asset.id,
            event_type="deletion.removed_unreferenced",
            detail="Removed (no downloaded file existed yet); no longer referenced by any playlist.",
        )
        await session.delete(asset)
        await session.commit()
        return True

    settings = get_settings()
    media_root = settings.media_dir.resolve()
    path = Path(asset.local_path).resolve()

    if media_root != path and media_root not in path.parents:
        raise DeletionRefusedError(f"Refusing to delete a path outside the configured media root: {path}")
    if path.exists() and not path.is_file():
        raise DeletionRefusedError(f"Refusing to delete a non-regular-file target: {path}")

    sidecar = sidecar_path_for(path)
    had_sidecar = sidecar.is_file()

    if path.is_file():
        path.unlink()
    if had_sidecar:
        sidecar.unlink()

    logger.warning(
        "deletion.media_asset_deleted",
        media_asset_id=asset.id,
        track_id=asset.track_id,
        path=str(path),
        sidecar_deleted=had_sidecar,
    )
    record_event(
        session,
        track_id=asset.track_id,
        media_asset_id=asset.id,
        event_type="deletion.removed_unreferenced",
        detail=f"Removed {path.name!r} (no longer referenced by any playlist).",
    )

    await session.delete(asset)
    await session.commit()
    return True
