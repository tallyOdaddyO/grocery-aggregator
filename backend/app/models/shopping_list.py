"""Saved baskets and their line items."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import BasketStrategy
from app.db.base import Base, TimestampMixin
from app.db.types import EnumString


class ShoppingList(Base, TimestampMixin):
    __tablename__ = "shopping_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    zip: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy: Mapped[BasketStrategy] = mapped_column(
        EnumString(BasketStrategy), default=BasketStrategy.SPLIT, nullable=False
    )

    items: Mapped[list["ShoppingListItem"]] = relationship(
        back_populates="shopping_list", cascade="all, delete-orphan"
    )


class ShoppingListItem(Base, TimestampMixin):
    """One line of a basket.

    ``query_text`` is retained alongside the resolved variant because a basket must
    still be able to say "you asked for milk and no store here stocks it" - dropping
    unmatched lines would quietly understate the basket.
    """

    __tablename__ = "shopping_list_items"
    __table_args__ = (Index("ix_list_items_list", "shopping_list_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shopping_list_id: Mapped[int] = mapped_column(
        ForeignKey("shopping_lists.id", ondelete="CASCADE"), nullable=False
    )

    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    #: Null when nothing in range matched - an explicit gap, not an omission.
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL")
    )
    store_id: Mapped[int | None] = mapped_column(
        ForeignKey("stores.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)

    shopping_list: Mapped[ShoppingList] = relationship(back_populates="items")
