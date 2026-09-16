"""What if PUSH-20 watched the FUTURES market (ES/NQ) to anticipate the trend?

Futures trade ~23h/day and react to news first — the question is whether that
lead survives to PUSH-20's daily-close, 232-day-momentum horizon.

Tests (full 2006-2024 + through today, via ext_gate):
  1. Information check: how different are ES=F daily closes from SPY's?
  2. Futures trend gate:    ES below its 50d SMA  -> no leverage
  3. Futures momentum gate: ES 20d return < 0     -> no leverage
  4. Overnight panic gate:  ES down >1.5% on day  -> no leverage next day
  5. NQ (tech futures) momentum gate              -> no leverage

Run: .venv/bin/python reports/futures_signal_test.py
"""
from __future__ import annotations
import os, sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from opt_harness import load_data, simulate, metrics, spy_bh, FETCH_END, FETCH_START
from trader.data_source import get_data_source

P = {"vol_cap_bull": 1.5, "vol_target": 0.22, "max_weight": 0.50}

df = load_data()
ds = get_data_source("yfinance")
fut = ds.history(["ES=F", "NQ=F"], FETCH_START, FETCH_END)
es = fut.get("ES=F", pd.DataFrame()).get("close")
nq = fut.get("NQ=F", pd.DataFrame()).get("close")
if es is None or es.empty:
    sys.exit("ES=F futures data unavailable")

# ── 1. information check ─────────────────────────────────────────────────────
spy = df["SPY"].dropna()
both = pd.concat([es, spy], axis=1, keys=["es", "spy"]).dropna()
r = both.pct_change().dropna()
corr = float(np.corrcoef(r["es"], r["spy"])[0, 1])
# does yesterday's futures return predict today's SPY return? (the "lead" claim)
lead = float(np.corrcoef(r["es"].iloc[:-1], r["spy"].iloc[1:])[0, 1])
print("=" * 100)
print("FUTURES-SIGNAL EXPERIMENT — PUSH-20 watching ES/NQ futures")
print("=" * 100)
print(f"\n  1) Information check ({both.index[0].date()} -> {both.index[-1].date()}, {len(r)} days)")
print(f"     Same-day corr(ES futures, SPY) : {corr:.4f}   <- at daily closes they are the same series")
print(f"     Yesterday-ES -> today-SPY corr : {lead:+.4f}   <- the 'futures see it first' lead at daily horizon")

def gate_dict(mask: pd.Series, cap: float = 1.0):
    return {d.strftime("%Y-%m-%d"): cap for d, bad in mask.items() if bad}

es_sma50 = es.rolling(50).mean()
es_mom20 = es.pct_change(20)
es_d1 = es.pct_change()
gates = {
    "baseline (no futures input)": None,
    "ES < 50d SMA -> no leverage": gate_dict((es < es_sma50)),
    "ES 20d momentum < 0 -> no leverage": gate_dict((es_mom20 < 0)),
    "ES overnight panic (-1.5%) -> no lev next day": gate_dict((es_d1 < -0.015).shift(1).fillna(False)),
}
if nq is not None and not nq.empty:
    gates["NQ 20d momentum < 0 -> no leverage"] = gate_dict((nq.pct_change(20) < 0))

spy_m = metrics(spy_bh(df, "2006-01-01", "2024-12-31"))
print(f"\n  2) Strategy replay (2006-2024 full window; SPY {spy_m['cagr']:+.1%}/-55%)")
print(f"\n  {'variant':<46} {'CAGR':>7} {'MDD':>7} {'Calmar':>7} {'Sharpe':>7} {'moWin':>6} {'now CAGR':>9}")
print("  " + "-" * 96)
out = {}
for name, g in gates.items():
    cfg = dict(P)
    if g is not None:
        cfg["ext_gate"] = g
    m = metrics(simulate(df, cfg, "2006-01-01", "2024-12-31"))
    mn = metrics(simulate(df, cfg, "2006-01-01", FETCH_END))
    print(f"  {name:<46} {m['cagr']:>+7.1%} {m['mdd']:>+7.1%} {m['calmar']:>7.2f} "
          f"{m['sharpe']:>7.2f} {m['win_rate_monthly']:>6.0%} {mn['cagr']:>+9.1%}")
    out[name] = {"full_06_24": {k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe")},
                 "full_06_now_cagr": mn["cagr"], "gated_days": len(g) if g else 0}

with open(os.path.join(os.path.dirname(__file__), "futures_signal_results.json"), "w") as fh:
    json.dump({"corr_same_day": corr, "corr_lead": lead, "results": out}, fh, indent=1)
print("\n  Saved -> reports/futures_signal_results.json")
