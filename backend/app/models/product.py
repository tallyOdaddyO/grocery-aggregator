"""Canonical products and the concrete packages they are sold in."""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Float, ForeignKey, Index, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import JSONVariant, jsonb_gin_index


class Product(Base, TimestampMixin):
    """Product identity, independent of package size or retailer.

    "Cheerios Original" is one Product; the 12oz box and the 18oz box are two
    ProductVariants of it.
    """

    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_upc", "upc"),
        Index("ix_products_normalized_name", "normalized_name"),
        jsonb_gin_index("ix_products_attributes_gin", "attributes"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: GTIN-14, zero-padded and check-digit validated. Nullable: plenty of real
    #: products (produce, deli, store brands) legitimately have no UPC.
    upc: Mapped[str | None] = mapped_column(String(14))

    #: Lowercased, stopword-stripped, size-descriptor-removed name used by Stage 3.
    normalized_name: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(320), nullable=False)

    brand: Mapped[str | None] = mapped_column(String(160))
    normalized_brand: Mapped[str | None] = mapped_column(String(160))
    category: Mapped[str | None] = mapped_column(String(96))

    #: Structured variant flags used as Stage 2 vetoes: organic, fat content,
    #: flavor, caffeine-free, etc. GIN-indexed on PostgreSQL for containment queries.
    attributes: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)

    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(Base, TimestampMixin):
    """A specific sellable package at a specific retailer.

    Net content is stored twice: as the retailer stated it (``net_content_value`` +
    ``net_content_uom``) and projected to a base unit (``base_quantity`` in g / ml /
    each) so that cross-retailer unit-price comparison is a plain arithmetic
    operation rather than a parse at query time.
    """

    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint(
            "retailer_id", "retailer_sku", name="uq_variant_retailer_sku"
        ),
        Index("ix_variants_product", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    retailer_id: Mapped[int] = mapped_column(
        ForeignKey("retailers.id", ondelete="CASCADE"), nullable=False
    )

    retailer_sku: Mapped[str] = mapped_column(String(96), nullable=False)
    upc: Mapped[str | None] = mapped_column(String(14))
    display_name: Mapped[str] = mapped_column(String(320), nullable=False)
    size_text: Mapped[str | None] = mapped_column(String(96))

    #: Units per package: a 12-pack of 12 fl oz cans has pack_count=12.
    pack_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Content of ONE unit, as labeled (12.0 for "12 fl oz").
    net_content_value: Mapped[float | None] = mapped_column(Float)
    net_content_uom: Mapped[str | None] = mapped_column(String(16))

    #: Total content of the whole package in the base unit for its kind
    #: (grams for mass, millilitres for volume, each for count).
    base_quantity: Mapped[float | None] = mapped_column(Float)
    base_uom: Mapped[str | None] = mapped_column(String(8))
    #: mass | volume | count - only like kinds are comparable.
    uom_kind: Mapped[str | None] = mapped_column(String(16))

    is_organic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)

    product: Mapped[Product] = relationship(back_populates="variants")
