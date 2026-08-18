"""POST /api/v1/compare-basket - cheapest single trip vs cheapest split."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.v1.common import STATUS_MAP, price_data_from_product
from app.core.enums import RetailerStatus
from app.schemas.basket import (
    BasketLineItem, BasketPlan, CompareBasketRequest, CompareBasketResponse,
    RetailerTrip, UnavailableItem,
)
from app.schemas.search import ConnectorHealth, RetailerID
from app.services.basket import (
    BasketResult, ItemOptions, build_options, cheapest_complete, cheapest_split,
)
from app.services.search import SearchService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["basket"])

#: Worst-wins ordering when a retailer behaved differently across item searches.
_SEVERITY = {
    RetailerStatus.ACTIVE: 0,
    RetailerStatus.DEGRADED: 1,
    RetailerStatus.UNAVAILABLE: 2,
}


def get_search_service() -> SearchService:
    """Overridable dependency, so tests can inject failing connectors."""
    from app.core.config import get_settings

    return SearchService(source=get_settings().retailscout_source)


def _merged_health(result: BasketResult) -> tuple[list[ConnectorHealth], bool]:
    """Collapse per-search reports into one row per retailer.

    A basket runs several searches; a retailer that failed in any of them is
    reported as failed for the basket. Taking the best result would let a
    retailer's outage disappear behind an unrelated successful lookup.
    """
    worst: dict[str, tuple[RetailerStatus, int, str | None, str]] = {}
    complete = True
    for outcome in result.outcomes:
        complete = complete and outcome.is_complete
        for report in outcome.reports:
            existing = worst.get(report.slug)
            if existing is None or _SEVERITY[report.status] > _SEVERITY[existing[0]]:
                worst[report.slug] = (
                    report.status, report.elapsed_ms, report.reason, report.name
                )
            elif _SEVERITY[report.status] == _SEVERITY[existing[0]]:
                worst[report.slug] = (
                    existing[0],
                    max(existing[1], report.elapsed_ms),
                    existing[2] or report.reason,
                    existing[3],
                )

    health: list[ConnectorHealth] = []
    for slug in sorted(worst):
        status, latency, reason, _name = worst[slug]
        try:
            retailer = RetailerID(slug)
        except ValueError:
            continue
        health.append(
            ConnectorHealth(
                retailer=retailer,
                status=STATUS_MAP[status],
                latency_ms=latency,
                error_reason=reason,
            )
        )
    return health, complete


def _line(option: ItemOptions, product, searched_at) -> BasketLineItem | None:
    try:
        retailer = RetailerID(product.retailer_slug)
    except ValueError:
        return None
    return BasketLineItem(
        query=option.query,
        quantity=option.quantity,
        product_id=f"{product.retailer_slug}:{product.retailer_sku}",
        retailer=retailer,
        title=product.display_name,
        size_raw=product.size_text or "",
        price=price_data_from_product(product, searched_at),
        # Exact integer cents. Never float dollars: a basket sums many lines and
        # float error compounds across them.
        line_total_cents=product.price.price_cents * option.quantity,
        notes=list(product.notes),
    )


def _trip(slug: str, lines: list[BasketLineItem], result: BasketResult) -> RetailerTrip:
    store = result.stores.get(slug)
    return RetailerTrip(
        retailer=RetailerID(slug),
        store_number=store.store_number if store else "unknown",
        items=lines,
        subtotal_cents=sum(line.line_total_cents for line in lines),
    )


def build_response(result: BasketResult) -> CompareBasketResponse:
    health, complete = _merged_health(result)
    searched_at = (
        result.outcomes[0].searched_at if result.outcomes else None
    )

    complete_plan: BasketPlan | None = None
    best = cheapest_complete(result)
    if best is not None:
        slug, options, total = best
        lines = [
            line
            for line in (
                _line(option, option.by_retailer[slug], searched_at)
                for option in options
            )
            if line is not None
        ]
        if lines:
            complete_plan = BasketPlan(
                strategy="single_store",
                trips=[_trip(slug, lines, result)],
                total_cents=total,
                item_count=sum(line.quantity for line in lines),
                stop_count=1,
            )

    split_plan: BasketPlan | None = None
    trips_by_retailer = cheapest_split(result)
    if trips_by_retailer:
        trips: list[RetailerTrip] = []
        for slug in sorted(trips_by_retailer):
            lines = [
                line
                for line in (
                    _line(option, product, searched_at)
                    for option, product in trips_by_retailer[slug]
                )
                if line is not None
            ]
            if lines:
                trips.append(_trip(slug, lines, result))
        if trips:
            split_plan = BasketPlan(
                strategy="split",
                trips=trips,
                total_cents=sum(trip.subtotal_cents for trip in trips),
                item_count=sum(
                    line.quantity for trip in trips for line in trip.items
                ),
                stop_count=len(trips),
            )

    savings = None
    if complete_plan and split_plan:
        savings = complete_plan.total_cents - split_plan.total_cents

    return CompareBasketResponse(
        zip_code=result.zip_code,
        is_complete=complete,
        connector_health=health,
        cheapest_complete=complete_plan,
        cheapest_split=split_plan,
        savings_cents=savings,
        unavailable_items=[
            UnavailableItem(
                query=option.query,
                quantity=option.quantity,
                reason=option.reason_unavailable or "No published price available.",
            )
            for option in result.unavailable
        ],
    )


@router.post("/compare-basket", response_model=CompareBasketResponse)
def compare_basket(
    request: CompareBasketRequest,
    service: SearchService = Depends(get_search_service),
) -> CompareBasketResponse:
    """Compare buying a basket in one stop versus shopping around.

    ``cheapest_complete`` is null unless a single retailer stocks every requested
    item at a published price. Items nobody can supply are listed in
    ``unavailable_items`` rather than dropped from the totals.
    """
    result = build_options(
        service,
        [(item.query, item.quantity) for item in request.items],
        request.zip_code,
    )
    return build_response(result)
