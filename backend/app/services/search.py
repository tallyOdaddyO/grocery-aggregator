"""Search orchestration across all retailers.

The contract this module upholds: **one retailer's failure never removes another
retailer's results.** Each connector runs in isolation with its own timeout, and a
connector that raises, hangs, or returns nothing is reported as degraded alongside
the results that did arrive - never silently dropped, and never allowed to empty
the response.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.connectors.base import (
    BaseRetailerConnector, ConnectorResult, NormalizedProduct,
)
from app.connectors.registry import build_connectors
from app.core.enums import PriceProvenance, RetailerStatus

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass
class RetailerReport:
    """Per-retailer outcome, always present in the response - success or not."""

    slug: str
    name: str
    status: RetailerStatus
    reason: str | None = None
    product_count: int = 0
    elapsed_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "status": self.status.value,
            "reason": self.reason,
            "product_count": self.product_count,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class SearchOutcome:
    """Everything a search produced, including everything it failed to produce."""

    term: str
    zip_code: str
    products: list[NormalizedProduct] = field(default_factory=list)
    reports: list[RetailerReport] = field(default_factory=list)
    searched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def degraded(self) -> list[RetailerReport]:
        return [r for r in self.reports if r.status is RetailerStatus.DEGRADED]

    @property
    def unavailable(self) -> list[RetailerReport]:
        return [r for r in self.reports if r.status is RetailerStatus.UNAVAILABLE]

    @property
    def healthy(self) -> list[RetailerReport]:
        return [r for r in self.reports if r.status is RetailerStatus.ACTIVE]

    @property
    def is_complete(self) -> bool:
        """False whenever any retailer failed to report - the UI must say so."""
        return not (self.degraded or self.unavailable)

    def as_dict(self) -> dict:
        return {
            "term": self.term,
            "zip": self.zip_code,
            "searched_at": self.searched_at.isoformat(),
            "is_complete": self.is_complete,
            "product_count": len(self.products),
            "retailers": [r.as_dict() for r in self.reports],
            "degraded_retailers": [r.as_dict() for r in self.degraded],
        }


class SearchService:
    """Fans a search out across connectors and merges what comes back."""

    def __init__(
        self,
        connectors: list[BaseRetailerConnector] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        source: str = "fixture",
    ) -> None:
        self.connectors = connectors if connectors is not None else build_connectors(source)
        self.timeout_seconds = timeout_seconds

    def search(self, term: str, zip_code: str) -> SearchOutcome:
        outcome = SearchOutcome(term=term, zip_code=zip_code)
        if not self.connectors:
            return outcome

        # NOTE: the executor is shut down with wait=False. Using it as a context
        # manager would call shutdown(wait=True) on exit, which blocks until the
        # slowest connector finishes - making the per-connector timeout report a
        # slow retailer as degraded while still paying its full latency, which
        # defeats the point of having a timeout at all. A connector that overran
        # its timeout is left running and its result discarded; connectors are
        # side-effect-free reads, so an abandoned one is harmless.
        pool = ThreadPoolExecutor(max_workers=max(1, len(self.connectors)))
        try:
            futures = {
                pool.submit(self._run_one, connector, term, zip_code): connector
                for connector in self.connectors
            }
            for future, connector in futures.items():
                try:
                    result = future.result(timeout=self.timeout_seconds)
                except FutureTimeout:
                    # The worker thread may still be running; we simply stop waiting
                    # on it. Its retailer is reported as degraded rather than
                    # holding up every other result.
                    logger.warning("connector %s timed out", connector.slug)
                    outcome.reports.append(
                        RetailerReport(
                            slug=connector.slug,
                            name=connector.name,
                            status=RetailerStatus.DEGRADED,
                            reason=(
                                f"{connector.name} did not respond within "
                                f"{self.timeout_seconds:g}s."
                            ),
                        )
                    )
                    continue
                except Exception as exc:
                    # Defence in depth: BaseRetailerConnector.search should never
                    # raise, but a broken subclass must not take the search down.
                    logger.exception("connector %s raised", connector.slug)
                    outcome.reports.append(
                        RetailerReport(
                            slug=connector.slug,
                            name=connector.name,
                            status=RetailerStatus.UNAVAILABLE,
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    continue

                outcome.products.extend(result.products)
                outcome.reports.append(
                    RetailerReport(
                        slug=result.retailer_slug,
                        name=result.retailer_name,
                        status=result.status,
                        reason=result.reason,
                        product_count=len(result.products),
                        elapsed_ms=result.elapsed_ms,
                    )
                )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        outcome.reports.sort(key=lambda r: r.slug)
        return outcome

    @staticmethod
    def _run_one(
        connector: BaseRetailerConnector, term: str, zip_code: str
    ) -> ConnectorResult:
        return connector.search(term, zip_code)
