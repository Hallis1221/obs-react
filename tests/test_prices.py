"""Tests for price fetcher utilities (no network calls)."""

from obs_react.prices.fetcher import best_available_interval, _ol_ticker


class TestOlTicker:
    def test_adds_suffix(self):
        assert _ol_ticker("EQNR") == "EQNR.OL"

    def test_already_has_suffix(self):
        assert _ol_ticker("EQNR.OL") == "EQNR.OL"

    def test_lowercase_uppercased(self):
        assert _ol_ticker("eqnr") == "EQNR.OL"

    def test_strips_whitespace(self):
        assert _ol_ticker("  EQNR  ") == "EQNR.OL"


class TestBestAvailableInterval:
    def test_recent_event(self):
        assert best_available_interval(3) == "1m"

    def test_week_old_event(self):
        assert best_available_interval(7) == "1m"

    def test_month_old_event(self):
        assert best_available_interval(30) == "5m"

    def test_old_event(self):
        assert best_available_interval(90) == "1d"

    def test_boundary_7_days(self):
        assert best_available_interval(7.0) == "1m"
        assert best_available_interval(7.1) == "5m"

    def test_boundary_60_days(self):
        assert best_available_interval(60.0) == "5m"
        assert best_available_interval(60.1) == "1d"
