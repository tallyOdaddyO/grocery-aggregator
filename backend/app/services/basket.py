"""Basket optimization: cheapest single trip vs cheapest split across stores.

Two questions, deliberately kept apart:

* **Cheapest complete** - what does this basket cost if I make one stop? Only a
  retailer stocking *every* requested item qualifies. If none does, the answer is
  "there isn't one", not a cheaper number assembled from stores you would have to
  drive between.
* **Cheapest split** - what is the lowest total if I am willing to shop around?

All arithmetic is integer cents. Anything that cannot be sourced at a published
price is reported, never quietly dropped from the total.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.connectors.base import NormalizedProduct, StoreRef
from app.services.grouping import ProductGroup, group_products
from app.services.search import SearchOutcome, SearchService


@dataclass
class ItemOptions:
    """Every way to buy one requested line, keyed by retailer."""

    query: str
    quantity: int
    #: retailer slug -> that retailer's cheapest priced product for this line.
    by_retailer: dict[str, NormalizedProduct] = field(default_factory=dict)
    reason_unavailable: str | None = None

    @property
    def is_available(self) -> bool:
        return bool(self.by_retailer)

    def cheapest(self) -> NormalizedProduct | None:
        if not self.by_retailer:
            return None
        return min(self.by_retailer.values(), key=lambda p: p.price.price_cents)


@dataclass
class BasketResult:
    zip_code: str
    outcomes: list[SearchOutcome] = field(default_factory=list)
    options: list[ItemOptions] = field(default_factory=list)
    stores: dict[str, StoreRef] = field(default_factory=dict)
    retailer_names: dict[str, str] = field(default_factory=dict)

    @property
    def unavailable(self) -> list[ItemOptions]:
        return [o for o in self.options if not o.is_available]

    @property
    def available(self) -> list[ItemOptions]:
        return [o for o in self.options if o.is_available]


def _select_group(groups: list[ProductGroup]) -> ProductGroup | None:
    """Pick which equivalence group a free-text query means.

    Preference is the group carried by the most distinct retailers: that is the
    item most comparable across stores, and the one most likely to make a
    single-stop basket possible. Ties break on the lowest sticker price, then on
    name for determinism.
    """
    if not groups:
        return None

    def rank(group: ProductGroup):
        retailers = {m.retailer_slug for m in group.members}
        priced = [m.price.price_cents for m in group.members if m.price]
        return (
            -len(retailers),
            min(priced) if priced else 10**9,
            group.canonical_name,
        )

    return min(groups, key=rank)


def build_options(
    service: SearchService, queries: list[tuple[str, int]], zip_code: str
) -> BasketResult:
    """Resolve each requested line into per-retailer purchase options."""
    result = BasketResult(zip_code=zip_code)
    # Distinct terms are searched once, even if requested repeatedly.
    searched: dict[str, SearchOutcome] = {}

    for query, quantity in queries:
        key = " ".join(query.lower().split())
        if key not in searched:
            outcome = service.search(query, zip_code)
            searched[key] = outcome
            result.outcomes.append(outcome)
            result.stores.update(outcome.stores)
            for report in outcome.reports:
                result.retailer_names.setdefault(report.slug, report.name)

        outcome = searched[key]
        options = ItemOptions(query=query, quantity=quantity)

        group = _select_group(group_products(outcome.products))
        if group is None:
            options.reason_unavailable = (
                f"No retailer in ZIP {zip_code} returned a match for '{query}'."
            )
            result.options.append(options)
            continue

        for member in group.members:
            if member.price is None:
                continue
            current = options.by_retailer.get(member.retailer_slug)
            if current is None or member.price.price_cents < current.price.price_cents:
                options.by_retailer[member.retailer_slug] = member

        if not options.by_retailer:
            # Stocked somewhere, but nobody published a price. Distinct from
            # "nobody has it", and reported as such.
            carriers = sorted({m.retailer_slug for m in group.members})
            options.reason_unavailable = (
                f"'{query}' is carried by {', '.join(carriers)} but no published "
                "price is available."
            )

        result.options.append(options)

    return result


def cheapest_complete(result: BasketResult) -> tuple[str, list[ItemOptions], int] | None:
    """The cheapest retailer that stocks EVERY requested item.

    Returns None when no single retailer can supply the whole basket - including
    when any requested item is unavailable everywhere, since in that case no
    single stop completes the basket either.
    """
    if not result.options or result.unavailable:
        return None

    candidates = set.intersection(
        *(set(option.by_retailer) for option in result.options)
    )
    if not candidates:
        return None

    def total_for(slug: str) -> int:
        return sum(
            option.by_retailer[slug].price.price_cents * option.quantity
            for option in result.options
        )

    best = min(sorted(candidates), key=total_for)
    return best, result.options, total_for(best)


def cheapest_split(result: BasketResult) -> dict[str, list[tuple[ItemOptions, NormalizedProduct]]]:
    """Cheapest source for each line, grouped into one trip per retailer."""
    trips: dict[str, list[tuple[ItemOptions, NormalizedProduct]]] = {}
    for option in result.available:
        pick = option.cheapest()
        if pick is None:
            continue
        trips.setdefault(pick.retailer_slug, []).append((option, pick))
    return trips
