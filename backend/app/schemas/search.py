"""Wire schemas for GET /api/v1/search.

These models are the external contract and are defined exactly as specified.

:class:`VerificationMethod` carries ``verified_online`` as a first-class grade, so
a live store-scoped retailer price is distinguishable from a circular-derived
estimate. ``stale`` has no slot and maps to the underlying method with
``is_fresh=False``; the precise internal grade is always preserved verbatim in
:attr:`PriceProvenance.status`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


class RetailerID(str, Enum):
    WALMART = "walmart"
    COSTCO = "costco"
    BJS = "bjs"
    PUBLIX = "publix"
    WINN_DIXIE = "winn_dixie"
    FRESCO_Y_MAS = "fresco_y_mas"
    PRESIDENTE = "presidente"
    REY_CHAVEZ = "rey_chavez"


class VerificationMethod(str, Enum):
    VERIFIED_IN_STORE = "verified_in_store"
    #: A live, store-scoped price published by the retailer. Real, but not
    #: confirmed against the shelf - the distinction between a Publix ad price and
    #: a Presidente circular estimate, which the provenance principle requires.
    VERIFIED_ONLINE = "verified_online"
    DELIVERY_PRICE = "delivery_price"
    ESTIMATED = "estimated"
    NO_PRICE_PUBLISHED = "no_price_published"


class ConnectorStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class PriceProvenance(BaseModel):
    #: The exact internal grade (verified_in_store, verified_online,
    #: delivery_price, estimated, stale, no_price_published). Finer-grained than
    #: ``verification_method``, and never lossy.
    status: str
    timestamp: datetime
    source_url: Optional[HttpUrl] = None
    verification_method: VerificationMethod
    is_fresh: bool


class PriceData(BaseModel):
    sticker_price_cents: Optional[int]
    unit_price_cents: Optional[int]
    unit_measure: str
    provenance: PriceProvenance


class ConnectorHealth(BaseModel):
    retailer: RetailerID
    status: ConnectorStatus
    latency_ms: int
    error_reason: Optional[str] = None


class SearchProductSummary(BaseModel):
    id: str
    retailer: RetailerID
    title: str
    size_raw: str
    price: PriceData


class MatchGroup(BaseModel):
    group_id: str
    canonical_name: str
    match_type: str
    items: List[SearchProductSummary]


class SearchResponse(BaseModel):
    query: str
    zip_code: str = Field(default="33009")
    is_complete: bool
    connector_health: List[ConnectorHealth]
    results: List[MatchGroup]
