"""SQLite (via SQLAlchemy's `DateTime(timezone=True)` type) does not
actually preserve tzinfo across a round-trip: a value written as UTC-aware
comes back **naive** the moment it's reloaded from the database — e.g.
after `session.refresh()`, or via any fresh query in a new session, which
is exactly what a production poll loop does every tick. Comparing that
naive value directly against a fresh `datetime.now(UTC)` raises
`TypeError: can't compare offset-naive and offset-aware datetimes`.

This is a real bug, not a hypothetical one: it was discovered while
building Phase 7 (the lyrics negative-cache expiry check) and turned out to
also be latent in Phase 5's retry/backoff due-check
(`app.services.acquisition.claim_next_ready_asset`) — the existing test
suite had worked around it by nulling `next_retry_at` directly rather than
exercising a real reloaded value, so it went uncaught. Every value loaded
from a `DateTime(timezone=True)` column must be passed through this before
being compared to a UTC-aware `now`.
"""

from datetime import UTC, datetime


def as_aware_utc(value: datetime | None) -> datetime | None:
    """Reattach UTC tzinfo to a DB-loaded datetime that SQLite silently
    stripped. A no-op for `None` or an already-aware value.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
