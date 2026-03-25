"""Real-time NewsWeb monitor with momentum signal detection.

Polls the NewsWeb API for new announcements, checks for >1% price moves
within 5 minutes, and emits trade signals when momentum is detected.

Based on analysis findings: 70% win rate, +1.81% mean return when
trading with the initial move direction on >1% threshold.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

import yfinance as yf

from obs_react.config import REACTION_THRESHOLD_SIGMA
from obs_react.db.operations import (
    get_known_message_ids,
    insert_announcement,
    get_stock_meta,
)
from obs_react.news.scraper import _fetch_api_page, _normalize_api_message, parse_announcement
from obs_react.utils.oslo_tz import is_trading_hours, to_oslo

log = logging.getLogger(__name__)


@dataclass
class Signal:
    """A trade signal emitted by the monitor."""
    ticker: str
    direction: str  # "LONG" or "SHORT"
    announcement_title: str
    category: str
    published_at: str
    price_at_news: float
    price_now: float
    move_pct: float
    volume_bucket: str | None
    detected_at: str
    classifier_agrees: bool | None = None  # True if text classifier agrees with momentum
    classifier_confidence: float | None = None

    def __str__(self) -> str:
        arrow = "↑" if self.direction == "LONG" else "↓"
        confirm = ""
        if self.classifier_agrees is True:
            confirm = " ✓CONFIRMED"
        elif self.classifier_agrees is False:
            confirm = " ✗DISAGREE"
        return (
            f"{arrow} {self.direction} {self.ticker} | "
            f"Move: {self.move_pct:+.2f}% | "
            f"Price: {self.price_at_news:.2f} -> {self.price_now:.2f} | "
            f"Vol: {self.volume_bucket or '?'}{confirm} | "
            f"{self.announcement_title[:50]}"
        )


def _get_current_price(ticker: str) -> float | None:
    """Fetch the most recent price for a ticker via yfinance."""
    ol = ticker if ticker.endswith(".OL") else ticker + ".OL"
    try:
        t = yf.Ticker(ol)
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.debug(f"Price fetch failed for {ol}: {e}")
        return None


def _get_price_at_time(ticker: str, target_time: datetime, window_minutes: int = 5) -> float | None:
    """Get price closest to a specific time."""
    ol = ticker if ticker.endswith(".OL") else ticker + ".OL"
    try:
        t = yf.Ticker(ol)
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            return None
        # Find bar closest to target time
        for idx in hist.index:
            bar_time = idx.to_pydatetime()
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)
            diff = abs((bar_time - target_time).total_seconds())
            if diff < window_minutes * 60:
                return float(hist.loc[idx, "Close"])
        # Fallback to first bar
        return float(hist["Close"].iloc[0])
    except Exception as e:
        log.debug(f"Historical price failed for {ol}: {e}")
        return None


def check_for_signals(
    move_threshold: float = 0.01,
    max_age_minutes: int = 30,
    strategy: str = "fade",
) -> list[Signal]:
    """Check recent announcements for trade signals.

    Strategy options:
    - "fade": Mean reversion — trade AGAINST 2-min overreaction (82% WR, +3.4% net)
    - "momentum": Trade WITH the initial move (historically unprofitable after costs)

    Looks at announcements from the last `max_age_minutes` minutes,
    checks if the stock has moved > `move_threshold` (1% default),
    and returns trade signals.
    """
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    known_ids = get_known_message_ids()

    # Fetch today's announcements
    try:
        raw_messages = _fetch_api_page(today_str, today_str)
    except Exception as e:
        log.error(f"API fetch failed: {e}")
        return []

    signals = []
    cutoff = now - timedelta(minutes=max_age_minutes)

    for item in raw_messages:
        msg = _normalize_api_message(item)
        if not msg:
            continue

        # Check if recent enough
        if msg["published_at"]:
            try:
                pub_time = datetime.fromisoformat(msg["published_at"])
                if pub_time < cutoff:
                    continue
            except (ValueError, TypeError):
                continue
        else:
            continue

        # Skip if not during trading hours
        if not is_trading_hours(pub_time):
            continue

        ticker = msg["ticker"]

        # Store announcement if new
        if msg["message_id"] not in known_ids:
            parsed = parse_announcement(msg)
            insert_announcement(**parsed)
            known_ids.add(msg["message_id"])

        # Only look at announcements >2 min old (need initial move to form)
        age_seconds = (now - pub_time).total_seconds()
        if strategy == "fade" and age_seconds < 120:
            continue  # Too fresh — wait for 2-min move to establish

        # Check price move since announcement
        price_at_news = _get_price_at_time(ticker, pub_time)
        price_now = _get_current_price(ticker)

        if price_at_news is None or price_now is None or price_at_news == 0:
            continue

        move = (price_now - price_at_news) / price_at_news

        if abs(move) >= move_threshold:
            meta = get_stock_meta(ticker + ".OL" if not ticker.endswith(".OL") else ticker)

            if strategy == "fade":
                # FADE: trade AGAINST the initial move (mean reversion)
                direction = "SHORT" if move > 0 else "LONG"
            else:
                # MOMENTUM: trade WITH the move
                direction = "LONG" if move > 0 else "SHORT"

            # Run text classifier to check if this is a real catalyst (don't fade those)
            classifier_agrees = None
            classifier_conf = None
            try:
                from obs_react.analysis.classifier import classify_rule_based
                from obs_react.models import Announcement
                fake_ann = Announcement(
                    id=0, message_id=msg["message_id"], ticker=ticker,
                    published_at=msg["published_at"], category=msg["category"],
                    title=msg["title"], url=msg["url"],
                    fetched_at=now.isoformat(),
                )
                pred = classify_rule_based(fake_ann)
                if pred.direction != "SKIP":
                    # For fade: classifier agreeing with the MOVE direction means
                    # this might be a real catalyst — risky to fade
                    move_dir = "LONG" if move > 0 else "SHORT"
                    classifier_agrees = (pred.direction != move_dir)  # agrees with FADE
                    classifier_conf = pred.confidence
            except Exception:
                pass

            signal = Signal(
                ticker=ticker,
                direction=direction,
                announcement_title=msg["title"],
                category=msg["category"],
                published_at=msg["published_at"],
                price_at_news=price_at_news,
                price_now=price_now,
                move_pct=move * 100,
                volume_bucket=meta.volume_bucket if meta else None,
                detected_at=now.isoformat(),
                classifier_agrees=classifier_agrees,
                classifier_confidence=classifier_conf,
            )
            signals.append(signal)
            log.info(f"SIGNAL: {signal}")

    return signals


def monitor_loop(
    interval_seconds: int = 60,
    move_threshold: float = 0.01,
    max_age_minutes: int = 30,
) -> None:
    """Continuously monitor for trade signals.

    Checks every `interval_seconds` for new announcements that have
    triggered > `move_threshold` price moves.
    """
    log.info(
        f"Starting monitor (interval={interval_seconds}s, "
        f"threshold={move_threshold*100:.1f}%, max_age={max_age_minutes}min)"
    )

    seen_signals: set[str] = set()

    while True:
        now = datetime.now(timezone.utc)
        oslo_now = to_oslo(now)

        if not is_trading_hours(now):
            log.info(f"Market closed ({oslo_now.strftime('%H:%M')} Oslo). Waiting...")
            _time.sleep(interval_seconds * 5)
            continue

        try:
            signals = check_for_signals(
                move_threshold=move_threshold,
                max_age_minutes=max_age_minutes,
            )

            for s in signals:
                key = f"{s.ticker}:{s.published_at}"
                if key not in seen_signals:
                    seen_signals.add(key)
                    print(f"\n*** NEW SIGNAL ***")
                    print(f"  {s}")
                    print(f"  Time: {s.detected_at}")
                    print()

        except KeyboardInterrupt:
            log.info("Monitor stopped by user")
            break
        except Exception as e:
            log.error(f"Monitor error: {e}")

        _time.sleep(interval_seconds)
