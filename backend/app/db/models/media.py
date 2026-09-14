"""ORM models for the local media library: `MediaAsset` (a physical file and
its lifecycle) and `PlaylistMediaReference` (the reference-counting join that
lets one physical file be safely shared by many playlists — §13/§14).

See docs/00-research-and-architecture-review.md §7 for field rationale and
§74 for the state-machine design this deliberately follows: media lifecycle
(`MediaState`) and external-server sync status (`SyncStatus`, one each for
Jellyfin/Plex) are modeled as separate, orthogonal columns so a Jellyfin/Plex
sync failure can never change a media asset's own lifecycle state (§61).
"""

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MediaState(enum.StrEnum):
    """A media asset's own lifecycle — never mutated by external-server sync
    outcomes (those live in `jellyfin_sync_status`/`plex_sync_status`
    instead). Deliberately explicit rather than a single boolean (§74).
    """

    WANTED = "wanted"
    SEARCHING = "searching"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    CANDIDATE_SELECTED = "candidate_selected"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    IMPORTING = "importing"
    AVAILABLE = "available"
    INCOMPLETE = "incomplete"
    DOWNLOAD_FAILED = "download_failed"
    MISSING = "missing"


class SyncStatus(enum.StrEnum):
    """External media-server sync status — orthogonal to `MediaState` (§61).
    Used identically for both `jellyfin_sync_status` and `plex_sync_status`.
    """

    NOT_CONFIGURED = "not_configured"
    PENDING_SYNC = "pending_sync"
    SYNCED = "synced"
    FAILED_SYNC = "failed_sync"


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Set once a match is confirmed (state == AVAILABLE and beyond). Nullable
    # because a MediaAsset in MANUAL_REVIEW_REQUIRED has only a *candidate*
    # track association (see candidate_track_id below), not a confirmed one.
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id"), nullable=True, index=True)

    local_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Human-readable label derived from the *actual* muxed/probed stream
    # (e.g. "1080p") — never the requested/expected quality (§80/§81). Left
    # null when unknown; app.domain.naming renders an explicit "[Unknown]"
    # placeholder rather than omitting the bracket.
    resolution_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Indexed (Phase 10 audit, §94): the queue poller, the media-list API,
    # and the upgrade monitor all filter on this column, and a large library
    # can have thousands of rows.
    state: Mapped[MediaState] = mapped_column(
        SAEnum(MediaState, native_enum=False, length=32), default=MediaState.WANTED, nullable=False, index=True
    )
    manual_selection: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    jellyfin_sync_status: Mapped[SyncStatus] = mapped_column(
        SAEnum(SyncStatus, native_enum=False, length=32), default=SyncStatus.NOT_CONFIGURED, nullable=False
    )
    plex_sync_status: Mapped[SyncStatus] = mapped_column(
        SAEnum(SyncStatus, native_enum=False, length=32), default=SyncStatus.NOT_CONFIGURED, nullable=False
    )

    # Where this asset came from — a short human-readable provenance string,
    # e.g. "existing-library-scan" or "youtube:<video_id>" (Phase 4).
    source_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # The winning candidate once one has been selected (automatically or
    # manually) — set alongside source_reference by app/services/search.py.
    # Nullable: not every MediaAsset comes from a YouTube search (e.g. an
    # existing-library match never has one).
    video_candidate_id: Mapped[int | None] = mapped_column(ForeignKey("video_candidates.id"), nullable=True, index=True)

    # Populated only while state == MANUAL_REVIEW_REQUIRED: the existing-
    # library scanner's best-guess Track association plus a human-readable
    # explanation of why it wasn't confident enough to confirm automatically
    # (§15 — "if uncertain, send it to Manual Review", never guess silently).
    candidate_track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id"), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Set by Phase 4 (app/services/search.py) alongside CANDIDATE_SELECTED
    # when the winning candidate is visualizer-only (§26): acquisition still
    # runs normally (a visualizer *is* downloaded, per §26 — "may be
    # acquired when no proper music video exists"), but Phase 5's import
    # step lands the asset on INCOMPLETE instead of AVAILABLE when this is
    # set, so it stays eligible for future upgrade search (§37). A Manual
    # Selection (§33) always clears this back to False — a human's explicit
    # choice is never auto-downgraded.
    candidate_is_visualizer_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Set by a manual Retry action (§50 — "manual retry may deliberately
    # reset/reopen acquisition"). DownloadAttempt history is never deleted
    # (§65 — History must be able to answer "what happened to this track"),
    # but app.services.acquisition only counts attempts *since* this
    # timestamp toward the backoff schedule / max-attempts decision, so a
    # manual retry genuinely grants a fresh attempt cycle rather than
    # immediately re-exhausting the original one.
    retry_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Phase 6 (Metadata): whether Spotify-canonical tags/artwork were
    # successfully written into the final file. `False` covers both "not
    # attempted yet" and "attempted but failed" — tagging is explicitly
    # best-effort (§48 applied to tagging) and must never fail the import
    # itself, so this is the visible record of that distinction rather than
    # a second lifecycle state.
    tags_written: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Phase 10 hardening: True only for the brief window (§34) between
    # `app.services.replacement.replace_track_media` creating this row and
    # its reference-swap step committing. A precise, explicit marker rather
    # than an inferred "zero references means abandoned" heuristic — the
    # latter turned out to be unsafe: nothing else in the schema actually
    # guarantees a track-linked MediaAsset always has a reference the
    # instant it's created (test fixtures and other edge cases can
    # legitimately have one this row's whole life). Only a row with this
    # flag still True after a restart is unambiguously an interrupted
    # replacement attempt safe to clean up automatically (see
    # `app.services.replacement.reconcile_orphaned_replacement_attempts`).
    pending_reference_swap: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<MediaAsset id={self.id} state={self.state.value} path={self.local_path!r}>"


class PlaylistMediaReference(Base):
    """One row per (playlist, track, media_asset) triple that is currently
    "live" — i.e. this playlist currently needs this physical file for this
    track. Deliberately per-track, not per-occurrence: two duplicate
    occurrences of the same track in one playlist (see PlaylistEntry) share
    a single reference row here, since they share the same physical file
    (§12/§13). Reference-counting (app.domain.reference_counting) counts
    these rows, not PlaylistEntry rows.
    """

    __tablename__ = "playlist_media_references"
    __table_args__ = (UniqueConstraint("playlist_id", "track_id", "media_asset_id", name="uq_playlist_track_media"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("spotify_playlists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)
    media_asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    playlist = relationship("SpotifyPlaylist")
    track = relationship("Track")
    media_asset: Mapped[MediaAsset] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<PlaylistMediaReference playlist_id={self.playlist_id} "
            f"track_id={self.track_id} media_asset_id={self.media_asset_id}>"
        )
