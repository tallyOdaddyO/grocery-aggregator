"""Unit registry and conversion.

Every price comparison in the system reduces to arithmetic performed here, so the
factors are exact definitions rather than rounded approximations, and conversions
across incompatible dimensions are refused rather than fudged.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import UomKind


class UnitError(ValueError):
    """Base class for unit problems."""


class UnknownUnitError(UnitError):
    pass


class IncompatibleUnitsError(UnitError):
    """Raised when a conversion would require information we do not have.

    Mass to volume needs a density; count to anything needs a per-item weight.
    Guessing here would produce a plausible, wrong unit price - the worst possible
    failure mode for a price comparison tool.
    """


@dataclass(frozen=True)
class Unit:
    symbol: str
    kind: UomKind
    #: How many base units one of this unit is worth (g, ml, or each).
    to_base_factor: float


#: Base unit per dimension.
BASE_UNIT: dict[UomKind, str] = {
    UomKind.MASS: "g",
    UomKind.VOLUME: "ml",
    UomKind.COUNT: "ct",
}

_M = UomKind.MASS
_V = UomKind.VOLUME
_C = UomKind.COUNT

UNITS: dict[str, Unit] = {
    # --- mass (base: gram) ---
    "mg": Unit("mg", _M, 0.001),
    "g": Unit("g", _M, 1.0),
    "kg": Unit("kg", _M, 1000.0),
    "oz": Unit("oz", _M, 28.349523125),      # international avoirdupois ounce
    "lb": Unit("lb", _M, 453.59237),         # exactly 16 oz
    # --- volume (base: millilitre), US customary ---
    "ml": Unit("ml", _V, 1.0),
    "l": Unit("l", _V, 1000.0),
    "fl_oz": Unit("fl_oz", _V, 29.5735295625),
    "cup": Unit("cup", _V, 236.5882365),
    "pt": Unit("pt", _V, 473.176473),
    "qt": Unit("qt", _V, 946.352946),
    "gal": Unit("gal", _V, 3785.411784),
    "tbsp": Unit("tbsp", _V, 14.78676478125),
    "tsp": Unit("tsp", _V, 4.92892159375),
    # --- count (base: each) ---
    "ct": Unit("ct", _C, 1.0),
    "dozen": Unit("dozen", _C, 12.0),
}

#: Spelling variants seen on shelf tags and retailer feeds.
#: Order matters at parse time: multi-word aliases must be tried before short ones,
#: so that "fluid ounce" never resolves through "ounce".
_ALIASES: dict[str, str] = {
    # mass
    "milligram": "mg", "milligrams": "mg", "mgs": "mg",
    "gram": "g", "grams": "g", "gm": "g", "gms": "g", "grammes": "g",
    "kilogram": "kg", "kilograms": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "ounce": "oz", "ounces": "oz", "ozs": "oz", "oz.": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "lb.": "lb", "#": "lb",
    # volume
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml",
    "millilitres": "ml", "mls": "ml", "cc": "ml",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l", "ltr": "l",
    "fluid ounce": "fl_oz", "fluid ounces": "fl_oz", "fluid oz": "fl_oz",
    "fl oz": "fl_oz", "fl.oz": "fl_oz", "fl. oz.": "fl_oz", "fl oz.": "fl_oz",
    "floz": "fl_oz", "floz.": "fl_oz", "fl-oz": "fl_oz",
    "gallon": "gal", "gallons": "gal", "gals": "gal",
    "quart": "qt", "quarts": "qt", "qts": "qt",
    "pint": "pt", "pints": "pt", "pts": "pt",
    "cups": "cup",
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp", "tbsps": "tbsp",
    "teaspoon": "tsp", "teaspoons": "tsp", "tsps": "tsp",
    # count
    "count": "ct", "counts": "ct", "cts": "ct", "ct.": "ct",
    "each": "ct", "ea": "ct", "piece": "ct", "pieces": "ct", "pc": "ct",
    "pcs": "ct", "unit": "ct", "units": "ct",
    "dozens": "dozen", "doz": "dozen",
    # --- Spanish. Fresco y Mas and Presidente list in Spanish as a matter of
    # --- course in this market, so these are core vocabulary, not an edge case.
    "gramo": "g", "gramos": "g",
    "kilogramo": "kg", "kilogramos": "kg",
    "libra": "lb", "libras": "lb",
    "onza": "oz", "onzas": "oz",
    "onza liquida": "fl_oz", "onzas liquidas": "fl_oz",
    "mililitro": "ml", "mililitros": "ml",
    "litro": "l", "litros": "l", "lt": "l",
    "galon": "gal", "galones": "gal",
    "cuarto": "qt", "cuartos": "qt",
    "unidad": "ct", "unidades": "ct", "pieza": "ct", "piezas": "ct",
    "docena": "dozen", "docenas": "dozen",
}

#: Longest-first so "fluid ounce" wins over "ounce" during tokenizing.
UNIT_TOKENS: list[str] = sorted(
    set(UNITS) | set(_ALIASES), key=lambda t: (-len(t), t)
)


def parse_unit(text: str) -> Unit:
    """Resolve a unit name or alias to its canonical :class:`Unit`."""
    if not text:
        raise UnknownUnitError("empty unit")
    key = " ".join(text.strip().lower().split())
    key = _ALIASES.get(key, key)
    key = key.replace(" ", "_") if key not in UNITS else key
    if key in UNITS:
        return UNITS[key]
    raise UnknownUnitError(f"unknown unit: {text!r}")


@dataclass(frozen=True)
class Quantity:
    """A magnitude with a unit. Immutable so it can be shared freely."""

    value: float
    uom: str
    kind: UomKind

    def __post_init__(self) -> None:
        if self.uom not in UNITS:
            raise UnknownUnitError(f"unknown unit: {self.uom!r}")

    @classmethod
    def of(cls, value: float, unit_text: str) -> "Quantity":
        unit = parse_unit(unit_text)
        return cls(value, unit.symbol, unit.kind)

    def scaled(self, factor: float) -> "Quantity":
        return Quantity(self.value * factor, self.uom, self.kind)

    def __str__(self) -> str:
        pretty = {"fl_oz": "fl oz"}.get(self.uom, self.uom)
        value = int(self.value) if self.value == int(self.value) else self.value
        return f"{value} {pretty}"


def convert(value: float, from_uom: str, to_uom: str) -> float:
    """Convert between two units of the same dimension."""
    src, dst = parse_unit(from_uom), parse_unit(to_uom)
    if src.kind is not dst.kind:
        raise IncompatibleUnitsError(
            f"cannot convert {src.kind.value} ({src.symbol}) to "
            f"{dst.kind.value} ({dst.symbol}) without additional information"
        )
    return value * src.to_base_factor / dst.to_base_factor


def to_base(quantity: Quantity) -> Quantity:
    """Project a quantity onto its dimension's base unit (g / ml / ct)."""
    base = BASE_UNIT[quantity.kind]
    return Quantity(convert(quantity.value, quantity.uom, base), base, quantity.kind)


def add(left: Quantity, right: Quantity) -> Quantity:
    """Sum two quantities of the same dimension, in the left one's unit."""
    if left.kind is not right.kind:
        raise IncompatibleUnitsError(
            f"cannot add {left.kind.value} to {right.kind.value}"
        )
    return Quantity(
        left.value + convert(right.value, right.uom, left.uom), left.uom, left.kind
    )
