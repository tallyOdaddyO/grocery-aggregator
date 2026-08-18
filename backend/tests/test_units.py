"""Unit conversion: the arithmetic every price comparison rests on."""
from __future__ import annotations

import pytest

from app.core.enums import UomKind
from app.services.units import (
    IncompatibleUnitsError, Quantity, UnknownUnitError, convert, parse_unit,
    to_base,
)


class TestUnitResolution:
    @pytest.mark.parametrize(
        "text,symbol,kind",
        [
            ("oz", "oz", UomKind.MASS),
            ("ounce", "oz", UomKind.MASS),
            ("ounces", "oz", UomKind.MASS),
            ("lb", "lb", UomKind.MASS),
            ("lbs", "lb", UomKind.MASS),
            ("pound", "lb", UomKind.MASS),
            ("g", "g", UomKind.MASS),
            ("gram", "g", UomKind.MASS),
            ("kg", "kg", UomKind.MASS),
            ("mg", "mg", UomKind.MASS),
            ("fl oz", "fl_oz", UomKind.VOLUME),
            ("fluid ounce", "fl_oz", UomKind.VOLUME),
            ("floz", "fl_oz", UomKind.VOLUME),
            ("ml", "ml", UomKind.VOLUME),
            ("l", "l", UomKind.VOLUME),
            ("liter", "l", UomKind.VOLUME),
            ("litre", "l", UomKind.VOLUME),
            ("gal", "gal", UomKind.VOLUME),
            ("gallon", "gal", UomKind.VOLUME),
            ("qt", "qt", UomKind.VOLUME),
            ("pt", "pt", UomKind.VOLUME),
            ("ct", "ct", UomKind.COUNT),
            ("count", "ct", UomKind.COUNT),
            ("each", "ct", UomKind.COUNT),
            ("dozen", "dozen", UomKind.COUNT),
        ],
    )
    def test_aliases_resolve(self, text, symbol, kind):
        unit = parse_unit(text)
        assert unit.symbol == symbol
        assert unit.kind == kind

    def test_fl_oz_is_volume_not_mass(self):
        """The single most dangerous unit confusion in a grocery dataset."""
        assert parse_unit("fl oz").kind is UomKind.VOLUME
        assert parse_unit("oz").kind is UomKind.MASS

    def test_unknown_unit_raises(self):
        with pytest.raises(UnknownUnitError):
            parse_unit("smoots")


class TestConversion:
    @pytest.mark.parametrize(
        "value,frm,to,expected",
        [
            (1, "lb", "oz", 16.0),
            (16, "oz", "lb", 1.0),
            (1, "lb", "g", 453.59237),
            (1, "kg", "g", 1000.0),
            (1, "gal", "fl_oz", 128.0),
            (1, "l", "ml", 1000.0),
            (2, "qt", "pt", 4.0),
            (1, "dozen", "ct", 12.0),
        ],
    )
    def test_known_conversions(self, value, frm, to, expected):
        assert convert(value, frm, to) == pytest.approx(expected, rel=1e-9)

    def test_mass_to_volume_is_refused(self):
        """Grams and millilitres are not interchangeable without a density."""
        with pytest.raises(IncompatibleUnitsError):
            convert(1, "oz", "fl_oz")

    def test_count_to_mass_is_refused(self):
        with pytest.raises(IncompatibleUnitsError):
            convert(6, "ct", "g")

    def test_round_trip_is_lossless(self):
        assert convert(convert(12.5, "oz", "g"), "g", "oz") == pytest.approx(12.5)


class TestBaseProjection:
    def test_mass_projects_to_grams(self):
        q = to_base(Quantity(1, "lb", UomKind.MASS))
        assert q.uom == "g"
        assert q.value == pytest.approx(453.59237)

    def test_volume_projects_to_millilitres(self):
        q = to_base(Quantity(1, "fl_oz", UomKind.VOLUME))
        assert q.uom == "ml"
        assert q.value == pytest.approx(29.5735295625)

    def test_count_projects_to_each(self):
        q = to_base(Quantity(1, "dozen", UomKind.COUNT))
        assert q.uom == "ct"
        assert q.value == pytest.approx(12.0)
