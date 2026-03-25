"""Click CLI entry point for obs-react."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import click
from tabulate import tabulate

from obs_react.config import DB_PATH, REACTION_THRESHOLD_SIGMA
from obs_react.db.schema import init_db

log = logging.getLogger("obs_react")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose: bool = False) -> None:
    """obs-react: Oslo Bors Reaction Timer."""
    _setup_logging(verbose)


# --- init ---

@cli.command()
def init() -> None:
    """Create SQLite database with all tables."""
    conn = init_db()
    conn.close()
    click.echo(f"Database initialized at {DB_PATH}")


# --- status ---

@cli.command()
def status() -> None:
    """Show database stats: counts, coverage, last fetch times."""
    from obs_react.db.operations import get_db_stats

    init_db()
    stats = get_db_stats()

    rows = [
        ["Announcements", stats["announcements_count"]],
        ["Price bars", stats["price_bars_count"]],
        ["Stock metadata", stats["stock_meta_count"]],
        ["Event results", stats["event_results_count"]],
        ["Distinct tickers", stats["distinct_tickers"]],
        ["Earliest announcement", stats["earliest_announcement"]],
        ["Latest announcement", stats["latest_announcement"]],
        ["Last news fetch", stats["last_newsweb_fetch"]],
        ["Last price fetch", stats["last_yfinance_price_fetch"]],
        ["Last meta fetch", stats["last_yfinance_meta_fetch"]],
    ]
    click.echo(tabulate(rows, headers=["Metric", "Value"], tablefmt="simple"))


# --- collect ---

@cli.group()
def collect() -> None:
    """Collect data from NewsWeb and yfinance."""
    init_db()


@collect.command("news")
@click.option("--watch", is_flag=True, help="Continuous polling mode")
@click.option("--max-pages", default=10, help="Max pages to scrape per run")
def collect_news(watch: bool, max_pages: int) -> None:
    """Scrape NewsWeb announcements."""
    from obs_react.news.poller import poll_once, poll_loop

    if watch:
        click.echo("Starting continuous news polling (Ctrl+C to stop)...")
        poll_loop()
    else:
        count = poll_once()
        click.echo(f"Fetched {count} new announcements")


@collect.command("prices")
@click.option("--ticker", default=None, help="Specific ticker to fetch")
def collect_prices(ticker: str | None) -> None:
    """Fetch/backfill price data from yfinance."""
    from obs_react.db.operations import get_announcements
    from obs_react.prices.fetcher import backfill_prices_for_ticker, backfill_benchmark

    if ticker:
        tickers = [ticker]
    else:
        announcements = get_announcements()
        tickers = sorted({a.ticker for a in announcements})

    if not tickers:
        click.echo("No tickers found. Run 'collect news' first.")
        return

    click.echo(f"Backfilling prices for {len(tickers)} ticker(s)...")
    total = 0
    for t in tickers:
        inserted = backfill_prices_for_ticker(t)
        total += inserted
        click.echo(f"  {t}: {inserted} bars inserted")

    click.echo(f"Backfilling benchmark...")
    bench = backfill_benchmark()
    click.echo(f"  Benchmark: {bench} bars inserted")
    click.echo(f"Total: {total + bench} bars inserted")


@collect.command("meta")
@click.option("--ticker", default=None, help="Specific ticker to fetch")
@click.option("--force", is_flag=True, help="Force refresh even if recent")
def collect_meta(ticker: str | None, force: bool) -> None:
    """Fetch/refresh stock metadata from yfinance."""
    from obs_react.db.operations import get_announcements
    from obs_react.prices.metadata import fetch_metadata, fetch_metadata_for_tickers

    if ticker:
        result = fetch_metadata(ticker, force=force)
        if result:
            click.echo(f"Updated metadata for {result['ticker']}: {result['company_name']}")
        else:
            click.echo(f"Metadata for {ticker} is up-to-date (use --force to refresh)")
    else:
        announcements = get_announcements()
        tickers = sorted({a.ticker for a in announcements})
        if not tickers:
            click.echo("No tickers found. Run 'collect news' first.")
            return
        count = fetch_metadata_for_tickers(tickers, force=force)
        click.echo(f"Updated metadata for {count} ticker(s)")


@collect.command("all")
def collect_all() -> None:
    """Run all collectors in sequence: news, prices, meta."""
    from obs_react.news.poller import poll_once
    from obs_react.db.operations import get_announcements
    from obs_react.prices.fetcher import backfill_prices_for_ticker, backfill_benchmark
    from obs_react.prices.metadata import fetch_metadata_for_tickers

    click.echo("=== Collecting news ===")
    news_count = poll_once()
    click.echo(f"  {news_count} new announcements")

    announcements = get_announcements()
    tickers = sorted({a.ticker for a in announcements})

    click.echo(f"\n=== Collecting prices for {len(tickers)} tickers ===")
    total_bars = 0
    for t in tickers:
        inserted = backfill_prices_for_ticker(t)
        total_bars += inserted
    bench = backfill_benchmark()
    click.echo(f"  {total_bars + bench} total bars inserted")

    click.echo(f"\n=== Collecting metadata ===")
    meta_count = fetch_metadata_for_tickers(tickers)
    click.echo(f"  {meta_count} tickers updated")

    click.echo("\nDone!")


# --- analyze ---

@cli.command()
@click.option("--ticker", default=None, help="Analyze specific ticker only")
@click.option("--since", default=None, help="Only announcements after this date (ISO)")
@click.option("--category", default=None, help="Filter by announcement category")
@click.option("--threshold-sigma", default=REACTION_THRESHOLD_SIGMA, type=float,
              help="Sigma threshold for reaction time detection")
def analyze(ticker: str | None, since: str | None, category: str | None,
            threshold_sigma: float) -> None:
    """Run event study analysis on collected data."""
    from obs_react.db.operations import get_announcements
    from obs_react.analysis.event_study import run_analysis

    init_db()
    announcements = get_announcements(ticker=ticker, since=since, category=category)
    if not announcements:
        click.echo("No announcements found matching criteria.")
        return

    click.echo(f"Analyzing {len(announcements)} announcements (sigma={threshold_sigma})...")
    count = run_analysis(announcements, threshold_sigma=threshold_sigma)
    click.echo(f"Generated {count} event results")


# --- report ---

@cli.group()
def report() -> None:
    """Display analysis reports."""
    init_db()


@report.command("events")
@click.option("--ticker", default=None)
@click.option("--window", default=None, help="Filter by window name")
def report_events(ticker: str | None, window: str | None) -> None:
    """Per-event results table."""
    from obs_react.db.operations import get_event_results, get_announcements

    results = get_event_results(ticker=ticker, window_name=window)
    if not results:
        click.echo("No event results found. Run 'analyze' first.")
        return

    # Build announcement lookup
    announcements = {a.id: a for a in get_announcements(ticker=ticker)}

    rows = []
    for r in results:
        ann = announcements.get(r.announcement_id)
        ann_title = ann.title[:30] if ann else "?"
        rows.append([
            r.ticker,
            ann_title,
            r.window_name,
            f"{r.abnormal_return:.4f}" if r.abnormal_return is not None else "N/A",
            f"{r.cumulative_ar:.4f}" if r.cumulative_ar is not None else "N/A",
            f"{r.reaction_time_seconds}s" if r.reaction_time_seconds is not None else "N/A",
            r.data_quality or "?",
        ])

    headers = ["Ticker", "Title", "Window", "AR", "CAR", "React Time", "Quality"]
    click.echo(tabulate(rows, headers=headers, tablefmt="simple"))
    click.echo(f"\n{len(rows)} result(s)")


@report.command("buckets")
def report_buckets() -> None:
    """Aggregate stats by market cap and volume buckets."""
    from obs_react.analysis.buckets import aggregate_by_bucket

    for bucket_type in ("market_cap", "volume"):
        click.echo(f"\n=== By {bucket_type} ===")
        data = aggregate_by_bucket(bucket_type)
        if not data:
            click.echo("  No data available")
            continue
        rows = []
        for d in data:
            rt = d.get("reaction_time", {})
            ar = d.get("abnormal_return", {})
            rows.append([
                d["bucket"],
                d.get("window", ""),
                d.get("event_count", 0),
                f"{rt['mean']:.0f}s" if rt.get("mean") is not None else "N/A",
                f"{rt['median']:.0f}s" if rt.get("median") is not None else "N/A",
                f"{ar['mean']:.4f}" if ar.get("mean") is not None else "N/A",
            ])
        headers = ["Bucket", "Window", "Events", "Mean RT", "Median RT", "Mean AR"]
        click.echo(tabulate(rows, headers=headers, tablefmt="simple"))


@report.command("summary")
def report_summary() -> None:
    """High-level summary of analysis findings."""
    from obs_react.db.operations import get_all_event_results, get_db_stats
    from obs_react.analysis.buckets import cross_bucket_analysis

    stats = get_db_stats()
    results = get_all_event_results()

    click.echo("=== obs-react Summary ===\n")
    click.echo(f"Announcements analyzed: {stats['announcements_count']}")
    click.echo(f"Event results:          {stats['event_results_count']}")
    click.echo(f"Distinct tickers:       {stats['distinct_tickers']}")

    if not results:
        click.echo("\nNo results yet. Run 'analyze' first.")
        return

    # Reaction times for post-event windows
    post_results = [r for r in results if r.window_name.startswith("[0,") and r.reaction_time_seconds is not None]
    if post_results:
        rts = [r.reaction_time_seconds for r in post_results]
        click.echo(f"\nReaction time ([0,+1h] window):")
        click.echo(f"  Mean:   {sum(rts) / len(rts):.0f}s")
        click.echo(f"  Min:    {min(rts)}s")
        click.echo(f"  Max:    {max(rts)}s")

    # Cross-bucket summary
    cross = cross_bucket_analysis()
    if cross:
        click.echo(f"\nCross-bucket analysis available: {len(cross)} cells")


# --- chart ---

@cli.group()
def chart() -> None:
    """Generate visualizations."""
    init_db()


@chart.command("scatter")
def chart_scatter() -> None:
    """Reaction time vs market cap/volume scatter plots."""
    from obs_react.viz.charts import scatter_reaction_vs_mcap, scatter_reaction_vs_volume
    path1 = scatter_reaction_vs_mcap()
    click.echo(f"Saved market cap scatter to {path1}")
    path2 = scatter_reaction_vs_volume()
    click.echo(f"Saved volume scatter to {path2}")


@chart.command("boxplot")
def chart_boxplot() -> None:
    """Box plot of reaction time by bucket."""
    from obs_react.viz.charts import boxplot_by_bucket
    path1 = boxplot_by_bucket("market_cap")
    click.echo(f"Saved market cap box plot to {path1}")
    path2 = boxplot_by_bucket("volume")
    click.echo(f"Saved volume box plot to {path2}")


@chart.command("car")
@click.argument("event_id", type=int)
def chart_car(event_id: int) -> None:
    """CAR timeline for a specific event (by announcement ID)."""
    from obs_react.viz.charts import car_timeline
    path = car_timeline(event_id)
    click.echo(f"Saved CAR plot to {path}")


@chart.command("coverage")
def chart_coverage() -> None:
    """Data quality heatmap."""
    from obs_react.viz.charts import coverage_heatmap
    path = coverage_heatmap()
    click.echo(f"Saved coverage heatmap to {path}")
