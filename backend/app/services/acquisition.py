"""Acquisition pipeline orchestration (§39-52 of the original spec, Phase 5
of the architecture doc's phase plan): yt-dlp download -> FFmpeg mux (always
MP4, §2/§3) -> ffprobe validation -> atomic import, with exponential-backoff
retry tracked via `DownloadAttempt` (§50) that is completely independent of
the Spotify sync scheduler — a routine re-sync never touches this table, so
it can never reset the failure counter. Only an explicit manual `Retry`
deliberately starts a fresh attempt sequence.

State machine reminder (§74): a `MediaAsset` moves
CANDIDATE_SELECTED -> QUEUED -> DOWNLOADING -> PROCESSING -> IMPORTING ->
AVAILABLE (or INCOMPLETE, for a flagged visualizer-only match, §26/§37)
on success, or QUEUED (backoff pending) / DOWNLOAD_FAILED (attempts
exhausted or non-retryable) on failure.
"""

import dataclasses
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db_time import as_aware_utc
from app.db.models.acquisition import AttemptStatus, DownloadAttempt
from app.db.models.candidates import VideoCandidate
from app.db.models.lyrics import LyricsKind
from app.db.models.media import MediaAsset, MediaState
from app.db.models.settings import AppSettings
from app.db.models.spotify import Track
from app.domain.atomic_move import AtomicMoveError, atomic_move
from app.domain.naming import NamingFields, disambiguate_filename, render_filename
from app.integrations.acquisition.errors import AcquisitionError, NonRetryableDownloadError, ValidationError
from app.integrations.acquisition.ffmpeg_mux import cleanup_paths, mux_to_mp4
from app.integrations.acquisition.probe import ProbeResult, probe_media, resolution_label
from app.integrations.acquisition.ytdlp_download import download_streams
from app.integrations.lyrics.sidecar import write_lrc_sidecar
from app.integrations.tagging.mp4_tags import TagFields, apply_tags_best_effort, format_artist_tag
from app.services.external_playlists import sync_media_servers_for_asset
from app.services.history import record_event
from app.services.lyrics import get_or_fetch_lyrics
from app.services.settings_service import get_app_settings

logger = structlog.get_logger(__name__)

# Backoff between attempts, in seconds — approximating §50's "5 minutes; 30
# minutes; 2 hours; 12 hours; final retry". len() + 1 == the default
# `max_download_attempts` (5); a smaller configured value just means fewer
# of these waits are ever used before landing on DOWNLOAD_FAILED.
BACKOFF_SCHEDULE_SECONDS = [300, 1800, 7200, 43200]

# A duration this far off the source track's known duration indicates a
# corrupt/truncated download or a broken mux, not a borderline candidate
# match — that judgment already happened in Phase 4's scoring, at a tighter
# 15% tolerance (§29). This check exists purely to catch acquisition-
# pipeline breakage, so it is deliberately looser.
VALIDATION_DURATION_TOLERANCE = 0.5

# Refuse to start a download if either filesystem has less than this much
# free space (§73/§94) — a simple, non-configurable guard for this phase;
# revisit if real-world use shows it needs to be a Settings field.
MIN_FREE_BYTES = 500 * 1024 * 1024


class DiskSpaceError(AcquisitionError):
    """Raised when DOWNLOADS_DIR or MEDIA_DIR doesn't have enough free
    space to safely proceed. Always retryable — the operator may free up
    space before the next scheduled attempt.
    """


@dataclass(frozen=True)
class AcquisitionResult:
    media_asset_id: int
    succeeded: bool
    final_state: MediaState


def check_disk_space() -> None:
    settings = get_settings()
    for path in (settings.downloads_dir, settings.media_dir):
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        if usage.free < MIN_FREE_BYTES:
            raise DiskSpaceError(
                f"Only {usage.free / (1024 * 1024):.0f} MiB free at {path} — refusing to start a download."
            )


async def enqueue_for_download(session: AsyncSession, asset: MediaAsset) -> None:
    """Explicitly move a CANDIDATE_SELECTED asset to QUEUED. Not required
    for the normal flow — `claim_next_ready_asset` already picks up
    CANDIDATE_SELECTED assets directly, so a fresh automatic/manual match
    starts downloading on its own within one poll tick — this exists as a
    harmless, explicit API action for a caller that wants to force the
    state transition itself.
    """
    if asset.state != MediaState.CANDIDATE_SELECTED:
        raise ValueError(f"MediaAsset {asset.id} is not CANDIDATE_SELECTED (state={asset.state.value})")
    asset.state = MediaState.QUEUED
    await session.commit()


async def retry_download(session: AsyncSession, asset: MediaAsset) -> None:
    """Manual Retry (§50): deliberately reopens a DOWNLOAD_FAILED asset with
    a fresh attempt sequence. This is the *only* way the failure counter is
    ever reset — a routine Spotify sync never calls this, and a routine
    queue poll never calls this on its own. Past `DownloadAttempt` rows are
    kept (§65 — History needs the full story), but
    `retry_requested_at` marks the start of a new cycle: `process_media_asset`
    only counts attempts from this point onward toward the backoff schedule
    and `max_download_attempts`.
    """
    if asset.state != MediaState.DOWNLOAD_FAILED:
        raise ValueError(f"MediaAsset {asset.id} is not DOWNLOAD_FAILED (state={asset.state.value})")
    asset.state = MediaState.QUEUED
    asset.retry_requested_at = datetime.now(UTC)
    record_event(
        session,
        track_id=asset.track_id,
        media_asset_id=asset.id,
        event_type="acquisition.manual_retry",
        detail="Manual Retry: attempt counter reset for a fresh cycle.",
    )
    await session.commit()


async def cancel_queued(session: AsyncSession, asset: MediaAsset) -> None:
    """Safe Cancel for a not-yet-started job: only QUEUED can be cancelled
    cleanly (once DOWNLOADING/PROCESSING/IMPORTING has actually started,
    there is a real subprocess/file-move in flight that must be allowed to
    finish or fail on its own rather than being torn down mid-step)."""
    if asset.state != MediaState.QUEUED:
        raise ValueError(f"MediaAsset {asset.id} is not QUEUED, cannot cancel (state={asset.state.value})")
    asset.state = MediaState.CANDIDATE_SELECTED
    await session.commit()


async def claim_next_ready_asset(session: AsyncSession) -> MediaAsset | None:
    """Pick one ready asset that isn't waiting on a backoff timer, and mark
    it DOWNLOADING immediately in the same commit so a concurrent poll — or
    a restart-equivalent re-run — can never claim it twice (§77 idempotency).

    Both CANDIDATE_SELECTED (a fresh automatic or manual match, never yet
    attempted — no DownloadAttempt row, so never backoff-delayed) and
    QUEUED (explicitly re-queued, e.g. after a manual Retry, and possibly
    still waiting out a backoff timer from its last failed attempt) are
    claimable — a match doesn't need a separate manual "start the download"
    action to proceed, matching the "obvious matches happen automatically"
    principle (§105).
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(MediaAsset)
        .where(MediaAsset.state.in_([MediaState.CANDIDATE_SELECTED, MediaState.QUEUED]))
        .order_by(MediaAsset.id)
    )
    for candidate in result.scalars().all():
        latest_attempt = await session.scalar(
            select(DownloadAttempt)
            .where(DownloadAttempt.media_asset_id == candidate.id)
            .order_by(DownloadAttempt.attempt_number.desc())
        )
        next_retry_at = latest_attempt.next_retry_at if latest_attempt else None
        due = next_retry_at is None or as_aware_utc(next_retry_at) <= now
        if not due:
            continue
        candidate.state = MediaState.DOWNLOADING
        await session.commit()
        return candidate
    return None


async def reconcile_interrupted_assets(session: AsyncSession) -> int:
    """Crash recovery (§78): on startup, any asset left in DOWNLOADING/
    PROCESSING/IMPORTING means the process died mid-flight last time. Mark
    the dangling RUNNING attempt as failed (so the attempt counter stays
    honest, and the normal backoff/max-attempts logic still applies on the
    next try) and requeue the asset so the worker pool retries it cleanly
    instead of leaving it stuck forever. Returns how many were reconciled.
    """
    result = await session.execute(
        select(MediaAsset).where(
            MediaAsset.state.in_([MediaState.DOWNLOADING, MediaState.PROCESSING, MediaState.IMPORTING])
        )
    )
    stuck = list(result.scalars().all())
    for asset in stuck:
        running = await session.scalar(
            select(DownloadAttempt)
            .where(DownloadAttempt.media_asset_id == asset.id, DownloadAttempt.status == AttemptStatus.RUNNING)
            .order_by(DownloadAttempt.attempt_number.desc())
        )
        if running is not None:
            running.status = AttemptStatus.FAILED
            running.finished_at = datetime.now(UTC)
            running.error_class = "Interrupted"
            running.error_message = "Process restarted while this attempt was in progress."
        asset.state = MediaState.QUEUED
    if stuck:
        await session.commit()
        logger.warning("acquisition.reconciled_interrupted_assets", count=len(stuck))
    return len(stuck)


def _build_tag_fields(track: Track) -> TagFields:
    """Bridges the DB-level `Track` (Phase 2) to the tagging module's
    generic `TagFields` (Phase 6) — kept here rather than inside
    `app.integrations.tagging` so that module stays decoupled from the DB
    models, matching how `probe.py`/`ffmpeg_mux.py` never import them
    either. `lyrics` is left `None`: Phase 7 (LRCLIB) is what actually
    resolves lyrics text; the field already exists on `TagFields` so no
    change is needed here once that phase wires it in.
    """
    artwork_path = Path(track.album_artwork_path) if track.album_artwork_path else None
    return TagFields(
        title=track.canonical_title,
        artist=format_artist_tag(track.canonical_artist, track.featured_artists),
        year=track.release_year,
        artwork_path=artwork_path,
        explicit=track.explicit,
    )


def _validate_probe(probe: ProbeResult, expected_duration_s: float | None) -> None:
    if probe.video_codec is None:
        raise ValidationError("Muxed output has no video stream")
    if probe.audio_codec is None:
        raise ValidationError("Muxed output has no audio stream")
    if probe.duration_s is None or probe.duration_s <= 0:
        raise ValidationError("Muxed output has no plausible duration")
    if expected_duration_s:
        ratio = abs(probe.duration_s - expected_duration_s) / expected_duration_s
        if ratio > VALIDATION_DURATION_TOLERANCE:
            raise ValidationError(
                f"Muxed output duration ({probe.duration_s:.1f}s) differs from the expected track "
                f"duration ({expected_duration_s:.1f}s) by {ratio:.0%} — likely a corrupt/truncated download"
            )


async def _import_asset(
    session: AsyncSession, asset: MediaAsset, track: Track | None, probe: ProbeResult, muxed_path: Path
) -> None:
    settings = get_settings()
    media_root = settings.media_dir
    media_root.mkdir(parents=True, exist_ok=True)

    quality = resolution_label(probe.height)
    filename = render_filename(
        NamingFields(
            artist=track.canonical_artist if track else "Unknown Artist",
            title=track.canonical_title if track else "Unknown Title",
            year=track.release_year if track else None,
            quality=quality,
            ext="mp4",
        )
    )
    existing = {p.name for p in media_root.glob("*") if p.is_file()}
    filename = disambiguate_filename(filename, existing)
    dest_path = media_root / filename

    try:
        atomic_move(muxed_path, dest_path)
    except AtomicMoveError as exc:
        raise ValidationError(f"Could not import the validated file: {exc}") from exc

    asset.local_path = str(dest_path)
    asset.container = "mp4"
    asset.video_codec = probe.video_codec
    asset.audio_codec = probe.audio_codec
    asset.resolution_label = quality
    asset.duration_s = probe.duration_s
    asset.file_size = dest_path.stat().st_size
    # A flagged visualizer-only match (§26/§37) lands on INCOMPLETE instead
    # of AVAILABLE, but is otherwise imported identically — it's a fully
    # acquired, validated file, just not an ideal one.
    asset.state = MediaState.INCOMPLETE if asset.candidate_is_visualizer_only else MediaState.AVAILABLE
    record_event(
        session,
        track_id=asset.track_id,
        media_asset_id=asset.id,
        event_type="acquisition.imported",
        detail=f"Imported as {filename!r} ({quality or 'unknown quality'}).",
    )
    await session.commit()


async def run_pipeline_core(
    session: AsyncSession,
    asset: MediaAsset,
    track: Track | None,
    app_settings: AppSettings,
    candidate: VideoCandidate,
    work_dir: Path,
) -> None:
    """The download -> mux -> validate -> tag -> import -> lyrics-sidecar
    core, shared by the normal queue-driven pipeline (`process_media_asset`,
    below) and the Phase 9 manual replacement workflow
    (`app.services.replacement`) so neither duplicates this logic (§34's
    explicit requirement). Raises `AcquisitionError` on any failure, leaving
    `asset` in whatever state it was in before this call — the caller owns
    attempt/backoff bookkeeping and deciding what a failure means for
    `asset.state`. On success, `asset.local_path`/`state`
    (AVAILABLE/INCOMPLETE per `candidate_is_visualizer_only`) are set and
    committed by `_import_asset`.
    """
    streams = await download_streams(candidate.youtube_video_id, work_dir)

    asset.state = MediaState.PROCESSING
    await session.commit()

    muxed_path = work_dir / "muxed.mp4"
    mux_result = await mux_to_mp4(streams.video_path, streams.audio_path, muxed_path)

    expected_duration_s = (track.duration_ms / 1000) if track else None
    probe = await probe_media(mux_result.output_path)
    _validate_probe(probe, expected_duration_s)

    # Phase 7 (Lyrics): best-effort LRCLIB lookup, run before tagging so a
    # found result can also be embedded as the `©lyr` bonus tag below. Never
    # allowed to fail or block the import (§48).
    lyrics_row = None
    if track is not None and app_settings.lyrics_enabled:
        try:
            async with httpx.AsyncClient(timeout=10.0) as lyrics_http:
                lyrics_row = await get_or_fetch_lyrics(session, lyrics_http, track)
        except Exception as exc:  # deliberately broad — see module docstring's §48 principle
            logger.warning("acquisition.lyrics_step_failed", media_asset_id=asset.id, error=str(exc))
            lyrics_row = None
        record_event(
            session,
            track_id=track.id,
            media_asset_id=asset.id,
            event_type="lyrics.found" if (lyrics_row and lyrics_row.kind != LyricsKind.MISSING) else "lyrics.missing",
            detail=f"Lyrics: {lyrics_row.kind.value}" if lyrics_row else "Lyrics lookup failed or found nothing.",
        )

    # Phase 6 (Metadata): tag-writing is explicitly best-effort (§48).
    if track is not None:
        tag_fields = _build_tag_fields(track)
        if lyrics_row is not None and lyrics_row.kind != LyricsKind.MISSING and lyrics_row.content:
            tag_fields = dataclasses.replace(tag_fields, lyrics=lyrics_row.content)
        tagging_result = apply_tags_best_effort(mux_result.output_path, tag_fields)
        asset.tags_written = tagging_result.success
        if tagging_result.success:
            logger.info("acquisition.tagged", media_asset_id=asset.id)
            record_event(session, track_id=track.id, media_asset_id=asset.id, event_type="metadata.tags_written")
        else:
            logger.warning("acquisition.tagging_failed", media_asset_id=asset.id, error=tagging_result.error)
            record_event(
                session,
                track_id=track.id,
                media_asset_id=asset.id,
                event_type="metadata.tags_failed",
                detail=str(tagging_result.error),
            )
        # Re-probe regardless of tagging outcome: a failed *text* write is
        # fine to continue past; a corrupted *stream* is not (§51).
        probe = await probe_media(mux_result.output_path)
        _validate_probe(probe, expected_duration_s)
    else:
        asset.tags_written = False

    asset.state = MediaState.IMPORTING
    await session.commit()

    await _import_asset(session, asset, track, probe, mux_result.output_path)

    # `.lrc` sidecar (§2-C/§3, the DEFAULT/authoritative lyrics artifact):
    # only writable now that _import_asset has moved the file to its final
    # basename. A write failure must never undo an otherwise-successful
    # import (§48).
    if lyrics_row is not None and lyrics_row.kind != LyricsKind.MISSING and lyrics_row.content and asset.local_path:
        try:
            sidecar_path = write_lrc_sidecar(Path(asset.local_path), lyrics_row.content)
            lyrics_row.sidecar_path = str(sidecar_path)
            await session.commit()
            logger.info("acquisition.lyrics_sidecar_written", media_asset_id=asset.id, path=str(sidecar_path))
        except Exception as exc:  # deliberately broad — see module docstring's §48 principle
            logger.warning("acquisition.lyrics_sidecar_failed", media_asset_id=asset.id, error=str(exc))


async def process_media_asset(session: AsyncSession, asset: MediaAsset) -> AcquisitionResult:
    """Run one full attempt (download -> mux -> validate -> import) for
    `asset`, which must already be in DOWNLOADING state (claim_next_ready_asset
    puts it there). Always leaves the asset in a terminal-for-this-attempt
    state: AVAILABLE/INCOMPLETE (success), QUEUED (retryable failure, a
    backoff timer is now set), or DOWNLOAD_FAILED (attempts exhausted or a
    non-retryable failure).
    """
    settings = get_settings()
    app_settings = await get_app_settings(session)
    max_attempts = app_settings.max_download_attempts

    # Only attempts from the current cycle count toward backoff/max-attempts
    # (§50) — a manual Retry starts a fresh cycle via `retry_requested_at`
    # without deleting older DownloadAttempt rows (§65). `attempt_number`
    # itself stays a simple, globally-incrementing history sequence (used
    # only as a stable ordering key), independent of which cycle it belongs to.
    all_attempts_query = select(DownloadAttempt).where(DownloadAttempt.media_asset_id == asset.id)
    latest_overall = await session.scalar(all_attempts_query.order_by(DownloadAttempt.attempt_number.desc()))
    attempt_number = (latest_overall.attempt_number + 1) if latest_overall else 1

    cycle_query = all_attempts_query
    if asset.retry_requested_at is not None:
        cycle_query = cycle_query.where(DownloadAttempt.started_at >= asset.retry_requested_at)
    attempts_this_cycle = await session.scalar(select(func.count()).select_from(cycle_query.subquery()))
    attempt_in_cycle = attempts_this_cycle + 1

    attempt = DownloadAttempt(
        media_asset_id=asset.id,
        attempt_number=attempt_number,
        status=AttemptStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(attempt)
    record_event(
        session,
        track_id=asset.track_id,
        media_asset_id=asset.id,
        event_type="acquisition.download_started",
        detail=f"Attempt {attempt_number}.",
    )
    await session.commit()

    work_dir = settings.downloads_dir / f"asset-{asset.id}-attempt-{attempt_number}"
    track = await session.get(Track, asset.track_id) if asset.track_id is not None else None

    try:
        check_disk_space()

        candidate = await session.get(VideoCandidate, asset.video_candidate_id) if asset.video_candidate_id else None
        if candidate is None:
            raise NonRetryableDownloadError(f"MediaAsset {asset.id} has no selected VideoCandidate to download")

        await run_pipeline_core(session, asset, track, app_settings, candidate, work_dir)

        # Phase 8 (Jellyfin/Plex): trigger a library refresh + best-effort
        # playlist reconstruction for whichever servers/playlists this asset
        # is enabled for. Entirely best-effort (§61) — a media-server outage
        # or misconfiguration must never undo an otherwise-successful
        # import, so any failure here only shows up in `MediaAsset.
        # jellyfin_sync_status`/`plex_sync_status`, never in `asset.state`
        # or `attempt.status`.
        try:
            async with httpx.AsyncClient(timeout=15.0) as media_server_http:
                await sync_media_servers_for_asset(session, media_server_http, asset)
        except Exception as exc:  # deliberately broad — see module docstring's §48 principle
            logger.warning("acquisition.media_server_sync_failed", media_asset_id=asset.id, error=str(exc))

        attempt.status = AttemptStatus.SUCCEEDED
        attempt.finished_at = datetime.now(UTC)
        attempt.next_retry_at = None
        record_event(
            session,
            track_id=asset.track_id,
            media_asset_id=asset.id,
            event_type="acquisition.download_succeeded",
            detail=f"Attempt {attempt_number} succeeded; final state {asset.state.value}.",
        )
        await session.commit()

        cleanup_paths(work_dir)
        logger.info("acquisition.succeeded", media_asset_id=asset.id, attempt=attempt_number, state=asset.state.value)
        return AcquisitionResult(media_asset_id=asset.id, succeeded=True, final_state=asset.state)

    except AcquisitionError as exc:
        retryable = getattr(exc, "retryable", True) and attempt_in_cycle < max_attempts
        attempt.status = AttemptStatus.FAILED
        attempt.finished_at = datetime.now(UTC)
        attempt.error_class = type(exc).__name__
        attempt.error_message = str(exc)[:2000]

        if retryable:
            delay = BACKOFF_SCHEDULE_SECONDS[min(attempt_in_cycle - 1, len(BACKOFF_SCHEDULE_SECONDS) - 1)]
            attempt.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            asset.state = MediaState.QUEUED
        else:
            attempt.next_retry_at = None
            asset.state = MediaState.DOWNLOAD_FAILED

        record_event(
            session,
            track_id=asset.track_id,
            media_asset_id=asset.id,
            event_type="acquisition.download_failed",
            detail=f"Attempt {attempt_number} ({attempt.error_class}): {attempt.error_message}",
        )
        await session.commit()
        cleanup_paths(work_dir)
        logger.warning(
            "acquisition.attempt_failed",
            media_asset_id=asset.id,
            attempt=attempt_number,
            attempt_in_cycle=attempt_in_cycle,
            error=attempt.error_class,
            retryable=retryable,
            final_state=asset.state.value,
        )
        return AcquisitionResult(media_asset_id=asset.id, succeeded=False, final_state=asset.state)
