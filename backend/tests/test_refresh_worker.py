"""Phase 7 end-to-end: a refresh job survives a WAF block without losing history.

The scenario under test is the one that actually happens in production: a retailer
that worked yesterday starts refusing us today. The job must record that honestly,
keep serving what it already knew, and never discard the evidence behind it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.connectors.base import StoreRef
from app.connectors.http import BlockedError, LiveUnsupported
from app.connectors.registry import build_connectors
from app.core.enums import PriceProvenance, RetailerStatus
from app.db.base import Base
from app.models import Price, PriceObservation, ProductVariant, Retailer
from app.services.refresh import CIRCUIT_BREAKER_THRESHOLD, run_refresh
from app.services.search import SearchService

ZIP = "33009"


@pytest.fixture
def session():
    from tests.conftest import make_engine

    engine = make_engine()
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


def block_live(connector, error=None):
    """Make a connector's live fetch behave like a WAF refusal."""
    def blocked(term, store):
        raise error or BlockedError(
            "https://www.example-retailer.test/search refused the request "
            "(HTTP 403). Treating as blocked; not retrying."
        )
    connector.fetch_live = blocked
    return connector


class TestFixtureRefresh:
    def test_a_refresh_records_observations_and_health(self, session):
        service = SearchService()
        report = run_refresh(session, service, "cheerios", ZIP)

        assert report.products_seen > 0
        assert report.observations_appended > 0
        assert session.scalar(select(func.count(PriceObservation.id))) > 0

        retailers = {r.slug: r for r in session.scalars(select(Retailer)).all()}
        assert retailers["publix"].status is RetailerStatus.ACTIVE
        assert retailers["publix"].last_sync_at is not None
        # The distributor publishes no prices; that is degraded, not healthy.
        assert retailers["rey_chavez"].status is RetailerStatus.DEGRADED


class TestWafBlockEndToEnd:
    def test_block_degrades_the_retailer_without_losing_history(self, session):
        """The required end-to-end scenario."""
        # 1. A healthy fixture refresh establishes a price and its history.
        run_refresh(session, SearchService(source="fixture"), "cheerios", ZIP)

        variant = session.scalar(
            select(ProductVariant).where(ProductVariant.retailer_sku == "10291024")
        )
        assert variant is not None, "expected the Walmart Cheerios variant"
        original_price = session.scalar(
            select(Price).where(Price.variant_id == variant.id)
        )
        original_cents = original_price.price_cents
        original_observations = session.scalar(
            select(func.count(PriceObservation.id)).where(
                PriceObservation.variant_id == variant.id
            )
        )
        assert original_observations >= 1

        walmart = session.scalar(select(Retailer).where(Retailer.slug == "walmart"))
        assert walmart.status is RetailerStatus.ACTIVE

        # 2. Live mode, with Walmart now behind a WAF.
        connectors = build_connectors(source="live")
        for connector in connectors:
            connector.source = "live"
        block_live(next(c for c in connectors if c.slug == "walmart"))

        report = run_refresh(
            session, SearchService(connectors), "cheerios", ZIP
        )

        # 3. The worker did not crash and the other retailers still refreshed.
        assert report.products_seen > 0

        # 4. Walmart is recorded as degraded, with a reason a human can read.
        session.expire_all()
        walmart = session.scalar(select(Retailer).where(Retailer.slug == "walmart"))
        assert walmart.status is RetailerStatus.DEGRADED
        assert "403" in walmart.status_reason
        assert "fixture data" in walmart.status_reason
        assert walmart.consecutive_failures == 1

        # 5. The previous history is intact - append-only, nothing rewritten.
        surviving = session.scalars(
            select(PriceObservation)
            .where(PriceObservation.variant_id == variant.id)
            .order_by(PriceObservation.observed_at)
        ).all()
        assert len(surviving) > original_observations
        assert surviving[0].price_cents == original_cents

        # 6. Data served during a block is never dressed up as freshly observed.
        latest = surviving[-1]
        assert latest.provenance is PriceProvenance.ESTIMATED

    def test_blocked_products_are_tagged_as_fixture_fallback(self, session):
        connectors = build_connectors(source="live")
        for connector in connectors:
            connector.source = "live"
        walmart = block_live(next(c for c in connectors if c.slug == "walmart"))

        result = walmart.search("cheerios", ZIP)
        assert result.status is RetailerStatus.DEGRADED
        assert result.products, "fallback should still yield the cached sample"
        for product in result.products:
            assert "fixture_fallback" in product.notes
            if product.price:
                assert product.price.provenance is PriceProvenance.ESTIMATED

    def test_an_unsupported_live_endpoint_degrades_rather_than_guessing(self, session):
        """We have no permitted API for most retailers; that is stated, not faked."""
        connectors = build_connectors(source="live")
        for connector in connectors:
            connector.source = "live"

        outcome = SearchService(connectors).search("cheerios", ZIP)
        assert outcome.is_complete is False
        for report in outcome.reports:
            assert report.status is not RetailerStatus.ACTIVE
            assert "fixture data" in (report.reason or "")

    def test_repeated_blocks_trip_the_circuit_breaker(self, session):
        run_refresh(session, SearchService(source="fixture"), "cheerios", ZIP)

        for attempt in range(CIRCUIT_BREAKER_THRESHOLD):
            connectors = build_connectors(source="live")
            for connector in connectors:
                connector.source = "live"
            block_live(next(c for c in connectors if c.slug == "walmart"))
            report = run_refresh(session, SearchService(connectors), "cheerios", ZIP)

        session.expire_all()
        walmart = session.scalar(select(Retailer).where(Retailer.slug == "walmart"))
        assert walmart.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD
        assert walmart.status is RetailerStatus.UNAVAILABLE
        assert "Circuit breaker tripped" in walmart.status_reason
        assert "walmart" in report.tripped

    def test_a_recovered_retailer_is_restored(self, session):
        connectors = build_connectors(source="live")
        for connector in connectors:
            connector.source = "live"
        block_live(next(c for c in connectors if c.slug == "walmart"))
        run_refresh(session, SearchService(connectors), "cheerios", ZIP)

        session.expire_all()
        assert (
            session.scalar(select(Retailer).where(Retailer.slug == "walmart")).status
            is RetailerStatus.DEGRADED
        )

        # Back to a working source: the breaker must reset, not stay latched.
        report = run_refresh(session, SearchService(source="fixture"), "cheerios", ZIP)
        session.expire_all()
        walmart = session.scalar(select(Retailer).where(Retailer.slug == "walmart"))
        assert walmart.status is RetailerStatus.ACTIVE
        assert walmart.consecutive_failures == 0
        assert "walmart" in report.recovered

    def test_a_timeout_is_handled_like_a_block_for_availability(self, session):
        from app.connectors.http import TransientError

        connectors = build_connectors(source="live")
        for connector in connectors:
            connector.source = "live"
        block_live(
            next(c for c in connectors if c.slug == "publix"),
            TransientError("timeout after 15.0s"),
        )
        run_refresh(session, SearchService(connectors), "cheerios", ZIP)

        session.expire_all()
        publix = session.scalar(select(Retailer).where(Retailer.slug == "publix"))
        assert publix.status is RetailerStatus.DEGRADED
        assert "Could not reach" in publix.status_reason

    def test_current_price_is_not_wiped_by_a_blocked_refresh(self, session):
        run_refresh(session, SearchService(source="fixture"), "cheerios", ZIP)
        before = {
            (p.variant_id, p.store_id): p.price_cents
            for p in session.scalars(select(Price)).all()
        }
        assert before

        connectors = build_connectors(source="live")
        for connector in connectors:
            connector.source = "live"
        for connector in connectors:
            block_live(connector)
        run_refresh(session, SearchService(connectors), "cheerios", ZIP)

        session.expire_all()
        after = {
            (p.variant_id, p.store_id): p.price_cents
            for p in session.scalars(select(Price)).all()
        }
        # Every previously known price is still known.
        for key, cents in before.items():
            assert key in after, "a blocked refresh deleted a known price"
            assert after[key] == cents
