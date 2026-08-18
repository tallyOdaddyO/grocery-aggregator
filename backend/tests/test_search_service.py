"""Partial failure isolation.

The guarantee under test: if one retailer breaks, the other seven still return
results, and the broken one is reported as degraded rather than silently omitted.
"""
from __future__ import annotations

import time

import pytest

from app.connectors.base import BaseRetailerConnector, NormalizedProduct, StoreRef
from app.connectors.registry import build_connectors
from app.core.enums import RetailerStatus
from app.services.search import SearchService

ZIP = "33009"


class _FakeConnector(BaseRetailerConnector):
    """A connector whose failure mode can be dialled in."""

    def __init__(self, slug, behaviour="ok", delay=0.0):
        super().__init__()
        self.slug = slug
        self.name = slug.title()
        self.behaviour = behaviour
        self.delay = delay

    def resolve_store(self, zip_code):
        if self.behaviour == "no_store":
            return None
        if self.behaviour == "store_raises":
            raise ConnectionError("store locator unreachable")
        return StoreRef(retailer_slug=self.slug, store_number="1", zip=zip_code)

    def fetch_raw(self, term, store):
        if self.delay:
            time.sleep(self.delay)
        if self.behaviour == "fetch_raises":
            raise TimeoutError("upstream timed out")
        if self.behaviour == "blocked":
            raise PermissionError("403 Forbidden (bot protection)")
        return [{"sku": f"{self.slug}-1", "name": "Whole Milk 1 gal"}]

    def parse_item(self, raw, store):
        if self.behaviour == "parse_raises":
            raise ValueError("unexpected payload shape")
        return self.build_product(
            store=store, sku=raw["sku"], display_name=raw["name"], price_cents=399
        )


class TestOneFailureDoesNotSinkTheSearch:
    def test_seven_retailers_survive_a_walmart_outage(self):
        """The headline requirement, with the real connector set."""
        connectors = build_connectors()
        walmart = next(c for c in connectors if c.slug == "walmart")

        def explode(term, store):
            raise PermissionError("403 Forbidden (bot protection)")

        walmart.fetch_raw = explode

        outcome = SearchService(connectors).search("milk", ZIP)

        assert len(outcome.reports) == 8
        walmart_report = next(r for r in outcome.reports if r.slug == "walmart")
        assert walmart_report.status is RetailerStatus.DEGRADED
        assert "403" in walmart_report.reason
        assert walmart_report.product_count == 0

        # Every other retailer still reported, and results still came back.
        others = [r for r in outcome.reports if r.slug != "walmart"]
        assert len(others) == 7
        assert outcome.products, "a single retailer failure emptied the search"
        assert all(p.retailer_slug != "walmart" for p in outcome.products)

    def test_the_response_admits_it_is_incomplete(self):
        connectors = build_connectors()
        next(c for c in connectors if c.slug == "publix").fetch_raw = (
            lambda term, store: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        outcome = SearchService(connectors).search("milk", ZIP)
        assert outcome.is_complete is False
        assert any(r.slug == "publix" for r in outcome.degraded)

    @pytest.mark.parametrize(
        "behaviour,expected",
        [
            ("fetch_raises", RetailerStatus.DEGRADED),
            ("blocked", RetailerStatus.DEGRADED),
            ("parse_raises", RetailerStatus.DEGRADED),
            ("store_raises", RetailerStatus.UNAVAILABLE),
            ("no_store", RetailerStatus.UNAVAILABLE),
        ],
    )
    def test_each_failure_mode_is_contained(self, behaviour, expected):
        connectors = [
            _FakeConnector("good_a"),
            _FakeConnector("broken", behaviour),
            _FakeConnector("good_b"),
        ]
        outcome = SearchService(connectors).search("milk", ZIP)

        broken = next(r for r in outcome.reports if r.slug == "broken")
        assert broken.status is expected
        assert broken.reason

        assert len(outcome.products) == 2
        assert {r.slug for r in outcome.healthy} == {"good_a", "good_b"}

    def test_a_hanging_connector_times_out_without_blocking_the_rest(self):
        connectors = [
            _FakeConnector("fast_a"),
            _FakeConnector("slow", delay=2.0),
            _FakeConnector("fast_b"),
        ]
        started = time.perf_counter()
        outcome = SearchService(connectors, timeout_seconds=0.3).search("milk", ZIP)
        elapsed = time.perf_counter() - started

        assert elapsed < 1.5, "a slow retailer held up the whole search"
        slow = next(r for r in outcome.reports if r.slug == "slow")
        assert slow.status is RetailerStatus.DEGRADED
        assert "did not respond" in slow.reason
        assert len(outcome.products) == 2

    def test_every_retailer_appears_in_the_report_even_when_it_fails(self):
        connectors = [
            _FakeConnector("a", "fetch_raises"),
            _FakeConnector("b", "no_store"),
            _FakeConnector("c"),
        ]
        outcome = SearchService(connectors).search("milk", ZIP)
        assert {r.slug for r in outcome.reports} == {"a", "b", "c"}

    def test_all_connectors_failing_yields_an_empty_but_honest_result(self):
        connectors = [
            _FakeConnector("a", "fetch_raises"), _FakeConnector("b", "no_store")
        ]
        outcome = SearchService(connectors).search("milk", ZIP)
        assert outcome.products == []
        assert outcome.is_complete is False
        assert len(outcome.reports) == 2


class TestFullFanOut:
    def test_all_eight_retailers_report(self):
        outcome = SearchService().search("milk", ZIP)
        assert len(outcome.reports) == 8
        assert {r.slug for r in outcome.reports} == {
            "walmart", "costco", "bjs", "publix",
            "winn_dixie", "fresco_y_mas", "presidente", "rey_chavez",
        }

    def test_products_arrive_from_multiple_retailers(self):
        outcome = SearchService().search("milk", ZIP)
        assert len({p.retailer_slug for p in outcome.products}) >= 5

    def test_outcome_serializes_for_the_api(self):
        import json

        payload = SearchService().search("milk", ZIP).as_dict()
        json.dumps(payload)
        assert set(payload) >= {
            "term", "zip", "searched_at", "is_complete", "retailers",
            "degraded_retailers",
        }
        for report in payload["retailers"]:
            assert set(report) == {
                "slug", "name", "status", "reason", "product_count", "elapsed_ms"
            }

    def test_a_broken_subclass_cannot_take_down_the_search(self):
        """Defence in depth: even if search() itself raises, isolate it."""

        class Sabotaged(_FakeConnector):
            def search(self, term, zip_code):
                raise SystemError("connector is completely broken")

        connectors = [_FakeConnector("ok_one"), Sabotaged("saboteur")]
        outcome = SearchService(connectors).search("milk", ZIP)
        saboteur = next(r for r in outcome.reports if r.slug == "saboteur")
        assert saboteur.status is RetailerStatus.UNAVAILABLE
        assert len(outcome.products) == 1
