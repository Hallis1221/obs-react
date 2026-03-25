"""Tests for data models."""

from obs_react.models import Announcement, PriceBar, StockMeta, EventResult


def test_announcement_creation():
    ann = Announcement(
        id=1,
        message_id="123",
        ticker="EQNR",
        published_at="2025-03-20T10:00:00+00:00",
        category="INSIDER TRADING",
        title="Test",
        url="https://example.com",
        fetched_at="2025-03-20T10:05:00+00:00",
    )
    assert ann.ticker == "EQNR"
    assert ann.issuer_name is None


def test_announcement_with_issuer():
    ann = Announcement(
        id=1,
        message_id="123",
        ticker="EQNR",
        published_at="2025-03-20T10:00:00+00:00",
        category="INSIDER TRADING",
        title="Test",
        url="https://example.com",
        fetched_at="2025-03-20T10:05:00+00:00",
        issuer_name="Equinor ASA",
    )
    assert ann.issuer_name == "Equinor ASA"


def test_price_bar_creation():
    bar = PriceBar(
        id=1,
        ticker="EQNR.OL",
        timestamp="2025-03-20T09:00:00+00:00",
        interval="1m",
        open=100.0,
        high=101.0,
        low=99.5,
        close=100.5,
        volume=10000,
        fetched_at="2025-03-20T10:00:00+00:00",
    )
    assert bar.interval == "1m"
    assert bar.close == 100.5


def test_stock_meta_optional_fields():
    meta = StockMeta(
        id=1,
        ticker="EQNR.OL",
        company_name="Equinor ASA",
        market_cap=None,
        avg_daily_volume=None,
        sector=None,
        industry=None,
        updated_at="2025-03-20T10:00:00+00:00",
    )
    assert meta.market_cap_bucket is None
    assert meta.volume_bucket is None


def test_event_result_creation():
    er = EventResult(
        id=1,
        announcement_id=1,
        ticker="EQNR.OL",
        window_name="[-5m,+5m]",
        abnormal_return=0.015,
        cumulative_ar=0.02,
        reaction_time_seconds=120,
        pre_event_mean=100.0,
        pre_event_std=1.5,
        benchmark_return=0.005,
        computed_at="2025-03-20T10:00:00+00:00",
        data_quality="full",
    )
    assert er.reaction_time_seconds == 120
    assert er.data_quality == "full"
