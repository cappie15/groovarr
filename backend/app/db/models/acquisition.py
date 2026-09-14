"""ORM model for `DownloadAttempt` — one row per attempt to acquire a
`MediaAsset`'s chosen `VideoCandidate` (§50/§51/§77/§78 of the architecture
doc).

Attempt/backoff bookkeeping lives in its own table, separate from
`MediaAsset`, specifically so a routine Spotify re-sync (which never
touches this table) can never reset the failure counter — only an explicit
manual Retry (app/services/acquisition.py::retry_download) deliberately
starts a fresh attempt sequence, per §50.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AttemptStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DownloadAttempt(Base):
    __tablename__ = "download_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AttemptStatus] = mapped_column(
        SAEnum(AttemptStatus, native_enum=False, length=16), default=AttemptStatus.RUNNING, nullable=False
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # When a retry may next be attempted (exponential backoff, §50). Null
    # once this was the final attempt (MediaAsset moves to DOWNLOAD_FAILED)
    # or once status == SUCCEEDED.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<DownloadAttempt media_asset_id={self.media_asset_id} "
            f"attempt={self.attempt_number} status={self.status.value}>"
        )
