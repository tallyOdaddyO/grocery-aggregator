"""GET /api/v1/health - service status and per-connector reachability."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.v1.common import STATUS_MAP
from app.connectors.base import BaseRetailerConnector
from app.connectors.registry import build_connectors
from app.core.config import get_settings
from app.schemas.search import ConnectorHealth, RetailerID

router = APIRouter(prefix="/api/v1", tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    target_zip: str
    source: str
    checked_at: datetime
    #: Per-retailer reachability for the requested ZIP. This only resolves the
    #: local store - it deliberately does not fetch prices, so the dashboard stays
    #: cheap and cannot be mistaken for a price refresh.
    connector_health: list[ConnectorHealth]


def get_connectors() -> list[BaseRetailerConnector]:
    return build_connectors(get_settings().retailscout_source)


@router.get("/health", response_model=HealthResponse)
def health(
    zip_code: str = Query("33009", alias="zip", min_length=5, max_length=10),
    connectors: list[BaseRetailerConnector] = Depends(get_connectors),
) -> HealthResponse:
    settings = get_settings()
    reports: list[ConnectorHealth] = []

    for connector in connectors:
        try:
            retailer = RetailerID(connector.slug)
        except ValueError:
            continue
        # health() already contains its own error handling; this guard is for a
        # subclass that overrides it badly. One broken connector must not take
        # down the status page that exists to report broken connectors.
        try:
            result = connector.health(zip_code)
            status, reason = result.status, result.reason
        except Exception as exc:  # pragma: no cover - defensive
            from app.core.enums import RetailerStatus

            status, reason = RetailerStatus.UNAVAILABLE, f"{type(exc).__name__}: {exc}"
        reports.append(
            ConnectorHealth(
                retailer=retailer,
                status=STATUS_MAP[status],
                latency_ms=0,
                error_reason=reason,
            )
        )

    return HealthResponse(
        status="ok",
        target_zip=settings.target_zip,
        source=settings.retailscout_source,
        checked_at=datetime.now(timezone.utc),
        connector_health=reports,
    )
