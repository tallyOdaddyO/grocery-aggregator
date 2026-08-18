"""Shared vocabulary for the whole system.

These enums are part of the contract with the client. Their *values* are what the
API emits and what the database stores, so they must not be renamed casually.
"""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """str-backed enum so values serialize cleanly to JSON and SQL."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class RetailerStatus(StrEnum):
    """Operational state of a retailer connector.

    ``unavailable`` means we write *no* price rows for this retailer at all -
    we never substitute a national or estimated price to fill the gap.
    """

    ACTIVE = "active"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class PriceProvenance(StrEnum):
    """How much we trust a price, ordered most to least trustworthy.

    This is recorded at observation time and never inferred later, with the single
    exception of demotion to ``STALE`` once a price outlives its category TTL.
    """

    VERIFIED_IN_STORE = "verified_in_store"
    VERIFIED_ONLINE = "verified_online"
    DELIVERY_PRICE = "delivery_price"
    ESTIMATED = "estimated"
    STALE = "stale"


#: Ranking used when choosing between two prices of differing trust.
PROVENANCE_RANK: dict[PriceProvenance, int] = {
    PriceProvenance.VERIFIED_IN_STORE: 0,
    PriceProvenance.VERIFIED_ONLINE: 1,
    PriceProvenance.DELIVERY_PRICE: 2,
    PriceProvenance.ESTIMATED: 3,
    PriceProvenance.STALE: 4,
}


class PromotionType(StrEnum):
    NONE = "none"
    SALE = "sale"
    BOGO = "bogo"
    MEMBER_PRICE = "member_price"
    DIGITAL_COUPON = "digital_coupon"
    CLEARANCE = "clearance"


class UomKind(StrEnum):
    """The dimension a unit measures. Only like kinds are comparable."""

    MASS = "mass"
    VOLUME = "volume"
    COUNT = "count"
    LENGTH = "length"


class MatchStage(StrEnum):
    """Which stage of the engine produced a match."""

    UPC = "upc"
    ATTRIBUTES = "attributes"
    FUZZY = "fuzzy"
    NONE = "none"


class BasketStrategy(StrEnum):
    SINGLE_STORE = "single_store"
    SPLIT = "split"
