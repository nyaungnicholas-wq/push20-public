#!/usr/bin/env python3
"""Parallel Monte Carlo of the BASE sector-rotation strategy over the maximum
history the data physically allows (~1999-2024).

Why not 50 years? The sector SPDR ETFs this strategy trades (XLK, XLF, ...)
launched on 1998-12-16, so ~1999 is the floor — there is no ETF data before
then. XLRE (2015), XLC (2018) and the BIL cash proxy (2007) postdate the start;
XLRE/XLC are dropped from the universe and BIL is handled gracefully (the
rotation simply parks unfilled slots in real cash until BIL exists). This is
the *base* rotation (no GLD hedge — that is the 2005+ v2 champion).

Two parts:
  1. REAL track record: one base-rotation backtest 1999-2024 vs SPY buy & hold.
  2. 50 Monte Carlo alternate histories: moving-block bootstrap of the
     strategy's realized daily returns, run in PARALLEL across CPU cores. This
     answers "how much of the realized result was sequence/regime luck?" by
     re-ordering the return stream in volatility-preserving blocks.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

# Make `trader` importable when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from trader.config import Config
from trader import metrics
from trader.data_source import get_data_source
from trader.montecarlo import (_block_bootstrap, _path_metrics, _percentile,
                               _histogram)


# --- universe trimmed to what existed in 1999 (drops XLRE 2015, XLC 2018) ---
ROTATION_1999 = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLB", "XLP", "XLU",
                 "QQQ", "SMH"]


def build_cfg(start: str, end: str) -> Config:
    cfg = Config()
    cfg.backtest_start = start
    cfg.backtest_end = end
    cfg.rotation_universe = list(ROTATION_1999)
    # base rotation: every v2 flag stays at its (off) default; no GLD hedge.
    cfg.rotation_defensive_symbol = ""        # → use cash symbol (BIL, 2007+)
    cfg.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    cfg.alpaca_key = os.getenv("ALPACA_KEY")
    cfg.alpaca_secret = os.getenv("ALPACA_SECRET")
    return cfg


def prefetch_parallel(cfg: Config) -> None:
    """Warm the parquet cache for every symbol concurrently (I/O-bound), using
    the exact warmup-adjusted window run_rotation_backtest will request, so the
    backtest itself reads from cache instead of downloading serially."""
    warmup_start = (pd.Timestamp(cfg.backtest_start)
                    - pd.Timedelta(days=cfg.rotation_warmup_days)).strftime("%Y-%m-%d")
    symbols = list(dict.fromkeys(
        cfg.rotation_universe
        + ([cfg.rotation_cash_symbol] if cfg.rotation_cash_symbol else [])
        + [cfg.regime_symbol]
    ))
    data = get_data_source("yfinance")

    def _one(sym):
        t = time.time()
        try:
            got = data.history([sym], warmup_start, cfg.backtest_end)
            df = got.get(sym)
            if df is None or df.empty:
                return (sym, "NO DATA", 0, None)
            first = df.index.min().strftime("%Y-%m-%d")
            return (sym, f"{len(df)} bars from {first}", time.time() - t, first)
        except Exception as e:  # noqa: BLE001
            return (sym, f"ERROR {e}", time.time() - t, None)

    print(f"Prefetching {len(symbols)} symbols in parallel "
          f"({warmup_start} → {cfg.backtest_end})...")
    with ThreadPoolExecutor(max_workers=len(symbols)) as ex:
        for sym, msg, dt, _first in sorted(ex.map(_one, symbols)):
            print(f"  {sym:5} {msg:34} ({dt:.1f}s)")


def _sim_worker(arg):
    """One Monte Carlo path: block-bootstrap the daily returns and measure.
    Top-level + re-imports so it is picklable under macOS 'spawn'."""
    import random as _random
    from trader.montecarlo import _block_bootstrap as _bb, _path_metrics as _pm
    daily, block, seed, start_cash = arg
    rng = _random.Random(seed)
    boot = _bb(daily, block, rng)
    return _pm(boot, start_cash)


def run(start: str, end: str, n_sims: int, block: int) -> dict:
    cfg = build_cfg(start, end)
    prefetch_parallel(cfg)

    from trader.rotation import run_rotation_backtest
    print(f"\nRunning REAL base-rotation backtest {start} → {end} "
          f"on {len(cfg.rotation_universe)} sectors...")
    t0 = time.time()
    base = run_rotation_backtest(cfg, verbose=False)
    broker = base["broker"]
    bench = base.get("benchmark", {})
    real_m = metrics.summarize(broker.equity_curve, broker.trades, cfg.starting_cash)
    print(f"  done in {time.time() - t0:.1f}s — "
          f"{len(broker.equity_curve)} trading days, {len(broker.trades)} trades")

    equities = [e for _, e in broker.equity_curve]
    daily = [equities[i] / equities[i - 1] - 1.0
             for i in range(1, len(equities)) if equities[i - 1] > 0]
    if len(daily) < 60:
        raise RuntimeError("Equity curve too short for Monte Carlo.")

    # --- 50 alternate histories, in parallel across cores ---
    print(f"\nRunning {n_sims} Monte Carlo paths in PARALLEL "
          f"(block bootstrap, block={block}d, {os.cpu_count()} cores)...")
    t1 = time.time()
    payload = [(daily, block, s, cfg.starting_cash) for s in range(1, n_sims + 1)]
    with ProcessPoolExecutor() as ex:
        sims = list(ex.map(_sim_worker, payload))
    mc_secs = time.time() - t1
    print(f"  {n_sims} sims done in {mc_secs:.2f}s")

    # --- aggregate the distribution ---
    returns = sorted(x["total_return"] for x in sims)
    cagrs = sorted(x["cagr"] for x in sims)
    dds = sorted(x["max_drawdown"] for x in sims)
    sharpes = sorted(x["sharpe"] for x in sims)
    eqs = sorted(x["final_equity"] for x in sims)

    spy_ret = bench.get("total_return")
    n_beat_spy = (sum(1 for r in returns if spy_ret is not None and r > spy_ret))

    summary = {
        "return_min": returns[0], "return_max": returns[-1],
        "return_p5": _percentile(returns, 5), "return_p25": _percentile(returns, 25),
        "return_p50": _percentile(returns, 50), "return_p75": _percentile(returns, 75),
        "return_p95": _percentile(returns, 95),
        "return_mean": sum(returns) / len(returns),
        "cagr_p5": _percentile(cagrs, 5), "cagr_p50": _percentile(cagrs, 50),
        "cagr_p95": _percentile(cagrs, 95),
        "dd_p50": _percentile(dds, 50), "dd_worst": dds[0], "dd_best": dds[-1],
        "sharpe_p5": _percentile(sharpes, 5), "sharpe_p50": _percentile(sharpes, 50),
        "sharpe_p95": _percentile(sharpes, 95),
        "equity_p50": _percentile(eqs, 50),
        "pct_profitable": sum(1 for r in returns if r > 0) / n_sims,
        "pct_dd_worse_20": sum(1 for d in dds if d <= -0.20) / n_sims,
        "pct_dd_worse_35": sum(1 for d in dds if d <= -0.35) / n_sims,
        "pct_doubled": sum(1 for r in returns if r >= 1.0) / n_sims,
        "pct_beat_spy": (n_beat_spy / n_sims) if spy_ret is not None else None,
    }

    return {
        "window": f"{start} → {end}", "n_sims": n_sims, "block_size": block,
        "mc_wall_seconds": round(mc_secs, 3),
        "universe": cfg.rotation_universe,
        "real": real_m, "spy_benchmark": {k: v for k, v in bench.items()
                                          if k != "equity_curve"},
        "summary": summary, "sims": sims,
    }


def report(res: dict) -> str:
    s = res["summary"]; real = res["real"]; bench = res["spy_benchmark"]
    n = res["n_sims"]
    L = ["", "=" * 70,
         f"  PARALLEL MONTE CARLO — base sector rotation — {res['window']}",
         f"  {n} alternate histories, block bootstrap (block={res['block_size']}d),"
         f" {res['mc_wall_seconds']}s wall",
         "=" * 70, "",
         "  ACTUAL TRACK RECORD (the one real 26-year path):",
         f"    Final equity : ${real.get('final_equity', 0):,.0f}  "
         f"(from ${100000:,.0f})",
         f"    Total return : {real.get('total_return', 0):+.1%}",
         f"    CAGR         : {real.get('cagr', 0):+.1%}",
         f"    Max drawdown : {real.get('max_drawdown', 0):.1%}",
         f"    Sharpe       : {real.get('sharpe', 0):.2f}",
         f"    Trades       : {real.get('num_closed', real.get('num_trades', 0))}"]
    if bench:
        edge = real.get("final_equity", 0) - bench.get("final_equity", 0)
        L += ["",
              "  vs SPY BUY & HOLD (same window, same data):",
              f"    SPY final    : ${bench.get('final_equity', 0):,.0f}   "
              f"(rotation edge ${edge:+,.0f})",
              f"    SPY CAGR     : {bench.get('cagr', 0):+.1%}   "
              f"(rotation {real.get('cagr', 0):+.1%})",
              f"    SPY max DD   : {bench.get('max_drawdown', 0):.1%}",
              f"    VERDICT      : "
              f"{'ROTATION BEATS SPY' if edge > 0 else 'underperforms SPY'}"]
    L += ["", f"  DISTRIBUTION ACROSS {n} SIMULATIONS:", "",
          "  Total return over the full window:",
          f"    worst   {s['return_min']:+.0%}    p5 {s['return_p5']:+.0%}"
          f"    p25 {s['return_p25']:+.0%}    median {s['return_p50']:+.0%}",
          f"    p75 {s['return_p75']:+.0%}    p95 {s['return_p95']:+.0%}"
          f"    best {s['return_max']:+.0%}    mean {s['return_mean']:+.0%}",
          "",
          f"  CAGR  (p5 / median / p95) : {s['cagr_p5']:+.1%}  /  "
          f"{s['cagr_p50']:+.1%}  /  {s['cagr_p95']:+.1%}",
          f"  Max DD(median/worst/best) : {s['dd_p50']:.1%}  /  "
          f"{s['dd_worst']:.1%}  /  {s['dd_best']:.1%}",
          f"  Sharpe(p5 / median / p95) : {s['sharpe_p5']:.2f}  /  "
          f"{s['sharpe_p50']:.2f}  /  {s['sharpe_p95']:.2f}",
          "",
          "  Probabilities across the 50 sims:",
          f"    profitable            : {s['pct_profitable']:.0%}",
          f"    doubled the account   : {s['pct_doubled']:.0%}",
          f"    drawdown worse -20%   : {s['pct_dd_worse_20']:.0%}",
          f"    drawdown worse -35%   : {s['pct_dd_worse_35']:.0%}"]
    if s.get("pct_beat_spy") is not None:
        L += [f"    beat SPY buy & hold   : {s['pct_beat_spy']:.0%}"]
    L += ["", "  RETURN DISTRIBUTION:",
          _histogram([x["total_return"] for x in res["sims"]]),
          "", "=" * 70]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="1999-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--sims", type=int, default=50)
    ap.add_argument("--block", type=int, default=15)
    a = ap.parse_args()

    res = run(a.start, a.end, a.sims, a.block)
    print(report(res))

    out = {k: v for k, v in res.items() if k != "sims"}
    out["sims_count"] = len(res["sims"])
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parallel_mc.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved → {path}")


if __name__ == "__main__":
    main()
