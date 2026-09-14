"""ORM models for the Spotify-derived domain: playlists, tracks, and the
playlist/track join that preserves position and duplicate occurrences.

See docs/00-research-and-architecture-review.md §7 for the rationale behind
each field, and §12 for why `occurrence_index` exists at all (a track that
appears twice in one Spotify playlist must produce two distinct
`PlaylistEntry` rows, not be silently deduplicated).
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SpotifyPlaylist(Base):
    __tablename__ = "spotify_playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    spotify_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)

    # Cheap change-detection signal from Spotify — re-fetch/re-diff the track
    # list only when this differs from the last-seen value (§3/§7).
    snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    connected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set once, on disconnect, and never cleared afterward (§10). A finalized
    # playlist is excluded from the sync scheduler but its PlaylistEntry rows
    # (and everything downstream) are left completely untouched.
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    plex_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    jellyfin_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Optional per-playlist destination-name overrides (§58) — null means
    # "use the Spotify playlist name".
    plex_name_override: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jellyfin_name_override: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Local cache path for the playlist cover image (Spotify CDN URLs expire
    # in under 24h, so the URL itself is never persisted — §3/§7).
    artwork_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    entries: Mapped[list["PlaylistEntry"]] = relationship(
        back_populates="playlist", cascade="all, delete-orphan", order_by="PlaylistEntry.position"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<SpotifyPlaylist spotify_id={self.spotify_id!r} name={self.name!r}>"


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    spotify_track_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    canonical_artist: Mapped[str] = mapped_column(String(512), nullable=False)
    # Structured, not folded into a display string (§2/§7) — a JSON list of
    # artist name strings beyond the primary artist.
    featured_artists: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    canonical_title: Mapped[str] = mapped_column(String(512), nullable=False)

    # Remix/edit/version text extracted from Spotify's free-text track name,
    # e.g. "Remix", "Radio Edit", "John Doe Remix" — None if the track name
    # carries no detectable version marker. See app/integrations/spotify/utils.py.
    parsed_version: Mapped[str | None] = mapped_column(String(256), nullable=True)

    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Local cache path for the track/album artwork, mirroring artwork_path on
    # SpotifyPlaylist for the same CDN-expiry reason.
    album_artwork_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Track {self.canonical_artist!r} - {self.canonical_title!r}>"


class PlaylistEntry(Base):
    __tablename__ = "playlist_entries"
    __table_args__ = (
        UniqueConstraint("playlist_id", "track_id", "occurrence_index", name="uq_playlist_track_occurrence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Indexed (Phase 10 audit, §94): every sync/reconciliation/reconstruction
    # pass filters or joins on one or both of these, and a connected library
    # can have thousands of PlaylistEntry rows.
    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("spotify_playlists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # 0-based index distinguishing multiple occurrences of the same track in
    # one playlist (§12) — e.g. track X at positions 2 and 9 produces two
    # PlaylistEntry rows: occurrence_index 0 and 1, both referencing the same
    # Track row exactly once each in the physical library (§13).
    occurrence_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    playlist: Mapped[SpotifyPlaylist] = relationship(back_populates="entries")
    track: Mapped[Track] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<PlaylistEntry playlist_id={self.playlist_id} track_id={self.track_id} pos={self.position}>"
