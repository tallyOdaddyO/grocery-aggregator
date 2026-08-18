"""Unit-price computation.

Sticker price and unit price answer different questions. Costco's $19.99 for 40
counts is cheaper per unit than Publix's $6.49 for 12, but it still costs three
times as much at the register and requires storing 40 of something. Both numbers
are computed and carried separately; nothing here ever collapses them.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import UomKind
from app.services.normalization import PackageSize
from app.services.units import BASE_UNIT, Quantity, convert

#: The unit each dimension is quoted in for display. These are the units shoppers
#: actually reason about in a US grocery store.
DISPLAY_UOM: dict[UomKind, str] = {
    UomKind.MASS: "lb",
    UomKind.VOLUME: "fl_oz",
    UomKind.COUNT: "ct",
}

#: Pretty forms for the UI.
_PRETTY = {"fl_oz": "fl oz", "ct": "count", "lb": "lb", "g": "g", "ml": "ml"}


@dataclass(frozen=True)
class UnitPrice:
    """A price normalized to a comparison basis."""

    #: Cents per one display unit. Fractional by nature.
    cents_per_unit: float
    uom: str
    kind: UomKind
    #: Cents per base unit (g / ml / ct); what cross-product comparison uses.
    cents_per_base_unit: float
    base_uom: str

    @property
    def display(self) -> str:
        dollars = self.cents_per_unit / 100
        precision = 4 if dollars < 0.1 else 2
        return f"${dollars:,.{precision}f}/{_PRETTY.get(self.uom, self.uom)}"

    def as_dict(self) -> dict:
        return {
            "cents_per_unit": round(self.cents_per_unit, 6),
            "uom": self.uom,
            "kind": self.kind.value,
            "cents_per_base_unit": round(self.cents_per_base_unit, 8),
            "base_uom": self.base_uom,
            "display": self.display,
        }


def compute_unit_price(price_cents: int, package: PackageSize | None) -> UnitPrice | None:
    """Derive the unit price for a package.

    Returns ``None`` when the package size is unknown - an unpriceable package is
    reported as such rather than defaulting to "per item", which would make a
    24-pack look like a single can.
    """
    if package is None or package.total_base is None:
        return None
    total = package.total_base
    if total.value <= 0:
        return None

    cents_per_base = price_cents / total.value
    display_uom = DISPLAY_UOM[total.kind]
    # Cents per display unit = cents per base unit x base units in one display unit.
    base_per_display = convert(1.0, display_uom, BASE_UNIT[total.kind])
    return UnitPrice(
        cents_per_unit=cents_per_base * base_per_display,
        uom=display_uom,
        kind=total.kind,
        cents_per_base_unit=cents_per_base,
        base_uom=total.uom,
    )


def cheaper_per_unit(a: UnitPrice | None, b: UnitPrice | None) -> UnitPrice | None:
    """Pick the better value, refusing to compare across dimensions."""
    if a is None or b is None:
        return a or b
    if a.kind is not b.kind:
        return None
    return a if a.cents_per_base_unit <= b.cents_per_base_unit else b
