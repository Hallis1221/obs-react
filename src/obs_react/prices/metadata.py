"""Stock metadata (market cap, volume, sector) via yfinance .info."""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone

import yfinance as yf

from obs_react.config import MCAP_BUCKETS, VOLUME_BUCKETS, YFINANCE_RATE_LIMIT
from obs_react.db.operations import (
    upsert_stock_meta,
    get_stock_meta,
    log_fetch_start,
    log_fetch_end,
)

log = logging.getLogger(__name__)


def _classify_market_cap(market_cap: float | None) -> str | None:
    if market_cap is None:
        return None
    for bucket, (lo, hi) in MCAP_BUCKETS.items():
        if lo <= market_cap < hi:
            return bucket
    return None


def _classify_volume(avg_volume: float | None) -> str | None:
    if avg_volume is None:
        return None
    for bucket, (lo, hi) in VOLUME_BUCKETS.items():
        if lo <= avg_volume < hi:
            return bucket
    return None


def fetch_metadata(ticker: str, force: bool = False) -> dict | None:
    """Fetch stock metadata from yfinance. Refreshes at most once per day unless force=True."""
    ol_ticker = ticker.strip().upper()
    if not ol_ticker.endswith(".OL"):
        ol_ticker += ".OL"

    # Check if fresh enough
    if not force:
        existing = get_stock_meta(ol_ticker)
        if existing and existing.updated_at:
            updated = datetime.fromisoformat(existing.updated_at)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - updated < timedelta(days=1):
                log.info(f"Metadata for {ol_ticker} is fresh, skipping")
                return None

    fetch_id = log_fetch_start("yfinance_meta", ol_ticker)
    error = None

    try:
        log.info(f"Fetching metadata for {ol_ticker}")
        info = yf.Ticker(ol_ticker).info

        company_name = info.get("longName") or info.get("shortName") or ol_ticker
        market_cap = info.get("marketCap")
        avg_volume = info.get("averageVolume")
        sector = info.get("sector")
        industry = info.get("industry")

        mcap_bucket = _classify_market_cap(market_cap)
        vol_bucket = _classify_volume(avg_volume)

        upsert_stock_meta(
            ticker=ol_ticker,
            company_name=company_name,
            market_cap=market_cap,
            avg_daily_volume=avg_volume,
            sector=sector,
            industry=industry,
            market_cap_bucket=mcap_bucket,
            volume_bucket=vol_bucket,
        )

        result = {
            "ticker": ol_ticker,
            "company_name": company_name,
            "market_cap": market_cap,
            "avg_daily_volume": avg_volume,
            "sector": sector,
            "industry": industry,
            "market_cap_bucket": mcap_bucket,
            "volume_bucket": vol_bucket,
        }
        log.info(
            f"  {company_name}: mcap={market_cap}, vol={avg_volume}, "
            f"bucket={mcap_bucket}/{vol_bucket}"
        )
        log_fetch_end(fetch_id, records_fetched=1)
        return result

    except Exception as e:
        error = str(e)
        log.error(f"Metadata fetch failed for {ol_ticker}: {e}")
        log_fetch_end(fetch_id, error_message=error)
        return None


def fetch_metadata_for_tickers(tickers: list[str], force: bool = False) -> int:
    """Fetch metadata for multiple tickers. Returns count updated."""
    count = 0
    for ticker in tickers:
        result = fetch_metadata(ticker, force=force)
        if result is not None:
            count += 1
        _time.sleep(YFINANCE_RATE_LIMIT)
    return count
