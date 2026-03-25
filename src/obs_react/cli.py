"""Click CLI entry point for obs-react."""

from __future__ import annotations

import logging

import click

from obs_react.config import DB_PATH, REACTION_THRESHOLD_SIGMA

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("obs_react")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.option("--db", default=None, help="Database path override")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, db: str | None) -> None:
    """obs-react: Oslo Bors Reaction Timer"""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    ctx.ensure_object(dict)
    if db:
        import obs_react.config as cfg
        from pathlib import Path
        cfg.DB_PATH = Path(db)


# --- init ---

@cli.command()
def init() -> None:
    """Create the SQLite database and tables."""
    from obs_react.db.schema import init_db
    conn = init_db()
    conn.close()
    click.echo(f"Database initialized at {DB_PATH}")


# --- status ---

@cli.command()
def status() -> None:
    """Show database stats: counts, coverage, last fetch times."""
    from obs_react.db.schema import init_db
    from obs_react.db.operations import get_db_stats

    init_db()
    stats = get_db_stats()

    click.echo("\n=== obs-react Database Status ===\n")
    click.echo(f"  Announcements:    {stats['announcements_count']:>8}")
    click.echo(f"  Distinct tickers: {stats['distinct_tickers']:>8}")
    click.echo(f"  Price bars:       {stats['price_bars_count']:>8}")
    click.echo(f"  Stock metadata:   {stats['stock_meta_count']:>8}")
    click.echo(f"  Event results:    {stats['event_results_count']:>8}")
    click.echo()
    click.echo(f"  Earliest announcement: {stats['earliest_announcement']}")
    click.echo(f"  Latest announcement:   {stats['latest_announcement']}")
    click.echo()
    click.echo(f"  Last NewsWeb fetch:    {stats['last_newsweb_fetch']}")
    click.echo(f"  Last price fetch:      {stats['last_yfinance_price_fetch']}")
    click.echo(f"  Last metadata fetch:   {stats['last_yfinance_meta_fetch']}")
    click.echo()


# --- collect ---

@cli.group()
def collect() -> None:
    """Collect data from NewsWeb and yfinance."""
    pass


@collect.command("news")
@click.option("--watch", is_flag=True, help="Continuous polling mode")
@click.option("--max-pages", default=5, help="Max pages to scrape")
def collect_news(watch: bool, max_pages: int) -> None:
    """Scrape announcements from NewsWeb."""
    from obs_react.db.schema import init_db
    init_db()

    if watch:
        click.echo("Starting continuous NewsWeb polling (Ctrl+C to stop)...")
        from obs_react.news.poller import poll_loop
        try:
            poll_loop()
        except KeyboardInterrupt:
            click.echo("\nStopped.")
    else:
        click.echo("Fetching latest announcements from NewsWeb...")
        from obs_react.news.poller import poll_once
        count = poll_once()
        click.echo(f"Inserted {count} new announcements.")


@collect.command("prices")
@click.option("--ticker", default=None, help="Specific ticker to fetch")
def collect_prices(ticker: str | None) -> None:
    """Fetch/backfill intraday price data from yfinance."""
    from obs_react.db.schema import init_db
    from obs_react.db.operations import get_announcements
    from obs_react.prices.fetcher import backfill_prices_for_ticker, backfill_benchmark

    init_db()

    if ticker:
        tickers = [ticker]
    else:
        anns = get_announcements()
        tickers = sorted(set(a.ticker for a in anns))

    if not tickers:
        click.echo("No tickers to fetch. Run 'collect news' first.")
        return

    click.echo(f"Fetching prices for {len(tickers)} ticker(s)...")

    total = 0
    for i, t in enumerate(tickers, 1):
        click.echo(f"  [{i}/{len(tickers)}] {t}...", nl=False)
        try:
            inserted = backfill_prices_for_ticker(t)
            click.echo(f" {inserted} bars")
            total += inserted
        except Exception as e:
            click.echo(f" ERROR: {e}")

    click.echo("\nFetching benchmark data...")
    bench = backfill_benchmark()
    total += bench

    click.echo(f"\nTotal: {total} price bars inserted.")


@collect.command("meta")
@click.option("--ticker", default=None, help="Specific ticker to fetch")
@click.option("--force", is_flag=True, help="Force refresh even if recent")
def collect_meta(ticker: str | None, force: bool) -> None:
    """Fetch/refresh stock metadata from yfinance."""
    from obs_react.db.schema import init_db
    from obs_react.db.operations import get_announcements
    from obs_react.prices.metadata import fetch_metadata_for_tickers

    init_db()

    if ticker:
        tickers = [ticker]
    else:
        anns = get_announcements()
        tickers = sorted(set(a.ticker for a in anns))

    if not tickers:
        click.echo("No tickers to fetch. Run 'collect news' first.")
        return

    click.echo(f"Fetching metadata for {len(tickers)} ticker(s)...")
    count = fetch_metadata_for_tickers(tickers, force=force)
    click.echo(f"Updated metadata for {count} tickers.")


@collect.command("all")
@click.pass_context
def collect_all(ctx: click.Context) -> None:
    """Run all collectors in sequence."""
    click.echo("=== Collecting News ===")
    ctx.invoke(collect_news, watch=False, max_pages=5)
    click.echo("\n=== Collecting Metadata ===")
    ctx.invoke(collect_meta, ticker=None, force=False)
    click.echo("\n=== Collecting Prices ===")
    ctx.invoke(collect_prices, ticker=None)


# --- analyze ---

@cli.command()
@click.option("--ticker", default=None, help="Analyze specific ticker only")
@click.option("--since", default=None, help="Only announcements after this date (YYYY-MM-DD)")
@click.option("--category", default=None, help="Filter by announcement category")
@click.option("--threshold-sigma", default=REACTION_THRESHOLD_SIGMA, type=float,
              help="Std dev threshold for reaction time")
def analyze(ticker: str | None, since: str | None, category: str | None,
            threshold_sigma: float) -> None:
    """Run event study analysis on collected data."""
    from obs_react.db.schema import init_db
    from obs_react.db.operations import get_announcements
    from obs_react.analysis.event_study import run_analysis

    init_db()
    anns = get_announcements(ticker=ticker, since=since, category=category)

    if not anns:
        click.echo("No announcements found matching criteria.")
        return

    click.echo(f"Analyzing {len(anns)} announcements (threshold={threshold_sigma} sigma)...")
    count = run_analysis(anns, threshold_sigma)
    click.echo(f"Computed {count} event results.")


# --- report ---

@cli.group()
def report() -> None:
    """Display analysis reports."""
    pass


@report.command("events")
@click.option("--ticker", default=None)
@click.option("--window", default=None)
@click.option("--limit", default=50, type=int)
def report_events(ticker: str | None, window: str | None, limit: int) -> None:
    """Per-event results table."""
    from obs_react.db.schema import init_db
    from obs_react.db.operations import get_all_event_results, get_announcements
    from tabulate import tabulate

    init_db()
    results = get_all_event_results()
    anns = {a.id: a for a in get_announcements()}

    if ticker:
        ol = ticker if ticker.endswith(".OL") else ticker + ".OL"
        results = [r for r in results if r.ticker == ol]
    if window:
        results = [r for r in results if r.window_name == window]

    rows = []
    for r in results[:limit]:
        ann = anns.get(r.announcement_id)
        rows.append([
            r.ticker,
            ann.title[:40] if ann else "?",
            r.window_name,
            f"{r.abnormal_return:.4f}" if r.abnormal_return is not None else "N/A",
            f"{r.reaction_time_seconds}s" if r.reaction_time_seconds is not None else "N/A",
            r.data_quality or "?",
        ])

    headers = ["Ticker", "Title", "Window", "Abn. Return", "React Time", "Quality"]
    click.echo(tabulate(rows, headers=headers, tablefmt="simple"))
    click.echo(f"\nShowing {len(rows)} of {len(results)} results")


@report.command("buckets")
@click.option("--type", "bucket_type", default="market_cap",
              type=click.Choice(["market_cap", "volume"]))
def report_buckets(bucket_type: str) -> None:
    """Aggregate stats by bucket."""
    from obs_react.db.schema import init_db
    from obs_react.analysis.buckets import aggregate_by_bucket, compare_buckets
    from tabulate import tabulate

    init_db()
    agg = aggregate_by_bucket(bucket_type)

    if not agg:
        click.echo("No data. Run 'analyze' first.")
        return

    rows = []
    for entry in agg:
        rt = entry["reaction_time"]
        ar = entry["abnormal_return"]
        rows.append([
            entry["bucket"],
            entry["window"],
            entry["event_count"],
            f"{rt['mean']:.0f}s" if rt["mean"] is not None else "N/A",
            f"{rt['median']:.0f}s" if rt["median"] is not None else "N/A",
            f"{ar['mean']:.4f}" if ar["mean"] is not None else "N/A",
        ])

    headers = ["Bucket", "Window", "Events", "Mean RT", "Median RT", "Mean AR"]
    click.echo(tabulate(rows, headers=headers, tablefmt="simple"))

    click.echo("\n--- Significance Tests (Welch's t-test on reaction times) ---")
    comparisons = compare_buckets(bucket_type)
    if comparisons:
        sig_rows = []
        for c in comparisons:
            sig_rows.append([
                f"{c['bucket_a']} vs {c['bucket_b']}",
                f"n={c['n_a']},{c['n_b']}",
                f"{c['t_statistic']:.2f}" if c["t_statistic"] is not None else "N/A",
                f"{c['p_value']:.4f}" if c["p_value"] is not None else "N/A",
                "YES" if c.get("significant") else "no",
            ])
        click.echo(tabulate(sig_rows, headers=["Comparison", "N", "t-stat", "p-value", "Sig?"],
                            tablefmt="simple"))
    else:
        click.echo("Insufficient data for significance tests.")


@report.command("summary")
def report_summary() -> None:
    """High-level summary of findings."""
    from obs_react.db.schema import init_db
    from obs_react.db.operations import get_db_stats, get_all_event_results
    from obs_react.analysis.buckets import aggregate_by_bucket

    init_db()
    stats = get_db_stats()
    results = get_all_event_results()

    click.echo("\n=== obs-react Summary Report ===\n")
    click.echo(f"Total announcements: {stats['announcements_count']}")
    click.echo(f"Distinct tickers:    {stats['distinct_tickers']}")
    click.echo(f"Event results:       {stats['event_results_count']}")

    if not results:
        click.echo("\nNo analysis results yet. Run 'analyze' first.")
        return

    rt_values = [r.reaction_time_seconds for r in results if r.reaction_time_seconds is not None]
    ar_values = [r.abnormal_return for r in results if r.abnormal_return is not None]

    if rt_values:
        click.echo(f"\nReaction times (all events):")
        click.echo(f"  Mean:   {sum(rt_values)/len(rt_values):.0f}s")
        click.echo(f"  Median: {sorted(rt_values)[len(rt_values)//2]:.0f}s")
        click.echo(f"  Min:    {min(rt_values)}s")
        click.echo(f"  Max:    {max(rt_values)}s")

    if ar_values:
        click.echo(f"\nAbnormal returns:")
        click.echo(f"  Mean:   {sum(ar_values)/len(ar_values):.4f}")
        click.echo(f"  Min:    {min(ar_values):.4f}")
        click.echo(f"  Max:    {max(ar_values):.4f}")

    agg = aggregate_by_bucket("market_cap")
    if agg:
        click.echo(f"\nBy market cap bucket (window [0,+1h]):")
        for entry in agg:
            if entry["window"] == "[0,+1h]":
                rt = entry["reaction_time"]
                rt_str = f"mean_rt={rt['mean']:.0f}s" if rt["mean"] is not None else "mean_rt=N/A"
                click.echo(f"  {entry['bucket']:>8}: n={entry['event_count']}, {rt_str}")

    quality_counts: dict[str, int] = {}
    for r in results:
        q = r.data_quality or "missing"
        quality_counts[q] = quality_counts.get(q, 0) + 1
    click.echo(f"\nData quality distribution:")
    for q in ["full", "partial", "daily_only", "missing"]:
        if q in quality_counts:
            click.echo(f"  {q:>12}: {quality_counts[q]}")
    click.echo()


# --- chart ---

@cli.group()
def chart() -> None:
    """Generate visualization charts."""
    pass


@chart.command("scatter")
@click.option("--show", is_flag=True, help="Also display interactively")
def chart_scatter(show: bool) -> None:
    """Reaction time vs market cap/volume scatter plots."""
    from obs_react.db.schema import init_db
    from obs_react.viz.charts import scatter_reaction_vs_mcap, scatter_reaction_vs_volume

    init_db()
    p1 = scatter_reaction_vs_mcap(show=show)
    p2 = scatter_reaction_vs_volume(show=show)
    click.echo(f"Saved: {p1}")
    click.echo(f"Saved: {p2}")


@chart.command("boxplot")
@click.option("--type", "bucket_type", default="market_cap",
              type=click.Choice(["market_cap", "volume"]))
@click.option("--show", is_flag=True)
def chart_boxplot(bucket_type: str, show: bool) -> None:
    """Box plot of reaction time by bucket."""
    from obs_react.db.schema import init_db
    from obs_react.viz.charts import boxplot_by_bucket

    init_db()
    p = boxplot_by_bucket(bucket_type=bucket_type, show=show)
    click.echo(f"Saved: {p}")


@chart.command("car")
@click.argument("event_id", type=int)
@click.option("--show", is_flag=True)
def chart_car(event_id: int, show: bool) -> None:
    """CAR timeline for a specific event (by announcement ID)."""
    from obs_react.db.schema import init_db
    from obs_react.viz.charts import car_timeline

    init_db()
    p = car_timeline(event_id, show=show)
    click.echo(f"Saved: {p}")


@chart.command("coverage")
@click.option("--show", is_flag=True)
def chart_coverage(show: bool) -> None:
    """Data quality coverage heatmap."""
    from obs_react.db.schema import init_db
    from obs_react.viz.charts import coverage_heatmap

    init_db()
    p = coverage_heatmap(show=show)
    click.echo(f"Saved: {p}")


# --- monitor ---

@cli.command()
@click.option("--interval", default=60, help="Polling interval in seconds")
@click.option("--threshold", default=1.0, help="Minimum price move %% to trigger signal")
@click.option("--max-age", default=30, help="Max age of announcements to check (minutes)")
def monitor(interval: int, threshold: float, max_age: int) -> None:
    """Real-time monitor: detect momentum signals from NewsWeb announcements."""
    from obs_react.db.schema import init_db
    from obs_react.monitor import monitor_loop

    init_db()
    click.echo(f"Starting real-time monitor (threshold={threshold}%, interval={interval}s)")
    click.echo("Press Ctrl+C to stop.\n")
    monitor_loop(
        interval_seconds=interval,
        move_threshold=threshold / 100.0,
        max_age_minutes=max_age,
    )


@cli.command("check-signals")
@click.option("--threshold", default=1.0, help="Minimum price move %% to trigger signal")
@click.option("--max-age", default=30, help="Max age of announcements to check (minutes)")
def check_signals(threshold: float, max_age: int) -> None:
    """One-shot check for current momentum signals."""
    from obs_react.db.schema import init_db
    from obs_react.monitor import check_for_signals

    init_db()
    signals = check_for_signals(
        move_threshold=threshold / 100.0,
        max_age_minutes=max_age,
    )
    if signals:
        click.echo(f"Found {len(signals)} signal(s):\n")
        for s in signals:
            click.echo(f"  {s}")
    else:
        click.echo("No signals detected.")


@cli.command()
@click.option("--use-llm", is_flag=True, help="Use LLM classifier (needs ANTHROPIC_API_KEY)")
def backtest(use_llm: bool) -> None:
    """Backtest the announcement classifier against historical data."""
    from obs_react.db.schema import init_db
    from obs_react.analysis.classifier import backtest_classifier

    init_db()
    click.echo("Running backtest...")
    results = backtest_classifier(use_llm=use_llm)

    if "error" in results:
        click.echo(f"Error: {results['error']}")
        return

    click.echo(f"\n{'='*50}")
    click.echo(f"BACKTEST RESULTS ({'LLM' if use_llm else 'Rule-based'})")
    click.echo(f"{'='*50}")
    click.echo(f"Events with data: {results['events_with_data']}")
    click.echo(f"Trades taken:     {results['trades']}")
    click.echo(f"Skipped:          {results['skipped']}")
    click.echo(f"Win rate:         {results['win_rate']*100:.1f}%")
    click.echo(f"Mean return:      {results['mean_return']*100:+.2f}%")
    click.echo(f"Total return:     {results['total_return']*100:+.1f}%")
    click.echo(f"Best trade:       {results['best_trade']*100:+.2f}%")
    click.echo(f"Worst trade:      {results['worst_trade']*100:+.2f}%")
