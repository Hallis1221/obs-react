"""Tests for event study analysis engine."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from obs_react.models import Announcement, PriceBar
from obs_react.analysis.event_study import (
    get_pre_event_stats,
    compute_abnormal_return,
    compute_reaction_time,
    _bars_to_returns,
    _get_data_quality,
)
from obs_react.utils.oslo_tz import UTC


def _make_bars(ticker: str, start: datetime, count: int, interval: str = "1m",
               base_price: float = 100.0, trend: float = 0.0) -> list[PriceBar]:
    """Generate synthetic price bars for testing."""
    bars = []
    dt = start
    step = timedelta(minutes=1) if interval == "1m" else timedelta(minutes=5)
    for i in range(count):
        price = base_price + trend * i
        bars.append(PriceBar(
            id=i + 1,
            ticker=ticker,
            timestamp=dt.isoformat(),
            interval=interval,
            open=price - 0.5,
            high=price + 1.0,
            low=price - 1.0,
            close=price,
            volume=10000,
            fetched_at=datetime.now(UTC).isoformat(),
        ))
        dt += step
    return bars


class TestBarsToReturns:
    def test_basic_returns(self):
        bars = _make_bars("EQNR.OL", datetime(2025, 3, 20, 9, 0, tzinfo=UTC), 3,
                          base_price=100.0, trend=1.0)
        returns = _bars_to_returns(bars)
        assert len(returns) == 2
        # 101/100 - 1 = 0.01
        assert abs(returns[0][1] - 0.01) < 0.001

    def test_empty_bars(self):
        assert _bars_to_returns([]) == []

    def test_single_bar(self):
        bars = _make_bars("EQNR.OL", datetime(2025, 3, 20, 9, 0, tzinfo=UTC), 1)
        assert _bars_to_returns(bars) == []


class TestDataQuality:
    def test_quality_labels(self):
        assert _get_data_quality("1m") == "full"
        assert _get_data_quality("5m") == "partial"
        assert _get_data_quality("1d") == "daily_only"
        assert _get_data_quality("unknown") == "missing"


class TestPreEventStats:
    def test_basic_stats(self):
        event_time = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
        bars = _make_bars("EQNR.OL", datetime(2025, 3, 20, 9, 30, tzinfo=UTC), 30,
                          base_price=100.0, trend=0.0)
        mean_p, std_p = get_pre_event_stats(bars, event_time, lookback_minutes=30)
        assert mean_p == 100.0
        assert std_p == 0.0  # constant price

    def test_with_trend(self):
        event_time = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
        bars = _make_bars("EQNR.OL", datetime(2025, 3, 20, 9, 30, tzinfo=UTC), 30,
                          base_price=100.0, trend=0.1)
        mean_p, std_p = get_pre_event_stats(bars, event_time, lookback_minutes=30)
        assert mean_p > 100.0
        assert std_p > 0.0

    def test_insufficient_data(self):
        event_time = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
        bars = _make_bars("EQNR.OL", datetime(2025, 3, 20, 11, 0, tzinfo=UTC), 5)
        mean_p, std_p = get_pre_event_stats(bars, event_time)
        assert mean_p == 0.0
        assert std_p == 0.0


class TestComputeAbnormalReturn:
    def test_basic_ar(self):
        start = datetime(2025, 3, 20, 9, 0, tzinfo=UTC)
        end = datetime(2025, 3, 20, 9, 10, tzinfo=UTC)

        stock_bars = _make_bars("EQNR.OL", start, 10, trend=1.0)
        bench_bars = _make_bars("OSEBX.OL", start, 10, trend=0.5)

        ar, bench_ret = compute_abnormal_return(stock_bars, bench_bars, start, end)
        assert ar is not None
        assert bench_ret is not None
        # Stock goes up more than benchmark => positive AR
        assert ar > 0

    def test_insufficient_stock_bars(self):
        start = datetime(2025, 3, 20, 9, 0, tzinfo=UTC)
        end = datetime(2025, 3, 20, 9, 10, tzinfo=UTC)

        stock_bars = _make_bars("EQNR.OL", start, 1)
        bench_bars = _make_bars("OSEBX.OL", start, 10)

        ar, bench_ret = compute_abnormal_return(stock_bars, bench_bars, start, end)
        assert ar is None

    def test_no_benchmark(self):
        start = datetime(2025, 3, 20, 9, 0, tzinfo=UTC)
        end = datetime(2025, 3, 20, 9, 10, tzinfo=UTC)

        stock_bars = _make_bars("EQNR.OL", start, 10, trend=1.0)
        bench_bars = []

        ar, bench_ret = compute_abnormal_return(stock_bars, bench_bars, start, end)
        # With no benchmark, should still work (bench_ret = 0)
        assert ar is not None


class TestComputeReactionTime:
    def test_immediate_reaction(self):
        event_time = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
        # Pre-event stats: mean=100, std=1
        # Bars after event jump to 105 (5 std devs)
        pre_bars = _make_bars("EQNR.OL", datetime(2025, 3, 20, 9, 30, tzinfo=UTC), 30,
                              base_price=100.0)
        post_bars = _make_bars("EQNR.OL", event_time, 10, base_price=105.0)
        all_bars = pre_bars + post_bars

        rt = compute_reaction_time(all_bars, event_time, pre_mean=100.0, pre_std=1.0,
                                   threshold_sigma=2.0)
        assert rt is not None
        assert rt == 0  # Immediate reaction

    def test_no_reaction(self):
        event_time = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
        # Price stays at 100 (within 2 sigma of mean=100, std=1)
        bars = _make_bars("EQNR.OL", datetime(2025, 3, 20, 9, 30, tzinfo=UTC), 60,
                          base_price=100.0)
        rt = compute_reaction_time(bars, event_time, pre_mean=100.0, pre_std=1.0,
                                   threshold_sigma=2.0)
        assert rt is None

    def test_zero_std(self):
        event_time = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
        bars = _make_bars("EQNR.OL", event_time, 10)
        rt = compute_reaction_time(bars, event_time, pre_mean=100.0, pre_std=0.0)
        assert rt is None
