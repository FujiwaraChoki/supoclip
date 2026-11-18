import asyncio

import pytest

from src.workers import media_cleanup
from src.workers import tasks as worker_tasks


async def sweep_pauses(monkeypatch, passes=2):
    """Pauses the scratch loop takes between its first few sweeps."""
    pauses = []

    async def fake_sleep(seconds):
        pauses.append(seconds)
        if len(pauses) > passes:
            raise asyncio.CancelledError

    async def fake_sweep(**_):
        return None

    monkeypatch.setattr(media_cleanup, "maintain_local_scratch", fake_sweep)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker_tasks._maintain_scratch_forever()
    return pauses[1:]  # the first pause is the startup stagger


@pytest.mark.asyncio
async def test_sweep_reuses_its_connection_before_the_server_closes_it(monkeypatch):
    # Regression: the default 600 s sweep equalled the default 600 s idle-session
    # timeout, so pre-ping raced PostgreSQL closing the connection.
    monkeypatch.delenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", raising=False)
    pauses = await sweep_pauses(monkeypatch)
    assert pauses and all(pause <= 600 - 60 for pause in pauses)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", ["120", "300", "900"])
async def test_sweep_stays_inside_a_configured_idle_timeout(monkeypatch, timeout):
    monkeypatch.setenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", timeout)
    pauses = await sweep_pauses(monkeypatch)
    assert pauses and all(pause <= int(timeout) * 0.75 for pause in pauses)


@pytest.mark.asyncio
async def test_sweep_keeps_its_interval_without_an_idle_timeout(monkeypatch):
    monkeypatch.setenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", "0")
    assert await sweep_pauses(monkeypatch) == [worker_tasks.SCRATCH_SWEEP_SECONDS] * 2
