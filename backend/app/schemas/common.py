"""Shared response primitives."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Money(BaseModel):
    """Money crosses the wire as both exact cents and a display string.

    The client never does float math on prices; it renders ``display``.
    """

    cents: int
    currency: str = "USD"

    @computed_field
    @property
    def display(self) -> str:
        return f"${self.cents / 100:,.2f}"

    @classmethod
    def of(cls, cents: int | None, currency: str = "USD") -> "Money | None":
        return None if cents is None else cls(cents=cents, currency=currency)


class Freshness(BaseModel):
    """Everything the UI needs to say 'Checked 18 minutes ago' honestly."""

    observed_at: datetime
    is_stale: bool = False

    @computed_field
    @property
    def age_seconds(self) -> int:
        observed = self.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))

    @computed_field
    @property
    def label(self) -> str:
        s = self.age_seconds
        if s < 90:
            return "Checked just now"
        if s < 3600:
            return f"Checked {s // 60} minutes ago"
        if s < 172800:
            hours = s // 3600
            return f"Checked {hours} hour{'s' if hours != 1 else ''} ago"
        return f"Checked {s // 86400} days ago"


class MatchSignal(BaseModel):
    """One reason a match scored the way it did."""

    name: str = Field(description="e.g. 'upc', 'size', 'brand', 'name_similarity'")
    detail: str = Field(description="Human-readable, e.g. 'equivalent 16oz = 1lb'")
    weight: float = Field(description="Contribution to the final confidence")


class MatchExplanation(BaseModel):
    """Why two products were (or were not) treated as equivalent."""

    confidence: float
    stage: str
    signals: list[MatchSignal] = []
    vetoed: bool = False
    veto_reason: str | None = None

    @computed_field
    @property
    def summary(self) -> str:
        if self.vetoed:
            return f"Not equivalent: {self.veto_reason or 'failed a hard constraint'}"
        if not self.signals:
            return "No match"
        reasons = ", ".join(s.detail for s in self.signals)
        return f"Confidence {round(self.confidence * 100)}%: {reasons}"
