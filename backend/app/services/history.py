"""History log (§65, Phase 10): "what happened to this track?"

`record_event` is called from existing lifecycle points in other services
(search, spotify_sync, acquisition, deletion, replacement,
external_playlists) — it never invents a new hook, it just adds a row
alongside a `logger.info`/`logger.warning` call that already exists. It only
`session.add()`s the row; the caller's own existing `session.commit()`
(already present at every call site, right after the state change the event
describes) is what persists it — so an event is never recorded for a change
that didn't actually commit.

Deliberately NOT called for high-frequency internal polling (queue ticks,
scheduler wake-ups, `claim_next_ready_asset` misses) — §65 explicitly warns
against flooding History with meaningless noise. Only genuinely meaningful,
track-relevant lifecycle events.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.history import HistoryEvent


def record_event(
    session: AsyncSession,
    *,
    event_type: str,
    track_id: int | None = None,
    media_asset_id: int | None = None,
    detail: str | None = None,
) -> None:
    """Stage a history row on `session` (no commit — see module docstring).
    `detail` must never contain a secret (API keys/tokens) — see
    docs/security.md's History section.
    """
    session.add(
        HistoryEvent(
            track_id=track_id,
            media_asset_id=media_asset_id,
            event_type=event_type,
            detail=detail,
            occurred_at=datetime.now(UTC),
        )
    )
