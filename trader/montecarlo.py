"""Monte Carlo simulation of the trading strategy.

Running one backtest gives one number — but that number is partly luck: the
specific *order* trades happened in, which regime fell where. Monte Carlo
answers the real question: "across many plausible alternate histories, what
is the DISTRIBUTION of outcomes?"

Method — moving-block bootstrap of daily returns:
  1. Run the real backtest once → get the daily equity curve.
  2. Convert to daily returns.
  3. For each of N simulations, resample the daily returns in random BLOCKS
     (preserving volatility clustering) and compound a new equity curve.
  4. Aggregate: percentiles, probability of profit, drawdown distribution.

Block bootstrap (vs. naive shuffle) keeps short runs of correlated days
together, so simulated crashes and rallies look realistic instead of being
averaged into noise.

Seeds are fixed (1..N) so the whole report is reproducible.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List

from .config import Config


def _block_bootstrap(returns: List[float], block_size: int,
                     rng: random.Random) -> List[float]:
    """Resample a return series in contiguous blocks (with replacement)."""
    n = len(returns)
    out: List[float] = []
    while len(out) < n:
        start = rng.randint(0, max(0, n - 1))
        out.extend(returns[start:start + block_size])
    return out[:n]


def _path_metrics(daily_returns: List[float], start_cash: float) -> Dict:
    """Compound a return series into an equity curve and measure it."""
    equity = start_cash
    peak = start_cash
    max_dd = 0.0
    curve = [start_cash]
    for r in daily_returns:
        equity *= (1.0 + r)
        curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, (equity - peak) / peak)

    total_return = (equity - start_cash) / start_cash
    n = len(daily_returns)
    years = n / 252 if n else 1
    cagr = (equity / start_cash) ** (1 / years) - 1 if years > 0 and equity > 0 else 0.0

    if len(daily_returns) > 1:
        mean = sum(daily_returns) / len(daily_returns)
        var = sum((x - mean) ** 2 for x in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(var)
        sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "final_equity": equity,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
    }


def _percentile(sorted_vals: List[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def run_monte_carlo(cfg: Config, n_sims: int = 50,
                    block_size: int = 15) -> Dict:
    """Run N Monte Carlo simulations on the strategy's daily returns."""
    from .backtest import run_backtest

    print(f"Running base backtest ({cfg.backtest_start} → {cfg.backtest_end}, "
          f"{len(cfg.universe)} symbols)...")
    base = run_backtest(cfg, news="neutral", verbose=False)
    broker = base["broker"]

    equities = [e for _, e in broker.equity_curve]
    if len(equities) < 30:
        raise RuntimeError("Not enough equity-curve data for Monte Carlo.")

    daily_returns = [equities[i] / equities[i - 1] - 1.0
                     for i in range(1, len(equities))
                     if equities[i - 1] > 0]

    # Real backtest result for reference
    real = _path_metrics(daily_returns, cfg.starting_cash)

    print(f"Base backtest: {real['total_return']:+.1%} return, "
          f"{real['max_drawdown']:.1%} max DD over {len(equities)} days.")
    print(f"\nRunning {n_sims} Monte Carlo simulations "
          f"(block bootstrap, block={block_size} days)...\n")

    sims: List[Dict] = []
    for s in range(1, n_sims + 1):
        rng = random.Random(s)              # fixed seed → reproducible
        boot = _block_bootstrap(daily_returns, block_size, rng)
        sims.append(_path_metrics(boot, cfg.starting_cash))

    # Aggregate distributions
    returns   = sorted(x["total_return"] for x in sims)
    cagrs     = sorted(x["cagr"] for x in sims)
    drawdowns = sorted(x["max_drawdown"] for x in sims)
    sharpes   = sorted(x["sharpe"] for x in sims)
    equities_f = sorted(x["final_equity"] for x in sims)

    n_profitable = sum(1 for r in returns if r > 0)
    n_dd_15 = sum(1 for d in drawdowns if d <= -0.15)
    n_dd_20 = sum(1 for d in drawdowns if d <= -0.20)
    n_double = sum(1 for r in returns if r >= 1.0)

    return {
        "n_sims": n_sims,
        "block_size": block_size,
        "real": real,
        "sims": sims,
        "summary": {
            "return_p5":  _percentile(returns, 5),
            "return_p25": _percentile(returns, 25),
            "return_p50": _percentile(returns, 50),
            "return_p75": _percentile(returns, 75),
            "return_p95": _percentile(returns, 95),
            "return_min": returns[0],
            "return_max": returns[-1],
            "return_mean": sum(returns) / len(returns),
            "cagr_p50": _percentile(cagrs, 50),
            "cagr_p5":  _percentile(cagrs, 5),
            "cagr_p95": _percentile(cagrs, 95),
            "dd_p50": _percentile(drawdowns, 50),
            "dd_worst": drawdowns[0],
            "dd_best": drawdowns[-1],
            "sharpe_p50": _percentile(sharpes, 50),
            "sharpe_p5": _percentile(sharpes, 5),
            "sharpe_p95": _percentile(sharpes, 95),
            "equity_p50": _percentile(equities_f, 50),
            "pct_profitable": n_profitable / n_sims,
            "pct_dd_worse_15": n_dd_15 / n_sims,
            "pct_dd_worse_20": n_dd_20 / n_sims,
            "pct_doubled": n_double / n_sims,
        },
    }


def _histogram(values: List[float], n_bins: int = 10, width: int = 40) -> str:
    """Render a simple text histogram of returns."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1e-9
    bins = [0] * n_bins
    for v in values:
        idx = min(n_bins - 1, int((v - lo) / (hi - lo) * n_bins))
        bins[idx] += 1
    peak = max(bins) or 1
    lines = []
    for i, count in enumerate(bins):
        bin_lo = lo + (hi - lo) * i / n_bins
        bar = "█" * int(count / peak * width)
        lines.append(f"  {bin_lo*100:+6.1f}% | {bar} {count}")
    return "\n".join(lines)


def format_report(result: Dict) -> str:
    s = result["summary"]
    real = result["real"]
    n = result["n_sims"]
    returns = [x["total_return"] for x in result["sims"]]

    lines = [
        "",
        "=" * 64,
        f"  MONTE CARLO RESULTS — {n} simulated alternate histories",
        "=" * 64,
        "",
        f"  Real backtest (actual sequence):",
        f"    Total return : {real['total_return']:+.1%}",
        f"    Max drawdown : {real['max_drawdown']:.1%}",
        f"    Sharpe       : {real['sharpe']:.2f}",
        "",
        f"  Distribution across {n} simulations:",
        "",
        f"  TOTAL RETURN over the full period:",
        f"    Worst case (min)   : {s['return_min']:+.1%}",
        f"    5th percentile     : {s['return_p5']:+.1%}   (1-in-20 downside)",
        f"    25th percentile    : {s['return_p25']:+.1%}",
        f"    MEDIAN (typical)   : {s['return_p50']:+.1%}",
        f"    75th percentile    : {s['return_p75']:+.1%}",
        f"    95th percentile    : {s['return_p95']:+.1%}   (1-in-20 upside)",
        f"    Best case (max)    : {s['return_max']:+.1%}",
        f"    Average            : {s['return_mean']:+.1%}",
        "",
        f"  ANNUALIZED (CAGR):",
        f"    5th / median / 95th: {s['cagr_p5']:+.1%}  /  "
        f"{s['cagr_p50']:+.1%}  /  {s['cagr_p95']:+.1%}",
        "",
        f"  MAX DRAWDOWN (worst peak-to-trough):",
        f"    Median             : {s['dd_p50']:.1%}",
        f"    Worst of all sims  : {s['dd_worst']:.1%}",
        f"    Best of all sims   : {s['dd_best']:.1%}",
        "",
        f"  SHARPE RATIO:",
        f"    5th / median / 95th: {s['sharpe_p5']:.2f}  /  "
        f"{s['sharpe_p50']:.2f}  /  {s['sharpe_p95']:.2f}",
        "",
        f"  PROBABILITIES:",
        f"    Profitable             : {s['pct_profitable']:.0%} of simulations",
        f"    Drawdown worse than -15%: {s['pct_dd_worse_15']:.0%} of simulations",
        f"    Drawdown worse than -20%: {s['pct_dd_worse_20']:.0%} of simulations",
        f"    Doubled the account     : {s['pct_doubled']:.0%} of simulations",
        "",
        f"  RETURN DISTRIBUTION (histogram):",
        _histogram(returns),
        "",
        "=" * 64,
    ]
    return "\n".join(lines)
