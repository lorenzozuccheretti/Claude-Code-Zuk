"""The spec card is the one place numbers live, so it is the first thing to test."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kdp_factory.errors import SpecViolation
from kdp_factory.spec.kdp import (
    KDP_SPEC,
    cover_geometry,
    even_up,
    gutter_margin_in,
    min_list_price_usd,
    printing_cost_usd,
    royalty_usd,
    spine_width_in,
    validate_page_count,
)


class TestSpineMath:
    def test_spine_is_pages_times_thickness(self):
        assert spine_width_in(120, "bw_cream") == pytest.approx(120 * 0.0025)
        assert spine_width_in(120, "bw_white") == pytest.approx(120 * 0.002252)

    def test_cream_is_thicker_than_white(self):
        assert spine_width_in(200, "bw_cream") > spine_width_in(200, "bw_white")

    def test_spine_needs_a_printable_page_count(self):
        with pytest.raises(SpecViolation):
            spine_width_in(23, "bw_white")


class TestPageCount:
    def test_below_minimum_is_rejected(self):
        with pytest.raises(SpecViolation, match="below the KDP minimum"):
            validate_page_count(20)

    def test_odd_page_count_is_rejected(self):
        with pytest.raises(SpecViolation, match="odd"):
            validate_page_count(121)

    def test_above_the_paper_maximum_is_rejected(self):
        with pytest.raises(SpecViolation, match="exceeds the maximum"):
            validate_page_count(800, "bw_cream")

    def test_even_up(self):
        assert even_up(119) == 120
        assert even_up(120) == 120


class TestGutter:
    def test_gutter_grows_with_the_book(self):
        assert gutter_margin_in(120) == 0.375
        assert gutter_margin_in(200) == 0.5
        assert gutter_margin_in(400) == 0.625

    def test_band_edges_are_inclusive(self):
        assert gutter_margin_in(150) == 0.375
        assert gutter_margin_in(151) == 0.5


class TestCoverGeometry:
    def test_wrap_is_two_trims_plus_spine_plus_bleed(self):
        geo = cover_geometry("6x9", 120, "bw_cream")
        assert geo.width == pytest.approx(2 * 6.0 + 0.30 + 2 * 0.125)
        assert geo.height == pytest.approx(9.0 + 2 * 0.125)

    def test_panels_sit_where_the_wrap_says(self):
        geo = cover_geometry("6x9", 120, "bw_cream")
        assert geo.back_panel_x == pytest.approx(0.125)
        assert geo.spine_x == pytest.approx(6.125)
        assert geo.front_panel_x == pytest.approx(6.425)

    def test_thicker_book_makes_a_wider_wrap(self):
        thin = cover_geometry("6x9", 100, "bw_white")
        thick = cover_geometry("6x9", 400, "bw_white")
        assert thick.width > thin.width

    def test_spine_text_needs_enough_pages(self):
        assert not cover_geometry("6x9", 60, "bw_white").spine_text_allowed
        assert cover_geometry("6x9", 120, "bw_white").spine_text_allowed

    def test_pixel_size_at_300dpi(self):
        geo = cover_geometry("6x9", 120, "bw_cream")
        assert geo.pixel_size(300) == (round(geo.width * 300), round(geo.height * 300))

    def test_unknown_trim_is_refused(self):
        with pytest.raises(SpecViolation, match="unknown trim size"):
            cover_geometry("5x7", 120)


class TestMoney:
    def test_short_books_use_the_flat_printing_rate(self):
        assert printing_cost_usd(100, "bw_white") == Decimal("2.30")

    def test_long_books_pay_per_page(self):
        assert printing_cost_usd(120, "bw_white") == Decimal("2.44")  # 1.00 + 120*0.012

    def test_min_price_clears_the_printing_cost(self):
        floor = min_list_price_usd(120, "bw_white")
        assert royalty_usd(floor, 120, "bw_white") >= Decimal("0.00")

    def test_a_penny_under_the_floor_loses_money(self):
        floor = min_list_price_usd(120, "bw_white")
        assert royalty_usd(floor - Decimal("0.02"), 120, "bw_white") < Decimal("0.00")

    def test_royalty_is_rate_times_price_minus_cost(self):
        assert royalty_usd(9.99, 120, "bw_white") == Decimal("3.55")


def test_spec_card_reports_its_own_version():
    from kdp_factory.spec.kdp import spec_card_text

    text = spec_card_text()
    assert KDP_SPEC["spec_version"] in text
    assert "verify" in text.lower()
