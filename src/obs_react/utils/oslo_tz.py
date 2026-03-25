"""Europe/Oslo timezone helpers and trading hours detection."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

OSLO_TZ = ZoneInfo("Europe/Oslo")
UTC = ZoneInfo("UTC")

TRADING_OPEN = time(9, 0)
TRADING_CLOSE = time(16, 20)


def to_utc(dt: datetime) -> datetime:
    """Convert a datetime to UTC. If naive, assume Oslo time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=OSLO_TZ)
    return dt.astimezone(UTC)


def to_oslo(dt: datetime) -> datetime:
    """Convert a datetime to Europe/Oslo. If naive, assume UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(OSLO_TZ)


def is_trading_hours(dt: datetime) -> bool:
    """Check if a datetime falls within Oslo Bors trading hours (weekday 09:00-16:20 CET)."""
    oslo_dt = to_oslo(dt)
    if oslo_dt.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = oslo_dt.time()
    return TRADING_OPEN <= t < TRADING_CLOSE


def next_trading_open(dt: datetime) -> datetime:
    """Return the next trading session open (09:00 Oslo time) at or after dt."""
    oslo_dt = to_oslo(dt)
    # If before open today on a weekday, return today's open
    if oslo_dt.weekday() < 5 and oslo_dt.time() < TRADING_OPEN:
        return oslo_dt.replace(
            hour=TRADING_OPEN.hour, minute=TRADING_OPEN.minute, second=0, microsecond=0
        )
    # Otherwise advance to next weekday
    days_ahead = 1
    next_dt = oslo_dt + timedelta(days=days_ahead)
    while next_dt.weekday() >= 5:
        next_dt += timedelta(days=1)
    return next_dt.replace(
        hour=TRADING_OPEN.hour, minute=TRADING_OPEN.minute, second=0, microsecond=0
    )


def trading_seconds_between(start: datetime, end: datetime) -> int:
    """Count the number of seconds during trading hours between two datetimes."""
    start_utc = to_utc(start)
    end_utc = to_utc(end)
    if start_utc >= end_utc:
        return 0

    total = 0
    current = start_utc
    while current < end_utc:
        if is_trading_hours(current):
            step = min(timedelta(seconds=1), end_utc - current)
            total += int(step.total_seconds())
            current += step
        else:
            # Jump to next trading open
            nto = next_trading_open(current)
            if nto <= current:
                nto = next_trading_open(current + timedelta(days=1))
            current = nto if nto < end_utc else end_utc

    return total


def parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 datetime string, returning a timezone-aware datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
