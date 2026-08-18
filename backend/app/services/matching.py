"""The multi-stage product matching engine.

Three stages, in order of decreasing certainty:

1. **Identity** - a validated, equal GTIN is conclusive.
2. **Normalized attributes** - brand, dimension, package size, and variant flags.
   This stage holds a *veto*: a hard mismatch here can never be outvoted by name
   similarity, because those attributes change what the product physically is.
3. **Fuzzy** - token-set similarity over normalized names, thresholded per category.

Every decision is accompanied by :class:`MatchSignal` records. The signals are
plain data - they serialize straight into ``product_matches.signals`` and into the
API response, so the system can always answer "why did you match these?".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.enums import MatchStage, UomKind
from app.services.units import sizes_equivalent
from app.services.normalization import (
    PackageSize, extract_attributes, normalize_brand, normalize_name,
    normalize_upc, parse_package_size,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MatchConfig:
    """Tunable thresholds. Categories differ in how much slack is safe."""

    #: Relative difference in total base quantity still considered the same size.
    size_tolerance: float = 0.02
    #: Minimum confidence for :attr:`MatchResult.is_match`.
    fuzzy_threshold: float = 0.82
    #: Weights for the Stage 2/3 blend. They sum to 1.0.
    brand_weight: float = 0.25
    size_weight: float = 0.25
    name_weight: float = 0.50
    #: Attributes that are treated as hard vetoes when they differ.
    veto_attributes: frozenset[str] = frozenset(
        {
            "organic", "decaf", "gluten_free", "sugar_free", "diet", "low_fat",
            "whole_fat", "lactose_free", "caffeine_free", "unsalted",
        }
    )


DEFAULT_CONFIG = MatchConfig()

#: Per-category overrides. Produce is loose because labelled weights genuinely
#: vary; anything ingested by an infant or used as medicine is strict, because a
#: wrong match there is not merely a bad deal.
CATEGORY_CONFIG: dict[str, MatchConfig] = {
    "produce": replace(DEFAULT_CONFIG, size_tolerance=0.10, fuzzy_threshold=0.75),
    "bakery": replace(DEFAULT_CONFIG, size_tolerance=0.08, fuzzy_threshold=0.78),
    "meat": replace(DEFAULT_CONFIG, size_tolerance=0.10, fuzzy_threshold=0.80),
    "deli": replace(DEFAULT_CONFIG, size_tolerance=0.10, fuzzy_threshold=0.80),
    "baby_formula": replace(DEFAULT_CONFIG, size_tolerance=0.01, fuzzy_threshold=0.95),
    "baby": replace(DEFAULT_CONFIG, size_tolerance=0.01, fuzzy_threshold=0.93),
    "pharmacy": replace(DEFAULT_CONFIG, size_tolerance=0.01, fuzzy_threshold=0.95),
    "supplements": replace(DEFAULT_CONFIG, size_tolerance=0.01, fuzzy_threshold=0.93),
}


def config_for_category(category: str | None) -> MatchConfig:
    if not category:
        return DEFAULT_CONFIG
    return CATEGORY_CONFIG.get(category.strip().lower(), DEFAULT_CONFIG)


# --------------------------------------------------------------------------- #
# Explainability primitives
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MatchSignal:
    """One reason contributing to (or withheld from) a match score."""

    name: str
    detail: str
    weight: float

    def as_dict(self) -> dict:
        return {"name": self.name, "detail": self.detail, "weight": float(self.weight)}


#: The Stage 2 hard constraints, in evaluation order. Named here so the API can
#: report which ones a match actually cleared rather than merely asserting a score.
VETO_CHECKS: tuple[str, ...] = (
    "unit_dimension",      # mass vs volume vs count
    "package_size",        # total base quantity within tolerance
    "variant_attributes",  # organic, decaf, low fat, ...
    "brand",
)


@dataclass(frozen=True)
class MatchResult:
    confidence: float
    stage: MatchStage
    signals: tuple[MatchSignal, ...] = ()
    vetoed: bool = False
    veto_reason: str | None = None
    threshold: float = DEFAULT_CONFIG.fuzzy_threshold
    #: Which hard constraints were evaluated AND passed. A check that could not be
    #: evaluated (an unparseable size, a brand missing on one side) is absent
    #: rather than listed - we report what we verified, not what we assumed.
    veto_checks_passed: tuple[str, ...] = ()
    #: Checks that were evaluated and failed. At most one, since a veto is final.
    veto_checks_failed: tuple[str, ...] = ()

    @property
    def is_match(self) -> bool:
        return not self.vetoed and self.confidence >= self.threshold

    @property
    def summary(self) -> str:
        """One line a shopper can read, built from the same data the API returns."""
        if self.vetoed:
            return f"Not equivalent: {self.veto_reason or 'failed a hard constraint'}"
        if not self.signals:
            return "No match"
        reasons = ", ".join(s.detail for s in self.signals)
        return f"Confidence {round(self.confidence * 100)}%: {reasons}"

    def as_dict(self) -> dict:
        """JSON-ready payload, stored verbatim and returned by the API."""
        return {
            "confidence": round(float(self.confidence), 4),
            "stage": self.stage.value,
            "signals": [s.as_dict() for s in self.signals],
            "vetoed": self.vetoed,
            "veto_reason": self.veto_reason,
            "is_match": self.is_match,
            "threshold": float(self.threshold),
            "summary": self.summary,
            "veto_checks_passed": list(self.veto_checks_passed),
            "veto_checks_failed": list(self.veto_checks_failed),
        }


# --------------------------------------------------------------------------- #
# Candidate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MatchCandidate:
    """A product reduced to the normalized form the engine compares."""

    display_name: str
    normalized_name: str
    brand: str | None = None
    normalized_brand: str | None = None
    category: str | None = None
    upc: str | None = None
    package: PackageSize | None = None
    attributes: dict = field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        name: str,
        brand: str | None = None,
        upc: str | None = None,
        category: str | None = None,
        size_text: str | None = None,
        attributes: dict | None = None,
    ) -> "MatchCandidate":
        parsed = parse_package_size(size_text or name, category=category)
        found = extract_attributes(name)
        if attributes:
            found.update(attributes)
        return cls(
            display_name=name,
            normalized_name=normalize_name(name),
            brand=brand,
            normalized_brand=normalize_brand(brand),
            category=category,
            upc=normalize_upc(upc),
            package=parsed,
            attributes=found,
        )


# --------------------------------------------------------------------------- #
# String similarity
# --------------------------------------------------------------------------- #


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def ratio(a: str, b: str) -> float:
    """Levenshtein similarity in [0, 1]."""
    if not a and not b:
        return 1.0
    longest = max(len(a), len(b))
    return 1.0 - levenshtein(a, b) / longest if longest else 1.0


def name_similarity(a: str, b: str) -> float:
    """Token-set similarity tolerant of word order and minor inflection.

    Exact shared tokens score fully; leftover tokens are greedily paired by
    Levenshtein ratio so "banana" and "bananas" are recognised as the same word
    without a stemmer's false positives.
    """
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = a.split(), b.split()
    if not tokens_a or not tokens_b:
        return 0.0

    remaining_a, remaining_b = list(tokens_a), list(tokens_b)
    matched = 0.0

    for token in list(remaining_a):
        if token in remaining_b:
            remaining_a.remove(token)
            remaining_b.remove(token)
            matched += 1.0

    for token in list(remaining_a):
        if not remaining_b:
            break
        best = max(remaining_b, key=lambda other: ratio(token, other))
        score = ratio(token, best)
        if score >= 0.7:
            matched += score
            remaining_a.remove(token)
            remaining_b.remove(best)

    return 2 * matched / (len(tokens_a) + len(tokens_b))


# --------------------------------------------------------------------------- #
# Stage 2 checks
# --------------------------------------------------------------------------- #


def _size_relation(
    left: PackageSize | None, right: PackageSize | None, tolerance: float
) -> tuple[str, str]:
    """Classify two package sizes: equivalent, differs, dimension, or unknown."""
    if not (left and right and left.total_base and right.total_base):
        return "unknown", "package size could not be parsed for both products"

    a, b = left.total_base, right.total_base
    if a.kind is not b.kind:
        return (
            "dimension",
            f"different dimension ({a.kind.value} vs {b.kind.value}) - "
            f"{a} and {b} are not comparable without a density",
        )

    if max(a.value, b.value) == 0:
        return "unknown", "package size is zero"
    if sizes_equivalent(a.value, b.value, tolerance):
        return "equivalent", f"size equivalent ({left.unit_quantity} ≈ {right.unit_quantity})"
    return (
        "differs",
        f"package size differs ({_describe(left)} vs {_describe(right)})",
    )


def _describe(package: PackageSize) -> str:
    if package.pack_count > 1 and package.unit_quantity:
        return f"{package.pack_count} x {package.unit_quantity}"
    return str(package.unit_quantity) if package.unit_quantity else package.raw_text


def _attribute_conflict(
    left: MatchCandidate, right: MatchCandidate, config: MatchConfig
) -> str | None:
    for key in sorted(config.veto_attributes):
        if bool(left.attributes.get(key)) != bool(right.attributes.get(key)):
            holder = left if left.attributes.get(key) else right
            other = right if holder is left else left
            label = key.replace("_", " ")
            return (
                f"{label} differs ('{holder.display_name}' is {label}, "
                f"'{other.display_name}' is not)"
            )
    return None


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


def match(
    left: MatchCandidate, right: MatchCandidate, config: MatchConfig | None = None
) -> MatchResult:
    """Compare two candidates and explain the outcome."""
    config = config or config_for_category(left.category or right.category)
    signals: list[MatchSignal] = []

    # --- Stage 2 constraints are evaluated first, because a veto outranks every
    # --- other signal including an identical UPC.
    size_state, size_detail = _size_relation(
        left.package, right.package, config.size_tolerance
    )
    attribute_conflict = _attribute_conflict(left, right, config)

    brand_conflict = None
    if left.normalized_brand and right.normalized_brand:
        if left.normalized_brand != right.normalized_brand:
            brand_conflict = (
                f"different brands ({left.brand} vs {right.brand})"
            )

    veto_reason: str | None = None
    failed_check: str | None = None
    if size_state == "dimension":
        veto_reason, failed_check = size_detail, "unit_dimension"
    elif size_state == "differs":
        veto_reason, failed_check = size_detail, "package_size"
    elif attribute_conflict:
        veto_reason, failed_check = attribute_conflict, "variant_attributes"
    elif brand_conflict:
        veto_reason, failed_check = brand_conflict, "brand"

    # Record only the constraints we could actually evaluate and that passed.
    passed: list[str] = []
    if size_state == "equivalent":
        passed.extend(("unit_dimension", "package_size"))
    elif size_state == "dimension":
        pass  # dimension itself failed
    if attribute_conflict is None and (left.attributes or right.attributes):
        passed.append("variant_attributes")
    if (
        brand_conflict is None
        and left.normalized_brand
        and right.normalized_brand
    ):
        passed.append("brand")
    checks_passed = tuple(c for c in VETO_CHECKS if c in passed)

    upc_equal = bool(left.upc and right.upc and left.upc == right.upc)

    if veto_reason:
        if size_state in ("differs", "dimension"):
            signals.append(MatchSignal("size", size_detail, 0.0))
        if attribute_conflict:
            signals.append(MatchSignal("attribute", attribute_conflict, 0.0))
        if brand_conflict:
            signals.append(MatchSignal("brand", brand_conflict, 0.0))
        if upc_equal:
            # The feed asserts one identity while the packages disagree. Trust the
            # physical measurement and record the contradiction for investigation.
            signals.append(
                MatchSignal(
                    "upc_size_conflict",
                    f"feed reports the same UPC ({left.upc}) for packages that "
                    f"are not equivalent; the UPC was not trusted",
                    0.0,
                )
            )
        return MatchResult(
            confidence=0.0,
            stage=MatchStage.ATTRIBUTES,
            signals=tuple(signals),
            vetoed=True,
            veto_reason=veto_reason,
            threshold=config.fuzzy_threshold,
            veto_checks_passed=checks_passed,
            veto_checks_failed=(failed_check,) if failed_check else (),
        )

    # --- Stage 1: identity.
    if upc_equal:
        signals.append(MatchSignal("upc", f"UPC exact match ({left.upc})", 1.0))
        if size_state == "equivalent":
            signals.append(MatchSignal("size", size_detail, 0.0))
        return MatchResult(
            confidence=1.0,
            stage=MatchStage.UPC,
            signals=tuple(signals),
            threshold=config.fuzzy_threshold,
            veto_checks_passed=checks_passed,
        )

    # --- Stages 2 and 3: weighted blend.
    confidence = 0.0

    if left.normalized_brand and right.normalized_brand:
        confidence += config.brand_weight
        signals.append(
            MatchSignal("brand", f"brand matches ({left.brand})", config.brand_weight)
        )
    else:
        signals.append(MatchSignal("brand", "brand unknown for one side", 0.0))

    if size_state == "equivalent":
        confidence += config.size_weight
        signals.append(MatchSignal("size", size_detail, config.size_weight))
    else:
        signals.append(
            MatchSignal("size_unknown", "package size could not be compared", 0.0)
        )

    similarity = name_similarity(left.normalized_name, right.normalized_name)
    contribution = similarity * config.name_weight
    confidence += contribution
    signals.append(
        MatchSignal(
            "name_similarity",
            f"name similarity {round(similarity * 100)}%",
            round(contribution, 4),
        )
    )

    shared = sorted(k for k in left.attributes if right.attributes.get(k))
    if shared:
        signals.append(
            MatchSignal(
                "attribute",
                "shared attributes: " + ", ".join(s.replace("_", " ") for s in shared),
                0.0,
            )
        )

    stage = MatchStage.FUZZY if similarity > 0 else MatchStage.NONE
    return MatchResult(
        confidence=min(1.0, round(confidence, 4)),
        stage=stage,
        signals=tuple(signals),
        threshold=config.fuzzy_threshold,
        veto_checks_passed=checks_passed,
    )
