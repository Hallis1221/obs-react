"""Tests for metadata classification functions (no network calls)."""

from obs_react.prices.metadata import _classify_market_cap, _classify_volume


class TestClassifyMarketCap:
    def test_micro(self):
        assert _classify_market_cap(500_000_000) == "micro"

    def test_small(self):
        assert _classify_market_cap(5_000_000_000) == "small"

    def test_mid(self):
        assert _classify_market_cap(20_000_000_000) == "mid"

    def test_large(self):
        assert _classify_market_cap(100_000_000_000) == "large"

    def test_none(self):
        assert _classify_market_cap(None) is None

    def test_boundary_micro_small(self):
        assert _classify_market_cap(1_000_000_000) == "small"  # exactly 1B = small

    def test_boundary_small_mid(self):
        assert _classify_market_cap(10_000_000_000) == "mid"

    def test_boundary_mid_large(self):
        assert _classify_market_cap(50_000_000_000) == "large"

    def test_zero(self):
        assert _classify_market_cap(0) == "micro"


class TestClassifyVolume:
    def test_low(self):
        assert _classify_volume(50_000) == "low"

    def test_medium(self):
        assert _classify_volume(500_000) == "medium"

    def test_high(self):
        assert _classify_volume(5_000_000) == "high"

    def test_none(self):
        assert _classify_volume(None) is None

    def test_boundary_low_medium(self):
        assert _classify_volume(100_000) == "medium"

    def test_boundary_medium_high(self):
        assert _classify_volume(1_000_000) == "high"

    def test_zero(self):
        assert _classify_volume(0) == "low"
