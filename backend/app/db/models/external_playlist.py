"""ORM model for the reconstructed playlist Groovarr maintains on an external
media server (Jellyfin/Plex) for one Spotify playlist (§53-61 of the
architecture doc).

Identity is by `spotify_playlist_id`, never by name (§57) — a Spotify rename
updates `SpotifyPlaylist.name`, and the external playlist gets renamed to
match unless a per-platform override is set (§58, `SpotifyPlaylist.
plex_name_override`/`jellyfin_name_override`, already added in Phase 2).
`sync_state`/`last_synced_snapshot` track this *playlist's* own membership/
ordering reconciliation, entirely independent of `MediaAsset.
jellyfin_sync_status`/`plex_sync_status` (§61) — those track whether one
*file* has been discovered by the server; this tracks whether one *playlist*
correctly reflects Spotify order given whichever files are discoverable so far.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExternalPlatform(enum.StrEnum):
    JELLYFIN = "jellyfin"
    PLEX = "plex"


class ExternalPlaylistSyncState(enum.StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
    # A same-named, Groovarr-unmanaged playlist already exists on the target
    # server (§59) — needs an explicit operator decision (adopt / rename /
    # cancel) before Groovarr will create or touch anything on that server
    # for this Spotify playlist.
    NEEDS_RESOLUTION = "needs_resolution"


class ExternalPlaylist(Base):
    __tablename__ = "external_playlists"
    __table_args__ = (
        UniqueConstraint(
            "platform", "spotify_playlist_id", name="uq_external_playlist_platform_spotify_playlist"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[ExternalPlatform] = mapped_column(
        SAEnum(ExternalPlatform, native_enum=False, length=16), nullable=False
    )
    spotify_playlist_id: Mapped[int] = mapped_column(
        ForeignKey("spotify_playlists.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The server's own playlist identifier once created/adopted (a Jellyfin
    # item GUID string, or a Plex ratingKey) — null while PENDING/
    # NEEDS_RESOLUTION. Also used, in the NEEDS_RESOLUTION state, to record
    # the id of the pre-existing unmanaged playlist a future "adopt" action
    # would take over.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # What name was actually used on the server as of the last successful
    # sync — for display/diffing against SpotifyPlaylist's current
    # name/override, which remains the source of truth read at sync time.
    last_synced_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # True once the operator has explicitly resolved a NEEDS_RESOLUTION
    # collision by choosing to adopt the pre-existing external playlist
    # found under this name (§59). False for an ordinary Groovarr-created
    # playlist, which needed no such resolution.
    adopted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sync_state: Mapped[ExternalPlaylistSyncState] = mapped_column(
        SAEnum(ExternalPlaylistSyncState, native_enum=False, length=32),
        default=ExternalPlaylistSyncState.PENDING,
        nullable=False,
    )
    sync_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Cheap re-sync-avoidance signal: a stable digest of the logical ordered
    # item list last successfully written to the server. Unchanged since the
    # last sync means there's nothing new to push.
    last_synced_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Plex-only (§2 row B): how many duplicate Spotify occurrences were
    # collapsed into one entry by Plex's own server-side dedup on the last
    # sync. Always null for Jellyfin, which preserves duplicates fully.
    duplicates_collapsed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<ExternalPlaylist platform={self.platform.value} "
            f"spotify_playlist_id={self.spotify_playlist_id} state={self.sync_state.value}>"
        )
