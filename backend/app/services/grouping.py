"""Cluster search results into equivalence groups.

Grouping is where a matching mistake becomes visible to a shopper: two products in
one group are being presented as the same thing at different prices. So the rule
here is stricter than pairwise matching. A candidate joins a group only if it
matches the representative **and** is not vetoed against any existing member.
One physical-attribute veto anywhere in the group excludes the item outright -
it can never be admitted on the strength of a good average score.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.connectors.base import NormalizedProduct
from app.core.enums import MatchStage, UomKind
from app.services.matching import (
    MatchCandidate, MatchResult, config_for_category, match,
)
from app.services.normalization import PackageSize
from app.services.units import Quantity


def candidate_from_product(product: NormalizedProduct) -> MatchCandidate:
    """Adapt a connector product into a matcher candidate.

    The sizes were already parsed by the adapter, so they are reused rather than
    re-derived from the title - re-parsing could disagree with the stored values
    and produce a group that contradicts the unit prices shown inside it.
    """
    package: PackageSize | None = None
    if product.base_quantity and product.base_uom and product.uom_kind:
        kind = product.uom_kind
        unit_quantity = None
        if product.net_content_value and product.net_content_uom:
            unit_quantity = Quantity(
                product.net_content_value, product.net_content_uom, kind
            )
        package = PackageSize(
            raw_text=product.size_text or "",
            pack_count=product.pack_count,
            unit_quantity=unit_quantity,
            total_base=Quantity(product.base_quantity, product.base_uom, kind),
            parse_confidence=product.size_parse_confidence,
        )

    return MatchCandidate(
        display_name=product.display_name,
        normalized_name=product.normalized_name,
        brand=product.brand,
        normalized_brand=product.normalized_brand,
        category=product.category,
        upc=product.upc,
        package=package,
        attributes=dict(product.attributes),
    )


@dataclass
class ProductGroup:
    """A set of products the engine considers equivalent."""

    group_id: str
    representative: NormalizedProduct
    members: list[NormalizedProduct] = field(default_factory=list)
    #: Best match stage that admitted any member; "singleton" when alone.
    stage: MatchStage = MatchStage.NONE
    #: Why each non-representative member was admitted, keyed by retailer_sku.
    explanations: dict[str, MatchResult] = field(default_factory=dict)
    #: Items considered and rejected, with the reason. Kept for diagnostics.
    rejected: list[tuple[str, str]] = field(default_factory=list)

    @property
    def canonical_name(self) -> str:
        """The clearest label for the group.

        The shortest display name among members is used: retailers pad titles with
        packaging nouns and marketing, and the shortest is usually the plainest.
        """
        return min(
            (m.display_name for m in self.members),
            key=lambda n: (len(n), n),
            default=self.representative.display_name,
        )

    @property
    def match_type(self) -> str:
        if len(self.members) <= 1:
            return "singleton"
        return self.stage.value


def _group_id(product: NormalizedProduct) -> str:
    """Stable id derived from the group's defining identity.

    Built from UPC when available, otherwise brand + normalized name + size, so the
    same group keeps the same id across repeated searches.
    """
    size = (
        f"{product.base_quantity:.4f}{product.base_uom}"
        if product.base_quantity and product.base_uom
        else "nosize"
    )
    # The size is always part of the seed, even when a UPC is present. Retailers
    # demonstrably reuse one UPC across pack sizes (BJ's ships the 12 oz barcode
    # on a 2 x 20.35 oz club pack), and those are different groups by our own veto
    # rules - so a UPC alone would collide two groups onto one id.
    if product.upc:
        seed = f"upc:{product.upc}|{size}"
    else:
        seed = f"nm:{product.normalized_brand or ''}|{product.normalized_name}|{size}"
    return hashlib.sha1(seed.encode()).hexdigest()[:16]


def group_products(products: list[NormalizedProduct]) -> list[ProductGroup]:
    """Cluster products into equivalence groups, enforcing vetoes strictly."""
    groups: list[ProductGroup] = []

    # Deterministic order so identical inputs always produce identical groups.
    ordered = sorted(
        products,
        key=lambda p: (
            p.upc is None,               # UPC-bearing products seed groups first
            p.normalized_name,
            p.retailer_slug,
            p.retailer_sku,
        ),
    )

    for product in ordered:
        candidate = candidate_from_product(product)
        placed = False

        for group in groups:
            representative = candidate_from_product(group.representative)
            config = config_for_category(product.category or group.representative.category)
            result = match(candidate, representative, config)

            if result.vetoed or not result.is_match:
                if result.vetoed:
                    group.rejected.append((product.retailer_sku, result.veto_reason or ""))
                continue

            # Strict rule: a veto against ANY member excludes the item, even though
            # it matched the representative. Equivalence must hold across the whole
            # group, not just with whichever product happened to arrive first.
            veto = next(
                (
                    r
                    for r in (
                        match(candidate, candidate_from_product(member), config)
                        for member in group.members
                    )
                    if r.vetoed
                ),
                None,
            )
            if veto is not None:
                group.rejected.append((product.retailer_sku, veto.veto_reason or ""))
                continue

            group.members.append(product)
            group.explanations[product.retailer_sku] = result
            # Record the strongest stage that admitted a member.
            if group.stage is MatchStage.NONE or result.stage is MatchStage.UPC:
                group.stage = result.stage
            placed = True
            break

        if not placed:
            groups.append(
                ProductGroup(
                    group_id=_group_id(product),
                    representative=product,
                    members=[product],
                )
            )

    # Largest, most-comparable groups first; stable tiebreak on name.
    groups.sort(key=lambda g: (-len(g.members), g.canonical_name))
    return groups
