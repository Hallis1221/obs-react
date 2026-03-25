"""News announcement classifier for predicting price direction.

Uses announcement text features (title, category) to predict whether
the stock will move up or down in the hour after the announcement.

Two approaches:
1. Rule-based: keyword/category signals from historical patterns
2. LLM-based: use Claude API to classify announcement sentiment/impact
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from obs_react.models import Announcement

log = logging.getLogger(__name__)


@dataclass
class Prediction:
    """A directional prediction for an announcement."""
    ticker: str
    direction: str  # "LONG", "SHORT", or "SKIP"
    confidence: float  # 0.0 to 1.0
    reason: str
    announcement_id: int | None = None


# Keywords strongly associated with positive moves (from historical data)
POSITIVE_KEYWORDS = [
    r"trading update.*first quarter",
    r"dividend.*declar",
    r"share buy.?back",
    r"disclosure of large shareholding",
    r"subsequent offer",
    r"acquisition",
    r"new contract",
    r"awarded.*contract",
    r"record.*(revenue|profit|earnings)",
    r"upgraded",
    r"increased.*guidance",
    r"ex.?date.*utbytte",  # Norwegian ex-dividend
]

# Keywords strongly associated with negative moves
NEGATIVE_KEYWORDS = [
    r"insolvency",
    r"recovery box",
    r"trading (halt|resumption)",
    r"financial.*restructur",
    r"delist",
    r"write.?down",
    r"profit warning",
    r"revised.*guidance.*down",
    r"loss.*widen",
    r"default",
    r"impairment",
    r"dilut",
    r"new share capital",  # Often dilutive
]

# Categories with clear directional bias (from analysis)
CATEGORY_SIGNALS = {
    "TOTAL NUMBER OF VOTING RIGHTS": ("LONG", 0.6),  # +2.82%, 100% pos
    "EX DATE": ("LONG", 0.5),  # +0.26%, 67% pos
    "MAJOR SHAREHOLDING NOTIFICATIONS": ("LONG", 0.4),  # +0.15%, 58% pos
    "TRADING HALTS": ("SHORT", 0.6),  # Usually bad news
    "ANNOUNCEMENT FROM OSLO": ("SHORT", 0.5),  # Exchange announcements often negative
}


def classify_rule_based(announcement: Announcement) -> Prediction:
    """Classify using keyword matching and category signals.

    Returns a Prediction with direction and confidence.
    """
    title_lower = announcement.title.lower()
    category = announcement.category.upper()

    # Check positive keywords
    pos_score = 0.0
    pos_reasons = []
    for pattern in POSITIVE_KEYWORDS:
        if re.search(pattern, title_lower):
            pos_score += 0.15
            pos_reasons.append(pattern.split("(")[0].strip("r").strip("\\"))

    # Check negative keywords
    neg_score = 0.0
    neg_reasons = []
    for pattern in NEGATIVE_KEYWORDS:
        if re.search(pattern, title_lower):
            neg_score += 0.15
            neg_reasons.append(pattern.split("(")[0].strip("r").strip("\\"))

    # Check category signal
    for cat_key, (direction, weight) in CATEGORY_SIGNALS.items():
        if cat_key in category:
            if direction == "LONG":
                pos_score += weight
                pos_reasons.append(f"category:{cat_key[:20]}")
            else:
                neg_score += weight
                neg_reasons.append(f"category:{cat_key[:20]}")

    # Determine direction
    net = pos_score - neg_score
    if abs(net) < 0.1:
        return Prediction(
            ticker=announcement.ticker,
            direction="SKIP",
            confidence=0.0,
            reason="No clear signal",
            announcement_id=announcement.id,
        )

    if net > 0:
        return Prediction(
            ticker=announcement.ticker,
            direction="LONG",
            confidence=min(pos_score, 1.0),
            reason=f"Positive: {', '.join(pos_reasons)}",
            announcement_id=announcement.id,
        )
    else:
        return Prediction(
            ticker=announcement.ticker,
            direction="SHORT",
            confidence=min(neg_score, 1.0),
            reason=f"Negative: {', '.join(neg_reasons)}",
            announcement_id=announcement.id,
        )


def classify_with_llm(announcement: Announcement, api_key: str | None = None) -> Prediction:
    """Classify using Claude API to analyze announcement text.

    Sends the announcement title and category to Claude and asks for
    a directional prediction with reasoning.
    """
    import os

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.warning("No ANTHROPIC_API_KEY set, falling back to rule-based")
        return classify_rule_based(announcement)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)

        prompt = f"""You are an Oslo Stock Exchange analyst. Given this announcement, predict the stock's 1-hour price direction.

Ticker: {announcement.ticker}
Category: {announcement.category}
Title: {announcement.title}
Published: {announcement.published_at}

Respond with EXACTLY one of these formats:
LONG|0.7|reason here
SHORT|0.6|reason here
SKIP|0.0|reason here

The number is your confidence (0.0-1.0). Be concise."""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )

        response = message.content[0].text.strip()
        parts = response.split("|", 2)
        if len(parts) >= 3:
            direction = parts[0].strip().upper()
            confidence = float(parts[1].strip())
            reason = parts[2].strip()

            if direction not in ("LONG", "SHORT", "SKIP"):
                direction = "SKIP"
                confidence = 0.0

            return Prediction(
                ticker=announcement.ticker,
                direction=direction,
                confidence=confidence,
                reason=f"LLM: {reason}",
                announcement_id=announcement.id,
            )

    except Exception as e:
        log.error(f"LLM classification failed: {e}")

    return classify_rule_based(announcement)


def backtest_classifier(
    use_llm: bool = False,
    api_key: str | None = None,
) -> dict:
    """Backtest the classifier against historical data.

    Returns performance metrics.
    """
    from obs_react.db.operations import get_announcements, get_all_event_results

    announcements = get_announcements()
    results = get_all_event_results()

    # Build AR lookup: announcement_id -> 1h AR
    ar_map: dict[int, float] = {}
    for r in results:
        if r.window_name == "[0,+1h]" and r.abnormal_return is not None and r.data_quality != "missing":
            ar_map[r.announcement_id] = r.abnormal_return

    trades = []
    skipped = 0

    for ann in announcements:
        if ann.id not in ar_map:
            continue

        if use_llm:
            pred = classify_with_llm(ann, api_key)
        else:
            pred = classify_rule_based(ann)

        actual_ar = ar_map[ann.id]

        if pred.direction == "SKIP":
            skipped += 1
            continue

        if pred.direction == "LONG":
            trade_return = actual_ar
        else:  # SHORT
            trade_return = -actual_ar

        trades.append({
            "ticker": ann.ticker,
            "direction": pred.direction,
            "confidence": pred.confidence,
            "reason": pred.reason,
            "actual_ar": actual_ar,
            "trade_return": trade_return,
            "title": ann.title[:50],
        })

    if not trades:
        return {"error": "No trades generated", "skipped": skipped}

    returns = [t["trade_return"] for t in trades]
    wins = len([r for r in returns if r > 0])

    return {
        "total_events": len(announcements),
        "events_with_data": len(ar_map),
        "trades": len(trades),
        "skipped": skipped,
        "win_rate": wins / len(trades),
        "mean_return": sum(returns) / len(returns),
        "total_return": sum(returns),
        "best_trade": max(returns),
        "worst_trade": min(returns),
        "sample_trades": trades[:10],
    }
