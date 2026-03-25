"""Tests for timezone utilities."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from obs_react.utils.oslo_tz import (
    to_utc,
    to_oslo,
    is_trading_hours,
    next_trading_open,
    trading_seconds_between,
    parse_iso,
    OSLO_TZ,
    UTC,
)


class TestToUtc:
    def test_naive_assumes_oslo(self):
        naive = datetime(2025, 3, 20, 10, 0)  # 10:00 Oslo
        utc = to_utc(naive)
        assert utc.tzinfo == UTC
        # Oslo is CET (UTC+1) in March
        assert utc.hour == 9

    def test_aware_converts(self):
        oslo = datetime(2025, 3, 20, 10, 0, tzinfo=OSLO_TZ)
        utc = to_utc(oslo)
        assert utc.tzinfo == UTC


class TestToOslo:
    def test_naive_assumes_utc(self):
        naive = datetime(2025, 3, 20, 9, 0)
        oslo = to_oslo(naive)
        assert oslo.tzinfo == OSLO_TZ
        assert oslo.hour == 10  # UTC+1

    def test_aware_converts(self):
        utc_dt = datetime(2025, 3, 20, 9, 0, tzinfo=UTC)
        oslo = to_oslo(utc_dt)
        assert oslo.hour == 10


class TestIsTradingHours:
    def test_during_trading(self):
        dt = datetime(2025, 3, 20, 10, 0, tzinfo=OSLO_TZ)  # Thursday 10:00
        assert is_trading_hours(dt) is True

    def test_before_open(self):
        dt = datetime(2025, 3, 20, 8, 30, tzinfo=OSLO_TZ)
        assert is_trading_hours(dt) is False

    def test_after_close(self):
        dt = datetime(2025, 3, 20, 16, 30, tzinfo=OSLO_TZ)
        assert is_trading_hours(dt) is False

    def test_at_close(self):
        dt = datetime(2025, 3, 20, 16, 20, tzinfo=OSLO_TZ)
        assert is_trading_hours(dt) is False

    def test_weekend(self):
        dt = datetime(2025, 3, 22, 10, 0, tzinfo=OSLO_TZ)  # Saturday
        assert is_trading_hours(dt) is False

    def test_utc_input(self):
        # 10:00 Oslo = 09:00 UTC in CET
        dt = datetime(2025, 3, 20, 9, 0, tzinfo=UTC)
        assert is_trading_hours(dt) is True


class TestNextTradingOpen:
    def test_before_open_today(self):
        dt = datetime(2025, 3, 20, 7, 0, tzinfo=OSLO_TZ)  # Thursday 7am
        nto = next_trading_open(dt)
        assert nto.hour == 9
        assert nto.minute == 0
        assert nto.day == 20

    def test_after_close_today(self):
        dt = datetime(2025, 3, 20, 17, 0, tzinfo=OSLO_TZ)  # Thursday 5pm
        nto = next_trading_open(dt)
        assert nto.day == 21  # Friday

    def test_friday_after_close(self):
        dt = datetime(2025, 3, 21, 17, 0, tzinfo=OSLO_TZ)  # Friday 5pm
        nto = next_trading_open(dt)
        assert nto.weekday() == 0  # Monday
        assert nto.day == 24

    def test_saturday(self):
        dt = datetime(2025, 3, 22, 10, 0, tzinfo=OSLO_TZ)  # Saturday
        nto = next_trading_open(dt)
        assert nto.weekday() == 0  # Monday


class TestParseIso:
    def test_with_timezone(self):
        dt = parse_iso("2025-03-20T10:30:00+01:00")
        assert dt.tzinfo is not None

    def test_naive_gets_utc(self):
        dt = parse_iso("2025-03-20T10:30:00")
        assert dt.tzinfo == UTC

    def test_with_z(self):
        dt = parse_iso("2025-03-20T10:30:00+00:00")
        assert dt.hour == 10
