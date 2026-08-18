"""ARQ worker configuration.

Redis + ARQ was chosen over Celery for the reason the architecture doc gives: the
jobs are IO-bound fan-outs with retry and backoff, and ARQ keeps that to a single
small dependency rather than a broker abstraction we do not need.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from arq.connections import RedisSettings  # noqa: E402
from arq.cron import cron  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from workers.jobs import refresh_term, refresh_watchlist, startup, shutdown  # noqa: E402

_settings = get_settings()


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(_settings.redis_url)


class WorkerSettings:
    functions = [refresh_term, refresh_watchlist]
    cron_jobs = [
        # Staggered off the hour so all eight retailers are not hit at once, and
        # so a scheduled sweep never lines up with a user-triggered refresh.
        cron(refresh_watchlist, hour={3, 9, 15, 21}, minute=17, timeout=1800),
    ]
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown

    #: Retries are for infrastructure faults. Retailer-level failures are already
    #: handled inside the fan-out and must not re-run the whole job.
    max_tries = 3
    job_timeout = int(timedelta(minutes=10).total_seconds())
    keep_result = int(timedelta(hours=6).total_seconds())
    #: One retailer sweep at a time per worker: politeness beats throughput here.
    max_jobs = 4
