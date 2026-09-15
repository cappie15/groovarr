"""Existing-library scan, MediaAsset listing, and Organize/Rename endpoints
(§15). The Manual Review *decision* UI (approve/reject a candidate match)
belongs to Phase 4 alongside the rest of the matching engine — this phase
only surfaces the ManualReviewRequired rows for a future UI to act on.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pagination import Pagination, paginate, pagination_params
from app.db.models.media import MediaAsset, MediaState, SyncStatus
from app.db.models.spotify import Track
from app.db.session import get_session
from app.domain.naming import NamingFields, render_filename
from app.services.library_scan import LibraryScanResult, scan_library
from app.services.organize import OrganizeError, organize_media_asset

router = APIRouter(prefix="/api/media", tags=["media"])


class LibraryScanResultOut(BaseModel):
    files_scanned: int
    matched_available: int
    manual_review_created: int
    skipped_no_signal: int
    skipped_already_known: int
    errors: list[str]

    @classmethod
    def from_result(cls, result: LibraryScanResult) -> "LibraryScanResultOut":
        return cls(
            files_scanned=result.files_scanned,
            matched_available=result.matched_available,
            manual_review_created=result.manual_review_created,
            skipped_no_signal=result.skipped_no_signal,
            skipped_already_known=result.skipped_already_known,
            errors=result.errors,
        )


class MediaAssetOut(BaseModel):
    id: int
    track_id: int | None
    candidate_track_id: int | None
    local_path: str | None
    container: str | None
    resolution_label: str | None
    duration_s: float | None
    file_size: int | None
    state: MediaState
    manual_selection: bool
    review_reason: str | None
    source_reference: str | None
    jellyfin_sync_status: SyncStatus
    plex_sync_status: SyncStatus
    # The winning candidate for a CandidateSelected/Available/Incomplete
    # asset (null for Wanted/Missing/ManualReviewRequired) — exposed so the
    # Automatic/Manual Search pages can fetch and render its score/
    # score_breakdown (§21) without the backend needing a second,
    # asset-shaped "what did automatic matching decide" endpoint.
    video_candidate_id: int | None
    # Denormalized for the frontend (Tracks page, §75) so it isn't forced
    # into an N+1 fetch-per-row just to show a human-readable name. Prefers
    # the confirmed track; falls back to the *candidate* track for a
    # ManualReviewRequired row (track_id is null there by design), which is
    # display-only — it is not a confirmed association.
    track_artist: str | None
    track_title: str | None
    track_release_year: int | None
    # Lets the frontend embed Spotify's own player (open.spotify.com/embed/
    # track/<id>) so a reviewer who recognizes a song by ear — but not by
    # its Spotify-sourced title/artist text, which can be messy or in a
    # different script — can actually hear it before picking a candidate.
    spotify_track_id: str | None
    # True when the file's current basename doesn't match what the naming
    # template (app.domain.naming) would render today — i.e. the Organize/
    # Rename action (§15) would actually do something. Always False when
    # there's no confirmed track/local_path to render a template from.
    needs_organize: bool

    @classmethod
    def from_model(cls, asset: MediaAsset, track: Track | None) -> "MediaAssetOut":
        needs_organize = False
        if track is not None and asset.track_id is not None and asset.local_path is not None:
            current_path = Path(asset.local_path)
            expected_name = render_filename(
                NamingFields(
                    artist=track.canonical_artist,
                    title=track.canonical_title,
                    year=track.release_year,
                    quality=asset.resolution_label,
                    ext=current_path.suffix,
                )
            )
            needs_organize = current_path.name != expected_name

        return cls(
            id=asset.id,
            track_id=asset.track_id,
            candidate_track_id=asset.candidate_track_id,
            local_path=asset.local_path,
            container=asset.container,
            resolution_label=asset.resolution_label,
            duration_s=asset.duration_s,
            file_size=asset.file_size,
            state=asset.state,
            manual_selection=asset.manual_selection,
            review_reason=asset.review_reason,
            source_reference=asset.source_reference,
            jellyfin_sync_status=asset.jellyfin_sync_status,
            plex_sync_status=asset.plex_sync_status,
            video_candidate_id=asset.video_candidate_id,
            track_artist=track.canonical_artist if track else None,
            track_title=track.canonical_title if track else None,
            track_release_year=track.release_year if track else None,
            spotify_track_id=track.spotify_track_id if track else None,
            needs_organize=needs_organize,
        )


@router.post("/scan", response_model=LibraryScanResultOut)
async def trigger_library_scan(session: AsyncSession = Depends(get_session)) -> LibraryScanResultOut:
    result = await scan_library(session)
    return LibraryScanResultOut.from_result(result)


async def _load_tracks_by_id(session: AsyncSession, track_ids: set[int]) -> dict[int, Track]:
    if not track_ids:
        return {}
    result = await session.execute(select(Track).where(Track.id.in_(track_ids)))
    return {t.id: t for t in result.scalars().all()}


@router.get("", response_model=list[MediaAssetOut])
async def list_media_assets(
    state: MediaState | None = None,
    for_track: int | None = None,
    page: Pagination = Depends(pagination_params),
    session: AsyncSession = Depends(get_session),
) -> list[MediaAssetOut]:
    query = select(MediaAsset)
    if state is not None:
        query = query.where(MediaAsset.state == state)
    if for_track is not None:
        # A ManualReviewRequired row's `track_id` is null by design (§7) —
        # its association is only a `candidate_track_id` until confirmed —
        # so "the asset for track X" means either column, not just track_id.
        query = query.where(or_(MediaAsset.track_id == for_track, MediaAsset.candidate_track_id == for_track))
    result = await session.execute(paginate(query.order_by(MediaAsset.id), page))
    assets = list(result.scalars().all())

    # One batched lookup rather than one query per row (§94).
    track_ids = {a.track_id or a.candidate_track_id for a in assets if a.track_id or a.candidate_track_id}
    tracks_by_id = await _load_tracks_by_id(session, track_ids)

    return [
        MediaAssetOut.from_model(a, tracks_by_id.get(a.track_id or a.candidate_track_id or -1)) for a in assets
    ]


@router.post("/{asset_id}/organize", response_model=MediaAssetOut)
async def organize(asset_id: int, session: AsyncSession = Depends(get_session)) -> MediaAssetOut:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"No media asset with id {asset_id}")
    try:
        asset = await organize_media_asset(session, asset)
    except OrganizeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    track = await session.get(Track, asset.track_id) if asset.track_id else None
    return MediaAssetOut.from_model(asset, track)
