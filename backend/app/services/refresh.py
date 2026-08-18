"""Refresh orchestration and retailer health bookkeeping.

Kept free of any queue library so it can be exercised directly in tests: the ARQ
job in ``workers/`` is a thin wrapper around :func:`run_refresh`.

The circuit breaker lives here. A retailer that fails repeatedly is moved to
``unavailable`` so the fan-out stops paying its latency, and is restored the
moment it succeeds again - failures are counted, not remembered forever.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import RetailerStatus
from app.models import Retailer
from app.services.ingest import IngestStats, ingest_outcome
from app.services.search import SearchOutcome, SearchService

#: Consecutive failures before a retailer is tripped to ``unavailable``.
CIRCUIT_BREAKER_THRESHOLD = 3


@dataclass
class RefreshReport:
    term: str
    zip_code: str
    products_seen: int = 0
    observations_appended: int = 0
    degraded: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    tripped: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "term": self.term,
            "zip": self.zip_code,
            "products_seen": self.products_seen,
            "observations_appended": self.observations_appended,
            "degraded": self.degraded,
            "unavailable": self.unavailable,
            "tripped": self.tripped,
            "recovered": self.recovered,
        }


def apply_health(session: Session, outcome: SearchOutcome, report: RefreshReport) -> None:
    """Record each retailer's outcome and advance the circuit breaker.

    Status is written from what actually happened on this run. A retailer that was
    blocked is stored as ``degraded`` with the reason, so the API and the dashboard
    can explain the gap instead of quietly showing fewer results.
    """
    for retailer_report in outcome.reports:
        retailer = session.scalar(
            select(Retailer).where(Retailer.slug == retailer_report.slug)
        )
        if retailer is None:
            continue

        succeeded = retailer_report.status is RetailerStatus.ACTIVE
        if succeeded:
            if retailer.consecutive_failures:
                report.recovered.append(retailer.slug)
            retailer.consecutive_failures = 0
            retailer.status = RetailerStatus.ACTIVE
            retailer.status_reason = None
        else:
            retailer.consecutive_failures += 1
            retailer.status = retailer_report.status
            retailer.status_reason = retailer_report.reason
            if retailer.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                retailer.status = RetailerStatus.UNAVAILABLE
                retailer.status_reason = (
                    f"Circuit breaker tripped after "
                    f"{retailer.consecutive_failures} consecutive failures. "
                    f"Last reason: {retailer_report.reason}"
                )
                report.tripped.append(retailer.slug)

        retailer.last_sync_at = outcome.searched_at

        if retailer_report.status is RetailerStatus.DEGRADED:
            report.degraded.append(retailer.slug)
        elif retailer_report.status is RetailerStatus.UNAVAILABLE:
            report.unavailable.append(retailer.slug)

    session.flush()


def run_refresh(
    session: Session, service: SearchService, term: str, zip_code: str
) -> RefreshReport:
    """Search every retailer for a term and persist what came back.

    Never raises for retailer-level problems: the fan-out already isolates them,
    and a refresh job that dies on one blocked retailer would stop refreshing the
    other seven.
    """
    report = RefreshReport(term=term, zip_code=zip_code)
    outcome = service.search(term, zip_code)
    report.products_seen = len(outcome.products)

    stats: IngestStats = ingest_outcome(session, outcome)
    report.observations_appended = stats.observations_appended

    apply_health(session, outcome, report)
    session.commit()
    return report
