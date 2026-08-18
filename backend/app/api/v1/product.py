"""GET /api/v1/product/{id} - detail, price history, and match confidence."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.api.v1.common import no_price_published, provenance_out
from app.core.enums import MatchStage, UomKind
from app.models import (
    Price, PriceObservation, Product, ProductVariant, Retailer, Store,
)
from app.schemas.product import (
    ConfidenceStats, MatchSignalOut, PriceObservationOut, ProductDetailResponse,
    StoreSummary,
)
from app.schemas.search import PriceData, RetailerID, SearchProductSummary
from app.services.matching import (
    MatchCandidate, MatchResult, config_for_category, match,
)
from app.services.normalization import PackageSize
from app.services.units import Quantity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["product"])

#: How many historical observations to return by default.
DEFAULT_HISTORY_LIMIT = 50


def _candidate_from_variant(
    variant: ProductVariant, product: Product | None
) -> MatchCandidate:
    """Build a matcher candidate from stored columns.

    Sizes are reused from the database rather than re-parsed from the title, so a
    confidence figure can never contradict the unit prices shown beside it.
    """
    package = None
    if variant.base_quantity and variant.base_uom and variant.uom_kind:
        kind = UomKind(variant.uom_kind)
        unit_quantity = None
        if variant.net_content_value and variant.net_content_uom:
            unit_quantity = Quantity(
                variant.net_content_value, variant.net_content_uom, kind
            )
        package = PackageSize(
            raw_text=variant.size_text or "",
            pack_count=variant.pack_count,
            unit_quantity=unit_quantity,
            total_base=Quantity(variant.base_quantity, variant.base_uom, kind),
            parse_confidence=1.0,
        )
    return MatchCandidate(
        display_name=variant.display_name,
        normalized_name=(product.normalized_name if product else variant.display_name),
        brand=(product.brand if product else None),
        normalized_brand=(product.normalized_brand if product else None),
        category=(product.category if product else None),
        upc=variant.upc,
        package=package,
        attributes=dict(variant.attributes or {}),
    )


def _resolve_variant(db: Session, raw_id: str) -> ProductVariant:
    """Look up a variant by either id form, or raise 404.

    Accepts the composite ``retailer:sku`` that /search emits, and the numeric
    primary key. Anything else is a 404 rather than a 500 - a malformed id is a
    client mistake, not a server fault.
    """
    identifier = (raw_id or "").strip()
    variant = None

    if ":" in identifier:
        slug, _, sku = identifier.partition(":")
        variant = db.scalar(
            select(ProductVariant)
            .join(Retailer, Retailer.id == ProductVariant.retailer_id)
            .where(Retailer.slug == slug, ProductVariant.retailer_sku == sku)
        )
    elif identifier.isdigit():
        variant = db.get(ProductVariant, int(identifier))

    if variant is None:
        raise HTTPException(
            status_code=404, detail=f"No product found with id '{raw_id}'."
        )
    return variant


def _price_data(price: Price | None, fallback_time) -> PriceData:
    if price is None:
        return no_price_published(fallback_time)
    return PriceData(
        sticker_price_cents=price.price_cents,
        # Sticker and unit price stay distinct fields; ranking always uses the
        # exact fractional value server-side, never this rounded one.
        unit_price_cents=(
            round(price.unit_price_cents) if price.unit_price_cents is not None else None
        ),
        unit_measure=price.unit_price_uom or "unknown",
        provenance=provenance_out(
            price.provenance, price.observed_at, price.source_url
        ),
    )


def _equivalents(
    db: Session, variant: ProductVariant, product: Product | None
) -> list[tuple[ProductVariant, MatchResult]]:
    """Find variants at other retailers that clear every veto check."""
    conditions = [ProductVariant.product_id == variant.product_id]
    if variant.upc:
        conditions.append(ProductVariant.upc == variant.upc)
    if product is not None:
        conditions.append(
            ProductVariant.product_id.in_(
                select(Product.id).where(
                    Product.normalized_name == product.normalized_name
                )
            )
        )

    candidates = db.scalars(
        select(ProductVariant).where(
            ProductVariant.id != variant.id, or_(*conditions)
        )
    ).all()

    subject = _candidate_from_variant(variant, product)
    matches: list[tuple[ProductVariant, MatchResult]] = []
    for other in candidates:
        other_product = db.get(Product, other.product_id)
        config = config_for_category(
            (product.category if product else None)
            or (other_product.category if other_product else None)
        )
        result = match(subject, _candidate_from_variant(other, other_product), config)
        # A vetoed pair is excluded outright, exactly as in search grouping.
        if result.vetoed or not result.is_match:
            continue
        matches.append((other, result))

    matches.sort(key=lambda pair: -pair[1].confidence)
    return matches


def _confidence_stats(
    matches: list[tuple[ProductVariant, MatchResult]]
) -> ConfidenceStats:
    if not matches:
        return ConfidenceStats(
            match_confidence=0.0,
            match_type="singleton",
            threshold=config_for_category(None).fuzzy_threshold,
            veto_checks_passed=[],
            veto_checks_failed=[],
            signals=[],
            explanation=(
                "No equivalent product was found at another retailer, so there is "
                "nothing to compare this against."
            ),
            equivalent_count=0,
        )

    best = matches[0][1]
    return ConfidenceStats(
        match_confidence=round(best.confidence, 4),
        match_type=best.stage.value,
        threshold=best.threshold,
        veto_checks_passed=list(best.veto_checks_passed),
        veto_checks_failed=list(best.veto_checks_failed),
        signals=[MatchSignalOut(**s.as_dict()) for s in best.signals],
        explanation=best.summary,
        equivalent_count=len(matches),
    )


def _current_price(db: Session, variant_id: int) -> Price | None:
    """The most recently observed price for this variant."""
    return db.scalar(
        select(Price)
        .where(Price.variant_id == variant_id)
        .order_by(Price.observed_at.desc())
        .limit(1)
    )


@router.get("/product/{product_id}", response_model=ProductDetailResponse)
def get_product(
    product_id: str = Path(..., description="'retailer:sku' or a numeric variant id"),
    history_limit: int = Query(DEFAULT_HISTORY_LIMIT, ge=1, le=500),
    db: Session = Depends(get_db_session),
) -> ProductDetailResponse:
    """Product detail with its full observed price history and match confidence."""
    variant = _resolve_variant(db, product_id)
    product = db.get(Product, variant.product_id)
    retailer = db.get(Retailer, variant.retailer_id)

    price = _current_price(db, variant.id)
    store = db.get(Store, price.store_id) if price else db.scalar(
        select(Store).where(Store.retailer_id == variant.retailer_id).limit(1)
    )
    if store is None or retailer is None:
        raise HTTPException(
            status_code=404,
            detail=f"No store is on record for product '{product_id}'.",
        )

    try:
        retailer_id = RetailerID(retailer.slug)
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Unknown retailer '{retailer.slug}'."
        ) from None

    # Newest first. This log is append-only, so ordering by observation time is
    # the true chronology, not merely insertion order.
    observations = db.scalars(
        select(PriceObservation)
        .where(PriceObservation.variant_id == variant.id)
        .order_by(
            PriceObservation.observed_at.desc(), PriceObservation.id.desc()
        )
        .limit(history_limit)
    ).all()

    history = [
        PriceObservationOut(
            observed_at=observation.observed_at,
            sticker_price_cents=observation.price_cents,
            unit_price_cents=(
                round(observation.unit_price_cents)
                if observation.unit_price_cents is not None
                else None
            ),
            unit_measure=observation.unit_price_uom or "unknown",
            promotion_type=observation.promotion_type.value,
            provenance=provenance_out(
                observation.provenance, observation.observed_at
            ),
        )
        for observation in observations
    ]

    matches = _equivalents(db, variant, product)
    equivalents: list[SearchProductSummary] = []
    for other, _result in matches:
        other_retailer = db.get(Retailer, other.retailer_id)
        if other_retailer is None:
            continue
        try:
            other_id = RetailerID(other_retailer.slug)
        except ValueError:
            continue
        other_price = _current_price(db, other.id)
        equivalents.append(
            SearchProductSummary(
                id=f"{other_retailer.slug}:{other.retailer_sku}",
                retailer=other_id,
                title=other.display_name,
                size_raw=other.size_text or "",
                price=_price_data(other_price, other.updated_at),
            )
        )

    return ProductDetailResponse(
        id=f"{retailer.slug}:{variant.retailer_sku}",
        retailer=retailer_id,
        title=variant.display_name,
        brand=product.brand if product else None,
        category=product.category if product else None,
        upc=variant.upc,
        size_raw=variant.size_text or "",
        pack_count=variant.pack_count,
        store=StoreSummary(
            retailer=retailer_id,
            store_number=store.store_number,
            name=store.name,
            city=store.city,
            state=store.state,
            zip=store.zip,
            address_verified=store.address_verified,
        ),
        current_price=_price_data(price, variant.updated_at),
        price_history=history,
        confidence_stats=_confidence_stats(matches),
        equivalent_products=equivalents,
    )
