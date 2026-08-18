"""Retailers and their physical stores."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import RetailerStatus
from app.db.base import Base, TimestampMixin
from app.db.types import EnumString, JSONVariant


class Retailer(Base, TimestampMixin):
    """A chain (Publix, Costco, ...), not a location."""

    __tablename__ = "retailers"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Drives whether the search fan-out even calls this connector, and is what the
    #: UI shows when results are incomplete. Never silently upgraded.
    status: Mapped[RetailerStatus] = mapped_column(
        EnumString(RetailerStatus), default=RetailerStatus.UNAVAILABLE, nullable=False
    )
    #: Why the retailer is degraded/unavailable, in words a user can read.
    status_reason: Mapped[str | None] = mapped_column(Text)

    #: True only when the retailer exposes prices resolvable to a specific store.
    supports_online_pricing: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    #: True when prices are membership-gated (Costco, BJ's).
    requires_membership: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Consecutive connector failures; the circuit breaker reads this.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    stores: Mapped[list["Store"]] = relationship(
        back_populates="retailer", cascade="all, delete-orphan"
    )


class Store(Base, TimestampMixin):
    """A physical location of a retailer.

    Prices attach to stores, never to retailers - an online national price is not a
    shelf price, and conflating the two is the single easiest way to mislead a user.
    """

    __tablename__ = "stores"
    __table_args__ = (
        UniqueConstraint("retailer_id", "store_number", name="uq_store_retailer_number"),
        Index("ix_stores_zip", "zip"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    retailer_id: Mapped[int] = mapped_column(
        ForeignKey("retailers.id", ondelete="CASCADE"), nullable=False
    )

    #: The retailer's own identifier for this location.
    store_number: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160))

    address_line1: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(96))
    state: Mapped[str | None] = mapped_column(String(2))
    zip: Mapped[str] = mapped_column(String(10), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    #: The store this retailer serves for the target ZIP. Exactly one per retailer
    #: per ZIP should be primary; the resolver picks it before fetching any price.
    is_primary_for_zip: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    #: False until the address has been confirmed against a real source. Unverified
    #: locations are seeded rather than invented, and are marked as such.
    address_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    retailer: Mapped[Retailer] = relationship(back_populates="stores")
