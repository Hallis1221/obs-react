"""Aggregate event results by market cap and volume buckets, with significance tests."""

from __future__ import annotations

import logging
from collections import defaultdict

from scipy import stats

from obs_react.db.operations import (
    get_all_event_results,
    get_all_stock_meta,
    get_stock_meta,
)
from obs_react.models import EventResult, StockMeta

log = logging.getLogger(__name__)


def _group_results_by_bucket(
    results: list[EventResult],
    meta_map: dict[str, StockMeta],
    bucket_type: str,
) -> dict[str, list[EventResult]]:
    """Group event results by the specified bucket type."""
    groups: dict[str, list[EventResult]] = defaultdict(list)
    for r in results:
        meta = meta_map.get(r.ticker)
        if meta is None:
            groups["unknown"].append(r)
            continue
        bucket = getattr(meta, f"{bucket_type}_bucket", None)
        if bucket is None:
            groups["unknown"].append(r)
        else:
            groups[bucket].append(r)
    return groups


def _compute_stats(values: list[float]) -> dict:
    """Compute summary statistics for a list of values."""
    if not values:
        return {"count": 0, "mean": None, "median": None, "std": None, "min": None, "max": None}
    n = len(values)
    sorted_v = sorted(values)
    mean = sum(values) / n
    median = sorted_v[n // 2] if n % 2 == 1 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
    return {
        "count": n,
        "mean": mean,
        "median": median,
        "std": variance ** 0.5,
        "min": min(values),
        "max": max(values),
    }


def aggregate_by_bucket(bucket_type: str = "market_cap") -> list[dict]:
    """Aggregate event results by market_cap or volume bucket.

    Returns a list of dicts with bucket stats per window.
    """
    results = get_all_event_results()
    all_meta = get_all_stock_meta()
    meta_map = {m.ticker: m for m in all_meta}

    groups = _group_results_by_bucket(results, meta_map, bucket_type)
    output = []

    for bucket, bucket_results in sorted(groups.items()):
        # Group by window within each bucket
        by_window: dict[str, list[EventResult]] = defaultdict(list)
        for r in bucket_results:
            by_window[r.window_name].append(r)

        for window, w_results in sorted(by_window.items()):
            rt_values = [r.reaction_time_seconds for r in w_results if r.reaction_time_seconds is not None]
            ar_values = [r.abnormal_return for r in w_results if r.abnormal_return is not None]

            output.append({
                "bucket_type": bucket_type,
                "bucket": bucket,
                "window": window,
                "event_count": len(w_results),
                "reaction_time": _compute_stats(rt_values),
                "abnormal_return": _compute_stats(ar_values),
            })

    return output


def cross_bucket_analysis() -> dict:
    """2D grid analysis: market_cap x volume buckets.

    Returns dict keyed by (mcap_bucket, vol_bucket) with aggregated stats.
    """
    results = get_all_event_results()
    all_meta = get_all_stock_meta()
    meta_map = {m.ticker: m for m in all_meta}

    grid: dict[tuple[str, str], list[EventResult]] = defaultdict(list)
    for r in results:
        meta = meta_map.get(r.ticker)
        mcap = meta.market_cap_bucket if meta else "unknown"
        vol = meta.volume_bucket if meta else "unknown"
        grid[(mcap or "unknown", vol or "unknown")].append(r)

    output = {}
    for (mcap, vol), cell_results in sorted(grid.items()):
        rt_values = [r.reaction_time_seconds for r in cell_results if r.reaction_time_seconds is not None]
        ar_values = [r.abnormal_return for r in cell_results if r.abnormal_return is not None]
        output[f"{mcap}/{vol}"] = {
            "market_cap_bucket": mcap,
            "volume_bucket": vol,
            "event_count": len(cell_results),
            "reaction_time": _compute_stats(rt_values),
            "abnormal_return": _compute_stats(ar_values),
        }

    return output


def significance_test(bucket_a_values: list[float], bucket_b_values: list[float]) -> dict:
    """Welch's t-test comparing two sets of values.

    Returns dict with t_statistic, p_value, significant (at 0.05).
    """
    if len(bucket_a_values) < 2 or len(bucket_b_values) < 2:
        return {"t_statistic": None, "p_value": None, "significant": None, "error": "insufficient data"}

    t_stat, p_val = stats.ttest_ind(bucket_a_values, bucket_b_values, equal_var=False)
    return {
        "t_statistic": float(t_stat),
        "p_value": float(p_val),
        "significant": p_val < 0.05,
    }


def compare_buckets(bucket_type: str = "market_cap", window: str = "[0,+1h]") -> list[dict]:
    """Compare all bucket pairs for a given window using Welch's t-test."""
    results = get_all_event_results()
    all_meta = get_all_stock_meta()
    meta_map = {m.ticker: m for m in all_meta}

    groups = _group_results_by_bucket(results, meta_map, bucket_type)

    # Filter to specific window and extract reaction times
    bucket_values: dict[str, list[float]] = {}
    for bucket, bucket_results in groups.items():
        vals = [
            r.reaction_time_seconds
            for r in bucket_results
            if r.window_name == window and r.reaction_time_seconds is not None
        ]
        if vals:
            bucket_values[bucket] = vals

    comparisons = []
    buckets = sorted(bucket_values.keys())
    for i, a in enumerate(buckets):
        for b in buckets[i + 1:]:
            test = significance_test(bucket_values[a], bucket_values[b])
            test["bucket_a"] = a
            test["bucket_b"] = b
            test["n_a"] = len(bucket_values[a])
            test["n_b"] = len(bucket_values[b])
            comparisons.append(test)

    return comparisons
