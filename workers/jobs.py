"""Background jobs.

Each job is a thin wrapper: the real work lives in ``app.services.refresh`` so it
can be tested without Redis, and so a queue change never rewrites business logic.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.refresh import run_refresh  # noqa: E402
from app.services.search import SearchService  # noqa: E402

logger = logging.getLogger(__name__)

#: Terms swept on a schedule. In production this would come from the union of
#: saved shopping lists rather than a constant.
WATCHLIST = ["milk", "eggs", "bread", "cheerios", "cola", "peanut butter", "rice"]


async def startup(ctx: dict) -> None:
    settings = get_settings()
    ctx["service"] = SearchService(source=settings.retailscout_source)
    ctx["zip"] = settings.target_zip
    logger.info("worker started (source=%s zip=%s)", settings.retailscout_source, settings.target_zip)


async def shutdown(ctx: dict) -> None:
    logger.info("worker shutting down")


def _refresh_sync(service: SearchService, term: str, zip_code: str) -> dict:
    """Blocking body, run in a thread so the event loop stays free."""
    with SessionLocal() as session:
        return run_refresh(session, service, term, zip_code).as_dict()


async def refresh_term(ctx: dict, term: str, zip_code: str | None = None) -> dict:
    """Refresh one search term across every retailer.

    Retailer failures are recorded, never raised: raising would mark the job failed
    and re-run the whole fan-out because one retailer is blocked.
    """
    import anyio

    service: SearchService = ctx.get("service") or SearchService(
        source=get_settings().retailscout_source
    )
    zip_code = zip_code or ctx.get("zip") or get_settings().target_zip

    report = await anyio.to_thread.run_sync(_refresh_sync, service, term, zip_code)
    logger.info(
        "refreshed %-16s observations=%s degraded=%s",
        term, report["observations_appended"], report["degraded"],
    )
    return report


async def refresh_watchlist(ctx: dict, terms: list[str] | None = None) -> dict:
    """Scheduled sweep over the watchlist."""
    results = []
    for term in terms or WATCHLIST:
        results.append(await refresh_term(ctx, term))
    return {
        "terms": len(results),
        "observations_appended": sum(r["observations_appended"] for r in results),
        "reports": results,
    }
