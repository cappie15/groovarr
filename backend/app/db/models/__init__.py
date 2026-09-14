"""ORM models. Importing this package registers every model on
`Base.metadata`, which is what Alembic autogenerate compares against — see
app/db/migrations/env.py.
"""

from app.db.models.acquisition import AttemptStatus, DownloadAttempt
from app.db.models.candidates import VideoCandidate
from app.db.models.external_playlist import ExternalPlatform, ExternalPlaylist, ExternalPlaylistSyncState
from app.db.models.history import HistoryEvent
from app.db.models.lyrics import Lyrics, LyricsKind
from app.db.models.media import MediaAsset, MediaState, PlaylistMediaReference, SyncStatus
from app.db.models.settings import AppSettings
from app.db.models.spotify import PlaylistEntry, SpotifyPlaylist, Track

__all__ = [
    "AppSettings",
    "AttemptStatus",
    "DownloadAttempt",
    "ExternalPlatform",
    "ExternalPlaylist",
    "ExternalPlaylistSyncState",
    "HistoryEvent",
    "Lyrics",
    "LyricsKind",
    "MediaAsset",
    "MediaState",
    "PlaylistEntry",
    "PlaylistMediaReference",
    "SpotifyPlaylist",
    "SyncStatus",
    "Track",
    "VideoCandidate",
]
