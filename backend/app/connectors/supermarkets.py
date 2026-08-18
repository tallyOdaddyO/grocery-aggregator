"""Publix, Winn-Dixie, Fresco y Más, Presidente, and Rey Chavez."""
from __future__ import annotations

import re
from html.parser import HTMLParser

from app.connectors.base import (
    BaseRetailerConnector, NormalizedProduct, StoreRef,
)
from app.connectors.fixtures import (
    captured_at, clean_title, load_json, load_text, matches, parse_date,
    parse_price_text,
)
from app.core.enums import PriceProvenance, PromotionType


class PublixConnector(BaseRetailerConnector):
    """Publix: store-scoped weekly ad, with multi-buy and BOGO pricing."""

    slug = "publix"
    name = "Publix"
    supports_online_pricing = True
    default_provenance = PriceProvenance.VERIFIED_ONLINE

    def resolve_store(self, zip_code: str) -> StoreRef | None:
        for store in load_json(self.slug, "stores.json").get("stores", []):
            if store.get("zipCode") == zip_code:
                return StoreRef(
                    retailer_slug=self.slug,
                    store_number=str(store["storeNumber"]),
                    name=store.get("name"),
                    city=store.get("city"),
                    state=store.get("state"),
                    zip=zip_code,
                    address_verified=bool(store.get("addressVerified")),
                )
        return None

    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        payload = load_json(self.slug, "catalog.json")
        self._observed_at = captured_at(payload)
        items = payload.get("ad", {}).get("items", [])
        return [
            i for i in items
            if matches(term, clean_title(i.get("title")), i.get("brand"))
        ]

    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        title = clean_title(raw.get("title"))
        if not title:
            return None

        cents, multi_buy_qty = parse_price_text(raw.get("priceText"))
        promotion = PromotionType.NONE
        promotion_text = None
        notes: list[str] = []

        label = (raw.get("promotion") or "").strip().upper()
        if label == "BOGO":
            promotion = PromotionType.BOGO
            promotion_text = "Buy one get one free"
        elif multi_buy_qty:
            promotion = PromotionType.SALE
            # The per-unit price is only real if you buy the required quantity.
            promotion_text = (
                f"{raw.get('priceText')} - price shown is per item and requires "
                f"buying {multi_buy_qty}"
            )
            notes.append("multi_buy_required")
        elif label:
            promotion = PromotionType.SALE
            promotion_text = raw.get("promotion")

        return self.build_product(
            store=store,
            sku=raw["id"],
            display_name=title,
            brand=raw.get("brand"),
            upc=raw.get("upc"),
            category=raw.get("category"),
            price_cents=cents,
            promotion_type=promotion,
            promotion_text=promotion_text,
            promotion_ends_at=parse_date(raw.get("promotionEnds")),
            observed_at=getattr(self, "_observed_at", None),
            extra_notes=notes,
        )


class _SEGCircularConnector(BaseRetailerConnector):
    """Shared implementation for the Southeastern Grocers circular platform.

    Winn-Dixie and Fresco y Más publish the same payload shape under different
    banners, so the parsing lives once here. Only identity and store lookup differ.
    """

    default_provenance = PriceProvenance.VERIFIED_ONLINE
    supports_online_pricing = True

    def resolve_store(self, zip_code: str) -> StoreRef | None:
        for store in load_json(self.slug, "stores.json").get("stores", []):
            if store.get("postal") == zip_code:
                return StoreRef(
                    retailer_slug=self.slug,
                    store_number=str(store["storeId"]),
                    name=store.get("banner"),
                    city=store.get("city"),
                    state=store.get("state"),
                    zip=zip_code,
                    address_verified=bool(store.get("addressVerified")),
                )
        return None

    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        payload = load_json(self.slug, "catalog.json")
        self._observed_at = captured_at(payload)
        products = payload.get("circular", {}).get("products", [])
        return [
            p for p in products if matches(term, p.get("name"), p.get("brandName"))
        ]

    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        title = clean_title(raw.get("name"))
        if not title:
            return None

        cents, _ = parse_price_text(raw.get("currentPrice"))
        was_cents, _ = parse_price_text(raw.get("wasPrice"))

        promotion = PromotionType.NONE
        promotion_text = None
        if (raw.get("dealType") or "").upper() == "SALE" and cents:
            promotion = PromotionType.SALE
            promotion_text = "On sale" + (
                f", was ${was_cents / 100:.2f}" if was_cents else ""
            )

        return self.build_product(
            store=store,
            sku=raw["sku"],
            display_name=title,
            brand=raw.get("brandName"),
            upc=raw.get("barcode"),
            category=raw.get("dept"),
            price_cents=cents,
            regular_price_cents=was_cents,
            promotion_type=promotion,
            promotion_text=promotion_text,
            promotion_ends_at=parse_date(raw.get("dealEnds")),
            observed_at=getattr(self, "_observed_at", None),
        )


class WinnDixieConnector(_SEGCircularConnector):
    slug = "winn_dixie"
    name = "Winn-Dixie"


class FrescoYMasConnector(_SEGCircularConnector):
    slug = "fresco_y_mas"
    name = "Fresco y Mas"


class _CircularHTMLParser(HTMLParser):
    """Extract items from the Presidente circular.

    Written against ``html.parser`` from the standard library: the markup is simple
    and regular, and a scraping dependency is not worth carrying for it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict] = []
        self._current: dict | None = None
        self._field: str | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "item" in classes:
            self._current = {"sku": attributes.get("data-sku")}
            return
        if self._current is None:
            return
        if "title" in classes:
            self._field = "title"
        elif "brand" in classes:
            self._field = "brand"
        elif "price" in classes:
            self._field = "price"
        elif "dept" in classes:
            self._field = "dept"

    def handle_data(self, data):
        if self._current is not None and self._field:
            self._current[self._field] = (
                self._current.get(self._field, "") + data
            ).strip()

    def handle_endtag(self, tag):
        self._field = None
        if tag == "div" and self._current is not None and "title" in self._current:
            self.items.append(self._current)
            self._current = None


class PresidenteConnector(BaseRetailerConnector):
    """Presidente: a regional chain whose circular is only available as markup.

    Prices derived from a circular are graded ``estimated``: the circular is a
    published advertisement, not a reading of the shelf or the register.
    """

    slug = "presidente"
    name = "Presidente Supermarket"
    supports_online_pricing = False
    default_provenance = PriceProvenance.ESTIMATED

    def resolve_store(self, zip_code: str) -> StoreRef | None:
        for store in load_json(self.slug, "stores.json").get("stores", []):
            if store.get("zip") == zip_code:
                return StoreRef(
                    retailer_slug=self.slug,
                    store_number=str(store["id"]),
                    name=store.get("name"),
                    city=store.get("city"),
                    state=store.get("state"),
                    zip=zip_code,
                    address_verified=bool(store.get("addressVerified")),
                )
        return None

    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        markup = load_text(self.slug, "circular.html")
        parser = _CircularHTMLParser()
        parser.feed(markup)
        captured = re.search(r'data-captured="([^"]+)"', markup)
        self._observed_at = parse_date(captured.group(1)) if captured else None
        return [
            i for i in parser.items
            if matches(term, i.get("title"), i.get("brand"))
        ]

    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        title = clean_title(raw.get("title"))
        if not title or not raw.get("sku"):
            # An item with no SKU cannot be tracked across refreshes.
            return None
        cents, multi_buy_qty = parse_price_text(raw.get("price"))
        notes = ["multi_buy_required"] if multi_buy_qty else []
        return self.build_product(
            store=store,
            sku=raw["sku"],
            display_name=title,
            brand=raw.get("brand") or None,
            upc=None,  # The circular carries no barcodes at all.
            category=raw.get("dept"),
            price_cents=cents,
            promotion_type=(
                PromotionType.SALE if multi_buy_qty else PromotionType.NONE
            ),
            promotion_text=(
                f"{raw.get('price')} - requires buying {multi_buy_qty}"
                if multi_buy_qty else None
            ),
            observed_at=getattr(self, "_observed_at", None),
            extra_notes=notes + ["price_from_circular"],
        )


class ReyChavezConnector(BaseRetailerConnector):
    """Rey Chavez: a wholesale distributor, not a retailer.

    It serves the ZIP and stocks the goods, but publishes no consumer prices -
    everything is quoted. Items are returned without prices so the UI can say
    "available here, price on request" instead of pretending the store does not
    exist or inventing a number.
    """

    slug = "rey_chavez"
    name = "Rey Chavez Distributors"
    supports_online_pricing = False
    default_provenance = PriceProvenance.ESTIMATED

    def resolve_store(self, zip_code: str) -> StoreRef | None:
        for location in load_json(self.slug, "stores.json").get("locations", []):
            if zip_code in (location.get("serves_zips") or []):
                return StoreRef(
                    retailer_slug=self.slug,
                    store_number=str(location["code"]),
                    name=location.get("name"),
                    city=location.get("city"),
                    state=location.get("state"),
                    zip=location.get("zip", zip_code),
                    address_verified=bool(location.get("addressVerified")),
                )
        return None

    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        payload = load_json(self.slug, "catalog.json")
        self._observed_at = captured_at(payload)
        return [
            i for i in payload.get("items", [])
            if matches(term, i.get("description"), i.get("brand"))
        ]

    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        title = clean_title(raw.get("description"))
        if not title:
            return None
        return self.build_product(
            store=store,
            sku=raw["itemCode"],
            display_name=title,
            brand=raw.get("brand"),
            upc=raw.get("upc"),
            category=None,
            price_cents=None,  # Quote-only. There is no price to report.
            observed_at=getattr(self, "_observed_at", None),
            extra_notes=["wholesale_quote_only"],
        )
