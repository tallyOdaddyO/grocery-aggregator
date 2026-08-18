"""Current prices and the append-only observation log.

Money is stored as integer cents throughout. Floating-point dollars accumulate
rounding error across basket sums, and ``NUMERIC`` behaves differently on SQLite
than on PostgreSQL; integers are exact on both.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import PriceProvenance, PromotionType
from app.db.base import Base, TimestampMixin
from app.db.types import EnumString, JSONVariant


class Price(Base, TimestampMixin):
    """The current known price of a variant at a store.

    One row per (variant, store). Updating this row does not lose history - every
    write also appends a :class:`PriceObservation`.
    """

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("variant_id", "store_id", name="uq_price_variant_store"),
        Index("ix_prices_store", "store_id"),
        Index("ix_prices_observed_at", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )

    #: Sticker price of the whole package, in cents.
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Price before promotion, when the retailer discloses it.
    regular_price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

    #: Price per base unit, in cents (fractional - $0.0412/g is normal).
    #: Kept separate from the sticker price so bulk quantity is never hidden behind
    #: an attractive unit price.
    unit_price_cents: Mapped[float | None] = mapped_column(Float)
    unit_price_uom: Mapped[str | None] = mapped_column(String(16))

    promotion_type: Mapped[PromotionType] = mapped_column(
        EnumString(PromotionType), default=PromotionType.NONE, nullable=False
    )
    promotion_text: Mapped[str | None] = mapped_column(Text)

    #: How much this price can be trusted. Set at observation time; the only
    #: permitted later transition is demotion to ``stale``.
    provenance: Mapped[PriceProvenance] = mapped_column(
        EnumString(PriceProvenance), nullable=False
    )
    #: Denormalized shortcut for the strongest grade, kept for cheap filtering.
    is_verified_in_store: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    #: When the price was actually seen - NOT when the row was written. The UI
    #: renders "checked N minutes ago" from this, so it must be observation time.
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Past this, the price is demoted to ``stale`` rather than deleted.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source_url: Mapped[str | None] = mapped_column(Text)


class PriceObservation(Base):
    """Append-only log of every price ever seen. Never updated, never deleted.

    This is the audit trail: it is what lets us show a price history chart, detect
    a connector that has started returning nonsense, and prove that a price we
    displayed was one we genuinely observed.
    """

    __tablename__ = "price_observations"
    __table_args__ = (
        Index("ix_observations_variant_store_time", "variant_id", "store_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )

    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[float | None] = mapped_column(Float)
    unit_price_uom: Mapped[str | None] = mapped_column(String(16))
    promotion_type: Mapped[PromotionType] = mapped_column(
        EnumString(PromotionType), default=PromotionType.NONE, nullable=False
    )
    provenance: Mapped[PriceProvenance] = mapped_column(
        EnumString(PriceProvenance), nullable=False
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Connector slug + fixture/live marker, for forensics.
    source: Mapped[str | None] = mapped_column(String(64))
    raw_payload: Mapped[dict | None] = mapped_column(JSONVariant)
