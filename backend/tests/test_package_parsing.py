"""Package-size parsing.

This is where grocery parsers fail: multi-packs, composites, and the "oz" that
sometimes means fluid ounces. Each case below is a shape that appears on real
shelf tags and retailer feeds.
"""
from __future__ import annotations

import pytest

from app.core.enums import UomKind
from app.services.normalization import parse_package_size


# Expected values are derived from primary unit definitions (1 oz = 28.349523125 g,
# 1 lb = 16 oz, 1 US fl oz = 29.5735295625 ml, 1 gal = 128 fl oz), computed
# independently of the parser so these tests can actually falsify it.


def _totals(text):
    """(pack_count, total base quantity, base uom) for terse assertions."""
    p = parse_package_size(text)
    total = p.total_base
    return p.pack_count, (round(total.value, 4) if total else None), (
        total.uom if total else None
    )


class TestSimpleSizes:
    @pytest.mark.parametrize(
        "text,pack,value,uom",
        [
            ("12 oz", 1, 340.1943, "g"),
            ("12oz", 1, 340.1943, "g"),
            ("3 lb", 1, 1360.7771, "g"),
            ("750 ml", 1, 750.0, "ml"),
            ("1.5 L", 1, 1500.0, "ml"),
            ("1 gal", 1, 3785.4118, "ml"),
            ("16.9 fl oz", 1, 499.7926, "ml"),
            ("500g", 1, 500.0, "g"),
        ],
    )
    def test_single_package(self, text, pack, value, uom):
        assert _totals(text) == (pack, value, uom)

    def test_size_text_is_preserved_verbatim(self):
        assert parse_package_size("12 OZ  ").raw_text == "12 OZ"


class TestMultiPack:
    """'24 x 12 oz cans' must yield 24 units of 12 oz, not 24 oz and not 12 oz."""

    @pytest.mark.parametrize(
        "text,pack,value,uom",
        [
            ("24 x 12 oz cans", 24, 8164.6627, "g"),
            ("24x12oz", 24, 8164.6627, "g"),
            ("24 × 12 fl oz", 24, 8517.1765, "ml"),
            ("12 x 12 fl oz cans", 12, 4258.5883, "ml"),
            ("6 x 1 L bottles", 6, 6000.0, "ml"),
            ("12/12 oz", 12, 4082.3313, "g"),
            ("pack of 6, 12 fl oz", 6, 2129.2941, "ml"),
            ("16.9 fl oz, 24 pack", 24, 11995.0236, "ml"),
            ("24 pack 16.9 fl oz", 24, 11995.0236, "ml"),
            ("12-pack, 12 fl oz cans", 12, 4258.5883, "ml"),
        ],
    )
    def test_multi_pack_multiplies(self, text, pack, value, uom):
        assert _totals(text) == (pack, value, uom)

    def test_unit_quantity_is_kept_separate_from_total(self):
        p = parse_package_size("24 x 12 fl oz")
        assert p.pack_count == 24
        assert p.unit_quantity.value == 12
        assert p.unit_quantity.uom == "fl_oz"
        # Sticker-size vs per-unit size must both survive parsing.
        assert p.total_base.value == pytest.approx(24 * 29.5735295625 * 12)


class TestCountOnlyPackages:
    @pytest.mark.parametrize(
        "text,pack",
        [
            ("2-pack", 2),
            ("2 pack", 2),
            ("40 ct", 40),
            ("40 count", 40),
            ("40ct", 40),
            ("dozen", 12),
            ("pack of 8", 8),
            ("6 pk", 6),
        ],
    )
    def test_count_without_content(self, text, pack):
        p = parse_package_size(text)
        assert p.pack_count == pack
        assert p.total_base.kind is UomKind.COUNT
        assert p.total_base.value == pack


class TestCompositeSizes:
    """'1 lb 4 oz' is one package of 20 oz, not a 1-pack of 4 oz."""

    @pytest.mark.parametrize(
        "text,pack,value,uom",
        [
            ("1 lb 4 oz", 1, 566.9905, "g"),
            ("1lb 4oz", 1, 566.9905, "g"),
            ("2 lb 3.5 oz", 1, 1006.4081, "g"),
            ("1 gal 1 qt", 1, 4731.7647, "ml"),
        ],
    )
    def test_composite_sums(self, text, pack, value, uom):
        assert _totals(text) == (pack, value, uom)

    def test_composite_inside_a_multipack(self):
        pack, value, uom = _totals("2 x 1 lb 4 oz")
        assert pack == 2
        assert value == pytest.approx(1133.9810, abs=1e-3)
        assert uom == "g"

    def test_mixed_kinds_do_not_sum(self):
        """'12 oz (340 g)' is a restatement, not 12 oz plus 340 g."""
        p = parse_package_size("12 oz")
        assert p.total_base.value == pytest.approx(340.1943)


class TestFractions:
    @pytest.mark.parametrize(
        "text,value,uom",
        [
            ("1/2 gal", 1892.7059, "ml"),
            ("1 1/2 lb", 680.3886, "g"),
            ("half gallon", 1892.7059, "ml"),
            ("1/2 oz", 14.1748, "g"),
        ],
    )
    def test_fractions_parse(self, text, value, uom):
        _, v, u = _totals(text)
        assert (v, u) == (pytest.approx(value, abs=1e-3), uom)

    def test_fraction_is_not_confused_with_a_multipack(self):
        """'1/2 gal' is a half gallon; '12/12 oz' is twelve 12oz units."""
        assert parse_package_size("1/2 gal").pack_count == 1
        assert parse_package_size("12/12 oz").pack_count == 12


class TestAmbiguityAndFailure:
    def test_bare_oz_is_mass_but_flagged_ambiguous(self):
        p = parse_package_size("12 oz")
        assert p.unit_quantity.kind is UomKind.MASS
        assert "ambiguous_oz" in p.notes

    def test_fl_oz_is_never_ambiguous(self):
        assert "ambiguous_oz" not in parse_package_size("12 fl oz").notes

    def test_beverage_hint_reinterprets_bare_oz_as_volume(self):
        p = parse_package_size("12 oz can", category="beverage")
        assert p.unit_quantity.kind is UomKind.VOLUME
        assert p.unit_quantity.uom == "fl_oz"

    @pytest.mark.parametrize("text", ["", "   ", "family size", "assorted", None])
    def test_unparseable_returns_empty_not_a_guess(self, text):
        """An unparseable size must not silently become '1 each'."""
        p = parse_package_size(text)
        assert p.total_base is None
        assert p.parse_confidence == 0.0
        assert p.pack_count == 1

    def test_noise_words_are_ignored(self):
        assert _totals("Family Size 18 oz box") == (1, 510.2914, "g")


class TestHyphenatedSizes:
    """Ad copy writes "12-oz box" constantly. Missing it is bad; misreading a
    neighbouring "12-pk" as the contents is worse."""

    @pytest.mark.parametrize(
        "text,pack,value,uom",
        [
            ("Cheerios Cereal, 12-oz box", 1, 340.1943, "g"),
            ("Skippy Peanut Butter, 16.3-oz jar", 1, 462.0972, "g"),
            ("Publix Whole Milk, 1-gal jug", 1, 3785.4118, "ml"),
        ],
    )
    def test_hyphen_between_number_and_unit(self, text, pack, value, uom):
        assert _totals(text) == (pack, pytest.approx(value, abs=1e-3), uom)

    def test_pack_and_hyphenated_size_together(self):
        """'12-pk 12-oz cans' is twelve 12 oz units, not twelve items."""
        p = parse_package_size("Coca-Cola Classic, 12-pk 12-oz cans")
        assert p.pack_count == 12
        assert p.unit_quantity.value == 12
        assert p.total_base.kind is not UomKind.COUNT

    def test_beverage_hint_applies_to_hyphenated_sizes(self):
        p = parse_package_size("Coca-Cola, 12-pk 12-oz cans", category="beverage")
        assert p.total_base.uom == "ml"
        assert p.total_base.value == pytest.approx(12 * 12 * 29.5735295625)


class TestSpanishUnits:
    """Fresco y Mas and Presidente list in Spanish as a matter of course."""

    @pytest.mark.parametrize(
        "text,value,uom",
        [
            ("Refresco Coca-Cola 2 LT", 2000.0, "ml"),
            ("Coca-Cola 2 Litros", 2000.0, "ml"),
            ("Leche Entera 1 galon", 3785.4118, "ml"),
            ("Leche Entera 1 galón", 3785.4118, "ml"),
            ("Arroz 2 libras", 907.1847, "g"),
            ("Queso 500 gramos", 500.0, "g"),
            ("medio galon", 1892.7059, "ml"),
        ],
    )
    def test_spanish_units_parse(self, text, value, uom):
        _, v, u = _totals(text)
        assert (v, u) == (pytest.approx(value, abs=1e-3), uom)

    def test_spanish_counts(self):
        assert parse_package_size("6 unidades").pack_count == 6
        assert parse_package_size("paquete de 8").pack_count == 8
        assert parse_package_size("docena").pack_count == 12
