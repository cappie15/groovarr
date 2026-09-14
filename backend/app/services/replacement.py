"""Manual replacement of an already-downloaded track's media file (§34), and
the shared "safe swap" this uses — also the mechanism Phase 9's upgrade
monitor (`app.jobs.upgrade_monitor`) relies on to actually apply a better
candidate it finds (§36/§37).

This is deliberately NOT "reopen the existing MediaAsset and let the normal
download queue reprocess it": `app.services.acquisition._import_asset` has
no notion of "retire the file this row used to point to", so repointing an
already-downloaded row's `local_path` at a freshly muxed file would silently
orphan the old one on disk. `app.services.search.run_search_for_track`
already refuses to self-transition an already-downloaded asset for exactly
this reason — this module is the one, safe path for actually applying a
replacement:

1. download the new candidate into a brand-new `MediaAsset` row (the OLD
   row's `state`/`local_path` are untouched throughout, so the OLD file
   keeps serving normally for the entire — possibly slow — download);
2. only once the new file is fully downloaded, muxed, validated, tagged,
   and lyrics-processed (via `app.services.acquisition.run_pipeline_core`,
   not a reimplementation of it) does anything change in the database: every
   `PlaylistMediaReference` that pointed at the old asset is re-pointed at
   the new one, in one commit;
3. media servers/playlists are refreshed for the new asset;
4. only then is the now-thoroughly-unreferenced old file (and its `.lrc`
   sidecar) deleted, via `app.services.deletion.delete_media_asset_if_eligible`
   — the same reference-counted, filesystem-safety-checked delete routine
   everything else uses, not a separate ad hoc unlink.

If anything fails before step 2's swap, the old asset is returned completely
untouched and a `ReplacementError` is raised — the caller (and the track's
own state) never sees anything change.

Phase 10 hardening note: a crash between step 2 (new asset fully imported,
possibly already AVAILABLE) and step 3 (references swapped) leaves `new_asset`
with `track_id` set but zero `PlaylistMediaReference` rows — a state that
never otherwise occurs, since every other path that links a MediaAsset to a
track (`app.services.search`, `app.services.library_scan`) syncs its
references in the same transaction. `reconcile_orphaned_replacement_attempts`
(called at startup, alongside `app.services.acquisition.
reconcile_interrupted_assets`) detects and cleans up exactly this: the OLD
asset was never touched, so nothing is lost — the abandoned new download is
simply retired via the normal reference-counted delete path.
"""

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.candidates import VideoCandidate
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference
from app.db.models.spotify import Track
from app.integrations.acquisition.errors import AcquisitionError
from app.integrations.acquisition.ffmpeg_mux import cleanup_paths
from app.services.acquisition import check_disk_space, run_pipeline_core
from app.services.deletion import delete_media_asset_if_eligible
from app.services.external_playlists import sync_media_servers_for_asset
from app.services.history import record_event
from app.services.settings_service import get_app_settings

logger = structlog.get_logger(__name__)

_LIVE_STATES = (MediaState.AVAILABLE, MediaState.INCOMPLETE)


class ReplacementError(ValueError):
    """Raised whenever a replacement cannot proceed. Whenever this is
    raised, the original (old) MediaAsset is guaranteed completely
    untouched — safe to surface directly as an API error.
    """


async def replace_track_media(
    session: AsyncSession, http: httpx.AsyncClient, track_id: int, video_candidate_id: int
) -> MediaAsset:
    """Perform §34's exact safe-replacement sequence for `track_id`, using
    `video_candidate_id` as the new candidate. Returns the new MediaAsset on
    success. Raises `ReplacementError` (old asset untouched) on any failure.
    """
    old_asset = await session.scalar(select(MediaAsset).where(MediaAsset.track_id == track_id).order_by(MediaAsset.id))
    if old_asset is None or old_asset.state not in _LIVE_STATES:
        raise ReplacementError(
            f"Track {track_id} has no existing Available/Incomplete media to replace "
            "(use Manual Selection instead if nothing has been downloaded yet)."
        )

    candidate = await session.get(VideoCandidate, video_candidate_id)
    if candidate is None or candidate.track_id != track_id:
        raise ReplacementError(f"No candidate {video_candidate_id} found for track {track_id}")

    track = await session.get(Track, track_id)
    if track is None:  # pragma: no cover - defensive; FK integrity should prevent this
        raise ReplacementError("The associated track no longer exists.")

    app_settings = await get_app_settings(session)
    settings = get_settings()

    try:
        check_disk_space()
    except AcquisitionError as exc:
        raise ReplacementError(str(exc)) from exc

    new_asset = MediaAsset(
        track_id=track_id,
        video_candidate_id=candidate.id,
        source_reference=f"youtube:{candidate.youtube_video_id}",
        manual_selection=True,
        candidate_is_visualizer_only=False,
        state=MediaState.DOWNLOADING,
        pending_reference_swap=True,
    )
    session.add(new_asset)
    await session.flush()

    work_dir = settings.downloads_dir / f"replace-track-{track_id}-asset-{new_asset.id}"
    try:
        # Step 1-2: download -> mux -> validate -> tag -> import -> sidecar,
        # entirely on `new_asset` — `old_asset` is never touched here, so it
        # keeps serving normally for however long this takes.
        await run_pipeline_core(session, new_asset, track, app_settings, candidate, work_dir)
    except AcquisitionError as exc:
        # The new download never got far enough to be referenced by
        # anything — just drop the failed attempt. The old asset was never
        # touched.
        await session.delete(new_asset)
        await session.commit()
        logger.warning("replacement.failed", track_id=track_id, old_asset_id=old_asset.id, error=str(exc))
        raise ReplacementError(f"Replacement download failed; the original file was kept. {exc}") from exc
    finally:
        cleanup_paths(work_dir)

    # Step 3: swap every reference from old_asset to new_asset in one
    # commit, so reference-counting is never wrong for either row at any
    # point an observer could see it.
    refs = (
        await session.scalars(
            select(PlaylistMediaReference).where(PlaylistMediaReference.media_asset_id == old_asset.id)
        )
    ).all()
    for ref in refs:
        ref.media_asset_id = new_asset.id
    old_asset.track_id = None  # no longer "the" asset for this track
    new_asset.pending_reference_swap = False
    record_event(
        session,
        track_id=track_id,
        media_asset_id=new_asset.id,
        event_type="replacement.replaced",
        detail=f"Replaced asset {old_asset.id} with {candidate.title!r} ({candidate.youtube_video_id}).",
    )
    await session.commit()

    # Step 4: refresh media servers/playlists for the new asset — best
    # effort, matching every other media-server touchpoint in the app (§61).
    try:
        async with httpx.AsyncClient(timeout=15.0) as media_server_http:
            await sync_media_servers_for_asset(session, media_server_http, new_asset)
    except Exception:  # deliberately broad — see module docstring's §48/§61 principle
        logger.exception("replacement.media_server_sync_failed", media_asset_id=new_asset.id)

    # Step 5: only now delete the now-thoroughly-unreferenced old file. Also
    # best-effort — a cleanup failure here must not undo the replacement
    # that already succeeded; it just leaves an orphaned-but-harmless old
    # file for a future manual cleanup pass to find.
    try:
        await delete_media_asset_if_eligible(session, old_asset)
    except Exception:  # deliberately broad — see module docstring's §48/§61 principle
        logger.exception("replacement.old_asset_cleanup_failed", media_asset_id=old_asset.id)

    logger.info(
        "replacement.succeeded",
        track_id=track_id,
        old_asset_id=old_asset.id,
        new_asset_id=new_asset.id,
        new_video_id=candidate.youtube_video_id,
    )
    return new_asset


async def reconcile_orphaned_replacement_attempts(session: AsyncSession) -> int:
    """Crash recovery for an interrupted `replace_track_media` call (§78) —
    see the module docstring's "Phase 10 hardening note". `pending_reference_
    swap` is set True only by this module, only for the brief window between
    creating the new asset and its reference-swap commit — a row still
    carrying it after a restart is unambiguously an interrupted attempt (the
    old asset it was trying to replace was never touched, so nothing is
    lost). Reconciled via the normal reference-counted delete path — safe
    even if the attempt never got as far as downloading a file, since
    `delete_media_asset_if_eligible` handles both "no local_path yet" and "a
    real file exists" cases. Returns how many were reconciled.
    """
    orphans = (await session.scalars(select(MediaAsset).where(MediaAsset.pending_reference_swap.is_(True)))).all()
    count = 0
    for orphan in orphans:
        deleted = await delete_media_asset_if_eligible(session, orphan)
        if deleted:
            count += 1
            logger.warning(
                "replacement.reconciled_orphaned_attempt", media_asset_id=orphan.id, track_id=orphan.track_id
            )
    return count
