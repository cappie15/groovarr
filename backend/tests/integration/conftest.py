"""Fixtures shared by integration tests: a freshly-created schema per test
(so tests never see another test's leftover rows) and a plain AsyncSession.
"""

import pytest
import pytest_asyncio

import app.db.models  # noqa: F401 - registers all ORM models on Base.metadata
from app.db.base import Base
from app.db.session import get_engine, get_sessionmaker
from app.integrations.spotify.client import _clear_app_token_cache


@pytest_asyncio.fixture(autouse=True)
async def _fresh_schema():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(autouse=True)
def _clear_spotify_token_cache():
    # The Client Credentials app-token cache is process-wide and keyed by
    # client_id; clear it between tests so one test's cached fake token can't
    # leak into another (e.g. a test that reuses the same client_id).
    _clear_app_token_cache()
    yield
    _clear_app_token_cache()


@pytest_asyncio.fixture
async def db_session():
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        yield session
