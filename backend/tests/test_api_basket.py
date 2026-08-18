"""POST /api/v1/compare-basket.

The line this endpoint must not cross: a single-stop total may only be reported
when one retailer genuinely stocks the whole basket. Anything else is a plan the
shopper cannot actually execute.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api.v1.basket import get_search_service
from app.connectors.registry import build_connectors
from app.main import app
from app.schemas.basket import CompareBasketResponse
from app.services.search import SearchService

URL = "/api/v1/compare-basket"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def use_connectors(connectors, **kwargs):
    app.dependency_overrides[get_search_service] = lambda: SearchService(
        connectors, **kwargs
    )


def post(client, items, zip_code="33009"):
    return client.post(
        URL,
        json={
            "zip_code": zip_code,
            "items": [
                {"query": q, "quantity": n} if isinstance(q, str) else q
                for q, n in items
            ],
        },
    )


class TestSingleRetailerHasEverything:
    def test_cheapest_complete_is_populated(self, client):
        response = post(client, [("milk", 1), ("cheerios", 1), ("cola", 1)])
        assert response.status_code == 200
        body = response.json()
        CompareBasketResponse.model_validate(body)

        plan = body["cheapest_complete"]
        assert plan is not None
        assert plan["strategy"] == "single_store"
        assert plan["stop_count"] == 1
        assert len(plan["trips"]) == 1
        # Every requested line is present in the single trip.
        assert {i["query"] for i in plan["trips"][0]["items"]} == {
            "milk", "cheerios", "cola"
        }

    def test_single_trip_really_is_one_retailer(self, client):
        body = post(client, [("milk", 1), ("cheerios", 1)]).json()
        trip = body["cheapest_complete"]["trips"][0]
        assert {i["retailer"] for i in trip["items"]} == {trip["retailer"]}

    def test_it_picks_the_cheapest_qualifying_retailer(self, client):
        """Among retailers stocking everything, the chosen total must be minimal."""
        from app.services.basket import build_options

        items = [("milk", 1), ("cheerios", 1), ("cola", 1)]
        body = post(client, items).json()
        chosen = body["cheapest_complete"]

        # Independently enumerate every retailer that stocks the whole basket.
        options = build_options(SearchService(), items, "33009").options
        qualifying = set.intersection(*(set(o.by_retailer) for o in options))
        totals = {
            slug: sum(
                o.by_retailer[slug].price.price_cents * o.quantity for o in options
            )
            for slug in qualifying
        }

        assert chosen["trips"][0]["retailer"] in qualifying
        assert chosen["total_cents"] == min(totals.values()), (
            f"chose {chosen['total_cents']} when {totals} was available"
        )

    def test_totals_are_exact_integer_cents(self, client):
        body = post(client, [("milk", 2), ("cheerios", 3)]).json()
        for key in ("cheapest_complete", "cheapest_split"):
            plan = body[key]
            for trip in plan["trips"]:
                for line in trip["items"]:
                    expected = line["price"]["sticker_price_cents"] * line["quantity"]
                    assert line["line_total_cents"] == expected
                    assert isinstance(line["line_total_cents"], int)
                assert trip["subtotal_cents"] == sum(
                    i["line_total_cents"] for i in trip["items"]
                )
            assert plan["total_cents"] == sum(t["subtotal_cents"] for t in plan["trips"])

    def test_quantity_multiplies_the_line_total(self, client):
        one = post(client, [("cheerios", 1)]).json()["cheapest_complete"]
        three = post(client, [("cheerios", 3)]).json()["cheapest_complete"]
        assert three["total_cents"] == one["total_cents"] * 3
        assert three["item_count"] == 3


class TestNoSingleRetailerHasEverything:
    def test_cheapest_complete_is_null_and_split_still_optimizes(self, client):
        """Guacamole is Walmart-only; malanga is Fresco y Mas-only."""
        response = post(client, [("guacamole", 1), ("malanga", 1)])
        assert response.status_code == 200
        body = response.json()

        assert body["cheapest_complete"] is None, (
            "reported a one-stop plan no retailer can actually fulfil"
        )
        split = body["cheapest_split"]
        assert split is not None
        assert split["stop_count"] == 2
        assert {t["retailer"] for t in split["trips"]} == {"walmart", "fresco_y_mas"}
        assert split["item_count"] == 2

    def test_savings_is_null_when_there_is_no_single_stop_option(self, client):
        body = post(client, [("guacamole", 1), ("malanga", 1)]).json()
        assert body["savings_cents"] is None

    def test_split_picks_the_globally_cheapest_source_per_line(self, client):
        body = post(client, [("cheerios", 1)]).json()
        chosen = body["cheapest_split"]["trips"][0]["items"][0]

        search = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        prices = [
            i["price"]["sticker_price_cents"]
            for g in search["results"]
            for i in g["items"]
            if i["price"]["sticker_price_cents"]
        ]
        assert chosen["price"]["sticker_price_cents"] == min(prices)

    def test_split_is_never_more_expensive_than_a_single_stop(self, client):
        body = post(client, [("milk", 1), ("cheerios", 1), ("cola", 1)]).json()
        if body["cheapest_complete"]:
            assert (
                body["cheapest_split"]["total_cents"]
                <= body["cheapest_complete"]["total_cents"]
            )
            assert body["savings_cents"] >= 0


class TestMissingAndUnknownItems:
    def test_unknown_item_is_reported_not_crashed(self, client):
        response = post(client, [("cheerios", 1), ("zzzz-nonexistent-item", 1)])
        assert response.status_code == 200
        body = response.json()
        assert [u["query"] for u in body["unavailable_items"]] == [
            "zzzz-nonexistent-item"
        ]
        assert body["unavailable_items"][0]["reason"]
        # The rest of the basket is still optimized.
        assert body["cheapest_split"]["item_count"] == 1

    def test_an_unavailable_item_forbids_a_single_stop_plan(self, client):
        """No one stop can complete a basket containing an unobtainable item."""
        body = post(client, [("cheerios", 1), ("zzzz-nonexistent-item", 1)]).json()
        assert body["cheapest_complete"] is None

    def test_basket_of_only_unknown_items(self, client):
        body = post(client, [("zzzz-nope", 1), ("also-not-real", 2)]).json()
        assert body["cheapest_complete"] is None
        assert body["cheapest_split"] is None
        assert len(body["unavailable_items"]) == 2
        assert body["unavailable_items"][1]["quantity"] == 2

    def test_item_carried_without_a_published_price_is_distinguished(self, client):
        """Rey Chavez stocks goods but quotes prices; that is not 'nobody has it'."""
        connectors = [c for c in build_connectors() if c.slug == "rey_chavez"]
        use_connectors(connectors)
        body = post(client, [("cheerios", 1)]).json()
        assert body["cheapest_complete"] is None
        assert body["unavailable_items"]
        reason = body["unavailable_items"][0]["reason"]
        assert "rey_chavez" in reason and "no published price" in reason

    def test_empty_basket_is_rejected(self, client):
        response = client.post(URL, json={"zip_code": "33009", "items": []})
        assert response.status_code == 422

    def test_invalid_quantity_is_rejected(self, client):
        response = client.post(
            URL, json={"items": [{"query": "milk", "quantity": 0}]}
        )
        assert response.status_code == 422

    def test_unknown_zip_returns_200_with_nothing_available(self, client):
        response = post(client, [("milk", 1)], zip_code="99999")
        assert response.status_code == 200
        body = response.json()
        assert body["cheapest_complete"] is None
        assert body["cheapest_split"] is None
        assert body["unavailable_items"]
        assert body["is_complete"] is False


class TestPartialFailureAndProvenance:
    def test_a_failing_retailer_does_not_fail_the_basket(self, client):
        connectors = build_connectors()
        next(c for c in connectors if c.slug == "walmart").fetch_raw = (
            lambda term, store: (_ for _ in ()).throw(PermissionError("403 Forbidden"))
        )
        use_connectors(connectors)

        response = post(client, [("milk", 1), ("cheerios", 1)])
        assert response.status_code == 200
        body = response.json()
        assert body["is_complete"] is False
        walmart = next(
            h for h in body["connector_health"] if h["retailer"] == "walmart"
        )
        assert walmart["status"] == "degraded"
        assert body["cheapest_split"] is not None

    def test_a_timeout_does_not_fail_the_basket(self, client):
        connectors = build_connectors()
        next(c for c in connectors if c.slug == "publix").fetch_raw = (
            lambda term, store: time.sleep(3)
        )
        use_connectors(connectors, timeout_seconds=0.3)

        response = post(client, [("cheerios", 1)])
        assert response.status_code == 200
        body = response.json()
        assert body["is_complete"] is False
        publix = next(h for h in body["connector_health"] if h["retailer"] == "publix")
        assert publix["status"] == "degraded"

    def test_a_retailer_failing_any_search_is_reported_failed(self, client):
        """Health must not be laundered by an unrelated successful lookup."""
        connectors = build_connectors()
        walmart = next(c for c in connectors if c.slug == "walmart")
        calls = {"n": 0}
        original = walmart.fetch_raw

        def flaky(term, store):
            calls["n"] += 1
            if calls["n"] > 1:
                raise PermissionError("403 Forbidden")
            return original(term, store)

        walmart.fetch_raw = flaky
        use_connectors(connectors)

        body = post(client, [("cheerios", 1), ("milk", 1)]).json()
        walmart_health = next(
            h for h in body["connector_health"] if h["retailer"] == "walmart"
        )
        assert walmart_health["status"] == "degraded"

    def test_every_line_carries_full_provenance(self, client):
        body = post(client, [("milk", 1), ("cheerios", 1)]).json()
        for key in ("cheapest_complete", "cheapest_split"):
            if not body[key]:
                continue
            for trip in body[key]["trips"]:
                for line in trip["items"]:
                    provenance = line["price"]["provenance"]
                    assert provenance["status"]
                    assert provenance["verification_method"] != "no_price_published"
                    assert "is_fresh" in provenance

    def test_multi_buy_caveat_is_surfaced_on_the_line(self, client):
        """A '2 for $7' unit price only applies if you buy two - say so."""
        body = post(client, [("cheerios", 1)]).json()
        lines = [
            line
            for key in ("cheapest_complete", "cheapest_split")
            if body[key]
            for trip in body[key]["trips"]
            for line in trip["items"]
        ]
        multi = [line for line in lines if "multi_buy_required" in line["notes"]]
        assert multi, "the multi-buy caveat was dropped from the basket line"

    def test_line_items_identify_a_specific_product(self, client):
        body = post(client, [("cheerios", 1)]).json()
        line = body["cheapest_split"]["trips"][0]["items"][0]
        assert ":" in line["product_id"]
        assert line["title"]
        # The id resolves through the product endpoint.
        assert line["product_id"].split(":")[0] == line["retailer"]
