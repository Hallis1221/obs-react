"""Tests for configuration module."""

from obs_react.config import (
    MCAP_BUCKETS,
    VOLUME_BUCKETS,
    EVENT_WINDOWS,
    BENCHMARK_TICKER,
)


def test_mcap_buckets_cover_range():
    """Market cap buckets should cover 0 to infinity without gaps."""
    sorted_buckets = sorted(MCAP_BUCKETS.items(), key=lambda x: x[1][0])
    assert sorted_buckets[0][1][0] == 0
    assert sorted_buckets[-1][1][1] == float("inf")
    for i in range(1, len(sorted_buckets)):
        assert sorted_buckets[i][1][0] == sorted_buckets[i - 1][1][1]


def test_volume_buckets_cover_range():
    """Volume buckets should cover 0 to infinity without gaps."""
    sorted_buckets = sorted(VOLUME_BUCKETS.items(), key=lambda x: x[1][0])
    assert sorted_buckets[0][1][0] == 0
    assert sorted_buckets[-1][1][1] == float("inf")
    for i in range(1, len(sorted_buckets)):
        assert sorted_buckets[i][1][0] == sorted_buckets[i - 1][1][1]


def test_event_windows_format():
    """Each window should be (name, start_offset, end_offset)."""
    for name, start, end in EVENT_WINDOWS:
        assert isinstance(name, str)
        assert isinstance(start, int)
        assert isinstance(end, int)
        assert end > start


def test_benchmark_ticker():
    assert BENCHMARK_TICKER.endswith(".OL")
