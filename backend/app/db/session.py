"""Async SQLAlchemy engine + sessionmaker for the Groovarr SQLite database.

The database lives at `{CONFIG_DIR}/groovarr.db`. `CONFIG_DIR` is created on
first use if it doesn't already exist (matters for a fresh bind-mounted
`/config` volume on first container start).
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def get_database_url() -> str:
    """Build the sqlite+aiosqlite URL from Settings, ensuring CONFIG_DIR exists."""
    settings = get_settings()
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{settings.config_dir}/groovarr.db"


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, created lazily and cached.

    Cached (rather than created at import time) so importing this module
    never has the side effect of touching the filesystem or opening a
    database connection before Settings are actually needed.
    """
    return create_async_engine(get_database_url(), future=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession, closed after the request."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        yield session


async def check_db_connection() -> bool:
    """Best-effort connectivity check used by GET /health. Returns True/False,
    never raises — callers should treat a False result as "db": "error".
    """
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    """Dispose of the engine's connection pool. Call on app shutdown."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
