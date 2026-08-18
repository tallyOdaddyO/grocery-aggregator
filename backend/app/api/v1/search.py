"""GET /api/v1/search - normalized products grouped by match."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.connectors.base import NormalizedProduct
from app.api.v1.common import (
    FRESHNESS_TTL, STATUS_MAP, VERIFICATION_MAP, is_fresh, no_price_published,
    provenance_out,
)
from app.core.config import get_settings
from app.core.enums import PriceProvenance as InternalProvenance
from app.schemas.search import (
    ConnectorHealth, MatchGroup, PriceData, RetailerID, SearchProductSummary,
    SearchResponse,
)
from app.services.grouping import group_products
from app.services.search import SearchOutcome, SearchService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])



def get_search_service() -> SearchService:
    """Overridable dependency, so tests can inject failing connectors."""
    settings = get_settings()
    return SearchService(source=settings.retailscout_source)


def _to_price_data(product: NormalizedProduct, fallback_time: datetime) -> PriceData:
    """Render a price for the wire, or an explicit 'no price published' record."""
    if product.price is None:
        # The retailer stocks the item but publishes no price. This is a real,
        # reportable state - not an absence to be filled with an estimate.
        return no_price_published(fallback_time)

    price = product.price
    return PriceData(
        sticker_price_cents=price.price_cents,
        # The wire type is an int; ranking server-side always uses the exact
        # fractional value, never this rounded one.
        unit_price_cents=(
            round(price.unit_price_cents) if price.unit_price_cents is not None else None
        ),
        unit_measure=price.unit_price_uom or "unknown",
        provenance=provenance_out(
            price.provenance, price.observed_at, price.source_url
        ),
    )


def _to_summary(
    product: NormalizedProduct, fallback_time: datetime
) -> SearchProductSummary | None:
    try:
        retailer = RetailerID(product.retailer_slug)
    except ValueError:
        # A connector outside the known set must not break the response.
        logger.warning("unknown retailer slug %s omitted", product.retailer_slug)
        return None
    return SearchProductSummary(
        id=f"{product.retailer_slug}:{product.retailer_sku}",
        retailer=retailer,
        title=product.display_name,
        size_raw=product.size_text or "",
        price=_to_price_data(product, fallback_time),
    )


def build_response(outcome: SearchOutcome) -> SearchResponse:
    """Translate an orchestrator outcome into the wire contract."""
    health: list[ConnectorHealth] = []
    for report in outcome.reports:
        try:
            retailer = RetailerID(report.slug)
        except ValueError:
            continue
        health.append(
            ConnectorHealth(
                retailer=retailer,
                status=STATUS_MAP[report.status],
                latency_ms=report.elapsed_ms,
                error_reason=report.reason,
            )
        )

    results: list[MatchGroup] = []
    for group in group_products(outcome.products):
        items = [
            summary
            for summary in (
                _to_summary(member, outcome.searched_at) for member in group.members
            )
            if summary is not None
        ]
        if not items:
            continue
        results.append(
            MatchGroup(
                group_id=group.group_id,
                canonical_name=group.canonical_name,
                match_type=group.match_type,
                items=items,
            )
        )

    return SearchResponse(
        query=outcome.term,
        zip_code=outcome.zip_code,
        # False whenever any retailer failed to report, so a partial search is
        # never presented as a complete one.
        is_complete=outcome.is_complete,
        connector_health=health,
        results=results,
    )


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, description="Search term, e.g. 'milk'"),
    zip_code: str = Query("33009", alias="zip", min_length=5, max_length=10),
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Search every retailer and return results grouped by product equivalence.

    A retailer that fails, times out, or has no local store degrades only itself:
    the request still returns 200 with whatever the others produced, and
    ``is_complete`` is False.
    """
    outcome = service.search(q, zip_code)
    return build_response(outcome)
