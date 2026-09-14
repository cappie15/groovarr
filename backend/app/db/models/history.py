"""ORM model for the History log (§65, Phase 10).

Answers "what happened to this track?" — a lightweight, append-only event
log written at existing lifecycle points across the other services (never a
new hook invented for this; see app/services/history.py's call sites).
Deliberately NOT tied to `MediaAsset` by a hard foreign key: a `MediaAsset`
row can be physically deleted (reference-counted deletion, replacement),
but its history must survive that deletion (§65 — history needs the full
story even after the file is gone), so `media_asset_id` is a plain,
unconstrained nullable integer rather than a `ForeignKey` with cascade
semantics. `track_id` IS a real (non-cascading) FK, since `Track` rows are
never deleted in this app.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HistoryEvent(Base):
    __tablename__ = "history_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id"), nullable=True, index=True)
    # Deliberately not a ForeignKey (see module docstring) — purely a record
    # of which asset this was about, even after that row no longer exists.
    media_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # A short, stable, dot-namespaced string (e.g. "search.automatic_selected",
    # "acquisition.download_failed", "deletion.removed_unreferenced") — not a
    # rigid enum, since new event types are just new string literals at a new
    # `record_event(...)` call site, not a schema change. Kept short/greppable
    # rather than free text so a future UI can group/filter by it.
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Human-readable detail for display — e.g. "Automatic match at 87%
    # confidence" or "Removed from playlist 'Ben's 90s' (no other playlist
    # references it)". Free text by design (§65 doesn't ask for structured
    # detail), but never includes secrets (see docs/security.md).
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<HistoryEvent track_id={self.track_id} event_type={self.event_type!r}>"
