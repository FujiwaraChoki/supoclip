from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout

from src.api.pool_timeout import PoolTimeoutRoute


@pytest.fixture
async def http():
    router = APIRouter(route_class=PoolTimeoutRoute)

    @router.get("/pool")
    async def pool():
        raise PoolTimeout("QueuePool limit of size 5 overflow 0 reached")

    @router.get("/database")
    async def database():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    @router.get("/bug")
    async def bug():
        raise RuntimeError("unrelated")

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client


async def test_pool_timeout_is_a_retryable_503(http, caplog):
    response = await http.get("/pool")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    # Still reported with its traceback, so a starved pool shows up in error tracking.
    assert any(record.exc_info and record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.parametrize("path", ["/database", "/bug"])
async def test_other_errors_keep_their_500(http, path):
    assert (await http.get(path)).status_code == 500
