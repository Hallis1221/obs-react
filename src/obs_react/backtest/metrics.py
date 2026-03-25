"""Performance metrics for backtest results."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from obs_react.backtest.engine import BacktestResult, Trade


def compute_metrics(result: BacktestResult) -> dict:
    """Compute comprehensive performance metrics."""
    trades = result.trades
    if not trades:
        return {"error": "no trades"}

    net = result.net_returns
    gross = result.gross_returns
    longs = result.long_trades
    shorts = result.short_trades

    winners = [r for r in net if r > 0]
    losers = [r for r in net if r <= 0]

    m = {
        "total_trades": len(trades),
        "total_announcements": result.total_announcements,
        "filtered_announcements": result.filtered_announcements,
        "signals_detected": result.signals_detected,
        "long_trades": len(longs),
        "short_trades": len(shorts),

        # Returns
        "gross_mean": statistics.mean(gross),
        "gross_median": statistics.median(gross),
        "net_mean": statistics.mean(net),
        "net_median": statistics.median(net),
        "net_total": sum(net),
        "net_std": statistics.stdev(net) if len(net) > 1 else 0,

        # Win/loss
        "win_count": len(winners),
        "loss_count": len(losers),
        "win_rate": len(winners) / len(trades),
        "avg_win": statistics.mean(winners) if winners else 0,
        "avg_loss": statistics.mean(losers) if losers else 0,

        # Risk
        "max_win": max(net),
        "max_loss": min(net),
        "profit_factor": (
            sum(winners) / abs(sum(losers)) if losers and sum(losers) != 0 else float("inf")
        ),

        # Costs
        "avg_spread_cost": statistics.mean([t.spread_cost for t in trades]),
        "avg_total_cost": statistics.mean([t.spread_cost + t.commission + t.slippage for t in trades]),
        "total_costs": sum(t.spread_cost + t.commission + t.slippage for t in trades),
    }

    # Sharpe (annualized, assuming ~5 trades/day, 250 days/year)
    if m["net_std"] > 0:
        trades_per_year = len(trades) / max(1, _trading_days(trades)) * 250
        m["sharpe"] = (m["net_mean"] / m["net_std"]) * math.sqrt(trades_per_year)
    else:
        m["sharpe"] = 0

    # Max drawdown (cumulative)
    cum = 0
    peak = 0
    max_dd = 0
    for r in net:
        cum += r
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)
    m["max_drawdown"] = max_dd

    # Long vs short breakdown
    if longs:
        long_net = [t.net_return for t in longs]
        long_winners = [r for r in long_net if r > 0]
        m["long_win_rate"] = len(long_winners) / len(longs)
        m["long_mean_return"] = statistics.mean(long_net)
    else:
        m["long_win_rate"] = 0
        m["long_mean_return"] = 0

    if shorts:
        short_net = [t.net_return for t in shorts]
        short_winners = [r for r in short_net if r > 0]
        m["short_win_rate"] = len(short_winners) / len(shorts)
        m["short_mean_return"] = statistics.mean(short_net)
    else:
        m["short_win_rate"] = 0
        m["short_mean_return"] = 0

    # By volume bucket
    m["by_volume"] = _breakdown_by(trades, "volume_bucket")
    m["by_mcap"] = _breakdown_by(trades, "market_cap_bucket")
    m["by_category"] = _breakdown_by_category(trades)

    # Data quality
    dq = defaultdict(int)
    for t in trades:
        dq[t.data_quality] += 1
    m["data_quality"] = dict(dq)

    return m


def _breakdown_by(trades: list[Trade], attr: str) -> dict:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        key = getattr(t, attr, None) or "unknown"
        groups[key].append(t)

    out = {}
    for key, group in sorted(groups.items()):
        net = [t.net_return for t in group]
        winners = [r for r in net if r > 0]
        out[key] = {
            "n": len(group),
            "win_rate": len(winners) / len(group),
            "mean_return": statistics.mean(net),
            "median_return": statistics.median(net),
            "total_return": sum(net),
        }
    return out


def _breakdown_by_category(trades: list[Trade]) -> dict:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        cat = t.category[:50] if t.category else "UNKNOWN"
        groups[cat].append(t)

    out = {}
    for key, group in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(group) < 2:
            continue
        net = [t.net_return for t in group]
        winners = [r for r in net if r > 0]
        out[key] = {
            "n": len(group),
            "win_rate": len(winners) / len(group),
            "mean_return": statistics.mean(net),
        }
    return out


def _trading_days(trades: list[Trade]) -> int:
    dates = set()
    for t in trades:
        dates.add(t.entry_time[:10])
    return len(dates)


def format_report(metrics: dict) -> str:
    """Format metrics dict into a readable report string."""
    if "error" in metrics:
        return f"No trades executed: {metrics['error']}"

    lines = []
    lines.append("=" * 70)
    lines.append("BACKTEST RESULTS")
    lines.append("=" * 70)

    lines.append(f"\n--- Pipeline ---")
    lines.append(f"  Announcements scanned:   {metrics['total_announcements']:>6}")
    lines.append(f"  Passed filters:          {metrics['filtered_announcements']:>6}")
    lines.append(f"  Signals detected:        {metrics['signals_detected']:>6}")
    lines.append(f"  Trades executed:         {metrics['total_trades']:>6}")

    lines.append(f"\n--- Performance (net of costs) ---")
    lines.append(f"  Mean return:        {metrics['net_mean']:>+8.2%}")
    lines.append(f"  Median return:      {metrics['net_median']:>+8.2%}")
    lines.append(f"  Total return:       {metrics['net_total']:>+8.2%}")
    lines.append(f"  Std dev:            {metrics['net_std']:>8.2%}")
    lines.append(f"  Sharpe ratio:       {metrics['sharpe']:>8.2f}")
    lines.append(f"  Max drawdown:       {metrics['max_drawdown']:>8.2%}")

    lines.append(f"\n--- Win/Loss ---")
    lines.append(f"  Win rate:           {metrics['win_rate']:>8.1%}")
    lines.append(f"  Wins / Losses:      {metrics['win_count']:>4} / {metrics['loss_count']}")
    lines.append(f"  Avg win:            {metrics['avg_win']:>+8.2%}")
    lines.append(f"  Avg loss:           {metrics['avg_loss']:>+8.2%}")
    lines.append(f"  Max win:            {metrics['max_win']:>+8.2%}")
    lines.append(f"  Max loss:           {metrics['max_loss']:>+8.2%}")
    lines.append(f"  Profit factor:      {metrics['profit_factor']:>8.2f}")

    lines.append(f"\n--- Costs ---")
    lines.append(f"  Avg spread cost:    {metrics['avg_spread_cost']:>8.2%}")
    lines.append(f"  Avg total cost:     {metrics['avg_total_cost']:>8.2%}")
    lines.append(f"  Total costs paid:   {metrics['total_costs']:>8.2%}")

    lines.append(f"\n--- Long vs Short ---")
    lines.append(f"  Long:  n={metrics['long_trades']:>4}  WR={metrics['long_win_rate']:.1%}  mean={metrics['long_mean_return']:+.2%}")
    lines.append(f"  Short: n={metrics['short_trades']:>4}  WR={metrics['short_win_rate']:.1%}  mean={metrics['short_mean_return']:+.2%}")

    lines.append(f"\n--- By Volume Bucket ---")
    for bucket, stats in metrics.get("by_volume", {}).items():
        lines.append(f"  {bucket:<10} n={stats['n']:>4}  WR={stats['win_rate']:.1%}  mean={stats['mean_return']:+.2%}  total={stats['total_return']:+.2%}")

    lines.append(f"\n--- By Market Cap ---")
    for bucket, stats in metrics.get("by_mcap", {}).items():
        lines.append(f"  {bucket:<10} n={stats['n']:>4}  WR={stats['win_rate']:.1%}  mean={stats['mean_return']:+.2%}  total={stats['total_return']:+.2%}")

    lines.append(f"\n--- By Category (top) ---")
    for cat, stats in list(metrics.get("by_category", {}).items())[:8]:
        lines.append(f"  {cat[:45]:<45} n={stats['n']:>3}  WR={stats['win_rate']:.0%}  mean={stats['mean_return']:+.2%}")

    lines.append(f"\n--- Data Quality ---")
    for dq, count in metrics.get("data_quality", {}).items():
        lines.append(f"  {dq}: {count}")

    return "\n".join(lines)
