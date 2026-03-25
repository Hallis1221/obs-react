"""yfinance wrapper with tiered intervals and caching."""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from obs_react.config import (
    BENCHMARK_TICKER,
    BENCHMARK_FALLBACK,
    YFINANCE_RATE_LIMIT,
)
from obs_react.db.operations import (
    insert_price_bars,
    get_price_bar_range,
    log_fetch_start,
    log_fetch_end,
)

log = logging.getLogger(__name__)


def _ol_ticker(ticker: str) -> str:
    """Ensure ticker has .OL suffix for Oslo Bors."""
    t = ticker.strip().upper()
    if not t.endswith(".OL"):
        t += ".OL"
    return t


def best_available_interval(event_age_days: float) -> str:
    """Return the best interval available given the event age."""
    if event_age_days <= 7:
        return "1m"
    elif event_age_days <= 60:
        return "5m"
    return "1d"


def fetch_prices(
    ticker: str,
    start: datetime,
    end: datetime,
    interval: str = "1m",
) -> list[dict]:
    """Fetch price bars from yfinance for a ticker and interval.

    Returns list of dicts ready for insert_price_bars.
    """
    ol_ticker = _ol_ticker(ticker)
    log.info(f"Fetching {ol_ticker} {interval} bars: {start.date()} to {end.date()}")

    try:
        data = yf.download(
            ol_ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        log.error(f"yfinance download failed for {ol_ticker}: {e}")
        return []

    if data.empty:
        log.warning(f"No data returned for {ol_ticker} ({interval})")
        return []

    # Handle MultiIndex columns from yfinance
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    bars = []
    for ts, row in data.iterrows():
        ts_dt = pd.Timestamp(ts)
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.tz_localize("UTC")
        else:
            ts_dt = ts_dt.tz_convert("UTC")

        bars.append({
            "ticker": ol_ticker,
            "timestamp": ts_dt.isoformat(),
            "interval": interval,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row.get("Volume", 0)),
        })

    log.info(f"Got {len(bars)} bars for {ol_ticker}")
    return bars


def backfill_prices_for_ticker(ticker: str) -> int:
    """Fetch all available intervals not yet stored. Returns total bars inserted."""
    ol_ticker = _ol_ticker(ticker)
    total_inserted = 0
    now = datetime.now(timezone.utc)

    fetch_id = log_fetch_start("yfinance_price", ol_ticker)
    error = None

    try:
        # Tier 1: 1-minute bars (last 7 days)
        start_1m = now - timedelta(days=7)
        existing_min, existing_max = get_price_bar_range(ol_ticker, "1m")
        if existing_max is None or datetime.fromisoformat(existing_max) < now - timedelta(hours=1):
            bars = fetch_prices(ticker, start_1m, now, "1m")
            inserted = insert_price_bars(bars)
            total_inserted += inserted
            log.info(f"1m bars: {inserted} new of {len(bars)} fetched")
            _time.sleep(YFINANCE_RATE_LIMIT)

        # Tier 2: 5-minute bars (last 59 days to stay within yfinance's 60-day limit)
        start_5m = now - timedelta(days=59)
        existing_min, existing_max = get_price_bar_range(ol_ticker, "5m")
        if existing_max is None or datetime.fromisoformat(existing_max) < now - timedelta(hours=1):
            bars = fetch_prices(ticker, start_5m, now, "5m")
            inserted = insert_price_bars(bars)
            total_inserted += inserted
            log.info(f"5m bars: {inserted} new of {len(bars)} fetched")
            _time.sleep(YFINANCE_RATE_LIMIT)

        # Tier 3: Daily bars (last 2 years)
        start_1d = now - timedelta(days=730)
        existing_min, existing_max = get_price_bar_range(ol_ticker, "1d")
        if existing_max is None or datetime.fromisoformat(existing_max) < now - timedelta(days=1):
            bars = fetch_prices(ticker, start_1d, now, "1d")
            inserted = insert_price_bars(bars)
            total_inserted += inserted
            log.info(f"1d bars: {inserted} new of {len(bars)} fetched")

    except Exception as e:
        error = str(e)
        log.error(f"Backfill failed for {ol_ticker}: {e}")
    finally:
        log_fetch_end(fetch_id, records_fetched=total_inserted, error_message=error)

    return total_inserted


def fetch_benchmark(
    start: datetime, end: datetime, interval: str = "1m"
) -> list[dict]:
    """Fetch benchmark index data. Tries OSEBX.OL, falls back to OBX.OL."""
    bars = fetch_prices(BENCHMARK_TICKER.replace(".OL", ""), start, end, interval)
    if not bars:
        log.info(f"No data for {BENCHMARK_TICKER}, trying {BENCHMARK_FALLBACK}")
        bars = fetch_prices(BENCHMARK_FALLBACK.replace(".OL", ""), start, end, interval)
    return bars


def backfill_benchmark() -> int:
    """Backfill benchmark data at all intervals."""
    total = 0
    for benchmark in [BENCHMARK_TICKER, BENCHMARK_FALLBACK]:
        bare = benchmark.replace(".OL", "")
        inserted = backfill_prices_for_ticker(bare)
        total += inserted
        if inserted > 0:
            break  # Got data from primary benchmark
        _time.sleep(YFINANCE_RATE_LIMIT)
    return total
