"""The retailer connector contract.

Everything a retailer returns is untrusted, differently-shaped, and often broken.
This module is the membrane: an adapter may parse whatever nonsense its retailer
emits, but it may only hand back validated :class:`NormalizedProduct` instances.
Retailer-specific quirks - dollar strings, HTML entities, sizes hidden in titles,
UPCs with bad check digits - must be resolved before that boundary, never after.

The base class owns the control flow (``search``), so no adapter can accidentally
skip store resolution, skip normalization, or let an exception escape into the
search fan-out.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import (
    PriceProvenance, PromotionType, RetailerStatus, UomKind,
)
from app.services.normalization import (
    extract_attributes, normalize_brand, normalize_name, normalize_upc,
    parse_package_size,
)
from app.connectors.http import (
    BlockedError, LiveFetchError, LiveUnsupported, PoliteClient,
)
from app.services.pricing import compute_unit_price


class ConnectorContractError(TypeError):
    """An adapter returned something other than a validated NormalizedProduct."""


# --------------------------------------------------------------------------- #
# Contract models
# --------------------------------------------------------------------------- #


class StoreRef(BaseModel):
    """A resolved physical location. Pricing may not be fetched without one."""

    model_config = ConfigDict(frozen=True)

    retailer_slug: str
    store_number: str
    name: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str
    latitude: float | None = None
    longitude: float | None = None
    #: False when the address has not been confirmed against a real source.
    address_verified: bool = False


class NormalizedPrice(BaseModel):
    """A price that has cleared validation and carries its own trust grade."""

    model_config = ConfigDict(frozen=True)

    price_cents: int = Field(gt=0, description="Sticker price of the whole package")
    regular_price_cents: int | None = Field(default=None, gt=0)
    currency: str = "USD"

    unit_price_cents: float | None = Field(default=None, ge=0)
    unit_price_uom: str | None = None

    promotion_type: PromotionType = PromotionType.NONE
    promotion_text: str | None = None
    #: Frequently missing from real circulars. Absence is recorded, not invented.
    promotion_ends_at: datetime | None = None

    provenance: PriceProvenance
    observed_at: datetime
    source_url: str | None = None

    @field_validator("regular_price_cents")
    @classmethod
    def _regular_not_below_sale(cls, v, info):
        price = info.data.get("price_cents")
        if v is not None and price is not None and v < price:
            # A "regular" price below the sale price is a feed error, not a deal.
            raise ValueError("regular_price_cents is below price_cents")
        return v

    @property
    def is_verified_in_store(self) -> bool:
        return self.provenance is PriceProvenance.VERIFIED_IN_STORE


class NormalizedProduct(BaseModel):
    """The only shape the core system ever sees from a retailer."""

    model_config = ConfigDict(frozen=True)

    retailer_slug: str
    retailer_sku: str
    store_number: str

    #: Validated GTIN-14, or None. Never a raw, unchecked barcode.
    upc: str | None = None
    display_name: str = Field(min_length=1)
    normalized_name: str
    brand: str | None = None
    normalized_brand: str | None = None
    category: str | None = None

    size_text: str | None = None
    pack_count: int = Field(default=1, ge=1)
    net_content_value: float | None = Field(default=None, gt=0)
    net_content_uom: str | None = None
    base_quantity: float | None = Field(default=None, gt=0)
    base_uom: str | None = None
    uom_kind: UomKind | None = None
    size_parse_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    is_organic: bool = False
    attributes: dict = Field(default_factory=dict)

    #: None when the retailer listed the item without a usable price. The item is
    #: still returned so the UI can say "stocked here, price unknown" rather than
    #: implying the store does not carry it.
    price: NormalizedPrice | None = None

    #: Parser observations (ambiguous_oz, composite_size, missing_upc, ...).
    notes: list[str] = Field(default_factory=list)

    @property
    def has_price(self) -> bool:
        return self.price is not None


class ConnectorHealth(BaseModel):
    slug: str
    status: RetailerStatus
    reason: str | None = None
    checked_at: datetime


class ConnectorResult(BaseModel):
    """The outcome of one connector's search. Never raises; always reports."""

    retailer_slug: str
    retailer_name: str
    status: RetailerStatus
    store: StoreRef | None = None
    products: list[NormalizedProduct] = Field(default_factory=list)
    #: Plain-language reason the retailer is degraded/unavailable, for the UI.
    reason: str | None = None
    #: Per-item problems that did not sink the whole connector.
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def is_usable(self) -> bool:
        return self.status is not RetailerStatus.UNAVAILABLE and bool(self.products)


# --------------------------------------------------------------------------- #
# Base connector
# --------------------------------------------------------------------------- #


def _render_size(package) -> str | None:
    """Describe a parsed package concisely.

    Used when a retailer supplies no dedicated size field and the size had to be
    recovered from the title. Echoing the whole title back as the "size" would be
    useless to a shopper comparing packages.
    """
    if not package.is_parsed:
        return None
    if package.pack_count > 1 and package.unit_quantity:
        return f"{package.pack_count} x {package.unit_quantity}"
    if package.unit_quantity:
        return str(package.unit_quantity)
    return str(package.total_base)


class BaseRetailerConnector(ABC):
    """Template for every retailer adapter.

    Subclasses implement three narrow hooks - resolve a store, fetch raw records,
    and map one raw record to a product. The base class owns everything else:
    ordering, normalization, validation, per-item error isolation, and the
    guarantee that :meth:`search` never raises.
    """

    #: Stable identifier, matching ``retailers.slug``.
    slug: str = ""
    name: str = ""
    supports_online_pricing: bool = False
    requires_membership: bool = False
    #: The best grade this source can honestly claim. An adapter may downgrade a
    #: specific price, but may never upgrade past this.
    default_provenance: PriceProvenance = PriceProvenance.ESTIMATED

    def __init__(self, source: str = "fixture", client: PoliteClient | None = None) -> None:
        self.source = source
        self._client = client
        #: Set during a search when a live attempt failed and fixtures were used
        #: instead. Everything produced in that state is marked accordingly.
        self._fallback_reason: str | None = None

    @property
    def using_fixture_fallback(self) -> bool:
        return self._fallback_reason is not None

    # --- hooks ------------------------------------------------------------- #

    @abstractmethod
    def resolve_store(self, zip_code: str) -> StoreRef | None:
        """Find the local store for this ZIP, or None if the retailer has none."""

    @abstractmethod
    def fetch_raw(self, term: str, store: StoreRef) -> list[dict]:
        """Return raw, unvalidated records. May raise; the base class isolates it."""

    @abstractmethod
    def parse_item(self, raw: dict, store: StoreRef) -> NormalizedProduct | None:
        """Map one raw record to a product, or None to skip it deliberately."""

    def fetch_live(self, term: str, store: StoreRef) -> list[dict]:
        """Fetch from the retailer's real endpoint.

        The default is to refuse: we have no lawful, documented way into most of
        these retailers, and guessing at private endpoints or working around bot
        protection is out of scope by design. An adapter overrides this only when
        it has a permitted API and the credentials to use it.
        """
        raise LiveUnsupported(
            f"No permitted live endpoint is configured for {self.name}."
        )

    # --- helper available to every adapter --------------------------------- #

    def build_product(
        self,
        *,
        store: StoreRef,
        sku: str,
        display_name: str,
        brand: str | None = None,
        upc: str | None = None,
        category: str | None = None,
        size_text: str | None = None,
        price_cents: int | None = None,
        regular_price_cents: int | None = None,
        promotion_type: PromotionType = PromotionType.NONE,
        promotion_text: str | None = None,
        promotion_ends_at: datetime | None = None,
        provenance: PriceProvenance | None = None,
        observed_at: datetime | None = None,
        source_url: str | None = None,
        extra_notes: list[str] | None = None,
    ) -> NormalizedProduct:
        """Run the Phase 3 normalizers and assemble a validated product.

        Every adapter funnels through here, so normalization cannot drift between
        retailers and no adapter can invent its own size or unit-price convention.
        """
        notes = list(extra_notes or [])

        clean_name = " ".join(str(display_name or "").split())
        package = parse_package_size(size_text or clean_name, category=category)
        if not package.is_parsed:
            notes.append("size_unparsed")
        notes.extend(package.notes)

        gtin = normalize_upc(upc)
        if upc and gtin is None:
            # The retailer supplied something, but it is not a usable identity.
            notes.append("upc_invalid")
        elif not upc:
            notes.append("upc_missing")

        attributes = extract_attributes(clean_name)

        price: NormalizedPrice | None = None
        if price_cents is not None and price_cents > 0:
            grade = provenance or self.default_provenance
            if self.using_fixture_fallback:
                # Fixture data may never wear a live grade. It is recorded as an
                # estimate and tagged, so nothing downstream - or the UI - can
                # present cached sample data as a price we just observed.
                grade = PriceProvenance.ESTIMATED
                notes.append("fixture_fallback")
            unit = compute_unit_price(price_cents, package)
            if promotion_type is not PromotionType.NONE and promotion_ends_at is None:
                notes.append("promotion_end_date_missing")
            price = NormalizedPrice(
                price_cents=price_cents,
                regular_price_cents=(
                    regular_price_cents
                    if regular_price_cents and regular_price_cents >= price_cents
                    else None
                ),
                unit_price_cents=unit.cents_per_unit if unit else None,
                unit_price_uom=unit.uom if unit else None,
                promotion_type=promotion_type,
                promotion_text=promotion_text,
                promotion_ends_at=promotion_ends_at,
                provenance=grade,
                observed_at=observed_at or datetime.now(timezone.utc),
                source_url=source_url,
            )
        else:
            notes.append("price_unavailable")

        return NormalizedProduct(
            retailer_slug=self.slug,
            retailer_sku=str(sku),
            store_number=store.store_number,
            upc=gtin,
            display_name=clean_name,
            normalized_name=normalize_name(clean_name),
            brand=brand or None,
            normalized_brand=normalize_brand(brand),
            category=category,
            size_text=size_text or _render_size(package),
            pack_count=package.pack_count,
            net_content_value=(
                package.unit_quantity.value if package.unit_quantity else None
            ),
            net_content_uom=(
                package.unit_quantity.uom if package.unit_quantity else None
            ),
            base_quantity=package.total_base.value if package.total_base else None,
            base_uom=package.total_base.uom if package.total_base else None,
            uom_kind=package.total_base.kind if package.total_base else None,
            size_parse_confidence=package.parse_confidence,
            is_organic=bool(attributes.get("organic")),
            attributes=attributes,
            price=price,
            notes=sorted(set(notes)),
        )

    # --- template method --------------------------------------------------- #

    def search(self, term: str, zip_code: str) -> ConnectorResult:
        """Search this retailer. Guaranteed not to raise."""
        started = time.perf_counter()

        def finish(**kw) -> ConnectorResult:
            return ConnectorResult(
                retailer_slug=self.slug,
                retailer_name=self.name,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                **kw,
            )

        try:
            store = self.resolve_store(zip_code)
        except Exception as exc:
            return finish(
                status=RetailerStatus.UNAVAILABLE,
                reason=f"Store lookup failed: {type(exc).__name__}: {exc}",
            )

        if store is None:
            # Location is a precondition, not a nicety. Without a local store we
            # emit nothing rather than falling back to a national price.
            return finish(
                status=RetailerStatus.UNAVAILABLE,
                reason=f"No {self.name} store could be resolved for ZIP {zip_code}.",
            )

        self._fallback_reason = None
        blocked = False

        if self.source == "live":
            try:
                raw_records = self.fetch_live(term, store)
            except LiveFetchError as exc:
                # Blocked, unsupported, or transiently broken. All three mean the
                # same thing here: we have no live data, so say so and fall back.
                blocked = isinstance(exc, BlockedError)
                self._fallback_reason = str(exc)
                try:
                    raw_records = self.fetch_raw(term, store)
                except Exception as fixture_exc:
                    return finish(
                        status=RetailerStatus.UNAVAILABLE,
                        store=store,
                        reason=(
                            f"Live fetch failed ({exc}) and no fixture is "
                            f"available: {type(fixture_exc).__name__}: {fixture_exc}"
                        ),
                    )
            except Exception as exc:
                return finish(
                    status=RetailerStatus.DEGRADED,
                    store=store,
                    reason=f"Live fetch failed: {type(exc).__name__}: {exc}",
                )
        else:
            try:
                raw_records = self.fetch_raw(term, store)
            except Exception as exc:
                return finish(
                    status=RetailerStatus.DEGRADED,
                    store=store,
                    reason=f"Search failed: {type(exc).__name__}: {exc}",
                )

        products: list[NormalizedProduct] = []
        warnings: list[str] = []
        for index, raw in enumerate(raw_records or []):
            try:
                product = self.parse_item(raw, store)
            except Exception as exc:
                # One malformed record must not discard the other results.
                warnings.append(
                    f"record {index}: skipped ({type(exc).__name__}: {exc})"
                )
                continue
            if product is None:
                continue
            if not isinstance(product, NormalizedProduct):
                raise ConnectorContractError(
                    f"{self.slug}.parse_item returned {type(product).__name__}; "
                    "adapters must return a validated NormalizedProduct"
                )
            products.append(product)

        status = RetailerStatus.ACTIVE
        reason = None
        if self._fallback_reason:
            # Never ACTIVE on fallback: the results are real records, but they are
            # cached samples rather than anything observed just now.
            status = RetailerStatus.DEGRADED
            reason = (
                f"{'Blocked by ' if blocked else 'Could not reach '}{self.name}"
                f" - showing fixture data instead. {self._fallback_reason}"
            )
        if warnings:
            status = RetailerStatus.DEGRADED
            reason = reason or (
                f"{len(warnings)} of {len(raw_records)} records could not be parsed."
            )
        if not products:
            status = RetailerStatus.DEGRADED
            reason = reason or f"No usable results for '{term}'."
        elif not any(p.has_price for p in products):
            # The retailer stocks the item but publishes no price for it. That is a
            # real, reportable state - not an outage, and not something to paper
            # over with an estimate.
            status = RetailerStatus.DEGRADED
            reason = reason or (
                f"{self.name} lists {len(products)} matching item(s) but publishes "
                "no consumer prices."
            )

        return finish(status=status, store=store, products=products,
                      reason=reason, warnings=warnings)

    def health(self, zip_code: str) -> ConnectorHealth:
        now = datetime.now(timezone.utc)
        try:
            store = self.resolve_store(zip_code)
        except Exception as exc:
            return ConnectorHealth(
                slug=self.slug, status=RetailerStatus.UNAVAILABLE,
                reason=f"{type(exc).__name__}: {exc}", checked_at=now,
            )
        if store is None:
            return ConnectorHealth(
                slug=self.slug, status=RetailerStatus.UNAVAILABLE,
                reason=f"No store for ZIP {zip_code}", checked_at=now,
            )
        return ConnectorHealth(slug=self.slug, status=RetailerStatus.ACTIVE,
                               checked_at=now)
