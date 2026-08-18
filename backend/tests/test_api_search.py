"""GET /api/v1/search.

The endpoint's job is to translate the orchestrator faithfully: partial failures
must surface as degraded health plus `is_complete: false`, never as a 500 and
never as a silently shortened list.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api.v1.common import FRESHNESS_TTL
from app.api.v1.search import get_search_service
from app.connectors.base import BaseRetailerConnector, StoreRef
from app.connectors.registry import build_connectors
from app.main import app
from app.schemas.search import SearchResponse
from app.services.search import SearchService

ZIP = "33009"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def use_connectors(connectors, **kwargs):
    """Point the endpoint at a specific connector set."""
    app.dependency_overrides[get_search_service] = lambda: SearchService(
        connectors, **kwargs
    )


class _StubConnector(BaseRetailerConnector):
    """A well-behaved connector, optionally slow or broken."""

    def __init__(self, slug, name, behaviour="ok", delay=0.0):
        super().__init__()
        self.slug, self.name = slug, name
        self.behaviour, self.delay = behaviour, delay

    def resolve_store(self, zip_code):
        return StoreRef(retailer_slug=self.slug, store_number="1", zip=zip_code)

    def fetch_raw(self, term, store):
        if self.delay:
            time.sleep(self.delay)
        if self.behaviour == "raises":
            raise PermissionError("403 Forbidden (bot protection)")
        return [{"sku": f"{self.slug}-1"}]

    def parse_item(self, raw, store):
        return self.build_product(
            store=store, sku=raw["sku"],
            display_name="Cheerios Original Cereal 12 oz",
            brand="General Mills", upc="016000275270",
            category="cereal", price_cents=449,
        )


class TestBasics:
    def test_returns_200_and_conforms_to_the_schema(self, client):
        response = client.get("/api/v1/search", params={"q": "milk"})
        assert response.status_code == 200
        SearchResponse.model_validate(response.json())  # raises if the shape drifts

    def test_echoes_query_and_defaults_the_zip(self, client):
        body = client.get("/api/v1/search", params={"q": "milk"}).json()
        assert body["query"] == "milk"
        assert body["zip_code"] == "33009"

    def test_zip_can_be_overridden(self, client):
        body = client.get("/api/v1/search", params={"q": "milk", "zip": "33009"}).json()
        assert body["zip_code"] == "33009"

    def test_missing_query_is_rejected(self, client):
        assert client.get("/api/v1/search").status_code == 422
        assert client.get("/api/v1/search", params={"q": ""}).status_code == 422

    def test_all_eight_retailers_report_health(self, client):
        body = client.get("/api/v1/search", params={"q": "milk"}).json()
        assert {h["retailer"] for h in body["connector_health"]} == {
            "walmart", "costco", "bjs", "publix",
            "winn_dixie", "fresco_y_mas", "presidente", "rey_chavez",
        }


class TestPartialFailure:
    def test_timeout_returns_200_with_partial_results(self, client):
        """The required case: a hanging connector must not fail the request."""
        connectors = build_connectors()
        slow = next(c for c in connectors if c.slug == "walmart")
        slow.fetch_raw = lambda term, store: time.sleep(3)
        use_connectors(connectors, timeout_seconds=0.3)

        started = time.perf_counter()
        response = client.get("/api/v1/search", params={"q": "cheerios"})
        elapsed = time.perf_counter() - started

        assert response.status_code == 200
        body = response.json()
        assert body["is_complete"] is False

        walmart = next(
            h for h in body["connector_health"] if h["retailer"] == "walmart"
        )
        assert walmart["status"] == "degraded"
        assert "did not respond" in walmart["error_reason"]
        # Latency must reflect the wait actually incurred, not a fictional 0ms.
        assert walmart["latency_ms"] >= 250

        # Partial results are still present, from the retailers that answered.
        assert body["results"], "a timeout emptied the response"
        retailers = {
            item["retailer"] for g in body["results"] for item in g["items"]
        }
        assert "walmart" not in retailers
        assert len(retailers) >= 5
        # And the timeout genuinely bounded the request.
        assert elapsed < 2.0

    def test_a_raising_connector_degrades_only_itself(self, client):
        connectors = build_connectors()
        next(c for c in connectors if c.slug == "publix").fetch_raw = (
            lambda term, store: (_ for _ in ()).throw(PermissionError("403 Forbidden"))
        )
        use_connectors(connectors)

        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        publix = next(h for h in body["connector_health"] if h["retailer"] == "publix")
        assert publix["status"] == "degraded"
        assert "403" in publix["error_reason"]
        assert body["is_complete"] is False
        assert body["results"]

    def test_is_complete_true_when_every_connector_succeeds(self, client):
        use_connectors([
            _StubConnector("walmart", "Walmart"),
            _StubConnector("publix", "Publix"),
        ])
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        assert body["is_complete"] is True
        assert all(h["status"] == "ok" for h in body["connector_health"])

    def test_unknown_zip_yields_unavailable_not_an_error(self, client):
        response = client.get("/api/v1/search", params={"q": "milk", "zip": "99999"})
        assert response.status_code == 200
        body = response.json()
        assert body["is_complete"] is False
        assert body["results"] == []
        assert all(
            h["status"] == "unavailable" for h in body["connector_health"]
        )

    def test_every_connector_failing_still_returns_200(self, client):
        use_connectors([
            _StubConnector("walmart", "Walmart", "raises"),
            _StubConnector("publix", "Publix", "raises"),
        ])
        response = client.get("/api/v1/search", params={"q": "cheerios"})
        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []
        assert body["is_complete"] is False


class TestVetoEnforcementInGrouping:
    def test_no_group_mixes_different_package_sizes(self, client):
        """The Stage 2 guarantee, asserted at the API boundary."""
        for term in ("cheerios", "cola", "milk"):
            body = client.get("/api/v1/search", params={"q": term}).json()
            for group in body["results"]:
                sizes = {
                    i["size_raw"] for i in group["items"] if i["size_raw"]
                }
                assert len(sizes) <= 1, (
                    f"group '{group['canonical_name']}' mixes sizes: {sizes}"
                )

    def test_club_multipacks_are_not_grouped_with_single_boxes(self, client):
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        for group in body["results"]:
            retailers = {i["retailer"] for i in group["items"]}
            # The 2 x 20.35 oz club packs must never share a group with the
            # 12 oz grocery boxes.
            assert not ({"bjs", "costco"} & retailers and {"publix", "walmart"} & retailers)

    def test_group_ids_are_unique(self, client):
        """UPCs are reused across pack sizes, so the id cannot be the UPC alone."""
        for term in ("cheerios", "cola", "milk"):
            body = client.get("/api/v1/search", params={"q": term}).json()
            ids = [g["group_id"] for g in body["results"]]
            assert len(ids) == len(set(ids)), f"duplicate group_id for '{term}'"

    def test_group_ids_are_stable_across_identical_searches(self, client):
        first = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        second = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        assert [g["group_id"] for g in first["results"]] == [
            g["group_id"] for g in second["results"]
        ]

    def test_match_type_reports_how_the_group_was_formed(self, client):
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        for group in body["results"]:
            assert group["match_type"] in {"upc", "attributes", "fuzzy", "singleton"}
            if len(group["items"]) == 1:
                assert group["match_type"] == "singleton"

    def test_multi_item_groups_agree_on_organic(self, client):
        body = client.get("/api/v1/search", params={"q": "milk"}).json()
        for group in body["results"]:
            organic = {"organic" in i["title"].lower() for i in group["items"]}
            assert len(organic) <= 1, group["canonical_name"]


class TestProvenance:
    def test_no_fixture_price_is_ever_reported_verified_in_store(self, client):
        for term in ("milk", "cheerios", "cola"):
            body = client.get("/api/v1/search", params={"q": term}).json()
            for group in body["results"]:
                for item in group["items"]:
                    assert (
                        item["price"]["provenance"]["verification_method"]
                        != "verified_in_store"
                    )

    def test_exact_internal_grade_is_preserved_in_status(self, client):
        """The wire enum is coarser than our grades; status must not lose them."""
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        statuses = {
            i["price"]["provenance"]["status"]
            for g in body["results"] for i in g["items"]
        }
        assert "verified_online" in statuses
        assert statuses <= {
            "verified_in_store", "verified_online", "delivery_price",
            "estimated", "stale", "no_price_published",
        }

    def test_costco_is_reported_as_a_delivery_price(self, client):
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        costco = [
            i for g in body["results"] for i in g["items"]
            if i["retailer"] == "costco"
        ]
        assert costco
        assert costco[0]["price"]["provenance"]["verification_method"] == "delivery_price"

    def test_items_without_a_price_say_no_price_published(self, client):
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        rey = [
            i for g in body["results"] for i in g["items"]
            if i["retailer"] == "rey_chavez"
        ]
        assert rey, "the distributor's stocked item was dropped instead of reported"
        price = rey[0]["price"]
        assert price["sticker_price_cents"] is None
        assert price["unit_price_cents"] is None
        assert price["provenance"]["verification_method"] == "no_price_published"
        assert price["provenance"]["is_fresh"] is False

    def test_fresh_prices_are_flagged_fresh(self, client):
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        priced = [
            i for g in body["results"] for i in g["items"]
            if i["price"]["sticker_price_cents"]
        ]
        assert priced
        assert all(i["price"]["provenance"]["is_fresh"] for i in priced)

    def test_stale_prices_are_flagged_not_fresh(self, client):
        """A price older than the TTL must not be presented as current."""
        from datetime import datetime, timedelta, timezone

        connectors = [_StubConnector("walmart", "Walmart")]
        old = datetime.now(timezone.utc) - FRESHNESS_TTL - timedelta(hours=1)
        original = connectors[0].parse_item

        def stale_parse(raw, store):
            product = original(raw, store)
            return product.model_copy(
                update={"price": product.price.model_copy(update={"observed_at": old})}
            )

        connectors[0].parse_item = stale_parse
        use_connectors(connectors)

        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        item = body["results"][0]["items"][0]
        assert item["price"]["provenance"]["is_fresh"] is False

    def test_unit_price_carries_its_measure(self, client):
        body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        for group in body["results"]:
            for item in group["items"]:
                price = item["price"]
                if price["unit_price_cents"] is not None:
                    assert price["unit_measure"] not in ("", "unknown")
                else:
                    assert price["unit_measure"] == "unknown"


class TestHealthEndpoint:
    def test_health(self, client):
        body = client.get("/api/v1/health").json()
        assert body["status"] == "ok"
        assert body["target_zip"] == "33009"
