"""Connector contract and fixture resilience.

The fixtures are deliberately broken in the ways real feeds are broken. These
tests assert the damage is contained inside the adapter and that what escapes the
adapter boundary is always a validated NormalizedProduct.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.connectors.base import (
    BaseRetailerConnector, ConnectorContractError, NormalizedPrice,
    NormalizedProduct, StoreRef,
)
from app.connectors.fixtures import clean_title, parse_price_text
from app.connectors.registry import CONNECTOR_CLASSES, build_connectors, connector_by_slug
from app.core.enums import PriceProvenance, PromotionType, RetailerStatus

ZIP = "33009"


@pytest.fixture
def connectors():
    return build_connectors()


class TestRegistry:
    def test_all_eight_retailers_are_registered(self):
        assert {c.slug for c in CONNECTOR_CLASSES} == {
            "walmart", "costco", "bjs", "publix",
            "winn_dixie", "fresco_y_mas", "presidente", "rey_chavez",
        }

    def test_every_connector_declares_its_identity(self, connectors):
        for c in connectors:
            assert c.slug and c.name
            assert isinstance(c.default_provenance, PriceProvenance)


class TestContractEnforcement:
    def test_adapters_may_only_return_normalized_products(self):
        """An adapter that leaks a raw dict must fail loudly, not silently."""

        class LeakyConnector(BaseRetailerConnector):
            slug, name = "leaky", "Leaky"

            def resolve_store(self, zip_code):
                return StoreRef(retailer_slug="leaky", store_number="1", zip=zip_code)

            def fetch_raw(self, term, store):
                return [{"anything": 1}]

            def parse_item(self, raw, store):
                return {"not": "a model"}  # the mistake this guard exists for

        with pytest.raises(ConnectorContractError):
            LeakyConnector().search("milk", ZIP)

    def test_every_product_from_every_connector_is_validated(self, connectors):
        for connector in connectors:
            for product in connector.search("milk", ZIP).products:
                assert isinstance(product, NormalizedProduct)
                assert product.retailer_slug == connector.slug
                assert product.display_name.strip() == product.display_name
                if product.price is not None:
                    assert isinstance(product.price, NormalizedPrice)
                    assert product.price.price_cents > 0

    def test_price_model_rejects_nonpositive_amounts(self):
        from datetime import datetime, timezone

        with pytest.raises(ValidationError):
            NormalizedPrice(
                price_cents=0, provenance=PriceProvenance.VERIFIED_ONLINE,
                observed_at=datetime.now(timezone.utc),
            )

    def test_price_model_rejects_regular_below_sale(self):
        from datetime import datetime, timezone

        with pytest.raises(ValidationError):
            NormalizedPrice(
                price_cents=500, regular_price_cents=400,
                provenance=PriceProvenance.VERIFIED_ONLINE,
                observed_at=datetime.now(timezone.utc),
            )

    def test_store_must_be_resolved_before_any_price_is_returned(self, connectors):
        """Rule: no local store, no prices - never a national fallback."""
        for connector in connectors:
            result = connector.search("milk", "99999")
            assert result.status is RetailerStatus.UNAVAILABLE
            assert result.products == []
            assert "99999" in (result.reason or "")


class TestMessyFixtures:
    def test_shouting_and_doubled_whitespace_are_cleaned(self):
        products = connector_by_slug("walmart").search("cheerios", ZIP).products
        cheerios = next(p for p in products if "CHEERIOS" in p.display_name)
        assert "  " not in cheerios.display_name
        assert cheerios.normalized_name == "cheerios cereal"

    def test_size_hidden_in_the_title_is_still_parsed(self):
        """Walmart's cola lists no size field; the size is only in the name."""
        products = connector_by_slug("walmart").search("coca-cola", ZIP).products
        cola = next(p for p in products if "Coca-Cola" in p.display_name)
        assert cola.pack_count == 24
        assert cola.net_content_uom == "fl_oz"
        assert cola.base_quantity == pytest.approx(24 * 12 * 29.5735295625)

    def test_missing_upc_is_recorded_not_invented(self):
        products = connector_by_slug("walmart").search("guacamole", ZIP).products
        item = products[0]
        assert item.upc is None
        assert "upc_missing" in item.notes

    def test_empty_string_upc_is_treated_as_missing(self):
        products = connector_by_slug("walmart").search("coca-cola", ZIP).products
        assert products[0].upc is None
        assert "upc_missing" in products[0].notes

    def test_invalid_check_digit_is_rejected_and_flagged(self):
        """Publix ships a GreenWise milk whose UPC fails its check digit."""
        products = connector_by_slug("publix").search("greenwise", ZIP).products
        item = products[0]
        assert item.upc is None
        assert "upc_invalid" in item.notes

    def test_punctuated_upc_is_normalized(self):
        products = connector_by_slug("publix").search("skippy", ZIP).products
        assert products[0].upc == "00037600106917"

    def test_html_entities_are_decoded(self):
        products = connector_by_slug("publix").search("cola", ZIP).products
        assert "Coca-Cola" in products[0].display_name
        assert "&#45;" not in products[0].display_name

    def test_null_price_yields_a_product_with_no_price(self):
        """A stocked item with no price is reported, not dropped."""
        products = connector_by_slug("walmart").search("skippy", ZIP).products
        item = products[0]
        assert item.price is None
        assert "price_unavailable" in item.notes
        # It still carries a usable size, so it can be matched and shown.
        assert item.base_quantity is not None

    def test_prose_prices_are_refused(self):
        """'Call for price' and 'not available' must not become a number."""
        costco = connector_by_slug("costco").search("member only", ZIP).products
        assert costco[0].price is None
        wd = connector_by_slug("winn_dixie").search("rotisserie", ZIP).products
        assert wd[0].price is None

    def test_zero_price_is_refused(self):
        products = connector_by_slug("bjs").search("seasonal", ZIP).products
        assert products[0].price is None

    def test_unparseable_size_is_flagged_not_defaulted(self):
        products = connector_by_slug("walmart").search("guacamole", ZIP).products
        item = products[0]
        assert item.base_quantity is None
        assert "size_unparsed" in item.notes
        # No size means no unit price - never a fabricated per-item figure.
        assert item.price is not None and item.price.unit_price_cents is None

    def test_records_with_no_recognisable_fields_are_skipped(self):
        result = connector_by_slug("walmart").search("milk", ZIP)
        assert all(p.display_name for p in result.products)

    def test_missing_promotion_end_date_is_recorded(self):
        """BJ's instant savings and Publix BOGO both omit an end date."""
        bjs = connector_by_slug("bjs").search("cheerios", ZIP).products[0]
        assert bjs.price.promotion_type is PromotionType.MEMBER_PRICE
        assert bjs.price.promotion_ends_at is None
        assert "promotion_end_date_missing" in bjs.notes

    def test_multi_buy_price_is_per_unit_and_says_so(self):
        """'2 for $7.00' is $3.50 each, but only if you buy two."""
        item = connector_by_slug("publix").search("cheerios", ZIP).products[0]
        assert item.price.price_cents == 350
        assert "multi_buy_required" in item.notes
        assert "buying 2" in item.price.promotion_text

    def test_composite_and_multipack_sizes_survive_the_adapter(self):
        bjs = connector_by_slug("bjs").search("wellsley", ZIP).products[0]
        assert bjs.pack_count == 2
        assert bjs.base_quantity == pytest.approx(2 * 3785.411784)


class TestProvenanceGrading:
    def test_costco_online_prices_are_graded_as_delivery_not_verified(self):
        """costco.com pricing differs from the warehouse shelf; say so."""
        item = connector_by_slug("costco").search("kirkland", ZIP).products[0]
        assert item.price.provenance is PriceProvenance.DELIVERY_PRICE
        assert not item.price.is_verified_in_store

    def test_circular_derived_prices_are_estimated(self):
        item = connector_by_slug("presidente").search("leche", ZIP).products[0]
        assert item.price.provenance is PriceProvenance.ESTIMATED
        assert "price_from_circular" in item.notes

    def test_no_connector_claims_verified_in_store_from_a_fixture(self, connectors):
        """Nothing here has been checked against a shelf, so nothing may claim it."""
        for connector in connectors:
            for product in connector.search("milk", ZIP).products:
                if product.price:
                    assert product.price.provenance is not PriceProvenance.VERIFIED_IN_STORE

    def test_membership_requirement_is_carried_through(self):
        for slug in ("costco", "bjs"):
            item = connector_by_slug(slug).search("cheerios", ZIP).products[0]
            assert "membership_required" in item.notes


class TestHonestUnavailability:
    def test_distributor_without_consumer_prices_is_degraded(self):
        result = connector_by_slug("rey_chavez").search("milk", ZIP)
        assert result.status is RetailerStatus.DEGRADED
        assert "no consumer prices" in result.reason
        # The items are still listed - the store does stock them.
        assert result.products and all(p.price is None for p in result.products)

    def test_a_term_nobody_stocks_degrades_rather_than_lying(self, connectors):
        for connector in connectors:
            result = connector.search("zzzz-nonexistent-item", ZIP)
            assert result.status is RetailerStatus.DEGRADED
            assert result.products == []


class TestSharedPlatform:
    def test_winn_dixie_and_fresco_share_a_parser_but_not_a_store(self):
        wd = connector_by_slug("winn_dixie").search("milk", ZIP)
        fm = connector_by_slug("fresco_y_mas").search("milk", ZIP)
        assert wd.store.store_number != fm.store.store_number
        assert wd.products[0].retailer_slug == "winn_dixie"
        assert fm.products[0].retailer_slug == "fresco_y_mas"

    def test_addresses_from_fixtures_remain_unverified(self, connectors):
        for connector in connectors:
            store = connector.resolve_store(ZIP)
            if store is not None:
                assert store.address_verified is False


class TestHelpers:
    @pytest.mark.parametrize(
        "text,cents,qty",
        [
            ("$4.99", 499, None),
            ("4.99", 499, None),
            (3.42, 342, None),
            ("2 for $7.00", 350, 2),
            ("2 x $4.00", 200, 2),
            ("2/$5", 250, 2),
            ("Call for price", None, None),
            ("not available", None, None),
            ("Precio especial", None, None),
            ("", None, None),
            (None, None, None),
            (0, None, None),
        ],
    )
    def test_price_text_parsing(self, text, cents, qty):
        assert parse_price_text(text) == (cents, qty)

    def test_clean_title(self):
        assert clean_title("  A   B® ") == "A B"
        assert clean_title("Coca&#45;Cola") == "Coca-Cola"
        assert clean_title(None) == ""
