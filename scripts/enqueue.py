"""Enqueue a refresh job onto the worker queue.

    python scripts/enqueue.py cheerios
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arq import create_pool  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402

from app.core.config import get_settings  # noqa: E402


async def main() -> None:
    term = sys.argv[1] if len(sys.argv) > 1 else "milk"
    settings = get_settings()
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        job = await pool.enqueue_job("refresh_term", term)
        print(f"enqueued refresh_term({term!r}) as {job.job_id}")
        result = await job.result(timeout=120)
        print("result:", result)
    finally:
        await pool.aclose()


if __name__ == "__main__":
    asyncio.run(main())
