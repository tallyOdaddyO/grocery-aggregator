"""Walmart, Costco, and BJ's.

Three completely different payload shapes - nested objects with float dollars,
warehouse listings with "$" strings, and a club feed that already speaks cents -
all reduced to the same contract before returning.
"""
from __future__ import annotations

from app.connectors.base import (
    BaseRetailerConnector, NormalizedProduct, StoreRef,
)
from app.connectors.fixtures import (
    captured_at, clean_title, load_json, matches, parse_date, parse_price_text,
)
from app.core.enums import PriceProvenance, PromotionType


class WalmartConnector(BaseRetailerConnector):
    """Walmart: store-scoped online pricing, heavy bot protection when live."""

    slug = "walmart"
    name = "Walmart"
    supports_online_pricing = True
    default_provenance = PriceProvenance.VERIFIED_ONLINE

    def resolve_store(self, zip_code: str) -> StoreRef | None:
        for store in load_json(self.slug, "stores.json").get("stores", []):
            address = store.get("address") or {}
            if address.get("postalCode") == zip_code:
                return StoreRef(
                    retailer_slug=self.slug,
                    store_number=str(store["id"]),
                    name=store.get("displayName"),
                    address_line1=address.get("line1"),
                    city=address.get("city"),
                    state=address.get("state"),
                    zip=zip_code,
                    address_verified=bool(store.get("addressVerified")),
                )
        return None

    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        payload = load_json(self.slug, "catalog.json")
        self._observed_at = captured_at(payload)
        items = payload.get("searchResult", {}).get("items", [])
        return [i for i in items if matches(term, i.get("name"), i.get("brand"))]

    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        title = clean_title(raw.get("name"))
        if not title or not raw.get("usItemId"):
            return None  # Not an error, just not a product record.
        price = (raw.get("priceInfo") or {}).get("currentPrice") or {}
        cents, _ = parse_price_text(price.get("price"))
        return self.build_product(
            store=store,
            sku=raw["usItemId"],
            display_name=title,
            brand=raw.get("brand"),
            upc=raw.get("upc"),
            category=raw.get("category"),
            price_cents=cents,
            observed_at=getattr(self, "_observed_at", None),
        )


class CostcoConnector(BaseRetailerConnector):
    """Costco: warehouse-scoped and membership-gated.

    Prices captured from costco.com are explicitly graded ``delivery_price``: they
    routinely differ from the warehouse shelf, and presenting them as verified
    would be a lie a shopper only discovers at the register.
    """

    slug = "costco"
    name = "Costco Wholesale"
    supports_online_pricing = True
    requires_membership = True
    default_provenance = PriceProvenance.DELIVERY_PRICE

    def resolve_store(self, zip_code: str) -> StoreRef | None:
        for warehouse in load_json(self.slug, "stores.json").get("warehouses", []):
            if warehouse.get("zipCode") == zip_code:
                return StoreRef(
                    retailer_slug=self.slug,
                    store_number=str(warehouse["warehouseNumber"]),
                    name=warehouse.get("name"),
                    city=warehouse.get("city"),
                    state=warehouse.get("state"),
                    zip=zip_code,
                    address_verified=bool(warehouse.get("addressVerified")),
                )
        return None

    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        payload = load_json(self.slug, "catalog.json")
        self._observed_at = captured_at(payload)
        return [
            p for p in payload.get("products", [])
            if matches(term, p.get("itemName"), p.get("brandName"))
        ]

    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        title = clean_title(raw.get("itemName"))
        if not title:
            return None
        cents, _ = parse_price_text(raw.get("listPrice"))
        return self.build_product(
            store=store,
            sku=raw["partNumber"],
            display_name=title,
            brand=raw.get("brandName"),
            upc=raw.get("itemUPC"),
            category=raw.get("category"),
            price_cents=cents,
            observed_at=getattr(self, "_observed_at", None),
            extra_notes=["membership_required"],
        )


class BJsConnector(BaseRetailerConnector):
    """BJ's: club-scoped. This feed already speaks integer cents."""

    slug = "bjs"
    name = "BJ's Wholesale Club"
    supports_online_pricing = True
    requires_membership = True
    default_provenance = PriceProvenance.VERIFIED_ONLINE

    def resolve_store(self, zip_code: str) -> StoreRef | None:
        for club in load_json(self.slug, "stores.json").get("clubs", []):
            if club.get("zip") == zip_code:
                return StoreRef(
                    retailer_slug=self.slug,
                    store_number=str(club["clubId"]),
                    name=club.get("clubName"),
                    city=club.get("city"),
                    state=club.get("state"),
                    zip=zip_code,
                    address_verified=bool(club.get("addressVerified")),
                )
        return None

    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        payload = load_json(self.slug, "catalog.json")
        self._observed_at = captured_at(payload)
        return [
            r for r in payload.get("results", [])
            if matches(term, r.get("description"), r.get("brand"))
        ]

    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        title = clean_title(raw.get("description"))
        if not title:
            return None
        amount = (raw.get("price") or {}).get("amount")
        cents = int(amount) if isinstance(amount, int) and amount > 0 else None

        savings = raw.get("instantSavings") or {}
        promotion = PromotionType.NONE
        promotion_text = None
        regular = None
        if savings.get("amount") and cents:
            promotion = PromotionType.MEMBER_PRICE
            promotion_text = f"Instant savings ${savings['amount'] / 100:.2f}"
            regular = cents + int(savings["amount"])

        return self.build_product(
            store=store,
            sku=raw["articleId"],
            display_name=title,
            brand=raw.get("brand"),
            upc=raw.get("gtin"),
            category=raw.get("category"),
            price_cents=cents,
            regular_price_cents=regular,
            promotion_type=promotion,
            promotion_text=promotion_text,
            promotion_ends_at=parse_date(savings.get("endsOn")),
            observed_at=getattr(self, "_observed_at", None),
            extra_notes=["membership_required"],
        )
