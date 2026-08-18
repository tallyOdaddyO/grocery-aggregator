"""Text normalization: package sizes, product names, brands, and UPCs.

The package-size parser is the load-bearing piece. Retailers describe the same
package a dozen different ways ("24 x 12 oz", "24/12oz", "12 fl oz, 24 pack",
"1 lb 4 oz"), and getting this wrong produces a unit price that is wrong by an
integer factor - which looks entirely plausible in a UI.

The parser never guesses. Text it cannot understand yields an empty result with
``parse_confidence == 0.0`` rather than a default of "1 each".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.core.enums import UomKind
from app.services.units import (
    BASE_UNIT, Quantity, UNIT_TOKENS, UnitError, add, parse_unit, to_base,
)

# --------------------------------------------------------------------------- #
# Package size
# --------------------------------------------------------------------------- #

#: A number: "12", "12.5", "1 1/2", "1/2".
_NUM = r"(?:\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:\.\d+)?)"

#: Unit alternation, longest token first so "fluid ounce" beats "ounce".
_UNIT_ALT = "|".join(re.escape(t) for t in UNIT_TOKENS)

#: A number followed by a unit. The trailing guard stops "g" from matching inside
#: a word, which would turn "grape" into grams.
_QTY_RE = re.compile(rf"({_NUM})\s*({_UNIT_ALT})(?![a-z])", re.IGNORECASE)

_PACK_OF_RE = re.compile(rf"\bpack\s+of\s+({_NUM})\b", re.IGNORECASE)
_N_PACK_RE = re.compile(r"\b(\d+)\s*-?\s*(?:packs?|pks?)\b", re.IGNORECASE)
#: Leading multiplier: "24 x 12 oz", "24x12oz". Requires a digit after the x so
#: that words containing "x" are never mistaken for a multiplier.
_MULT_RE = re.compile(r"(?<![a-z0-9])(\d+)\s*[x×*]\s*(?=\d)", re.IGNORECASE)
#: "12/12 oz" style. Disambiguated from the fraction "1/2 gal" below.
_SLASH_RE = re.compile(r"(?<![\d/])(\d+)\s*/\s*(\d+(?:\.\d+)?)\s*(?=[a-z])",
                       re.IGNORECASE)
_COUNT_RE = re.compile(
    r"\b(\d+)\s*-?\s*(?:ct|cts|ct\.|count|counts|pieces?|pcs?)\b", re.IGNORECASE
)
_DOZEN_RE = re.compile(r"\bdozen\b", re.IGNORECASE)

#: Categories where a bare "oz" means fluid ounces, not weight.
_VOLUME_CATEGORIES = {
    "beverage", "beverages", "drink", "drinks", "soda", "soft drink", "water",
    "juice", "beer", "wine", "spirits", "liquor", "seltzer", "energy drink",
    "sports drink", "coffee drink", "tea drink",
}

#: Words that decorate a size without changing it.
_NOISE = {
    "family", "size", "value", "pack", "packs", "box", "boxes", "bag", "bags",
    "bottle", "bottles", "can", "cans", "jar", "jars", "carton", "cartons",
    "container", "containers", "case", "cases", "tub", "tubs", "pouch",
    "pouches", "sleeve", "bunch", "approx", "approximately", "net", "wt",
    "weight", "about", "avg", "average", "per", "total", "club", "jumbo",
    "large", "small", "mini", "party", "bulk", "multipack", "variety",
}


@dataclass(frozen=True)
class PackageSize:
    """A parsed package descriptor.

    ``unit_quantity`` is the content of ONE unit; ``total_base`` is the whole
    package projected onto its base unit. Both are kept because a shopper needs
    the sticker size ("24 x 12 fl oz") and the comparison basis (8.5 L) at once -
    collapsing them hides how much you are actually required to buy.
    """

    raw_text: str
    pack_count: int = 1
    unit_quantity: Quantity | None = None
    total_base: Quantity | None = None
    parse_confidence: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_parsed(self) -> bool:
        return self.total_base is not None

    def as_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "pack_count": self.pack_count,
            "unit_value": self.unit_quantity.value if self.unit_quantity else None,
            "unit_uom": self.unit_quantity.uom if self.unit_quantity else None,
            "base_quantity": self.total_base.value if self.total_base else None,
            "base_uom": self.total_base.uom if self.total_base else None,
            "uom_kind": self.total_base.kind.value if self.total_base else None,
            "parse_confidence": self.parse_confidence,
            "notes": list(self.notes),
        }


def _to_float(num_text: str) -> float:
    """Parse '12', '12.5', '1/2', or '1 1/2'."""
    text = " ".join(num_text.split())
    if "/" in text:
        whole = 0.0
        if " " in text:
            whole_text, text = text.split(" ", 1)
            whole = float(whole_text)
        numerator, denominator = (p.strip() for p in text.split("/", 1))
        return whole + float(numerator) / float(denominator)
    return float(text)


def _prepare(text: str) -> str:
    """Lowercase, fold unicode fractions and separators, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).lower()
    for glyph, ascii_form in (
        ("½", " 1/2"), ("⅓", " 1/3"), ("¼", " 1/4"), ("¾", " 3/4"),
        ("⅔", " 2/3"), ("⅛", " 1/8"), ("×", "x"), ("–", "-"), ("—", "-"),
    ):
        text = text.replace(glyph, ascii_form)
    text = re.sub(r"\bhalf\b", "1/2", text)
    return " ".join(text.split())


def _extract_pack_count(text: str) -> tuple[int, str, list[str]]:
    """Pull every pack indicator out of the text.

    Returns the pack count, the text with those indicators removed, and any notes.
    """
    pack = 1
    notes: list[str] = []

    def take(match: re.Match, value: float) -> None:
        nonlocal pack
        pack = max(1, int(round(value)))

    # "pack of 6"
    if m := _PACK_OF_RE.search(text):
        take(m, _to_float(m.group(1)))
        text = text[: m.start()] + " " + text[m.end() :]

    # "12-pack", "6 pk"
    if m := _N_PACK_RE.search(text):
        take(m, float(m.group(1)))
        text = text[: m.start()] + " " + text[m.end() :]

    # "24 x 12 oz"
    if m := _MULT_RE.search(text):
        take(m, float(m.group(1)))
        text = text[: m.start()] + " " + text[m.end() :]

    # "12/12 oz" is a multipack; "1/2 gal" is a fraction. The distinguishing rule:
    # a multipack never has a numerator smaller than its denominator, whereas a
    # real fraction of a package almost always does.
    if m := _SLASH_RE.search(text):
        left, right = float(m.group(1)), float(m.group(2))
        if left >= right:
            take(m, left)
            text = text[: m.start()] + " " + str(m.group(2)) + " " + text[m.end() :]
            notes.append("slash_multipack")

    # "40 ct", "40 count"
    if m := _COUNT_RE.search(text):
        take(m, float(m.group(1)))
        text = text[: m.start()] + " " + text[m.end() :]

    if pack == 1 and _DOZEN_RE.search(text):
        pack = 12
        text = _DOZEN_RE.sub(" ", text)

    return pack, " ".join(text.split()), notes


def _extract_quantities(text: str) -> list[Quantity]:
    quantities: list[Quantity] = []
    for match in _QTY_RE.finditer(text):
        try:
            unit = parse_unit(match.group(2))
            quantities.append(
                Quantity(_to_float(match.group(1)), unit.symbol, unit.kind)
            )
        except UnitError:
            continue
    return quantities


def parse_package_size(text: str | None, category: str | None = None) -> PackageSize:
    """Parse a free-text package descriptor.

    ``category`` disambiguates a bare "oz": in a beverage context it means fluid
    ounces. Without a hint, "oz" is read as weight and the result is tagged
    ``ambiguous_oz`` so downstream matching can treat it cautiously instead of
    assuming the parser was certain.
    """
    raw = (text or "").strip()
    if not raw:
        return PackageSize(raw_text=raw)

    prepared = _prepare(raw)
    pack_count, remainder, notes = _extract_pack_count(prepared)

    # Drop decorative words so they cannot be mistaken for units.
    remainder = " ".join(
        w for w in re.split(r"[\s,;()]+", remainder) if w and w not in _NOISE
    )

    quantities = _extract_quantities(remainder)

    is_beverage = bool(category) and category.strip().lower() in _VOLUME_CATEGORIES
    resolved: list[Quantity] = []
    for q in quantities:
        if q.uom == "oz":
            if is_beverage:
                # Reinterpret, do not convert: the number was always fluid ounces.
                q = Quantity(q.value, "fl_oz", UomKind.VOLUME)
                if "reinterpreted_oz_as_fluid" not in notes:
                    notes.append("reinterpreted_oz_as_fluid")
            elif "ambiguous_oz" not in notes:
                notes.append("ambiguous_oz")
        resolved.append(q)

    if not resolved:
        if pack_count > 1:
            # A bare count: "2-pack", "40 ct". The package content is the count.
            total = Quantity(float(pack_count), "ct", UomKind.COUNT)
            return PackageSize(
                raw_text=raw, pack_count=pack_count,
                unit_quantity=Quantity(1.0, "ct", UomKind.COUNT),
                total_base=total, parse_confidence=0.8, notes=tuple(notes),
            )
        return PackageSize(raw_text=raw, notes=tuple(notes))

    # Composite sizes ("1 lb 4 oz") describe ONE unit and are summed. Quantities of
    # a different dimension are a restatement, not an addition, so only the leading
    # dimension is summed.
    leading_kind = resolved[0].kind
    same_kind = [q for q in resolved if q.kind is leading_kind]
    unit_quantity = same_kind[0]
    for extra in same_kind[1:]:
        unit_quantity = add(unit_quantity, extra)
    if len(same_kind) > 1:
        notes.append("composite_size")
    if len(resolved) > len(same_kind):
        notes.append("mixed_units_ignored")

    total_base = to_base(unit_quantity).scaled(pack_count)
    confidence = 1.0 if len(resolved) == len(same_kind) else 0.85
    if "ambiguous_oz" in notes:
        confidence = min(confidence, 0.9)

    return PackageSize(
        raw_text=raw,
        pack_count=pack_count,
        unit_quantity=unit_quantity,
        total_base=total_base,
        parse_confidence=confidence,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #

def gtin_check_digit(digits: str) -> int:
    """Compute the GS1 mod-10 check digit for a GTIN body (without its check)."""
    total = 0
    for i, char in enumerate(reversed(digits)):
        total += int(char) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def normalize_upc(raw: str | None) -> str | None:
    """Normalize a UPC/EAN/GTIN to a validated, zero-padded GTIN-14.

    Returns ``None`` when the value is malformed or its check digit fails. A UPC is
    an identity claim, and Stage 1 of the matcher treats an exact match as
    conclusive - so a corrupt barcode must not be allowed to act as one.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) not in (8, 12, 13, 14):
        return None
    if gtin_check_digit(digits[:-1]) != int(digits[-1]):
        return None
    return digits.zfill(14)


# --------------------------------------------------------------------------- #
# Names, brands, attributes
# --------------------------------------------------------------------------- #

#: Words that carry no distinguishing meaning in a product name.
_NAME_STOPWORDS = {
    "the", "a", "an", "of", "with", "and", "&", "in", "for", "brand", "new",
    "original",
}

#: Attribute flags parsed out of names. These become Stage 2 vetoes: they change
#: what the product IS, so two products differing on one are never equivalent.
_ATTRIBUTE_PATTERNS: dict[str, re.Pattern] = {
    "organic": re.compile(r"\borganic\b", re.I),
    "decaf": re.compile(r"\bdecaf(?:feinated)?\b", re.I),
    "gluten_free": re.compile(r"\bgluten[\s-]?free\b", re.I),
    "sugar_free": re.compile(r"\b(?:sugar[\s-]?free|no sugar added|unsweetened)\b", re.I),
    "diet": re.compile(r"\b(?:diet|zero|zero sugar)\b", re.I),
    # A trailing \b cannot follow "%" (it is not a word character), so the
    # percentage forms carry their own boundaries.
    "low_fat": re.compile(
        r"(?:\blow[\s-]?fat\b|\breduced[\s-]?fat\b|\bskim\b|\bnon[\s-]?fat\b"
        r"|\bfat[\s-]?free\b|(?<!\d)[12]\s?%)",
        re.I,
    ),
    "whole_fat": re.compile(r"\bwhole\b", re.I),
    "lactose_free": re.compile(r"\blactose[\s-]?free\b", re.I),
    "caffeine_free": re.compile(r"\bcaffeine[\s-]?free\b", re.I),
    "frozen": re.compile(r"\bfrozen\b", re.I),
    "unsalted": re.compile(r"\bunsalted\b", re.I),
}

#: Corporate suffixes and punctuation that differ between feeds for one brand.
_BRAND_NOISE = re.compile(
    r"\b(?:inc|llc|ltd|co|corp|company|brands?|foods?)\b\.?", re.I
)


def normalize_brand(raw: str | None) -> str | None:
    """Fold a brand to a comparable key: 'General Mills, Inc.' -> 'general mills'."""
    if not raw:
        return None
    text = unicodedata.normalize("NFKD", str(raw)).lower()
    text = text.replace("'", "").replace("’", "")
    text = _BRAND_NOISE.sub(" ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = " ".join(text.split())
    return text or None


def extract_attributes(text: str | None) -> dict[str, bool]:
    """Pull structured variant flags out of a product name."""
    if not text:
        return {}
    found = {
        key: bool(pattern.search(text)) for key, pattern in _ATTRIBUTE_PATTERNS.items()
    }
    # "whole" only means full-fat when nothing contradicts it ("whole grain" is not
    # a fat claim, and "whole" alongside a low-fat claim is not either).
    if found.get("whole_fat") and found.get("low_fat"):
        found["whole_fat"] = False
    if text and re.search(r"\bwhole\s+(?:grain|wheat|bean|kernel)\b", text, re.I):
        found["whole_fat"] = False
    return {k: v for k, v in found.items() if v}


def normalize_name(raw: str | None) -> str:
    """Reduce a product name to comparable tokens.

    Size descriptors are stripped because size is compared structurally by the
    package parser; leaving "12 oz" in the name would let string similarity do a
    job that must be done by exact arithmetic.
    """
    if not raw:
        return ""
    text = _prepare(str(raw))
    text = _PACK_OF_RE.sub(" ", text)
    text = _N_PACK_RE.sub(" ", text)
    text = _COUNT_RE.sub(" ", text)
    # The multiplier must go before the quantity: removing "12 oz" from
    # "24 x 12 oz" first would strand the "24 x" and leave a bare "x" token.
    text = _MULT_RE.sub(" ", text)
    text = _QTY_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9%\s]+", " ", text)
    tokens = [
        t for t in text.split()
        if t and t not in _NAME_STOPWORDS and t not in _NOISE
        and not t.isdigit() and t != "x"
    ]
    return " ".join(tokens)
