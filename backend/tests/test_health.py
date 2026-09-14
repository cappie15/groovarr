"""Foundation smoke test: GET /health returns 200 with the expected shape and
an actually-checked (not hardcoded) DB status.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_ok_shape() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
    assert body["db"] in ("ok", "error")
    # With a writable temp CONFIG_DIR (set in conftest.py) the DB check should
    # actually succeed, not just be present in the payload.
    assert body["db"] == "ok"
