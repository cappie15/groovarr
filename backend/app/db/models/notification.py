"""ORM models for the Connect / notification-connection framework — a
Sonarr/Radarr-style "list of connections, each subscribing to specific event
types" replacing the previous implicit, always-on post-import Jellyfin/Plex
refresh.

A `NotificationConnection` is a named instance of a provider type
(`NotificationProvider`: currently `jellyfin`/`plex`) with its own
enabled/disabled state; `NotificationConnectionEvent` rows are the set of
event types it is subscribed to (many-to-one, one row per subscribed event).

Event types are NOT a fabricated parallel taxonomy: every string ever stored
in `NotificationConnectionEvent.event_type` is one of the exact `event_type`
strings already passed to `app.services.history.record_event` at a real
lifecycle call site — see `app.services.notifications.NOTIFICATION_EVENT_TYPES`
for the full enumerated list (kept in sync with those call sites) and
`app.services.notifications.MEDIA_SERVER_REFRESH_EVENTS` for the two of them
("acquisition.imported", "replacement.replaced") that a Jellyfin/Plex
connection needs to preserve today's refresh-on-import/-upgrade behavior.

Architected so a future provider (webhook, Discord, ...) is just a new
`NotificationProvider` member plus a new branch in
`app.services.notifications`'s dispatch logic — `config` exists for exactly
that: Jellyfin/Plex use none of it (there is still only zero-or-one of each,
configured globally in `AppSettings`, per §97), but a future webhook
connection would store its URL there without any schema change.
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class NotificationProvider(enum.StrEnum):
    JELLYFIN = "jellyfin"
    PLEX = "plex"


class NotificationConnection(Base):
    __tablename__ = "notification_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[NotificationProvider] = mapped_column(
        SAEnum(
            NotificationProvider,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Provider-specific config — unused by jellyfin/plex today (see module
    # docstring): both reuse the single already-configured server from
    # `AppSettings`, so there is nothing per-connection to store yet. Reserved
    # for a future provider (e.g. a webhook URL) that does need its own.
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # selectin (not the default lazy-select) so this relationship is safe to
    # touch from async code without an extra explicit load step — the same
    # concern SQLAlchemy's async docs call out for any lazy-loaded attribute.
    events: Mapped[list["NotificationConnectionEvent"]] = relationship(
        "NotificationConnectionEvent",
        cascade="all, delete-orphan",
        back_populates="connection",
        lazy="selectin",
        order_by="NotificationConnectionEvent.event_type",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<NotificationConnection {self.name!r} provider={self.provider.value} enabled={self.enabled}>"


class NotificationConnectionEvent(Base):
    __tablename__ = "notification_connection_events"
    __table_args__ = (
        UniqueConstraint("connection_id", "event_type", name="uq_notification_connection_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("notification_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # One of app.services.notifications.NOTIFICATION_EVENT_TYPES's keys — a
    # plain string rather than a DB enum for the same reason
    # `HistoryEvent.event_type` is: a new event type is just a new string at
    # a new `record_event(...)` call site, not a schema change.
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)

    connection: Mapped["NotificationConnection"] = relationship("NotificationConnection", back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<NotificationConnectionEvent connection_id={self.connection_id} event_type={self.event_type!r}>"
