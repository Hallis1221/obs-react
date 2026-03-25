"""NewsWeb scraper using the Oslo Bors JSON API."""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone

import httpx

from obs_react.config import NEWSWEB_BASE_URL, NEWSWEB_RATE_LIMIT
from obs_react.utils.oslo_tz import to_utc, OSLO_TZ

log = logging.getLogger(__name__)

# Discovered API base (from newsweb.oslobors.no/urls.json)
API_BASE = "https://api3.oslo.oslobors.no/v1/newsreader"
API_LIST = f"{API_BASE}/list"
API_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://newsweb.oslobors.no",
}


def _fetch_api_page(from_date: str, to_date: str) -> list[dict]:
    """POST to the newsreader /list endpoint for a date range.

    Returns raw message dicts. The API caps at ~601 results per call,
    so use narrow date windows for full coverage.
    """
    params = {"fromDate": from_date, "toDate": to_date}
    log.info(f"API fetch: {from_date} to {to_date}")
    resp = httpx.post(API_LIST, params=params, headers=API_HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    # Response shape: {"header": {...}, "data": {"messages": [...], "overflow": bool}}
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner.get("messages") or []
        return inner if isinstance(inner, list) else []
    if isinstance(data, list):
        return data
    return []


def scrape_announcements(
    since: datetime | None = None,
    max_pages: int = 10,
    days_back: int = 7,
) -> list[dict]:
    """Fetch announcements from the NewsWeb API.

    Iterates day-by-day from `since` (or `days_back` days ago) to today.
    """
    now = datetime.now(timezone.utc)
    if since is None:
        since = now - timedelta(days=days_back)

    announcements: list[dict] = []
    current = since.date() if hasattr(since, "date") else since
    end_date = now.date() if hasattr(now, "date") else now

    # Iterate day by day to avoid the 601-result cap
    day = current
    while day <= end_date:
        day_str = day.isoformat()
        try:
            raw_messages = _fetch_api_page(day_str, day_str)
            for item in raw_messages:
                msg = _normalize_api_message(item)
                if msg:
                    announcements.append(msg)
            log.info(f"  {day_str}: {len(raw_messages)} messages")
        except Exception as e:
            log.error(f"API fetch failed for {day_str}: {e}")

        day += timedelta(days=1)
        if day <= end_date:
            _time.sleep(NEWSWEB_RATE_LIMIT)

    log.info(f"Total: {len(announcements)} announcements from API")
    return announcements


def _normalize_api_message(item: dict) -> dict | None:
    """Normalize a raw API message dict to our standard format."""
    message_id = item.get("messageId")
    if message_id is None:
        return None
    message_id = str(message_id)

    ticker = item.get("issuerSign") or ""
    if not ticker:
        return None

    published_at = item.get("publishedTime") or ""
    if published_at:
        try:
            # API returns ISO format like "2026-03-25T11:18:00+01:00"
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            published_at = to_utc(dt).isoformat()
        except (ValueError, TypeError):
            pass

    # Category is a list of dicts: [{"id": 1102, "category_en": "...", "category_no": "..."}]
    categories = item.get("category") or []
    if isinstance(categories, list) and categories:
        # Use English category name
        cat_names = []
        for c in categories:
            if isinstance(c, dict):
                cat_names.append(c.get("category_en") or c.get("category_no") or "")
            else:
                cat_names.append(str(c))
        category = ", ".join(n for n in cat_names if n) or "UNKNOWN"
    elif isinstance(categories, str):
        category = categories
    else:
        category = "UNKNOWN"

    title = item.get("title") or ""
    issuer_name = item.get("issuerName")
    url = f"{NEWSWEB_BASE_URL}/message/{message_id}"

    return {
        "message_id": message_id,
        "ticker": ticker.upper().strip(),
        "published_at": published_at,
        "category": category,
        "title": title,
        "url": url,
        "issuer_name": issuer_name,
    }


def parse_announcement(raw: dict) -> dict:
    """Normalize a raw announcement dict for database insertion."""
    return {
        "message_id": str(raw["message_id"]),
        "ticker": raw["ticker"].upper().strip(),
        "published_at": raw.get("published_at", ""),
        "category": raw.get("category", "UNKNOWN"),
        "title": raw.get("title", ""),
        "url": raw.get("url", ""),
        "issuer_name": raw.get("issuer_name"),
    }
