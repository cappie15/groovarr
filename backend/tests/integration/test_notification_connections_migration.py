"""Runs the REAL Alembic migration (revision 8f7970353c4e,
notification_connections) against a throwaway sqlite file — not the ORM's
`Base.metadata.create_all` shortcut the rest of the suite's `_fresh_schema`
fixture uses, which would skip this migration's data-migration step
entirely and prove nothing about it.

This is the guarantee the project owner explicitly asked for: an
already-deployed install with `AppSettings.jellyfin_enabled`/`plex_enabled`
already true (like the live container this was built against) must, the
moment this migration runs, end up with an enabled `NotificationConnection`
per already-enabled server, subscribed to exactly the two events that used
to trigger a refresh unconditionally — so existing behavior keeps working
with zero operator action. A fresh install with neither server enabled must
get zero connections (nothing to preserve).

Deliberately a plain (non-async) test function: Alembic's own env.py drives
its async engine via `asyncio.run(...)` internally (see
app/db/migrations/env.py's `run_migrations_online`), which cannot be called
from inside an already-running event loop — the async test wrapper
`asyncio_mode = "auto"` would install for an `async def` test.
"""

import asyncio
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PREVIOUS_HEAD = "a8642b6713c1"  # youtube_quota_counter — the revision just before this one
_THIS_REVISION = "8f7970353c4e"  # notification_connections


def _alembic_config() -> Config:
    return Config(str(_BACKEND_DIR / "alembic.ini"))


def _seed_app_settings(db_path: Path, *, jellyfin_enabled: bool, plex_enabled: bool) -> None:
    """Insert the singleton AppSettings row the same way the app itself
    would (via the ORM, so every other column gets its normal Python-level
    default) — at schema revision `_PREVIOUS_HEAD`, which the current
    `AppSettings` model matches exactly (this test predates any further
    schema change), so this is safe.
    """

    async def _seed() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.db.models.settings import SETTINGS_SINGLETON_ID, AppSettings

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        session_factory = async_sessionmaker(engine)
        async with session_factory() as session:
            session.add(
                AppSettings(id=SETTINGS_SINGLETON_ID, jellyfin_enabled=jellyfin_enabled, plex_enabled=plex_enabled)
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())


def _migrate_and_inspect(tmp_path, monkeypatch, *, jellyfin_enabled: bool, plex_enabled: bool) -> list[sqlite3.Row]:
    """Runs the migration chain up to (not including) this one, seeds
    AppSettings, then upgrades through this migration, and returns every
    resulting notification_connections row joined with its event types."""
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        cfg = _alembic_config()
        command.upgrade(cfg, _PREVIOUS_HEAD)

        db_path = tmp_path / "groovarr.db"
        _seed_app_settings(db_path, jellyfin_enabled=jellyfin_enabled, plex_enabled=plex_enabled)

        command.upgrade(cfg, _THIS_REVISION)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            connections = conn.execute("SELECT * FROM notification_connections ORDER BY id").fetchall()
            result = []
            for row in connections:
                events = [
                    r["event_type"]
                    for r in conn.execute(
                        "SELECT event_type FROM notification_connection_events WHERE connection_id = ? "
                        "ORDER BY event_type",
                        (row["id"],),
                    ).fetchall()
                ]
                result.append({**dict(row), "event_types": events})
            return result
        finally:
            conn.close()
    finally:
        get_settings.cache_clear()


def test_both_servers_enabled_seeds_two_matching_connections(tmp_path, monkeypatch) -> None:
    connections = _migrate_and_inspect(tmp_path, monkeypatch, jellyfin_enabled=True, plex_enabled=True)

    by_provider = {c["provider"]: c for c in connections}
    assert set(by_provider) == {"jellyfin", "plex"}

    for connection in by_provider.values():
        assert connection["enabled"] == 1
        # Exactly the two events that trigger a refresh today — no more, no
        # fewer — so existing behavior is preserved exactly, not broadened.
        assert connection["event_types"] == ["acquisition.imported", "replacement.replaced"]


def test_neither_server_enabled_seeds_nothing(tmp_path, monkeypatch) -> None:
    connections = _migrate_and_inspect(tmp_path, monkeypatch, jellyfin_enabled=False, plex_enabled=False)
    assert connections == []


def test_only_jellyfin_enabled_seeds_only_a_jellyfin_connection(tmp_path, monkeypatch) -> None:
    connections = _migrate_and_inspect(tmp_path, monkeypatch, jellyfin_enabled=True, plex_enabled=False)
    assert [c["provider"] for c in connections] == ["jellyfin"]
    assert connections[0]["event_types"] == ["acquisition.imported", "replacement.replaced"]


def test_full_round_trip_upgrade_downgrade_upgrade(tmp_path, monkeypatch) -> None:
    """CONTRIBUTING.md's migration checklist: upgrade head -> downgrade -1 ->
    upgrade head, starting from a from-scratch (empty) database — not just an
    already-migrated one. Re-running the data migration a second time (after
    the downgrade dropped its tables) must reseed the same default
    connections without error."""
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        cfg = _alembic_config()
        command.upgrade(cfg, _PREVIOUS_HEAD)
        _seed_app_settings(tmp_path / "groovarr.db", jellyfin_enabled=True, plex_enabled=False)

        command.upgrade(cfg, "head")
        command.downgrade(cfg, "-1")
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(tmp_path / "groovarr.db")
        conn.row_factory = sqlite3.Row
        try:
            connections = conn.execute("SELECT * FROM notification_connections").fetchall()
        finally:
            conn.close()
        assert [c["provider"] for c in connections] == ["jellyfin"]
    finally:
        get_settings.cache_clear()


def test_downgrade_drops_the_new_tables_cleanly(tmp_path, monkeypatch) -> None:
    """The migration's own downgrade() must be able to run at all (mainly a
    guard against a batch_alter_table/index-name mistake), leaving the
    schema back at exactly the previous head."""
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        cfg = _alembic_config()
        command.upgrade(cfg, _THIS_REVISION)
        command.downgrade(cfg, _PREVIOUS_HEAD)

        db_path = tmp_path / "groovarr.db"
        conn = sqlite3.connect(db_path)
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
        finally:
            conn.close()
        assert "notification_connections" not in tables
        assert "notification_connection_events" not in tables
    finally:
        get_settings.cache_clear()
