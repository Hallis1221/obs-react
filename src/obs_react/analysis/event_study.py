"""Core event study engine: abnormal returns and reaction time."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from obs_react.config import (
    EVENT_WINDOWS,
    REACTION_THRESHOLD_SIGMA,
    PRE_EVENT_LOOKBACK_MINUTES,
    MIN_BARS_FOR_ANALYSIS,
    BENCHMARK_TICKER,
    BENCHMARK_FALLBACK,
)
from obs_react.db.operations import (
    get_price_bars,
    insert_event_result,
)
from obs_react.models import Announcement, PriceBar
from obs_react.utils.oslo_tz import (
    parse_iso,
    is_trading_hours,
    next_trading_open,
    to_utc,
)

log = logging.getLogger(__name__)


def _bars_to_returns(bars: list[PriceBar]) -> list[tuple[datetime, float]]:
    """Convert price bars to (timestamp, return) pairs."""
    if len(bars) < 2:
        return []
    returns = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        if prev_close == 0:
            continue
        ret = (bars[i].close - prev_close) / prev_close
        ts = parse_iso(bars[i].timestamp)
        returns.append((ts, ret))
    return returns


def _get_data_quality(interval: str) -> str:
    """Map interval to data quality label."""
    return {"1m": "full", "5m": "partial", "1d": "daily_only"}.get(interval, "missing")


def _pick_interval(bars: list[PriceBar]) -> str:
    """Determine the best interval from available bars."""
    intervals = {b.interval for b in bars}
    for pref in ("1m", "5m", "1d"):
        if pref in intervals:
            return pref
    return "1d"


def get_pre_event_stats(
    bars: list[PriceBar],
    event_time: datetime,
    lookback_minutes: int = PRE_EVENT_LOOKBACK_MINUTES,
) -> tuple[float, float]:
    """Compute pre-event price mean and standard deviation.

    Returns (mean_price, std_price). If insufficient data, returns (0, 0).
    """
    cutoff = event_time - timedelta(minutes=lookback_minutes)
    pre_bars = [
        b for b in bars
        if cutoff <= parse_iso(b.timestamp) < event_time
    ]

    if len(pre_bars) < 2:
        # Fall back to any bars before event
        pre_bars = [b for b in bars if parse_iso(b.timestamp) < event_time]

    if len(pre_bars) < 2:
        return 0.0, 0.0

    prices = [b.close for b in pre_bars]
    mean_p = sum(prices) / len(prices)
    variance = sum((p - mean_p) ** 2 for p in prices) / (len(prices) - 1)
    std_p = variance ** 0.5
    return mean_p, std_p


def compute_abnormal_return(
    stock_bars: list[PriceBar],
    bench_bars: list[PriceBar],
    start: datetime,
    end: datetime,
) -> tuple[float | None, float | None]:
    """Compute abnormal return and benchmark return over a window.

    Returns (abnormal_return, benchmark_return).
    """
    # Filter bars to window
    s_in_window = [
        b for b in stock_bars
        if start <= parse_iso(b.timestamp) <= end
    ]
    if len(s_in_window) < 2:
        return None, None

    stock_ret = (s_in_window[-1].close - s_in_window[0].close) / s_in_window[0].close \
        if s_in_window[0].close != 0 else None
    if stock_ret is None:
        return None, None

    # Benchmark return
    b_in_window = [
        b for b in bench_bars
        if start <= parse_iso(b.timestamp) <= end
    ]
    if len(b_in_window) >= 2 and b_in_window[0].close != 0:
        bench_ret = (b_in_window[-1].close - b_in_window[0].close) / b_in_window[0].close
    else:
        bench_ret = 0.0  # No benchmark data — use raw return

    ar = stock_ret - bench_ret
    return ar, bench_ret


def compute_reaction_time(
    bars: list[PriceBar],
    event_time: datetime,
    pre_mean: float,
    pre_std: float,
    threshold_sigma: float = REACTION_THRESHOLD_SIGMA,
) -> int | None:
    """Measure seconds until price deviates > threshold_sigma std devs from pre-event mean.

    Returns seconds, or None if threshold never exceeded within available bars.
    """
    if pre_std == 0 or pre_mean == 0:
        return None

    threshold = pre_std * threshold_sigma
    post_bars = [b for b in bars if parse_iso(b.timestamp) >= event_time]

    for bar in post_bars:
        bar_time = parse_iso(bar.timestamp)
        # Skip overnight gaps
        if not is_trading_hours(bar_time):
            continue
        deviation = abs(bar.close - pre_mean)
        if deviation > threshold:
            delta = bar_time - event_time
            return max(0, int(delta.total_seconds()))

    return None


def compute_event(
    announcement: Announcement,
    threshold_sigma: float = REACTION_THRESHOLD_SIGMA,
) -> list[dict]:
    """Run the full event study for a single announcement.

    Returns a list of result dicts (one per window) ready for insert_event_result.
    """
    ticker = announcement.ticker
    if not ticker.endswith(".OL"):
        ol_ticker = ticker + ".OL"
    else:
        ol_ticker = ticker

    event_time = parse_iso(announcement.published_at)

    # If outside trading hours, adjust to next open
    if not is_trading_hours(event_time):
        effective_time = next_trading_open(event_time)
        log.info(
            f"Announcement {announcement.message_id} outside trading hours, "
            f"using next open: {effective_time.isoformat()}"
        )
    else:
        effective_time = event_time

    # Determine widest window needed
    max_offset = max(abs(w[1]) for w in EVENT_WINDOWS) + max(abs(w[2]) for w in EVENT_WINDOWS)
    data_start = effective_time - timedelta(minutes=max_offset + PRE_EVENT_LOOKBACK_MINUTES)
    data_end = effective_time + timedelta(minutes=max_offset)

    # Fetch stock bars — try best interval first
    stock_bars = get_price_bars(ol_ticker, interval="1m",
                                start=data_start.isoformat(), end=data_end.isoformat())
    if len(stock_bars) < MIN_BARS_FOR_ANALYSIS:
        stock_bars = get_price_bars(ol_ticker, interval="5m",
                                    start=data_start.isoformat(), end=data_end.isoformat())
    if len(stock_bars) < MIN_BARS_FOR_ANALYSIS:
        stock_bars = get_price_bars(ol_ticker, interval="1d",
                                    start=data_start.isoformat(), end=data_end.isoformat())

    if len(stock_bars) < MIN_BARS_FOR_ANALYSIS:
        log.warning(f"Insufficient data for {ol_ticker} around {event_time.isoformat()}")
        # Still record results with "missing" quality
        results = []
        for window_name, start_off, end_off in EVENT_WINDOWS:
            results.append({
                "announcement_id": announcement.id,
                "ticker": ol_ticker,
                "window_name": window_name,
                "data_quality": "missing",
            })
        return results

    interval = _pick_interval(stock_bars)
    data_quality = _get_data_quality(interval)

    # Filter to just this interval for consistency
    stock_bars = [b for b in stock_bars if b.interval == interval] if interval else stock_bars
    stock_bars.sort(key=lambda b: b.timestamp)

    # Fetch benchmark bars
    bench_bars = get_price_bars(BENCHMARK_TICKER, interval=interval,
                                start=data_start.isoformat(), end=data_end.isoformat())
    if len(bench_bars) < 2:
        bench_bars = get_price_bars(BENCHMARK_FALLBACK, interval=interval,
                                    start=data_start.isoformat(), end=data_end.isoformat())

    # Pre-event stats
    pre_mean, pre_std = get_pre_event_stats(stock_bars, effective_time)

    results = []
    for window_name, start_off, end_off in EVENT_WINDOWS:
        w_start = effective_time + timedelta(minutes=start_off)
        w_end = effective_time + timedelta(minutes=end_off)

        ar, bench_ret = compute_abnormal_return(stock_bars, bench_bars, w_start, w_end)

        # Cumulative AR: sum of bar-by-bar abnormal returns
        s_window = [b for b in stock_bars if w_start <= parse_iso(b.timestamp) <= w_end]
        b_window = [b for b in bench_bars if w_start <= parse_iso(b.timestamp) <= w_end]
        car = _compute_car(s_window, b_window)

        # Reaction time (only meaningful for post-event windows)
        rt = None
        if start_off >= 0:
            rt = compute_reaction_time(
                stock_bars, effective_time, pre_mean, pre_std, threshold_sigma
            )

        results.append({
            "announcement_id": announcement.id,
            "ticker": ol_ticker,
            "window_name": window_name,
            "abnormal_return": ar,
            "cumulative_ar": car,
            "reaction_time_seconds": rt,
            "pre_event_mean": pre_mean,
            "pre_event_std": pre_std,
            "benchmark_return": bench_ret,
            "data_quality": data_quality,
        })

    return results


def _compute_car(stock_bars: list[PriceBar], bench_bars: list[PriceBar]) -> float | None:
    """Compute cumulative abnormal return from bar-by-bar returns."""
    if len(stock_bars) < 2:
        return None

    # Build benchmark return lookup
    bench_returns: dict[str, float] = {}
    for i in range(1, len(bench_bars)):
        if bench_bars[i - 1].close != 0:
            ret = (bench_bars[i].close - bench_bars[i - 1].close) / bench_bars[i - 1].close
            bench_returns[bench_bars[i].timestamp] = ret

    car = 0.0
    for i in range(1, len(stock_bars)):
        if stock_bars[i - 1].close == 0:
            continue
        stock_ret = (stock_bars[i].close - stock_bars[i - 1].close) / stock_bars[i - 1].close
        bench_ret = bench_returns.get(stock_bars[i].timestamp, 0.0)
        car += stock_ret - bench_ret

    return car


def run_analysis(
    announcements: list[Announcement],
    threshold_sigma: float = REACTION_THRESHOLD_SIGMA,
) -> int:
    """Run event study for multiple announcements. Returns count of results inserted."""
    total = 0
    for ann in announcements:
        log.info(f"Analyzing [{ann.ticker}] {ann.title[:50]} (id={ann.message_id})")
        try:
            results = compute_event(ann, threshold_sigma)
            for r in results:
                insert_event_result(**r)
                total += 1
        except Exception as e:
            log.error(f"Analysis failed for announcement {ann.id}: {e}")
    return total
