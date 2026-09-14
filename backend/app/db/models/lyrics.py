"""ORM model for lyrics (§44-48, Phase 7).

One row per `Track`. `.lrc` sidecar is the DEFAULT/authoritative artifact
(app/services/lyrics.py + the acquisition pipeline write it next to the
imported video, same basename, per the researched Jellyfin/general sidecar
convention) — embedding plain text into the video's own `©lyr` tag
(app/integrations/tagging/mp4_tags.py) is a secondary, harmless best-effort
bonus, not the primary channel, per §2-C/§3: Plex ignores embedded lyric
tags entirely, VLC has no LRC-aware engine, and Jellyfin's Lyrics feature is
hard-scoped to `Audio` items at three independent code layers (scan/probe
dispatch, `LyricManager`, and the REST API) — never `MusicVideo`.

`negative_cache_until` (§48) is what stops a routine re-sync from
re-querying LRCLIB for a song it has already confirmed doesn't have lyrics.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LyricsKind(enum.StrEnum):
    SYNCED = "synced"
    PLAIN = "plain"
    MISSING = "missing"


class Lyrics(Base):
    __tablename__ = "lyrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), unique=True, index=True)

    provider: Mapped[str] = mapped_column(String(32), default="lrclib", nullable=False)
    kind: Mapped[LyricsKind] = mapped_column(SAEnum(LyricsKind, native_enum=False, length=16), nullable=False)

    # Raw text as returned by the provider (LRC-formatted for `synced`, plain
    # lines for `plain`, null for `missing`). Kept in the DB (not just on
    # disk) so the `.lrc` sidecar can be rewritten — e.g. after Organize/
    # Rename moves the video to a new basename — without re-querying LRCLIB.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Path of the `.lrc` sidecar actually written next to the imported video,
    # if any (null until the acquisition pipeline's import step writes it —
    # see app/services/acquisition.py).
    sidecar_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Only meaningful when kind == MISSING: don't re-query LRCLIB for this
    # track again until this timestamp has passed (§48 negative caching).
    negative_cache_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Lyrics track_id={self.track_id} kind={self.kind.value}>"
