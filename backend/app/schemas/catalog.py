"""Retailer, store, product, and price schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field, computed_field

from app.core.enums import PriceProvenance, PromotionType, RetailerStatus
from app.schemas.common import Freshness, Money, ORMModel


class RetailerOut(ORMModel):
    id: int
    slug: str
    name: str
    status: RetailerStatus
    status_reason: str | None = None
    supports_online_pricing: bool
    requires_membership: bool
    last_sync_at: datetime | None = None


class StoreOut(ORMModel):
    id: int
    retailer_id: int
    store_number: str
    name: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str
    is_primary_for_zip: bool
    #: False means the address is a placeholder awaiting confirmation.
    address_verified: bool


class ProductVariantOut(ORMModel):
    id: int
    product_id: int
    retailer_id: int
    retailer_sku: str
    upc: str | None = None
    display_name: str
    size_text: str | None = None
    pack_count: int
    net_content_value: float | None = None
    net_content_uom: str | None = None
    base_quantity: float | None = None
    base_uom: str | None = None
    uom_kind: str | None = None
    is_organic: bool
    attributes: dict = {}


class ProductOut(ORMModel):
    id: int
    upc: str | None = None
    display_name: str
    normalized_name: str
    brand: str | None = None
    category: str | None = None
    attributes: dict = {}


class PriceOut(ORMModel):
    """A price, always carrying its trust grade and its age.

    ``provenance`` and ``freshness`` are not optional decorations - a price shown
    without them invites the user to assume it is a verified shelf price.
    """

    id: int
    variant_id: int
    store_id: int

    price_cents: int
    regular_price_cents: int | None = None
    currency: str = "USD"

    unit_price_cents: float | None = None
    unit_price_uom: str | None = None

    promotion_type: PromotionType = PromotionType.NONE
    promotion_text: str | None = None

    provenance: PriceProvenance
    is_verified_in_store: bool
    observed_at: datetime
    source_url: str | None = None

    @computed_field
    @property
    def price(self) -> Money:
        return Money(cents=self.price_cents, currency=self.currency)

    @computed_field
    @property
    def unit_price_display(self) -> str | None:
        """Sticker price and unit price are always rendered separately."""
        if self.unit_price_cents is None or not self.unit_price_uom:
            return None
        dollars = self.unit_price_cents / 100
        precision = 4 if dollars < 0.1 else 2
        return f"${dollars:,.{precision}f}/{self.unit_price_uom}"

    @computed_field
    @property
    def freshness(self) -> Freshness:
        return Freshness(
            observed_at=self.observed_at,
            is_stale=self.provenance == PriceProvenance.STALE,
        )


class DegradedRetailer(ORMModel):
    """Reported alongside results so an incomplete search never looks complete."""

    slug: str
    name: str
    status: RetailerStatus
    reason: str = Field(description="Plain-language explanation for the user")
