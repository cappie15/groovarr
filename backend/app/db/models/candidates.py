"""ORM model for `VideoCandidate` — a scored YouTube search result for one
`Track` (§8/§19-21 of the architecture doc).

Every candidate that survives the hard filters (§31 — no Shorts/portrait) is
persisted here, not just the eventual winner, so Manual Search has real
alternatives to show and every automatic decision is fully explainable via
`score_breakdown` (an ordered list of `{signal, delta, explanation}` — see
app/matching/scoring.py, which is the only code that computes these values).
"""

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VideoCandidate(Base):
    __tablename__ = "video_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), nullable=False, index=True)

    youtube_video_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Always "landscape" in practice today — anything "portrait"/"short" is
    # hard-filtered before a VideoCandidate row is ever created (§31). Kept
    # as an explicit column (rather than only implicit-by-absence) so a
    # future diagnostic "show rejected candidates" mode has somewhere to
    # record what it excluded, per §62's inspectability principle.
    orientation: Mapped[str] = mapped_column(String(16), nullable=False, default="landscape")

    # Which official-style signals fired, by name — e.g.
    # {"official_video_phrase": true, "vevo_or_topic_channel": true}. A
    # convenience projection of what's already inside score_breakdown, kept
    # separate so a UI can render badges without re-parsing the breakdown.
    official_signals: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    score: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ordered list of {"signal": str, "delta": int, "explanation": str} —
    # exactly what §21's "+Exact artist / -Duration mismatch" UI renders.
    score_breakdown: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # Human-readable flags that don't change the score's arithmetic outcome
    # by themselves but are worth surfacing verbatim — e.g. a duration
    # red-flag past the 15% tolerance (§29 — never a hard exclusion, always
    # a visible warning).
    rejection_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<VideoCandidate track_id={self.track_id} youtube_video_id={self.youtube_video_id!r} score={self.score}>"
        )
