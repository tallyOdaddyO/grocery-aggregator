"""The matching engine, with emphasis on what must NOT match.

A false positive here is worse than a miss: it tells a shopper that a 12 oz jar
and a 24 oz jar are the same product and that one store is half the price.
"""
from __future__ import annotations

import json

import pytest

from app.core.enums import MatchStage
from app.services.matching import (
    MatchCandidate, MatchConfig, config_for_category, match,
)


def candidate(name, **kw) -> MatchCandidate:
    return MatchCandidate.from_text(name=name, **kw)


class TestStage1Upc:
    def test_identical_upc_is_a_certain_match(self):
        a = candidate("Cheerios Original 12 oz", upc="016000275270")
        b = candidate("General Mills Cheerios, 12oz box", upc="016000275270")
        result = match(a, b)
        assert result.stage is MatchStage.UPC
        assert result.confidence == 1.0
        assert not result.vetoed
        assert any(s.name == "upc" for s in result.signals)

    def test_upc_is_compared_after_gtin14_padding(self):
        a = candidate("Milk", upc="016000275270")
        b = candidate("Milk", upc="00016000275270")
        assert match(a, b).stage is MatchStage.UPC

    def test_invalid_check_digit_is_not_trusted_as_an_identity(self):
        """A malformed UPC must fall through to attribute matching, not assert
        identity on a number we know is corrupt."""
        a = candidate("Cheerios 12 oz", upc="016000275271")  # bad check digit
        b = candidate("Cheerios 12 oz", upc="016000275271")
        assert match(a, b).stage is not MatchStage.UPC

    def test_different_upcs_do_not_short_circuit(self):
        a = candidate("Cheerios Original 12 oz", upc="016000275270")
        b = candidate("Cheerios Original 12 oz", upc="016000275287")
        assert match(a, b).stage is not MatchStage.UPC


class TestSizeVeto:
    def test_same_name_different_size_is_vetoed(self):
        a = candidate("Skippy Peanut Butter 12 oz", brand="Skippy")
        b = candidate("Skippy Peanut Butter 24 oz", brand="Skippy")
        result = match(a, b)
        assert result.vetoed
        assert result.confidence == 0.0
        assert "size" in result.veto_reason.lower()

    def test_equivalent_sizes_in_different_units_do_match(self):
        a = candidate("Store Brand Rice 16 oz", brand="Acme")
        b = candidate("Store Brand Rice 1 lb", brand="Acme")
        result = match(a, b)
        assert not result.vetoed
        assert any(s.name == "size" for s in result.signals)

    def test_mass_versus_volume_is_vetoed(self):
        a = candidate("Honey 12 oz", brand="Acme")
        b = candidate("Honey 12 fl oz", brand="Acme")
        result = match(a, b)
        assert result.vetoed
        assert "dimension" in result.veto_reason.lower()

    def test_multipack_totals_are_compared_not_unit_sizes(self):
        a = candidate("Cola 24 x 12 fl oz", brand="Cola Co")
        b = candidate("Cola 12 x 12 fl oz", brand="Cola Co")
        assert match(a, b).vetoed

    def test_multipack_matches_equivalent_multipack(self):
        a = candidate("Cola 24 x 12 fl oz", brand="Cola Co")
        b = candidate("Cola 24 pack, 12 fl oz cans", brand="Cola Co")
        assert not match(a, b).vetoed

    def test_tolerance_absorbs_rounded_label_sizes(self):
        a = candidate("Yogurt 5.3 oz", brand="Acme")
        b = candidate("Yogurt 150 g", brand="Acme")  # 5.3 oz = 150.25 g
        assert not match(a, b).vetoed


class TestAttributeVetoes:
    def test_organic_versus_conventional_is_vetoed(self):
        a = candidate("Organic Whole Milk 1 gal", brand="Acme")
        b = candidate("Whole Milk 1 gal", brand="Acme")
        result = match(a, b)
        assert result.vetoed
        assert "organic" in result.veto_reason.lower()

    def test_different_brands_are_vetoed(self):
        a = candidate("Cheerios 12 oz", brand="General Mills")
        b = candidate("Toasted Oats 12 oz", brand="Store Brand")
        assert match(a, b).vetoed

    def test_decaf_versus_regular_is_vetoed(self):
        a = candidate("Coffee Decaf 12 oz", brand="Acme")
        b = candidate("Coffee 12 oz", brand="Acme")
        assert match(a, b).vetoed


class TestVetoPrecedence:
    def test_a_veto_cannot_be_outvoted_by_name_similarity(self):
        """The core guarantee: identical names, different sizes, still no match."""
        a = candidate("Skippy Creamy Peanut Butter 12 oz", brand="Skippy")
        b = candidate("Skippy Creamy Peanut Butter 24 oz", brand="Skippy")
        result = match(a, b)
        assert result.vetoed
        assert result.stage is MatchStage.ATTRIBUTES
        assert result.confidence == 0.0

    def test_veto_survives_a_matching_upc(self):
        """If a feed gives the same UPC for clearly different sizes, trust the
        physical evidence and refuse the match rather than the label."""
        a = candidate("Soup 10 oz", brand="Acme", upc="016000275270")
        b = candidate("Soup 26 oz", brand="Acme", upc="016000275270")
        result = match(a, b)
        assert result.vetoed
        assert "upc_size_conflict" in [s.name for s in result.signals]


class TestStage3Fuzzy:
    def test_reordered_tokens_still_match(self):
        a = candidate("Peanut Butter Creamy 16 oz", brand="Acme")
        b = candidate("Creamy Peanut Butter 1 lb", brand="Acme")
        result = match(a, b)
        assert not result.vetoed
        assert result.confidence > 0.8

    def test_unrelated_products_do_not_match(self):
        a = candidate("Dish Soap 16 oz", brand="Acme")
        b = candidate("Apple Juice 16 oz", brand="Acme")
        result = match(a, b)
        assert result.confidence < 0.6
        assert not result.is_match

    def test_thresholds_are_configurable_per_category(self):
        strict = config_for_category("baby_formula")
        loose = config_for_category("produce")
        assert strict.fuzzy_threshold > loose.fuzzy_threshold
        assert strict.size_tolerance <= loose.size_tolerance

    def test_a_strict_category_rejects_what_a_loose_one_accepts(self):
        a = candidate("Bananas Organic 2 lb", brand="Acme", category="produce")
        b = candidate("Organic Banana 2 lb", brand="Acme", category="produce")
        assert match(a, b, config_for_category("produce")).is_match


class TestExplainability:
    """Signals must be structured data the API can return verbatim."""

    def test_result_serializes_to_plain_json(self):
        a = candidate("Cheerios 12 oz", upc="016000275270")
        b = candidate("Cheerios 12 oz", upc="016000275270")
        payload = match(a, b).as_dict()
        json.dumps(payload)  # must not raise
        assert set(payload) >= {
            "confidence", "stage", "signals", "vetoed", "veto_reason", "summary"
        }
        assert isinstance(payload["signals"], list)
        for signal in payload["signals"]:
            assert set(signal) == {"name", "detail", "weight"}
            assert isinstance(signal["weight"], float)

    def test_summary_reads_like_the_spec_example(self):
        a = candidate("Cheerios Original 12 oz", upc="016000275270")
        b = candidate("Cheerios Original 12 oz", upc="016000275270")
        summary = match(a, b).summary
        assert summary.startswith("Confidence 100%")
        assert "UPC" in summary

    def test_veto_explains_itself_in_plain_language(self):
        a = candidate("Skippy Peanut Butter 12 oz", brand="Skippy")
        b = candidate("Skippy Peanut Butter 24 oz", brand="Skippy")
        payload = match(a, b).as_dict()
        assert payload["vetoed"] is True
        assert payload["veto_reason"]
        assert payload["summary"].startswith("Not equivalent")
        # The rejected comparison is still reported, so a user can see the reason.
        assert any(s["name"] == "size" for s in payload["signals"])

    def test_every_signal_weight_is_finite_and_bounded(self):
        a = candidate("Peanut Butter Creamy 16 oz", brand="Acme")
        b = candidate("Creamy Peanut Butter 1 lb", brand="Acme")
        for s in match(a, b).signals:
            assert 0.0 <= s.weight <= 1.0

    def test_unparseable_size_is_reported_not_hidden(self):
        a = candidate("Mystery Item", brand="Acme")
        b = candidate("Mystery Item", brand="Acme")
        result = match(a, b)
        assert any("size_unknown" == s.name for s in result.signals)
        assert result.confidence < 1.0
