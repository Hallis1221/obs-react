"""Playwright-based NewsWeb scraper with API interception."""

from __future__ import annotations

import json
import logging
import re
import time as _time
from datetime import datetime, timezone

from obs_react.config import NEWSWEB_BASE_URL, NEWSWEB_RATE_LIMIT
from obs_react.utils.oslo_tz import to_utc, OSLO_TZ

log = logging.getLogger(__name__)

# Known API patterns discovered from NewsWeb SPA
_API_MESSAGE_LIST = "https://newsweb.oslobors.no/search/category"
_API_ENDPOINTS: list[str] = []


def discover_api() -> list[str]:
    """Launch a headless browser, navigate to NewsWeb, and intercept API calls.

    Returns a list of discovered API endpoint URLs.
    """
    from playwright.sync_api import sync_playwright

    discovered: list[str] = []

    def _on_response(response):
        url = response.url
        if "api" in url.lower() or response.request.resource_type in ("xhr", "fetch"):
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                discovered.append(url)
                log.info(f"Discovered API endpoint: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", _on_response)

        log.info(f"Navigating to {NEWSWEB_BASE_URL}")
        page.goto(NEWSWEB_BASE_URL, wait_until="networkidle", timeout=30000)
        _time.sleep(3)

        # Scroll down to trigger more API calls
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        _time.sleep(2)

        browser.close()

    log.info(f"Discovered {len(discovered)} API endpoints")
    for ep in discovered:
        log.info(f"  - {ep}")

    _API_ENDPOINTS.clear()
    _API_ENDPOINTS.extend(discovered)
    return discovered


def _scrape_via_api(since: datetime | None, max_pages: int) -> list[dict]:
    """Try to fetch announcements via the discovered JSON API."""
    import httpx

    announcements = []
    # NewsWeb uses a search endpoint; we'll try common patterns
    search_url = f"{NEWSWEB_BASE_URL}/search/category"

    for page_num in range(1, max_pages + 1):
        params = {"page": page_num}
        log.info(f"Fetching API page {page_num}: {search_url}")
        try:
            resp = httpx.get(search_url, params=params, timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                log.warning(f"API returned {resp.status_code}, falling back to DOM scraping")
                return []

            data = resp.json()
            messages = _extract_messages_from_json(data)
            if not messages:
                log.info("No more messages found, stopping pagination")
                break

            for msg in messages:
                if since and msg.get("published_at"):
                    pub = datetime.fromisoformat(msg["published_at"])
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    if pub < since:
                        log.info(f"Reached messages before {since}, stopping")
                        announcements.extend([m for m in messages if _is_after(m, since)])
                        return announcements
                announcements.append(msg)

            _time.sleep(NEWSWEB_RATE_LIMIT)
        except Exception as e:
            log.warning(f"API request failed: {e}, falling back to DOM scraping")
            return []

    return announcements


def _is_after(msg: dict, since: datetime) -> bool:
    pub = msg.get("published_at", "")
    if not pub:
        return True
    dt = datetime.fromisoformat(pub)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= since


def _extract_messages_from_json(data: dict | list) -> list[dict]:
    """Extract announcement records from various JSON response shapes."""
    messages = []

    # Try common response shapes
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (
            data.get("messages")
            or data.get("data")
            or data.get("results")
            or data.get("items")
            or data.get("content")
            or []
        )
        if not isinstance(items, list):
            items = [data]
    else:
        return []

    for item in items:
        if not isinstance(item, dict):
            continue
        msg = _normalize_message(item)
        if msg:
            messages.append(msg)

    return messages


def _normalize_message(item: dict) -> dict | None:
    """Normalize a raw JSON message dict to our standard format."""
    # Try various field name patterns
    message_id = (
        item.get("messageId")
        or item.get("message_id")
        or item.get("id")
        or item.get("disclosureId")
    )
    if message_id is None:
        return None
    message_id = str(message_id)

    ticker = (
        item.get("ticker")
        or item.get("issuerSign")
        or item.get("issuer_sign")
        or _extract_ticker_from_item(item)
        or ""
    )

    published_at = (
        item.get("publishedTime")
        or item.get("published_at")
        or item.get("publishedAt")
        or item.get("published")
        or item.get("dateTime")
        or ""
    )
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            published_at = to_utc(dt).isoformat()
        except (ValueError, TypeError):
            pass

    category = (
        item.get("category")
        or item.get("categoryName")
        or item.get("category_name")
        or item.get("type")
        or "UNKNOWN"
    )

    title = (
        item.get("title")
        or item.get("headline")
        or item.get("subject")
        or ""
    )

    url = item.get("url") or item.get("link") or ""
    if not url and message_id:
        url = f"{NEWSWEB_BASE_URL}/message/{message_id}"

    issuer_name = (
        item.get("issuerName")
        or item.get("issuer_name")
        or item.get("companyName")
        or item.get("company")
    )

    if not ticker:
        return None

    return {
        "message_id": message_id,
        "ticker": ticker.upper().strip(),
        "published_at": published_at,
        "category": category,
        "title": title,
        "url": url,
        "issuer_name": issuer_name,
    }


def _extract_ticker_from_item(item: dict) -> str | None:
    """Try to extract a ticker from nested structures."""
    issuer = item.get("issuer") or {}
    if isinstance(issuer, dict):
        return issuer.get("sign") or issuer.get("ticker")
    return None


def _scrape_via_dom(since: datetime | None, max_pages: int) -> list[dict]:
    """Fallback: render the page and scrape the DOM with Playwright."""
    from playwright.sync_api import sync_playwright

    announcements = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for page_num in range(1, max_pages + 1):
            url = NEWSWEB_BASE_URL
            if page_num > 1:
                url = f"{NEWSWEB_BASE_URL}?page={page_num}"

            log.info(f"Scraping DOM page {page_num}: {url}")
            page.goto(url, wait_until="networkidle", timeout=30000)
            _time.sleep(2)

            # Wait for the message list to appear
            try:
                page.wait_for_selector(
                    "table, .message-list, [class*='message'], [class*='disclosure']",
                    timeout=10000,
                )
            except Exception:
                log.warning("Could not find message elements on page")
                break

            # Try to extract data from table rows or list items
            rows = page.query_selector_all(
                "table tbody tr, .message-list .message, [class*='message-row']"
            )

            if not rows:
                log.info("No rows found, trying alternative selectors")
                rows = page.query_selector_all("a[href*='/message/']")

            if not rows:
                log.info("No more messages found")
                break

            page_messages = []
            for row in rows:
                msg = _parse_dom_row(row, page)
                if msg:
                    if since and msg.get("published_at"):
                        dt = datetime.fromisoformat(msg["published_at"])
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < since:
                            announcements.extend(page_messages)
                            browser.close()
                            return announcements
                    page_messages.append(msg)

            announcements.extend(page_messages)
            log.info(f"Found {len(page_messages)} messages on page {page_num}")
            _time.sleep(NEWSWEB_RATE_LIMIT)

        browser.close()

    return announcements


def _parse_dom_row(element, page) -> dict | None:
    """Parse a DOM element into an announcement dict."""
    try:
        text = element.inner_text()
        href = element.get_attribute("href") or ""

        # Try to extract message_id from href
        match = re.search(r"/message/(\d+)", href)
        message_id = match.group(1) if match else None

        if not message_id:
            # Try from child links
            link = element.query_selector("a[href*='/message/']")
            if link:
                href = link.get_attribute("href") or ""
                match = re.search(r"/message/(\d+)", href)
                message_id = match.group(1) if match else None

        if not message_id:
            return None

        # Split text into fields — layout varies
        parts = [p.strip() for p in text.split("\n") if p.strip()]

        # Heuristic extraction
        ticker = ""
        title = ""
        category = ""
        published_at = ""

        for part in parts:
            # Date patterns
            if re.match(r"\d{4}-\d{2}-\d{2}", part) or re.match(r"\d{2}\.\d{2}\.\d{4}", part):
                published_at = part
            elif re.match(r"^[A-Z]{2,10}$", part):
                ticker = part
            elif len(part) > 30:
                title = part
            elif not category and part.isupper() and len(part) > 3:
                category = part

        if not ticker and parts:
            ticker = parts[0]

        if published_at:
            try:
                for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        dt = datetime.strptime(published_at, fmt)
                        dt = dt.replace(tzinfo=OSLO_TZ)
                        published_at = to_utc(dt).isoformat()
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        url = f"{NEWSWEB_BASE_URL}/message/{message_id}" if not href.startswith("http") else href

        return {
            "message_id": message_id,
            "ticker": ticker.upper().strip(),
            "published_at": published_at,
            "category": category or "UNKNOWN",
            "title": title or "(no title)",
            "url": url,
            "issuer_name": None,
        }
    except Exception as e:
        log.debug(f"Failed to parse DOM row: {e}")
        return None


def scrape_announcements(
    since: datetime | None = None, max_pages: int = 10
) -> list[dict]:
    """Fetch announcements from NewsWeb. Tries API first, falls back to DOM scraping."""
    log.info(f"Scraping NewsWeb announcements (since={since}, max_pages={max_pages})")

    # Try API approach first
    results = _scrape_via_api(since, max_pages)
    if results:
        log.info(f"Got {len(results)} announcements via API")
        return results

    # Fallback to DOM scraping
    log.info("API approach failed or returned no results, trying DOM scraping")
    results = _scrape_via_dom(since, max_pages)
    log.info(f"Got {len(results)} announcements via DOM scraping")
    return results


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
