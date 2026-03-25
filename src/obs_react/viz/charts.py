"""Matplotlib visualizations: scatter, boxplot, CAR timeline, coverage heatmap."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from obs_react.db.operations import (
    get_all_event_results,
    get_all_stock_meta,
    get_event_results,
    get_price_bars,
    get_announcements,
)
from obs_react.models import EventResult, StockMeta
from obs_react.utils.oslo_tz import parse_iso

log = logging.getLogger(__name__)

CHART_DIR = Path("data/charts")


def _ensure_chart_dir() -> Path:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    return CHART_DIR


def _meta_map() -> dict[str, StockMeta]:
    return {m.ticker: m for m in get_all_stock_meta()}


def scatter_reaction_vs_mcap(show: bool = False) -> Path:
    """Scatter plot: x=log(market_cap), y=reaction_time_seconds, colored by category."""
    _ensure_chart_dir()
    results = get_all_event_results()
    meta = _meta_map()
    anns = {a.id: a for a in get_announcements()}

    xs, ys, colors = [], [], []
    cat_colors = {}
    color_cycle = plt.cm.tab10.colors

    for r in results:
        if r.reaction_time_seconds is None:
            continue
        m = meta.get(r.ticker)
        if not m or not m.market_cap or m.market_cap <= 0:
            continue

        ann = anns.get(r.announcement_id)
        cat = ann.category if ann else "UNKNOWN"
        if cat not in cat_colors:
            cat_colors[cat] = color_cycle[len(cat_colors) % len(color_cycle)]

        xs.append(math.log10(m.market_cap))
        ys.append(r.reaction_time_seconds)
        colors.append(cat_colors[cat])

    fig, ax = plt.subplots(figsize=(10, 6))
    if xs:
        ax.scatter(xs, ys, c=colors, alpha=0.6, s=30)

        # Legend
        for cat, color in cat_colors.items():
            ax.scatter([], [], c=[color], label=cat[:25], s=30)
        ax.legend(fontsize=7, loc="upper right")

    ax.set_xlabel("log10(Market Cap NOK)")
    ax.set_ylabel("Reaction Time (seconds)")
    ax.set_title("Reaction Time vs Market Cap")
    ax.grid(True, alpha=0.3)

    path = CHART_DIR / "scatter_mcap.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    log.info(f"Saved {path}")
    return path


def scatter_reaction_vs_volume(show: bool = False) -> Path:
    """Scatter plot: x=log(avg_daily_volume), y=reaction_time_seconds."""
    _ensure_chart_dir()
    results = get_all_event_results()
    meta = _meta_map()

    xs, ys = [], []
    for r in results:
        if r.reaction_time_seconds is None:
            continue
        m = meta.get(r.ticker)
        if not m or not m.avg_daily_volume or m.avg_daily_volume <= 0:
            continue
        xs.append(math.log10(m.avg_daily_volume))
        ys.append(r.reaction_time_seconds)

    fig, ax = plt.subplots(figsize=(10, 6))
    if xs:
        ax.scatter(xs, ys, alpha=0.6, s=30, color="steelblue")

    ax.set_xlabel("log10(Avg Daily Volume)")
    ax.set_ylabel("Reaction Time (seconds)")
    ax.set_title("Reaction Time vs Trading Volume")
    ax.grid(True, alpha=0.3)

    path = CHART_DIR / "scatter_volume.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    log.info(f"Saved {path}")
    return path


def boxplot_by_bucket(bucket_type: str = "market_cap", show: bool = False) -> Path:
    """Box plot: reaction time distribution per bucket."""
    _ensure_chart_dir()
    results = get_all_event_results()
    meta = _meta_map()

    bucket_data: dict[str, list[float]] = defaultdict(list)
    for r in results:
        if r.reaction_time_seconds is None:
            continue
        m = meta.get(r.ticker)
        if not m:
            continue
        bucket = getattr(m, f"{bucket_type}_bucket", None) or "unknown"
        bucket_data[bucket].append(r.reaction_time_seconds)

    order = (
        ["micro", "small", "mid", "large"] if bucket_type == "market_cap"
        else ["low", "medium", "high"]
    )
    labels = [b for b in order if b in bucket_data]
    data = [bucket_data[b] for b in labels]

    fig, ax = plt.subplots(figsize=(8, 6))
    if data:
        bp = ax.boxplot(data, labels=labels, patch_artist=True)
        colors = ["#ff9999", "#ffcc99", "#99ccff", "#99ff99"]
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)

    ax.set_xlabel(bucket_type.replace("_", " ").title() + " Bucket")
    ax.set_ylabel("Reaction Time (seconds)")
    ax.set_title(f"Reaction Time by {bucket_type.replace('_', ' ').title()}")
    ax.grid(True, alpha=0.3, axis="y")

    path = CHART_DIR / f"boxplot_{bucket_type}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    log.info(f"Saved {path}")
    return path


def bar_abnormal_returns(show: bool = False) -> Path:
    """Grouped bar chart: mean abnormal return per window per market_cap bucket."""
    _ensure_chart_dir()
    results = get_all_event_results()
    meta = _meta_map()

    # Group by bucket and window
    data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        if r.abnormal_return is None:
            continue
        m = meta.get(r.ticker)
        bucket = (m.market_cap_bucket if m else None) or "unknown"
        data[bucket][r.window_name].append(r.abnormal_return)

    buckets = ["micro", "small", "mid", "large"]
    buckets = [b for b in buckets if b in data]
    windows = sorted({r.window_name for r in results if r.abnormal_return is not None})

    if not buckets or not windows:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        path = CHART_DIR / "bar_ar.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        return path

    x = np.arange(len(windows))
    width = 0.8 / len(buckets)

    fig, ax = plt.subplots(figsize=(12, 6))
    for i, bucket in enumerate(buckets):
        means = []
        for w in windows:
            vals = data[bucket].get(w, [])
            means.append(sum(vals) / len(vals) if vals else 0)
        ax.bar(x + i * width, means, width, label=bucket)

    ax.set_xlabel("Event Window")
    ax.set_ylabel("Mean Abnormal Return")
    ax.set_title("Abnormal Returns by Window and Market Cap Bucket")
    ax.set_xticks(x + width * len(buckets) / 2)
    ax.set_xticklabels(windows, rotation=45, ha="right", fontsize=8)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(y=0, color="black", linewidth=0.5)

    path = CHART_DIR / "bar_ar.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    log.info(f"Saved {path}")
    return path


def car_timeline(announcement_id: int, show: bool = False) -> Path:
    """Cumulative abnormal return over time for a specific event."""
    _ensure_chart_dir()
    anns = get_announcements()
    ann = next((a for a in anns if a.id == announcement_id), None)
    if not ann:
        raise ValueError(f"Announcement {announcement_id} not found")

    ticker = ann.ticker if ann.ticker.endswith(".OL") else ann.ticker + ".OL"
    event_time = parse_iso(ann.published_at)

    # Get bars around the event
    from datetime import timedelta
    start = event_time - timedelta(hours=2)
    end = event_time + timedelta(hours=4)

    bars = get_price_bars(ticker, interval="1m",
                          start=start.isoformat(), end=end.isoformat())
    if len(bars) < 2:
        bars = get_price_bars(ticker, interval="5m",
                              start=start.isoformat(), end=end.isoformat())
    if len(bars) < 2:
        bars = get_price_bars(ticker, start=start.isoformat(), end=end.isoformat())

    if len(bars) < 2:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, f"No price data for {ticker}", ha="center", va="center")
        path = CHART_DIR / f"car_{announcement_id}.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        return path

    # Compute cumulative return from first bar
    base_price = bars[0].close
    times = []
    cum_returns = []
    for b in bars:
        t = parse_iso(b.timestamp)
        minutes_from_event = (t - event_time).total_seconds() / 60
        times.append(minutes_from_event)
        cum_returns.append((b.close - base_price) / base_price if base_price != 0 else 0)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(times, cum_returns, linewidth=1.5, color="steelblue")
    ax.axvline(x=0, color="red", linestyle="--", linewidth=1, label="Announcement")
    ax.axhline(y=0, color="black", linewidth=0.5)

    ax.set_xlabel("Minutes from Announcement")
    ax.set_ylabel("Cumulative Return")
    ax.set_title(f"CAR Timeline: {ticker} — {ann.title[:60]}")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Format y-axis as percentage
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.1%}"))

    path = CHART_DIR / f"car_{announcement_id}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    log.info(f"Saved {path}")
    return path


def coverage_heatmap(show: bool = False) -> Path:
    """Data quality heatmap: tickers vs data quality levels."""
    _ensure_chart_dir()
    results = get_all_event_results()

    # Build ticker x quality matrix
    ticker_quality: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        quality = r.data_quality or "missing"
        ticker_quality[r.ticker][quality] = ticker_quality[r.ticker].get(quality, 0) + 1

    tickers = sorted(ticker_quality.keys())
    qualities = ["full", "partial", "daily_only", "missing"]

    if not tickers:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        path = CHART_DIR / "coverage.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        return path

    matrix = np.zeros((len(tickers), len(qualities)))
    for i, t in enumerate(tickers):
        for j, q in enumerate(qualities):
            matrix[i, j] = ticker_quality[t].get(q, 0)

    # Limit to top 30 tickers for readability
    if len(tickers) > 30:
        # Sort by total events
        totals = [(t, sum(ticker_quality[t].values())) for t in tickers]
        totals.sort(key=lambda x: x[1], reverse=True)
        top_tickers = [t for t, _ in totals[:30]]
        top_indices = [tickers.index(t) for t in top_tickers]
        matrix = matrix[top_indices]
        tickers = top_tickers

    fig, ax = plt.subplots(figsize=(8, max(6, len(tickers) * 0.3)))
    im = ax.imshow(matrix, aspect="auto", cmap="YlOrRd")
    ax.set_xticks(range(len(qualities)))
    ax.set_xticklabels(qualities)
    ax.set_yticks(range(len(tickers)))
    ax.set_yticklabels(tickers, fontsize=7)
    ax.set_title("Data Coverage by Ticker")
    fig.colorbar(im, label="Event Count")

    path = CHART_DIR / "coverage.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    log.info(f"Saved {path}")
    return path
