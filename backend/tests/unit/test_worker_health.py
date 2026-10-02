from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.main import create_app
from src.config import Config


@pytest.mark.parametrize("heartbeat,status", [(b"arq heartbeat", 200), (None, 503)])
async def test_worker_health_requires_live_heartbeat(heartbeat, status):
    pool = SimpleNamespace(get=AsyncMock(return_value=heartbeat))
    adapter = SimpleNamespace(get_pool=AsyncMock(return_value=pool))
    app = create_app(config=Config(), queue_adapter=adapter)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/worker")
    assert response.status_code == status
    pool.get.assert_awaited_once_with("supoclip_tasks:health-check")


async def test_worker_health_does_not_leak_redis_connection_error():
    adapter = SimpleNamespace(get_pool=AsyncMock(side_effect=RuntimeError("secret connection string")))
    app = create_app(config=Config(), queue_adapter=adapter)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/worker")
    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "worker": "unavailable"}
