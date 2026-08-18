"""Unit price: the number that makes bulk and small packages comparable."""
from __future__ import annotations

import pytest

from app.core.enums import UomKind
from app.services.normalization import parse_package_size
from app.services.pricing import compute_unit_price


def unit_price(price_cents, size_text, category=None):
    return compute_unit_price(price_cents, parse_package_size(size_text, category))


class TestUnitPrice:
    def test_dollars_per_pound(self):
        # $4.99 for 12 oz -> $6.65/lb
        up = unit_price(499, "12 oz")
        assert up.kind is UomKind.MASS
        assert up.uom == "lb"
        assert up.cents_per_unit == pytest.approx(499 / 12 * 16, rel=1e-9)
        assert up.display == "$6.65/lb"

    def test_dollars_per_fluid_ounce(self):
        up = unit_price(599, "2 L")
        assert up.uom == "fl_oz"
        assert up.display.endswith("/fl oz")

    def test_dollars_per_count(self):
        up = unit_price(649, "12 ct")
        assert up.kind is UomKind.COUNT
        assert up.cents_per_unit == pytest.approx(649 / 12)
        # Display rounds to cents for readability; ranking uses the exact
        # cents_per_base_unit below, never the rendered string.
        assert up.display == "$0.54/count"
        assert up.cents_per_base_unit == pytest.approx(649 / 12)

    def test_multipack_unit_price_uses_total_contents(self):
        """A 24-pack must not be priced as if it were one can."""
        single = unit_price(199, "12 fl oz")
        case = unit_price(1799, "24 x 12 fl oz")
        assert case.cents_per_base_unit < single.cents_per_base_unit
        assert case.cents_per_unit == pytest.approx(1799 / (24 * 12), rel=1e-9)


class TestStickerVersusUnitPrice:
    def test_bulk_can_win_on_unit_price_while_costing_more_at_the_register(self):
        costco_sticker, publix_sticker = 1999, 649
        costco = unit_price(costco_sticker, "40 ct")
        publix = unit_price(publix_sticker, "12 ct")

        # Costco is the better value per unit...
        assert costco.cents_per_base_unit < publix.cents_per_base_unit
        # ...and simultaneously three times the cost at the till. Both facts must
        # remain visible; the unit price alone would mislead.
        assert costco_sticker > publix_sticker * 3

    def test_unit_price_is_kept_apart_from_sticker_price(self):
        up = unit_price(1999, "40 ct")
        assert up.as_dict()["cents_per_unit"] == pytest.approx(49.975)
        assert "price_cents" not in up.as_dict()


class TestRefusals:
    def test_unknown_size_yields_no_unit_price(self):
        """Better no unit price than a fabricated one."""
        assert unit_price(499, "family size") is None
        assert unit_price(499, "") is None

    def test_zero_quantity_is_refused(self):
        assert unit_price(499, "0 oz") is None
