"""The queue itself: a job enqueued to Redis is picked up and run by a worker.

Skipped when no Redis is reachable, so the suite stays runnable on a bare machine
- but it is a real worker against a real broker when one is available, not a mock.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connectors.http import BlockedError  # noqa: E402
from app.connectors.registry import build_connectors  # noqa: E402
from app.services.search import SearchService  # noqa: E402

arq = pytest.importorskip("arq")
from arq import create_pool  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402
from arq.worker import Worker  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:56379/0")


def _redis_available() -> bool:
    async def ping() -> bool:
        try:
            pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
        except Exception:
            return False
        try:
            await pool.ping()
            return True
        except Exception:
            return False
        finally:
            await pool.aclose()

    try:
        return asyncio.run(ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(), reason=f"no Redis at {REDIS_URL}"
)


async def echo_job(ctx, value: str) -> str:
    return f"handled:{value}"


async def blocked_refresh_job(ctx, term: str) -> dict:
    """Runs the real fan-out with every connector behind a simulated WAF."""
    connectors = build_connectors(source="live")
    for connector in connectors:
        connector.source = "live"

        def blocked(term_, store, _name=connector.name):
            raise BlockedError(f"{_name} refused the request (HTTP 403).")

        connector.fetch_live = blocked

    outcome = SearchService(connectors).search(term, "33009")
    return {
        "products": len(outcome.products),
        "degraded": sorted(r.slug for r in outcome.degraded),
        "is_complete": outcome.is_complete,
    }


async def _run_burst(functions, enqueue):
    settings = RedisSettings.from_dsn(REDIS_URL)
    pool = await create_pool(settings)
    try:
        job = await enqueue(pool)
        worker = Worker(
            functions=functions,
            redis_settings=settings,
            burst=True,          # drain the queue, then exit
            poll_delay=0.05,
            max_jobs=4,
        )
        await worker.async_run()
        result = await job.result(timeout=30)
        await worker.close()
        return result
    finally:
        await pool.aclose()


def test_a_worker_picks_up_and_runs_an_enqueued_job():
    result = asyncio.run(
        _run_burst(
            [echo_job],
            lambda pool: pool.enqueue_job("echo_job", "refresh"),
        )
    )
    assert result == "handled:refresh"


def test_a_worker_completes_a_refresh_job_despite_every_retailer_blocking():
    """A blocked fan-out must finish the job, not fail it."""
    result = asyncio.run(
        _run_burst(
            [blocked_refresh_job],
            lambda pool: pool.enqueue_job("blocked_refresh_job", "cheerios"),
        )
    )
    assert result["is_complete"] is False
    # Every retailer degraded, yet the job returned normally with fallback data.
    assert len(result["degraded"]) >= 7
    assert result["products"] > 0
