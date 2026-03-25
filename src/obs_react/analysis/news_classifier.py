"""Advanced news classifier using full announcement body text.

Classifies announcements into tradeable signals by analyzing:
1. News type (contract win, earnings, insider trade, dilution, etc.)
2. Sentiment/magnitude indicators in the text
3. Historical performance of similar news types
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class NewsClassification:
    direction: str  # "LONG", "SHORT", "SKIP"
    confidence: float  # 0.0 to 1.0
    news_type: str  # e.g. "contract_win", "dilution", "insider_buy"
    reason: str
    magnitude: str  # "high", "medium", "low"


# High-impact positive patterns (from body text analysis)
STRONG_POSITIVE = [
    # Contract wins with value mentioned
    (r"awarded.*contract", "contract_win", 0.7),
    (r"new (contract|order|agreement).*(?:USD|NOK|EUR|million|billion)", "contract_win", 0.75),
    (r"substantial\*?\s*contract", "contract_win", 0.7),
    (r"secures.*(?:NOK|USD|EUR)\s*\d+.*(?:order|contract)", "contract_win", 0.75),
    # Earnings beats
    (r"(record|strong|significant).*(revenue|earnings|profit|ebitda)", "earnings_beat", 0.65),
    (r"(exceed|above|beat).*(expectation|estimate|consensus|guidance)", "earnings_beat", 0.7),
    (r"(increase|raise|upgrade).*(?:dividend|guidance|outlook|forecast)", "guidance_up", 0.65),
    (r"trading update.*(?:strong|positive|ahead|above)", "earnings_beat", 0.65),
    # Dividends declared (strong positive for small caps)
    (r"dividend.*(?:declared|proposed|approved).*(?:NOK|per share)", "dividend_declared", 0.7),
    (r"ex.?(?:date|dividend).*(?:NOK|utbytte)", "dividend_ex", 0.55),
    # Strategic positive
    (r"(strategic|transformative).*(acquisition|partnership|alliance)", "strategic_positive", 0.6),
    (r"(merger|acquisition).*(?:agreed|completed|closed)", "ma_positive", 0.6),
    # Capital raise COMPLETED (often positive — shows demand)
    (r"final results.*(?:subsequent offer|private placement|subscription)", "capital_raise_done", 0.6),
    (r"(?:oversubscribed|fully subscribed)", "capital_raise_done", 0.65),
    (r"disclosure of large shareholding", "major_holder_buying", 0.55),
    # Insider buying (not selling)
    (r"(buy|purchase|acquisition).*(?:shares|stock).*(?:primary insider|pdmr|board|ceo|cfo)", "insider_buy", 0.6),
    # Buyback
    (r"share (buy.?back|repurchase).*(?:program|programme)", "buyback", 0.55),
    # Listing / IPO positive
    (r"first day of trading", "ipo_listing", 0.5),
    # Robust/positive clinical/product results
    (r"(robust|positive|promising).*(immunogenicity|efficacy|data|results)", "positive_results", 0.6),
    # Underwriting (shows strong investor demand)
    (r"underwr(?:iting|itten).*(?:\d+%|fully)", "underwriting", 0.6),
]

# High-impact negative patterns
STRONG_NEGATIVE = [
    # Financial distress
    (r"insolvenc", "insolvency", 0.9),
    (r"recovery box", "distress", 0.85),
    (r"(liquidat|bankrupt|wind.?up)", "distress", 0.85),
    (r"(default|cross.default|breach of covenant)", "default", 0.8),
    (r"(restructur|refinanc).*(?:debt|loan|bond)", "restructuring", 0.65),
    (r"update on previously announced financial", "financial_distress", 0.8),
    # Bond/debt issues
    (r"(?:bond coupon|coupon payment).*(?:multi.currency|subordinated)", "bond_distress", 0.65),
    # Dilution — capital raises (often negative on announcement)
    (r"(private placement|share issue|new share capital).*(?:NOK|shares)", "dilution", 0.6),
    (r"(dilut|capital increase)", "dilution", 0.55),
    (r"commencement of subscription period.*(?:rights issue|offer)", "capital_raise", 0.6),
    (r"share option.*(?:grant|exercise).*(?:incentive|employee)", "option_dilution", 0.55),
    # Earnings miss
    (r"(below|miss|disappoint).*(expectation|estimate|guidance)", "earnings_miss", 0.7),
    (r"(profit warning|revised.*guidance.*down|lower.*outlook)", "profit_warning", 0.75),
    (r"(impairment|write.?down|provision)", "writedown", 0.6),
    (r"fourth quarter report.*(?:loss|negative|decline)", "earnings_miss", 0.65),
    # Insider selling
    (r"(sale|sold|disposal).*(?:shares|stock).*(?:primary insider|pdmr|board|ceo|cfo)", "insider_sell", 0.55),
    # Trading halt / suspension (usually bad)
    (r"trading (halt|suspension|resumption)", "halt", 0.65),
    # Delisting
    (r"(delist|strykning)", "delisting", 0.65),
    # Loss / negative results
    (r"(net loss|operating loss|negative)", "loss", 0.5),
    # Major holder SELLING (Goldman etc. disclosing = often selling)
    (r"(goldman sachs|morgan stanley|jpmorgan).*disclosure", "major_holder_selling", 0.55),
]

# Noise patterns — skip these
NOISE_PATTERNS = [
    r"adjustment of interest rate",
    r"financial calendar",
    r"annual general meeting.*notice",
    r"innkalling til.*generalforsamling",
    r"protokoll.*generalforsamling",
    r"total number of voting rights",
    r"endring i antall aksjer",
    r"new isin",
    r"status share buy.?back programme",  # Routine status, not new program
]

# Magnitude indicators
HIGH_MAGNITUDE = [
    r"(?:USD|NOK|EUR)\s*\d+\s*(?:billion|milliard)",
    r"(?:USD|NOK|EUR)\s*[1-9]\d{2,}\s*million",  # >100M
    r"\d+%.*(?:increase|growth|decline|drop)",
    r"(substantial|significant|material|transformative)",
    r"(record|all.time|historic)",
]

MEDIUM_MAGNITUDE = [
    r"(?:USD|NOK|EUR)\s*\d+\s*million",
    r"\d+%",
    r"(strong|solid|robust|improved|weaker)",
]


def classify_announcement(title: str, body: str, category: str) -> NewsClassification:
    """Classify an announcement using full text analysis."""
    text = f"{title}\n{body}".lower()
    category_lower = category.lower()

    # Check noise first — skip routine announcements
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, text):
            return NewsClassification(
                direction="SKIP", confidence=0.0,
                news_type="noise", reason=f"Routine: {pattern[:30]}",
                magnitude="low",
            )

    # Score positive signals
    pos_score = 0.0
    pos_type = "unknown"
    pos_reason = ""
    for pattern, ntype, weight in STRONG_POSITIVE:
        if re.search(pattern, text):
            if weight > pos_score:
                pos_score = weight
                pos_type = ntype
                pos_reason = pattern[:40]

    # Score negative signals
    neg_score = 0.0
    neg_type = "unknown"
    neg_reason = ""
    for pattern, ntype, weight in STRONG_NEGATIVE:
        if re.search(pattern, text):
            if weight > neg_score:
                neg_score = weight
                neg_type = ntype
                neg_reason = pattern[:40]

    # Determine magnitude
    magnitude = "low"
    for pattern in HIGH_MAGNITUDE:
        if re.search(pattern, text):
            magnitude = "high"
            break
    if magnitude == "low":
        for pattern in MEDIUM_MAGNITUDE:
            if re.search(pattern, text):
                magnitude = "medium"
                break

    # Boost confidence for high-magnitude events
    mag_boost = {"high": 0.15, "medium": 0.05, "low": 0.0}[magnitude]

    # Make decision
    if pos_score > neg_score and pos_score >= 0.5:
        return NewsClassification(
            direction="LONG",
            confidence=min(pos_score + mag_boost, 1.0),
            news_type=pos_type,
            reason=pos_reason,
            magnitude=magnitude,
        )
    elif neg_score > pos_score and neg_score >= 0.5:
        return NewsClassification(
            direction="SHORT",
            confidence=min(neg_score + mag_boost, 1.0),
            news_type=neg_type,
            reason=neg_reason,
            magnitude=magnitude,
        )

    # No strong signal — check if there's a weak signal worth trading
    if pos_score > 0.3 and pos_score > neg_score + 0.1:
        return NewsClassification(
            direction="LONG", confidence=pos_score,
            news_type=pos_type, reason=f"Weak: {pos_reason}",
            magnitude=magnitude,
        )
    if neg_score > 0.3 and neg_score > pos_score + 0.1:
        return NewsClassification(
            direction="SHORT", confidence=neg_score,
            news_type=neg_type, reason=f"Weak: {neg_reason}",
            magnitude=magnitude,
        )

    return NewsClassification(
        direction="SKIP", confidence=0.0,
        news_type="unclear", reason="No clear signal",
        magnitude=magnitude,
    )


def classify_with_llm(title: str, body: str, category: str,
                      api_key: str | None = None) -> NewsClassification:
    """Classify using Claude API for nuanced text understanding."""
    import os
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return classify_announcement(title, body, category)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)

        prompt = f"""You are a quantitative analyst for Oslo Stock Exchange. Analyze this announcement and predict the stock's 1-hour price direction.

Category: {category}
Title: {title}

Body (first 1500 chars):
{body[:1500]}

Consider:
- Is this genuinely market-moving or routine noise?
- What is the likely magnitude of price impact?
- Is the direction clear from the text?

Respond in EXACTLY this format (one line):
DIRECTION|CONFIDENCE|NEWS_TYPE|MAGNITUDE|REASON

Where:
- DIRECTION: LONG, SHORT, or SKIP
- CONFIDENCE: 0.0-1.0
- NEWS_TYPE: contract_win, earnings_beat, insider_buy, dilution, distress, etc.
- MAGNITUDE: high, medium, low
- REASON: brief explanation (max 50 chars)

Only predict LONG or SHORT if you are genuinely confident. Default to SKIP for routine noise."""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )

        resp = message.content[0].text.strip().split("\n")[0]
        parts = resp.split("|")
        if len(parts) >= 5:
            return NewsClassification(
                direction=parts[0].strip().upper(),
                confidence=float(parts[1].strip()),
                news_type=parts[2].strip(),
                magnitude=parts[3].strip(),
                reason=f"LLM: {parts[4].strip()[:50]}",
            )
    except Exception as e:
        log.error(f"LLM classification failed: {e}")

    return classify_announcement(title, body, category)
