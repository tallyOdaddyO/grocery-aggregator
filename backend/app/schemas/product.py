"""Wire schemas for GET /api/v1/product/{id}.

`ProductDetailResponse` was not supplied with the search schemas, so it is defined
here following the same conventions: integer cents, an explicit provenance record
on every price, and reuse of `RetailerID` / `PriceData` / `PriceProvenance` so the
two endpoints describe a price in exactly the same language.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.search import (
    PriceData, PriceProvenance, RetailerID, SearchProductSummary,
)


class StoreSummary(BaseModel):
    """Where the price was observed. Prices attach to stores, never to chains."""

    retailer: RetailerID
    store_number: str
    name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: str
    #: False when the address is a placeholder awaiting confirmation.
    address_verified: bool


class PriceObservationOut(BaseModel):
    """One entry from the append-only observation log."""

    observed_at: datetime
    sticker_price_cents: int
    unit_price_cents: Optional[int] = None
    unit_measure: str
    promotion_type: str
    provenance: PriceProvenance


class MatchSignalOut(BaseModel):
    name: str
    detail: str
    weight: float


class ConfidenceStats(BaseModel):
    """Why this product was matched to its equivalents - or why it stands alone."""

    match_confidence: float = Field(ge=0.0, le=1.0)
    match_type: str
    threshold: float
    #: Hard constraints that were evaluated AND passed. A check that could not be
    #: evaluated is absent rather than listed: this reports what was verified,
    #: never what was assumed.
    veto_checks_passed: List[str] = []
    veto_checks_failed: List[str] = []
    signals: List[MatchSignalOut] = []
    explanation: str
    equivalent_count: int = 0


class ProductDetailResponse(BaseModel):
    id: str
    retailer: RetailerID
    title: str
    brand: Optional[str] = None
    category: Optional[str] = None
    upc: Optional[str] = None
    size_raw: str
    pack_count: int

    store: StoreSummary
    #: Sticker price and unit price are carried as separate fields on PriceData
    #: and are never collapsed into one another.
    current_price: PriceData
    #: Newest observation first. Append-only; entries are never rewritten.
    price_history: List[PriceObservationOut] = []
    confidence_stats: ConfidenceStats
    #: Equivalent products at other retailers, cleared by the same veto checks.
    equivalent_products: List[SearchProductSummary] = []
