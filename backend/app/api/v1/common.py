"""Mapping shared by the v1 endpoints.

Provenance translation lives here so /search and /product cannot drift apart and
start describing the same price with different trust language.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.enums import PriceProvenance as InternalProvenance
from app.core.enums import RetailerStatus
from app.schemas.search import (
    ConnectorStatus, PriceData, PriceProvenance, VerificationMethod,
)

#: A price older than this is reported as not fresh. Deliberately conservative:
#: grocery prices move weekly, and a stale price shown as current is a lie the
#: shopper only discovers at the register.
FRESHNESS_TTL = timedelta(hours=24)

STATUS_MAP = {
    RetailerStatus.ACTIVE: ConnectorStatus.OK,
    RetailerStatus.DEGRADED: ConnectorStatus.DEGRADED,
    RetailerStatus.UNAVAILABLE: ConnectorStatus.UNAVAILABLE,
}

#: Internal grade -> wire enum. One-to-one except ``stale``, which is an age state
#: rather than a method: it maps to ``estimated`` and is communicated through
#: ``is_fresh=False``. The exact grade always survives in ``PriceProvenance.status``.
VERIFICATION_MAP = {
    InternalProvenance.VERIFIED_IN_STORE: VerificationMethod.VERIFIED_IN_STORE,
    InternalProvenance.VERIFIED_ONLINE: VerificationMethod.VERIFIED_ONLINE,
    InternalProvenance.DELIVERY_PRICE: VerificationMethod.DELIVERY_PRICE,
    InternalProvenance.ESTIMATED: VerificationMethod.ESTIMATED,
    InternalProvenance.STALE: VerificationMethod.ESTIMATED,
}


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_fresh(provenance: InternalProvenance, observed_at: datetime) -> bool:
    return (
        provenance is not InternalProvenance.STALE
        and datetime.now(timezone.utc) - aware(observed_at) <= FRESHNESS_TTL
    )


def provenance_out(
    provenance: InternalProvenance, observed_at: datetime, source_url: str | None = None
) -> PriceProvenance:
    observed = aware(observed_at)
    return PriceProvenance(
        status=provenance.value,
        timestamp=observed,
        source_url=source_url,
        verification_method=VERIFICATION_MAP[provenance],
        is_fresh=is_fresh(provenance, observed),
    )


def no_price_published(timestamp: datetime) -> PriceData:
    """An explicit 'stocked here, no price published' record.

    Never an estimate, never an omission: the item is carried, and we say so.
    """
    return PriceData(
        sticker_price_cents=None,
        unit_price_cents=None,
        unit_measure="unknown",
        provenance=PriceProvenance(
            status="no_price_published",
            timestamp=aware(timestamp),
            source_url=None,
            verification_method=VerificationMethod.NO_PRICE_PUBLISHED,
            is_fresh=False,
        ),
    )


def price_data_from_product(product, fallback_time: datetime) -> PriceData:
    """Render a connector product's price for the wire.

    Shared by /search and /compare-basket so a price is never described one way in
    a search result and another way inside a basket.
    """
    if product.price is None:
        return no_price_published(fallback_time)
    price = product.price
    return PriceData(
        sticker_price_cents=price.price_cents,
        # The wire type is an int; every comparison and total uses the exact
        # integer cents or the exact fractional unit price, never this rounding.
        unit_price_cents=(
            round(price.unit_price_cents) if price.unit_price_cents is not None else None
        ),
        unit_measure=price.unit_price_uom or "unknown",
        provenance=provenance_out(price.provenance, price.observed_at, price.source_url),
    )
