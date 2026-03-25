"""Continuous polling loop for NewsWeb announcements."""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone

from obs_react.config import NEWSWEB_POLL_INTERVAL
from obs_react.db.operations import (
    get_known_message_ids,
    insert_announcement,
    log_fetch_start,
    log_fetch_end,
)
from obs_react.news.scraper import scrape_announcements, parse_announcement

log = logging.getLogger(__name__)


def poll_once() -> int:
    """Fetch latest announcements, insert new ones, stop at known message_ids.

    Returns the number of new announcements inserted.
    """
    fetch_id = log_fetch_start("newsweb")
    new_count = 0
    error = None

    try:
        known_ids = get_known_message_ids()
        log.info(f"Known message IDs: {len(known_ids)}")

        raw_announcements = scrape_announcements(max_pages=5)
        log.info(f"Fetched {len(raw_announcements)} raw announcements")

        for raw in raw_announcements:
            parsed = parse_announcement(raw)
            if parsed["message_id"] in known_ids:
                continue

            row_id = insert_announcement(**parsed)
            if row_id is not None:
                new_count += 1
                log.info(
                    f"New: [{parsed['ticker']}] {parsed['title'][:60]} "
                    f"(id={parsed['message_id']})"
                )

        log.info(f"Inserted {new_count} new announcements")
    except Exception as e:
        error = str(e)
        log.error(f"Poll failed: {e}")
        raise
    finally:
        log_fetch_end(fetch_id, records_fetched=new_count, error_message=error)

    return new_count


def poll_loop(interval_seconds: int | None = None) -> None:
    """Continuously poll NewsWeb at the configured interval."""
    interval = interval_seconds or NEWSWEB_POLL_INTERVAL
    log.info(f"Starting continuous polling (interval={interval}s)")

    while True:
        try:
            count = poll_once()
            log.info(f"Poll complete: {count} new. Next poll in {interval}s")
        except KeyboardInterrupt:
            log.info("Polling stopped by user")
            break
        except Exception as e:
            log.error(f"Poll error: {e}. Retrying in {interval}s")

        _time.sleep(interval)
