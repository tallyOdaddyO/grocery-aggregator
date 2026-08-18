"""Refresh prices for a set of search terms and persist every observation.

This is the manual form of what the Phase 7 worker will do on a schedule: run a
search across every connector, then ingest the result so `prices` reflects the
latest reading and `price_observations` keeps the full history.

Usage:
    python scripts/refresh.py milk cheerios cola
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services.ingest import ingest_outcome  # noqa: E402
from app.services.search import SearchService  # noqa: E402

DEFAULT_TERMS = ["milk", "cheerios", "cola", "peanut butter", "guacamole", "malanga"]


def main() -> None:
    settings = get_settings()
    terms = sys.argv[1:] or DEFAULT_TERMS
    service = SearchService(source=settings.retailscout_source)

    print(f"Database: {engine.url.render_as_string(hide_password=True)}")
    print(f"Source:   {settings.retailscout_source}\n")

    totals = {"observations": 0, "variants": 0, "unpriced": 0}
    with SessionLocal() as session:
        for term in terms:
            outcome = service.search(term, settings.target_zip)
            stats = ingest_outcome(session, outcome)
            totals["observations"] += stats.observations_appended
            totals["variants"] += stats.variants
            totals["unpriced"] += stats.skipped_unpriced
            degraded = [r.slug for r in outcome.degraded + outcome.unavailable]
            print(
                f"{term:<16} products={len(outcome.products):<3} "
                f"observations+={stats.observations_appended:<3} "
                f"degraded={','.join(degraded) or 'none'}"
            )

    print(
        f"\nNew variants: {totals['variants']}  "
        f"Observations appended: {totals['observations']}  "
        f"Stocked but unpriced: {totals['unpriced']}"
    )


if __name__ == "__main__":
    main()
