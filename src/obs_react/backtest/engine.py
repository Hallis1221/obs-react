"""Backtesting engine with realistic transaction costs and slippage."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from obs_react.db.operations import get_price_bars, get_stock_meta
from obs_react.models import Announcement, PriceBar
from obs_react.utils.oslo_tz import parse_iso, is_trading_hours, next_trading_open

log = logging.getLogger(__name__)


@dataclass
class Trade:
    announcement_id: int
    ticker: str
    direction: Literal["long", "short"]
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    gross_return: float       # before costs
    spread_cost: float        # bid-ask spread estimate
    commission: float         # broker commission
    slippage: float           # market impact estimate
    net_return: float         # after all costs
    hold_seconds: int
    data_quality: str
    category: str
    signal_type: str          # "momentum", "immediate", "fade"
    volume_bucket: str | None = None
    market_cap_bucket: str | None = None


@dataclass
class BacktestConfig:
    # Strategy params
    signal_type: str = "momentum"         # "momentum" or "immediate"
    entry_delay_seconds: int = 60         # time to detect + place order
    hold_minutes: int = 60                # how long to hold
    move_threshold: float = 0.01          # min |AR| in detection window to trigger
    detection_window_minutes: int = 5     # window to detect initial move

    # Cost model
    spread_bps: float = 30               # bid-ask spread in bps (30 bps = 0.3%)
    commission_bps: float = 10            # round-trip commission in bps
    slippage_bps: float = 10              # market impact in bps

    # Filters
    min_volume: float | None = None       # min avg daily volume
    max_volume: float | None = None
    min_mcap: float | None = None
    max_mcap: float | None = None
    volume_buckets: list[str] | None = None  # e.g. ["low", "medium"]
    mcap_buckets: list[str] | None = None
    categories: list[str] | None = None   # announcement categories to include

    # Risk
    max_loss_pct: float = 0.05            # stop loss (5%)


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade]
    total_announcements: int = 0
    filtered_announcements: int = 0
    signals_detected: int = 0
    trades_executed: int = 0

    @property
    def net_returns(self) -> list[float]:
        return [t.net_return for t in self.trades]

    @property
    def gross_returns(self) -> list[float]:
        return [t.gross_return for t in self.trades]

    @property
    def long_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.direction == "long"]

    @property
    def short_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.direction == "short"]


def _estimate_spread(meta, config: BacktestConfig) -> float:
    """Estimate spread based on volume. Low-volume stocks have wider spreads."""
    base = config.spread_bps / 10000
    if meta and meta.avg_daily_volume:
        vol = meta.avg_daily_volume
        if vol < 10000:
            return base * 5       # very illiquid: 1.5%
        elif vol < 50000:
            return base * 3       # illiquid: 0.9%
        elif vol < 100000:
            return base * 2       # low: 0.6%
        elif vol < 500000:
            return base * 1.5     # medium: 0.45%
    return base


def _get_bars_around_event(
    ticker: str, event_time: datetime, pre_min: int, post_min: int
) -> list[PriceBar]:
    """Get price bars around an event, preferring 1m then 5m."""
    ol = ticker if ticker.endswith(".OL") else ticker + ".OL"
    start = event_time - timedelta(minutes=pre_min)
    end = event_time + timedelta(minutes=post_min)

    bars = get_price_bars(ol, interval="1m", start=start.isoformat(), end=end.isoformat())
    if len(bars) >= 3:
        return bars

    bars = get_price_bars(ol, interval="5m", start=start.isoformat(), end=end.isoformat())
    return bars


def _find_price_at(bars: list[PriceBar], target: datetime, after: bool = True) -> PriceBar | None:
    """Find the bar closest to target time (after=True means at or after target)."""
    if not bars:
        return None
    if after:
        candidates = [b for b in bars if parse_iso(b.timestamp) >= target]
        return candidates[0] if candidates else None
    else:
        candidates = [b for b in bars if parse_iso(b.timestamp) <= target]
        return candidates[-1] if candidates else None


def run_backtest(
    announcements: list[Announcement],
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run the backtest over a list of announcements."""
    if config is None:
        config = BacktestConfig()

    result = BacktestResult(config=config, trades=[], total_announcements=len(announcements))

    for ann in announcements:
        ticker = ann.ticker
        ol = ticker if ticker.endswith(".OL") else ticker + ".OL"
        meta = get_stock_meta(ol)

        # --- Filter ---
        if config.volume_buckets and (not meta or meta.volume_bucket not in config.volume_buckets):
            continue
        if config.mcap_buckets and (not meta or meta.market_cap_bucket not in config.mcap_buckets):
            continue
        if config.min_volume and (not meta or not meta.avg_daily_volume or meta.avg_daily_volume < config.min_volume):
            continue
        if config.max_volume and meta and meta.avg_daily_volume and meta.avg_daily_volume > config.max_volume:
            continue
        if config.categories:
            if not any(cat in (ann.category or "") for cat in config.categories):
                continue

        result.filtered_announcements += 1

        # --- Determine event time ---
        event_time = parse_iso(ann.published_at)
        if not is_trading_hours(event_time):
            event_time = next_trading_open(event_time)

        # --- Get price data ---
        pre_window = config.detection_window_minutes + 30
        post_window = config.hold_minutes + config.detection_window_minutes + 30
        bars = _get_bars_around_event(ticker, event_time, pre_window, post_window)

        if len(bars) < 3:
            continue

        data_quality = "1m" if bars[0].interval == "1m" else "5m"

        # --- Strategy logic ---
        if config.signal_type == "momentum":
            trade = _momentum_strategy(ann, bars, event_time, meta, config, data_quality)
        elif config.signal_type == "immediate":
            trade = _immediate_strategy(ann, bars, event_time, meta, config, data_quality)
        else:
            continue

        if trade:
            result.signals_detected += 1
            result.trades.append(trade)

    result.trades_executed = len(result.trades)
    return result


def _momentum_strategy(
    ann: Announcement,
    bars: list[PriceBar],
    event_time: datetime,
    meta,
    config: BacktestConfig,
    data_quality: str,
) -> Trade | None:
    """Wait for initial move, then trade in the same direction."""
    ol = ann.ticker if ann.ticker.endswith(".OL") else ann.ticker + ".OL"

    # Find price at event time
    event_bar = _find_price_at(bars, event_time, after=True)
    if not event_bar:
        return None
    event_price = event_bar.close

    # Find price after detection window
    detect_time = event_time + timedelta(minutes=config.detection_window_minutes)
    detect_bar = _find_price_at(bars, detect_time, after=True)
    if not detect_bar:
        return None

    initial_move = (detect_bar.close - event_price) / event_price if event_price else 0

    if abs(initial_move) < config.move_threshold:
        return None  # No signal

    direction: Literal["long", "short"] = "long" if initial_move > 0 else "short"

    # Entry after detection + execution delay
    entry_time = detect_time + timedelta(seconds=config.entry_delay_seconds)
    entry_bar = _find_price_at(bars, entry_time, after=True)
    if not entry_bar:
        return None
    entry_price = entry_bar.close

    # Exit after hold period
    exit_time = entry_time + timedelta(minutes=config.hold_minutes)
    exit_bar = _find_price_at(bars, exit_time, after=False)
    if not exit_bar:
        # Use last available bar
        exit_bar = bars[-1]
    exit_price = exit_bar.close

    # Check stop loss — scan bars between entry and exit
    entry_ts = parse_iso(entry_bar.timestamp)
    for b in bars:
        bt = parse_iso(b.timestamp)
        if bt <= entry_ts:
            continue
        if bt > parse_iso(exit_bar.timestamp):
            break
        if direction == "long":
            drawdown = (b.low - entry_price) / entry_price
            if drawdown < -config.max_loss_pct:
                exit_price = entry_price * (1 - config.max_loss_pct)
                exit_bar = b
                break
        else:
            drawdown = (b.high - entry_price) / entry_price
            if drawdown > config.max_loss_pct:
                exit_price = entry_price * (1 + config.max_loss_pct)
                exit_bar = b
                break

    # Calculate returns
    if direction == "long":
        gross_ret = (exit_price - entry_price) / entry_price
    else:
        gross_ret = (entry_price - exit_price) / entry_price

    spread = _estimate_spread(meta, config)
    commission = config.commission_bps / 10000
    slippage = config.slippage_bps / 10000
    total_cost = spread + commission + slippage
    net_ret = gross_ret - total_cost

    hold_secs = int((parse_iso(exit_bar.timestamp) - entry_ts).total_seconds())

    return Trade(
        announcement_id=ann.id,
        ticker=ol,
        direction=direction,
        entry_time=entry_bar.timestamp,
        exit_time=exit_bar.timestamp,
        entry_price=entry_price,
        exit_price=exit_price,
        gross_return=gross_ret,
        spread_cost=spread,
        commission=commission,
        slippage=slippage,
        net_return=net_ret,
        hold_seconds=hold_secs,
        data_quality=data_quality,
        category=ann.category or "",
        signal_type="momentum",
        volume_bucket=meta.volume_bucket if meta else None,
        market_cap_bucket=meta.market_cap_bucket if meta else None,
    )


def _immediate_strategy(
    ann: Announcement,
    bars: list[PriceBar],
    event_time: datetime,
    meta,
    config: BacktestConfig,
    data_quality: str,
) -> Trade | None:
    """Trade immediately on any news — test whether speed alone is alpha."""
    ol = ann.ticker if ann.ticker.endswith(".OL") else ann.ticker + ".OL"

    # Entry right after announcement + delay
    entry_time = event_time + timedelta(seconds=config.entry_delay_seconds)
    entry_bar = _find_price_at(bars, entry_time, after=True)
    if not entry_bar:
        return None
    entry_price = entry_bar.close

    # For immediate strategy, we go long by default
    direction: Literal["long", "short"] = "long"

    # Exit after hold period
    exit_time = entry_time + timedelta(minutes=config.hold_minutes)
    exit_bar = _find_price_at(bars, exit_time, after=False)
    if not exit_bar:
        exit_bar = bars[-1]
    exit_price = exit_bar.close

    entry_ts = parse_iso(entry_bar.timestamp)

    # Stop loss
    for b in bars:
        bt = parse_iso(b.timestamp)
        if bt <= entry_ts:
            continue
        if bt > parse_iso(exit_bar.timestamp):
            break
        drawdown = (b.low - entry_price) / entry_price
        if drawdown < -config.max_loss_pct:
            exit_price = entry_price * (1 - config.max_loss_pct)
            exit_bar = b
            break

    gross_ret = (exit_price - entry_price) / entry_price
    spread = _estimate_spread(meta, config)
    commission = config.commission_bps / 10000
    slippage = config.slippage_bps / 10000
    net_ret = gross_ret - (spread + commission + slippage)

    hold_secs = int((parse_iso(exit_bar.timestamp) - entry_ts).total_seconds())

    return Trade(
        announcement_id=ann.id,
        ticker=ol,
        direction=direction,
        entry_time=entry_bar.timestamp,
        exit_time=exit_bar.timestamp,
        entry_price=entry_price,
        exit_price=exit_price,
        gross_return=gross_ret,
        spread_cost=spread,
        commission=commission,
        slippage=slippage,
        net_return=net_ret,
        hold_seconds=hold_secs,
        data_quality=data_quality,
        category=ann.category or "",
        signal_type="immediate",
        volume_bucket=meta.volume_bucket if meta else None,
        market_cap_bucket=meta.market_cap_bucket if meta else None,
    )
