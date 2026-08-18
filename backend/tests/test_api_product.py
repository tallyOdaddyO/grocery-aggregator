"""GET /api/v1/product/{id}.

Price history is the append-only evidence behind a displayed price, so ordering
and completeness are correctness properties, not presentation details.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.deps import get_db_session
from app.db.base import Base
from app.main import app
from app.schemas.product import ProductDetailResponse
from app.services.ingest import ingest_outcome
from app.services.search import SearchService

ZIP = "33009"


@pytest.fixture
def db_session():
    # TestClient serves requests on a different thread than the fixture, so a
    # SQLite database needs a shared connection permitting cross-thread use.
    # make_engine() handles that and honours RETAILSCOUT_TEST_DB.
    from tests.conftest import make_engine

    engine = make_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db_session] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session):
    """Ingest one search, so there is something real to look up."""
    for term in ("milk", "cheerios", "cola", "guacamole"):
        ingest_outcome(db_session, SearchService().search(term, ZIP))
    return db_session


def ingest_at(session, term: str, when: datetime, price_delta: int = 0):
    """Ingest a search as if it had been observed at a specific time.

    This is how the Phase 7 worker will produce history: repeated refreshes of the
    same variants. The timestamps and any price movement are supplied explicitly
    by the test - nothing is invented inside the application.
    """
    outcome = SearchService().search(term, ZIP)
    outcome.searched_at = when
    adjusted = []
    for product in outcome.products:
        if product.price is None:
            adjusted.append(product)
            continue
        new_cents = max(1, product.price.price_cents + price_delta)
        # Scale the unit price with the sticker price. Moving one without the
        # other would produce history that contradicts itself and could hide a
        # real inconsistency behind plausible-looking numbers.
        scale = new_cents / product.price.price_cents
        price = product.price.model_copy(
            update={
                "observed_at": when,
                "price_cents": new_cents,
                "unit_price_cents": (
                    product.price.unit_price_cents * scale
                    if product.price.unit_price_cents is not None
                    else None
                ),
            }
        )
        adjusted.append(product.model_copy(update={"price": price}))
    outcome.products = adjusted
    return ingest_outcome(session, outcome)


class TestLookup:
    def test_returns_200_and_conforms_to_the_schema(self, client, seeded):
        response = client.get("/api/v1/product/publix:P-1002")
        assert response.status_code == 200
        ProductDetailResponse.model_validate(response.json())

    def test_accepts_the_id_form_that_search_emits(self, client, seeded):
        search_body = client.get("/api/v1/search", params={"q": "cheerios"}).json()
        item_id = search_body["results"][0]["items"][0]["id"]
        assert client.get(f"/api/v1/product/{item_id}").status_code == 200

    def test_accepts_a_numeric_variant_id(self, client, seeded):
        from app.models import ProductVariant
        from sqlalchemy import select

        variant_id = seeded.scalars(select(ProductVariant.id)).first()
        response = client.get(f"/api/v1/product/{variant_id}")
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "bad_id",
        ["publix:NOPE", "nosuchretailer:P-1002", "999999", "garbage", "::"],
    )
    def test_invalid_id_returns_404(self, client, seeded, bad_id):
        response = client.get(f"/api/v1/product/{bad_id}")
        assert response.status_code == 404
        assert bad_id.split("/")[0] in response.json()["detail"] or "No product" in (
            response.json()["detail"]
        )

    def test_404_on_an_empty_database(self, client):
        assert client.get("/api/v1/product/publix:P-1002").status_code == 404

    def test_core_fields_are_populated(self, client, seeded):
        body = client.get("/api/v1/product/publix:P-1002").json()
        assert body["id"] == "publix:P-1002"
        assert body["retailer"] == "publix"
        assert body["title"]
        assert body["brand"] == "General Mills"
        assert body["store"]["zip"] == ZIP
        # Fixture-derived addresses stay unverified.
        assert body["store"]["address_verified"] is False


class TestPriceHistory:
    def test_history_is_ordered_newest_to_oldest(self, client, db_session):
        """Three refreshes, ingested out of chronological order on purpose."""
        now = datetime.now(timezone.utc)
        t_old = now - timedelta(days=2)
        t_mid = now - timedelta(days=1)

        ingest_at(db_session, "cheerios", t_mid, price_delta=+30)
        ingest_at(db_session, "cheerios", t_old, price_delta=-20)
        ingest_at(db_session, "cheerios", now, price_delta=0)

        body = client.get("/api/v1/product/publix:P-1002").json()
        history = body["price_history"]
        assert len(history) == 3

        timestamps = [datetime.fromisoformat(h["observed_at"]) for h in history]
        assert timestamps == sorted(timestamps, reverse=True), "history is not newest-first"
        assert timestamps[0].date() == now.date()

    def test_every_observation_is_retained(self, client, db_session):
        """The log is append-only: a price change adds, it never overwrites."""
        now = datetime.now(timezone.utc)
        for index in range(5):
            ingest_at(
                db_session, "cheerios", now - timedelta(hours=index), price_delta=index * 10
            )
        body = client.get("/api/v1/product/publix:P-1002").json()
        assert len(body["price_history"]) == 5
        prices = [h["sticker_price_cents"] for h in body["price_history"]]
        assert len(set(prices)) == 5, "distinct observations were collapsed"

    def test_current_price_reflects_the_newest_observation(self, client, db_session):
        now = datetime.now(timezone.utc)
        ingest_at(db_session, "cheerios", now - timedelta(days=1), price_delta=+100)
        ingest_at(db_session, "cheerios", now, price_delta=0)

        body = client.get("/api/v1/product/publix:P-1002").json()
        assert (
            body["current_price"]["sticker_price_cents"]
            == body["price_history"][0]["sticker_price_cents"]
        )

    def test_an_older_observation_does_not_overwrite_a_newer_price(
        self, client, db_session
    ):
        """A late-arriving stale reading is logged but must not become current."""
        now = datetime.now(timezone.utc)
        ingest_at(db_session, "cheerios", now, price_delta=0)
        current = client.get("/api/v1/product/publix:P-1002").json()["current_price"]

        ingest_at(db_session, "cheerios", now - timedelta(days=3), price_delta=+500)
        body = client.get("/api/v1/product/publix:P-1002").json()

        assert body["current_price"]["sticker_price_cents"] == current["sticker_price_cents"]
        assert len(body["price_history"]) == 2  # still recorded

    def test_history_honours_the_limit(self, client, db_session):
        now = datetime.now(timezone.utc)
        for index in range(4):
            ingest_at(db_session, "cheerios", now - timedelta(hours=index))
        body = client.get(
            "/api/v1/product/publix:P-1002", params={"history_limit": 2}
        ).json()
        assert len(body["price_history"]) == 2

    def test_history_entries_are_internally_consistent(self, client, db_session):
        """Unit price must track sticker price across observations."""
        now = datetime.now(timezone.utc)
        ingest_at(db_session, "cheerios", now - timedelta(days=1), price_delta=+40)
        ingest_at(db_session, "cheerios", now, price_delta=0)

        history = client.get("/api/v1/product/publix:P-1002").json()["price_history"]
        ratios = [
            h["unit_price_cents"] / h["sticker_price_cents"]
            for h in history
            if h["unit_price_cents"]
        ]
        assert len(ratios) >= 2
        # The wire rounds unit_price_cents to a whole cent, so allow that much
        # slack and no more: anything larger is real drift, not formatting.
        assert max(ratios) - min(ratios) < 0.01, (
            f"unit price drifted from sticker price: {ratios}"
        )

    def test_history_entries_carry_full_provenance(self, client, seeded):
        body = client.get("/api/v1/product/publix:P-1002").json()
        for entry in body["price_history"]:
            provenance = entry["provenance"]
            assert provenance["status"]
            assert provenance["verification_method"]
            assert "is_fresh" in provenance
            assert entry["unit_measure"]

    def test_verified_online_survives_into_history(self, client, seeded):
        """The grade added to the enum must reach the wire, not collapse to estimated."""
        body = client.get("/api/v1/product/publix:P-1002").json()
        methods = {h["provenance"]["verification_method"] for h in body["price_history"]}
        assert "verified_online" in methods
        assert body["current_price"]["provenance"]["verification_method"] == "verified_online"

    def test_delivery_price_grade_survives_into_history(self, client, seeded):
        body = client.get("/api/v1/product/costco:1119693").json()
        assert body["current_price"]["provenance"]["verification_method"] == "delivery_price"

    def test_unpriced_item_has_no_history_and_says_so(self, client, seeded):
        body = client.get("/api/v1/product/rey_chavez:RC-1002").json()
        assert body["price_history"] == []
        price = body["current_price"]
        assert price["sticker_price_cents"] is None
        assert price["provenance"]["verification_method"] == "no_price_published"


class TestDistinctPrices:
    def test_sticker_and_unit_price_are_separate_fields(self, client, seeded):
        body = client.get("/api/v1/product/bjs:300045").json()
        price = body["current_price"]
        assert price["sticker_price_cents"] == 899
        assert price["unit_price_cents"] is not None
        assert price["unit_price_cents"] != price["sticker_price_cents"]
        assert price["unit_measure"] == "lb"

    def test_bulk_beats_on_unit_price_while_costing_more_at_the_till(
        self, client, seeded
    ):
        club = client.get("/api/v1/product/bjs:300045").json()["current_price"]
        box = client.get("/api/v1/product/publix:P-1002").json()["current_price"]
        assert club["sticker_price_cents"] > box["sticker_price_cents"]
        assert club["unit_price_cents"] < box["unit_price_cents"]


class TestConfidenceStats:
    def test_veto_checks_passed_is_surfaced(self, client, seeded):
        stats = client.get("/api/v1/product/publix:P-1002").json()["confidence_stats"]
        assert stats["equivalent_count"] > 0
        assert "package_size" in stats["veto_checks_passed"]
        assert "unit_dimension" in stats["veto_checks_passed"]
        assert stats["veto_checks_failed"] == []

    def test_explanation_is_human_readable(self, client, seeded):
        stats = client.get("/api/v1/product/publix:P-1002").json()["confidence_stats"]
        assert stats["explanation"].startswith("Confidence ")
        assert stats["match_confidence"] >= stats["threshold"]

    def test_signals_are_structured_data(self, client, seeded):
        stats = client.get("/api/v1/product/publix:P-1002").json()["confidence_stats"]
        assert stats["signals"]
        for signal in stats["signals"]:
            assert set(signal) == {"name", "detail", "weight"}

    def test_only_checks_actually_verified_are_reported(self, client, seeded):
        """A check that could not be evaluated must be absent, not assumed passed."""
        response = client.get("/api/v1/product/walmart:99999001")  # no parseable size
        assert response.status_code == 200
        stats = response.json()["confidence_stats"]
        # The size could not be parsed, so the size veto could not be evaluated -
        # it must be absent rather than reported as cleared.
        assert "package_size" not in stats["veto_checks_passed"]
        assert "unit_dimension" not in stats["veto_checks_passed"]

    def test_a_product_with_no_equivalent_says_so_plainly(self, client, seeded):
        body = client.get("/api/v1/product/costco:1234567").json()  # 36-pack cola
        stats = body["confidence_stats"]
        assert stats["match_type"] == "singleton"
        assert stats["equivalent_count"] == 0
        assert "nothing to compare" in stats["explanation"]


class TestEquivalents:
    def test_equivalents_come_from_other_retailers(self, client, seeded):
        body = client.get("/api/v1/product/publix:P-1002").json()
        assert body["equivalent_products"]
        assert all(e["retailer"] != "publix" for e in body["equivalent_products"])

    def test_no_equivalent_violates_the_size_veto(self, client, seeded):
        """The 12 oz box must never list the 2 x 20.35 oz club pack as equivalent."""
        body = client.get("/api/v1/product/publix:P-1002").json()
        assert all(
            e["id"] not in ("bjs:300045", "costco:1119693")
            for e in body["equivalent_products"]
        )

    def test_equivalents_carry_full_price_data(self, client, seeded):
        body = client.get("/api/v1/product/publix:P-1002").json()
        for equivalent in body["equivalent_products"]:
            assert "provenance" in equivalent["price"]
            assert equivalent["price"]["provenance"]["status"]
