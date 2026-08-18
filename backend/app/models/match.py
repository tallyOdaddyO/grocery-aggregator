"""Materialized match edges between variants."""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Float, ForeignKey, Index, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import MatchStage
from app.db.base import Base, TimestampMixin
from app.db.types import EnumString, JSONVariant, jsonb_gin_index


class ProductMatch(Base, TimestampMixin):
    """A scored equivalence between two variants, with its reasoning attached.

    ``signals`` is the whole point: a match that cannot explain itself is a match a
    user cannot trust. It holds the ordered list of contributing signals, e.g.
    ``[{"name": "upc", "detail": "exact", "weight": 1.0}]``.
    """

    __tablename__ = "product_matches"
    __table_args__ = (
        UniqueConstraint("left_variant_id", "right_variant_id", name="uq_match_pair"),
        Index("ix_matches_left", "left_variant_id"),
        Index("ix_matches_confidence", "confidence"),
        jsonb_gin_index("ix_matches_signals_gin", "signals"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Stored with left_variant_id < right_variant_id so each pair appears once.
    left_variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )
    right_variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )

    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    stage: Mapped[MatchStage] = mapped_column(EnumString(MatchStage), nullable=False)
    signals: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)

    #: True when a Stage 2 hard constraint (size, organic) rejected the pair. A veto
    #: is final: no amount of name similarity may overturn it.
    vetoed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    veto_reason: Mapped[str | None] = mapped_column(Text)

    #: Set when a human confirms or rejects the match; overrides the engine.
    human_verified: Mapped[bool | None] = mapped_column(Boolean)
