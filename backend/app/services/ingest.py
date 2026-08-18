"""Persist connector results into the database.

Two things happen for every observed price, and the order matters:

1. ``prices`` is upserted - one row per (variant, store) holding what we currently
   believe the price to be.
2. ``price_observations`` is appended - never updated, never deleted.

The append is what makes the current price defensible. Without it an updated price
overwrites its own evidence, and the system can no longer show a history, detect a
connector that started returning nonsense, or prove a displayed price was one it
genuinely saw.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import NormalizedProduct, StoreRef
from app.core.enums import RetailerStatus
from app.models import (
    Price, PriceObservation, Product, ProductVariant, Retailer, Store,
)
from app.services.search import SearchOutcome


@dataclass
class IngestStats:
    retailers: int = 0
    stores: int = 0
    products: int = 0
    variants: int = 0
    prices_written: int = 0
    observations_appended: int = 0
    skipped_unpriced: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _get_or_create_retailer(session: Session, slug: str, name: str) -> tuple[Retailer, bool]:
    retailer = session.scalar(select(Retailer).where(Retailer.slug == slug))
    if retailer:
        return retailer, False
    retailer = Retailer(slug=slug, name=name, status=RetailerStatus.ACTIVE)
    session.add(retailer)
    session.flush()
    return retailer, True


def _get_or_create_store(
    session: Session, retailer: Retailer, ref: StoreRef
) -> tuple[Store, bool]:
    store = session.scalar(
        select(Store).where(
            Store.retailer_id == retailer.id, Store.store_number == ref.store_number
        )
    )
    if store:
        return store, False
    store = Store(
        retailer_id=retailer.id,
        store_number=ref.store_number,
        name=ref.name,
        address_line1=ref.address_line1,
        city=ref.city,
        state=ref.state,
        zip=ref.zip,
        latitude=ref.latitude,
        longitude=ref.longitude,
        is_primary_for_zip=True,
        # Carried through verbatim: a connector-resolved address is not a verified
        # one until a human or an authoritative source confirms it.
        address_verified=ref.address_verified,
    )
    session.add(store)
    session.flush()
    return store, True


def _get_or_create_product(
    session: Session, item: NormalizedProduct
) -> tuple[Product, bool]:
    """Find the canonical product for this item.

    Identity is the UPC when we have a validated one. Otherwise it is the
    (brand, normalized name, total size) triple - which deliberately keeps a 12 oz
    and a 24 oz box as separate products, consistent with the matcher's size veto.
    """
    size_key = round(item.base_quantity, 4) if item.base_quantity else None

    if item.upc:
        # A shared UPC is necessary but NOT sufficient. Retailers demonstrably ship
        # one barcode across pack sizes (BJ's puts the 12 oz Cheerios UPC on a
        # 2 x 20.35 oz club pack), and the matcher vetoes those as different
        # products. Identity here must agree with that veto, or the database would
        # quietly merge two products the rest of the system keeps apart.
        for existing in session.scalars(
            select(Product).where(Product.upc == item.upc)
        ).all():
            if _size_matches(existing, size_key):
                return existing, False

    stmt = select(Product).where(
        Product.normalized_name == item.normalized_name,
        Product.normalized_brand.is_(item.normalized_brand)
        if item.normalized_brand is None
        else Product.normalized_brand == item.normalized_brand,
    )
    for candidate in session.scalars(stmt).all():
        if _size_matches(candidate, size_key):
            return candidate, False

    attributes = dict(item.attributes)
    if item.base_quantity:
        # Stored so the size participates in identity without a dedicated column.
        attributes["_base_quantity"] = item.base_quantity
    product = Product(
        upc=item.upc,
        normalized_name=item.normalized_name,
        display_name=item.display_name,
        brand=item.brand,
        normalized_brand=item.normalized_brand,
        category=item.category,
        attributes=attributes,
    )
    session.add(product)
    session.flush()
    return product, True


def _get_or_create_variant(
    session: Session, product: Product, retailer: Retailer, item: NormalizedProduct
) -> tuple[ProductVariant, bool]:
    variant = session.scalar(
        select(ProductVariant).where(
            ProductVariant.retailer_id == retailer.id,
            ProductVariant.retailer_sku == item.retailer_sku,
        )
    )
    created = variant is None
    if variant is None:
        variant = ProductVariant(
            product_id=product.id,
            retailer_id=retailer.id,
            retailer_sku=item.retailer_sku,
        )
        session.add(variant)

    variant.upc = item.upc
    variant.display_name = item.display_name
    variant.size_text = item.size_text
    variant.pack_count = item.pack_count
    variant.net_content_value = item.net_content_value
    variant.net_content_uom = item.net_content_uom
    variant.base_quantity = item.base_quantity
    variant.base_uom = item.base_uom
    variant.uom_kind = item.uom_kind.value if item.uom_kind else None
    variant.is_organic = item.is_organic
    variant.attributes = dict(item.attributes)
    session.flush()
    return variant, created


def ingest_outcome(session: Session, outcome: SearchOutcome) -> IngestStats:
    """Persist everything a search observed. Idempotent per (variant, store)."""
    stats = IngestStats()
    retailer_cache: dict[str, Retailer] = {}
    store_cache: dict[str, Store] = {}

    for slug, ref in outcome.stores.items():
        name = next(
            (r.name for r in outcome.reports if r.slug == slug), slug.title()
        )
        retailer, created_r = _get_or_create_retailer(session, slug, name)
        stats.retailers += int(created_r)
        retailer.last_sync_at = outcome.searched_at
        store, created_s = _get_or_create_store(session, retailer, ref)
        stats.stores += int(created_s)
        retailer_cache[slug] = retailer
        store_cache[slug] = store

    for item in outcome.products:
        retailer = retailer_cache.get(item.retailer_slug)
        store = store_cache.get(item.retailer_slug)
        if retailer is None or store is None:
            continue

        product, created_p = _get_or_create_product(session, item)
        stats.products += int(created_p)
        variant, created_v = _get_or_create_variant(session, product, retailer, item)
        stats.variants += int(created_v)

        if item.price is None:
            # Stocked but unpriced. The variant is recorded so the item can be
            # shown as carried; no price row is invented for it.
            stats.skipped_unpriced += 1
            continue

        price_in = item.price
        observed = price_in.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)

        current = session.scalar(
            select(Price).where(
                Price.variant_id == variant.id, Price.store_id == store.id
            )
        )
        if current is None:
            current = Price(variant_id=variant.id, store_id=store.id)
            session.add(current)

        # Only move the current price forward in time. A late-arriving older
        # observation is still logged below, but must not overwrite a newer price.
        if current.observed_at is None or observed >= _aware(current.observed_at):
            current.price_cents = price_in.price_cents
            current.regular_price_cents = price_in.regular_price_cents
            current.currency = price_in.currency
            current.unit_price_cents = price_in.unit_price_cents
            current.unit_price_uom = price_in.unit_price_uom
            current.promotion_type = price_in.promotion_type
            current.promotion_text = price_in.promotion_text
            current.provenance = price_in.provenance
            current.is_verified_in_store = price_in.is_verified_in_store
            current.observed_at = observed
            current.source_url = price_in.source_url
            stats.prices_written += 1

        session.add(
            PriceObservation(
                variant_id=variant.id,
                store_id=store.id,
                price_cents=price_in.price_cents,
                unit_price_cents=price_in.unit_price_cents,
                unit_price_uom=price_in.unit_price_uom,
                promotion_type=price_in.promotion_type,
                provenance=price_in.provenance,
                observed_at=observed,
                source=f"{item.retailer_slug}:{outcome.zip_code}",
                raw_payload={"notes": item.notes, "sku": item.retailer_sku},
            )
        )
        stats.observations_appended += 1
        session.flush()

    session.commit()
    return stats


def _size_matches(product: Product, size_key: float | None) -> bool:
    """Whether a stored product has the same package size as the incoming item.

    Both sizes unknown counts as a match; one known and one unknown does not -
    we do not merge a sized product into an unsized one on a hunch.
    """
    stored = (product.attributes or {}).get("_base_quantity")
    stored = round(stored, 4) if stored else None
    if stored is None or size_key is None:
        return stored is None and size_key is None
    larger = max(stored, size_key)
    return abs(stored - size_key) / larger <= 0.02 if larger else True


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
