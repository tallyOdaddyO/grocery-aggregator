"""Name, brand, attribute, and UPC normalization."""
from __future__ import annotations

import pytest

from app.services.normalization import (
    extract_attributes, gtin_check_digit, normalize_brand, normalize_name,
    normalize_upc,
)


class TestUpc:
    def test_valid_upc_a_is_padded_to_gtin14(self):
        assert normalize_upc("016000275270") == "00016000275270"

    def test_formatting_is_stripped(self):
        assert normalize_upc("0-16000-27527-0") == "00016000275270"

    def test_already_gtin14_is_stable(self):
        assert normalize_upc("00016000275270") == "00016000275270"

    @pytest.mark.parametrize(
        "bad", ["016000275271", "12345", "", None, "abcdefghijkl", "0000000000000000"]
    )
    def test_invalid_values_are_rejected(self, bad):
        """A corrupt barcode must never be usable as an identity claim."""
        assert normalize_upc(bad) is None

    def test_check_digit_algorithm(self):
        assert gtin_check_digit("01600027527") == 0
        assert gtin_check_digit("03600029145") == 2


class TestBrand:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("General Mills, Inc.", "general mills"),
            ("GENERAL MILLS", "general mills"),
            ("Ben & Jerry's", "ben jerrys"),
            ("Kellogg Company", "kellogg"),
            ("  Publix  ", "publix"),
        ],
    )
    def test_brands_fold_to_a_common_key(self, raw, expected):
        assert normalize_brand(raw) == expected

    def test_empty_brand_is_none(self):
        assert normalize_brand(None) is None
        assert normalize_brand("   ") is None


class TestNameNormalization:
    def test_size_descriptors_are_removed(self):
        """Size is compared arithmetically, so it must not also be compared as text."""
        assert "oz" not in normalize_name("Cheerios Original 12 oz box")
        assert "12" not in normalize_name("Cheerios Original 12 oz box")

    def test_multipack_leaves_no_stray_tokens(self):
        assert normalize_name("Cola 24 x 12 fl oz cans") == "cola"

    def test_stopwords_and_packaging_nouns_are_dropped(self):
        assert normalize_name("The Original Peanut Butter in a Jar") == "peanut butter"

    def test_word_order_is_preserved_for_similarity(self):
        assert normalize_name("Creamy Peanut Butter") == "creamy peanut butter"

    def test_empty_input(self):
        assert normalize_name(None) == ""
        assert normalize_name("") == ""


class TestAttributeExtraction:
    def test_organic_is_detected(self):
        assert extract_attributes("Organic Whole Milk")["organic"] is True

    def test_absent_flags_are_omitted_not_false(self):
        assert "organic" not in extract_attributes("Whole Milk")

    def test_whole_grain_is_not_a_fat_claim(self):
        """'Whole grain bread' must not be read as full-fat."""
        assert "whole_fat" not in extract_attributes("Whole Grain Bread")

    def test_whole_milk_is_a_fat_claim(self):
        assert extract_attributes("Whole Milk")["whole_fat"] is True

    def test_low_fat_variants(self):
        for text in ["2% Milk", "Reduced Fat Milk", "Skim Milk", "Nonfat Yogurt"]:
            assert extract_attributes(text).get("low_fat") is True

    def test_decaf_and_sugar_free(self):
        assert extract_attributes("Decaf Coffee")["decaf"] is True
        assert extract_attributes("Sugar Free Syrup")["sugar_free"] is True
