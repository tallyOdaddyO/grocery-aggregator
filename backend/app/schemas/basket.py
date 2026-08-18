"""Wire schemas for POST /api/v1/compare-basket.

`CompareBasketRequest` / `CompareBasketResponse` were not supplied, so they follow
the conventions already established: integer cents everywhere, a full `PriceData`
(with provenance) on every line, reuse of `RetailerID` and `ConnectorHealth`, and
explicit reporting of anything that could not be sourced.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.search import ConnectorHealth, PriceData, RetailerID


class BasketItemRequest(BaseModel):
    query: str = Field(min_length=1, description="What to buy, e.g. 'milk'")
    quantity: int = Field(default=1, ge=1, le=99)


class CompareBasketRequest(BaseModel):
    zip_code: str = Field(default="33009", min_length=5, max_length=10)
    items: List[BasketItemRequest] = Field(min_length=1, max_length=50)


class BasketLineItem(BaseModel):
    """One line of a plan, resolved to a specific product at a specific store."""

    query: str
    quantity: int
    product_id: str
    retailer: RetailerID
    title: str
    size_raw: str
    price: PriceData
    #: quantity x sticker price, in exact integer cents.
    line_total_cents: int
    #: Caveats carried from the adapter, e.g. ``multi_buy_required`` (the shown
    #: price is per item but only applies if you buy N), ``membership_required``,
    #: or ``price_from_circular``. Surfaced so a total is never quietly wrong.
    notes: List[str] = []


class RetailerTrip(BaseModel):
    """Everything to buy at one store - one physical stop."""

    retailer: RetailerID
    store_number: str
    items: List[BasketLineItem]
    subtotal_cents: int


class BasketPlan(BaseModel):
    """A concrete way to buy the basket.

    Each line names one specific product, not a category. A free-text query like
    "milk" resolves to a single equivalence group - one brand at one size - because
    the matcher vetoes cross-brand and cross-size substitution. The basket compares
    like for like; it does not silently swap in a different product to win on price.
    """

    strategy: str = Field(description="'single_store' or 'split'")
    trips: List[RetailerTrip]
    total_cents: int
    item_count: int
    #: How many stores you must physically visit.
    stop_count: int


class UnavailableItem(BaseModel):
    """An item no retailer in range could supply at a published price.

    Reported explicitly rather than dropped: a basket that silently omits a line
    understates its own total.
    """

    query: str
    quantity: int
    reason: str


class CompareBasketResponse(BaseModel):
    zip_code: str
    #: False when any retailer failed to report, so a partial comparison is never
    #: presented as an exhaustive one.
    is_complete: bool
    connector_health: List[ConnectorHealth]

    #: Cheapest basket obtainable in ONE stop. Null when no single retailer stocks
    #: every requested item - never approximated by substituting another store.
    cheapest_complete: Optional[BasketPlan] = None
    #: Cheapest total across all retailers, one trip per contributing store.
    cheapest_split: Optional[BasketPlan] = None
    #: cheapest_complete.total - cheapest_split.total, when both exist.
    savings_cents: Optional[int] = None

    unavailable_items: List[UnavailableItem] = []
