"""MAXIMUM-CAGR frontier search — how high can honest CAGR go, and at what cost?

Stage 0: validate 3x synthesis vs real TQQQ/TECL/SOXL/FAS overlap.
Stage 1: aggressive grid (3x tier, top1-3, vol_target up to 'always max') on
         full_06_24 AND early_06_14 (the 2008 test) + wipeout depth.
Stage 2: full multi-window eval of the top candidates by CAGR.
Stage 3: Monte Carlo on the top two (the max and the best-surviving max).
Benchmarks: SPY, QLD B&H, TQQQ B&H (same window, synthesized pre-2010).

Run: .venv/bin/python reports/maxcagr_search.py
"""
from __future__ import annotations
import os, sys, json, random, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from opt_harness import (load_data, simulate, metrics, spy_bh, full_eval,
                         WINDOWS, LEV3X, SYN_DRAG_3X)
from trader.montecarlo import _block_bootstrap, _percentile

df = load_data()
W0, W1 = "2006-01-01", "2024-12-31"

# ── Stage 0: 3x synthesis validation ─────────────────────────────────────────
print("=" * 108)
print(f"0) 3x SYNTHESIS VALIDATION — model (3x daily − {SYN_DRAG_3X:.0%}/yr) vs real ETF returns (overlap)")
print("=" * 108)
print(f"\n  {'pair':<12} {'overlap':<24} {'daily corr':>10} {'real CAGR':>10} {'model CAGR':>11} {'drift/yr':>9}")
from trader.data_source import get_data_source
ds = get_data_source("yfinance")
for und, lv in LEV3X.items():
    if und not in df.columns or lv not in df.columns: continue
    raw = ds.history([und, lv], "2003-01-01", pd.Timestamp.today().strftime("%Y-%m-%d"))
    if und not in raw or lv not in raw: continue
    both = pd.concat([raw[und]["close"], raw[lv]["close"]], axis=1, keys=["u","l"]).dropna()
    if len(both) < 252: continue
    r_u = both["u"].pct_change().dropna(); r_l = both["l"].pct_change().dropna()
    model = 3.0 * r_u - SYN_DRAG_3X / 252
    corr = float(np.corrcoef(model.values, r_l.values)[0,1])
    yrs_o = len(r_l) / 252
    rc = float((1+r_l).prod() ** (1/yrs_o) - 1); mc_ = float((1+model).prod() ** (1/yrs_o) - 1)
    print(f"  {und+'->'+lv:<12} {str(both.index[0].date())+' .. '+str(both.index[-1].date()):<24} "
          f"{corr:>10.4f} {rc:>+10.1%} {mc_:>+11.1%} {mc_-rc:>+9.1%}")

# ── benchmarks ───────────────────────────────────────────────────────────────
def bh(sym, a=W0, b=W1):
    s = df[sym][(df.index >= a) & (df.index <= b)].dropna().astype(float)
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    eq = s / s.iloc[0]
    mdd = float(((eq - eq.cummax()) / eq.cummax()).min())
    return float(eq.iloc[-1] ** (1/yrs) - 1), mdd

spy_m = metrics(spy_bh(df, W0, W1))
qld_c, qld_d = bh("QLD"); tqqq_c, tqqq_d = bh("TQQQ")
print(f"\n  BENCHMARKS 2006-2024:  SPY {spy_m['cagr']:+.1%}/-55%   "
      f"QLD B&H {qld_c:+.1%}/{qld_d:+.0%}   TQQQ B&H {tqqq_c:+.1%}/{tqqq_d:+.0%}")

# ── Stage 1: aggressive grid ─────────────────────────────────────────────────
GRID = {
    # ---- 3x tier, top-1 (max concentration) ----
    "3x_top1_vt30":        {"use_3x":1,"top_n":1,"vol_cap_bull":3.0,"vol_target":0.30},
    "3x_top1_vt40":        {"use_3x":1,"top_n":1,"vol_cap_bull":3.0,"vol_target":0.40},
    "3x_top1_alwaysmax":   {"use_3x":1,"top_n":1,"vol_cap_bull":3.0,"vol_target":9.9},
    "3x_top1_vt40_bear15": {"use_3x":1,"top_n":1,"vol_cap_bull":3.0,"vol_target":0.40,"vol_cap_bear":1.5},
    "3x_top1_max_bear3":   {"use_3x":1,"top_n":1,"vol_cap_bull":3.0,"vol_target":9.9,"vol_cap_bear":3.0},
    # ---- 3x tier, top-2 ----
    "3x_top2_vt30":        {"use_3x":1,"top_n":2,"vol_cap_bull":3.0,"vol_target":0.30},
    "3x_top2_vt40":        {"use_3x":1,"top_n":2,"vol_cap_bull":3.0,"vol_target":0.40},
    "3x_top2_alwaysmax":   {"use_3x":1,"top_n":2,"vol_cap_bull":3.0,"vol_target":9.9},
    "3x_top2_vt40_sq":     {"use_3x":1,"top_n":2,"vol_cap_bull":3.0,"vol_target":0.40,"weight_scheme":"squared"},
    # ---- 3x tier, top-3 (diversified-ish max) ----
    "3x_top3_vt30":        {"use_3x":1,"top_n":3,"vol_cap_bull":3.0,"vol_target":0.30},
    "3x_top3_vt40":        {"use_3x":1,"top_n":3,"vol_cap_bull":3.0,"vol_target":0.40},
    "3x_top3_alwaysmax":   {"use_3x":1,"top_n":3,"vol_cap_bull":3.0,"vol_target":9.9},
    "3x_top3_max_bear3":   {"use_3x":1,"top_n":3,"vol_cap_bull":3.0,"vol_target":9.9,"vol_cap_bear":3.0},
    "3x_top3_vt40_bear15": {"use_3x":1,"top_n":3,"vol_cap_bull":3.0,"vol_target":0.40,"vol_cap_bear":1.5},
    # ---- 2x-only controls (is 3x actually worth it?) ----
    "2x_top1_vt30":        {"top_n":1,"vol_cap_bull":2.0,"vol_target":0.30},
    "2x_top1_alwaysmax":   {"top_n":1,"vol_cap_bull":2.0,"vol_target":9.9},
    "2x_top3_vt30":        {"top_n":3,"vol_cap_bull":2.0,"vol_target":0.30},
    "2x_top3_alwaysmax":   {"top_n":3,"vol_cap_bull":2.0,"vol_target":9.9},
    # ---- current live, for reference ----
    "V7_PUSH20_live":      {"vol_cap_bull":1.5,"vol_target":0.22,"max_weight":0.50},
}

def run_one(cfg, a, b):
    r = simulate(df, cfg, a, b)
    m = metrics(r)
    vals = [v for _, v in r["curve"]]
    m["min_equity"] = round(min(vals)) if vals else 0
    return m

print()
print("=" * 108)
print("1) MAX-CAGR GRID — full window AND the 2008 test (early era), wipeout depth tracked")
print("=" * 108)
print(f"\n  {'config':<22} {'FULL cagr':>9} {'mdd':>7} {'calmar':>7} {'minEq':>9}   {'EARLY cagr':>10} {'mdd':>7}   {'final $':>13}")
screen = {}
for lbl, cfg in GRID.items():
    mf = run_one(cfg, W0, W1)
    me = run_one(cfg, "2006-01-01", "2014-12-31")
    screen[lbl] = (mf, me)
    print(f"  {lbl:<22} {mf['cagr']:>+9.1%} {mf['mdd']:>+7.0%} {mf['calmar']:>7.2f} ${mf['min_equity']:>8,}"
          f"   {me['cagr']:>+10.1%} {me['mdd']:>+7.0%}   ${mf['final']:>12,}")

# ── Stage 2: full eval of top-5 by full-window CAGR ──────────────────────────
top = sorted(screen, key=lambda l: -screen[l][0]["cagr"])[:5]
print()
print("=" * 108)
print("2) FULL MULTI-WINDOW EVAL — top 5 by raw CAGR")
print("=" * 108)
spy_w = {n: metrics(spy_bh(df, a, b)) for n, a, b in WINDOWS}
evals = {}
for lbl in top:
    evals[lbl] = full_eval(df, GRID[lbl])
    print(f"\n  {lbl}   {json.dumps(GRID[lbl])}")
    for wname, a, b in WINDOWS:
        m = evals[lbl][wname]; s = spy_w[wname]
        if not m: continue
        print(f"    {wname:<14} CAGR {m['cagr']:>+6.1%}  Sharpe {m['sharpe']:>5.2f}  MDD {m['mdd']:>+6.1%}  "
              f"Calmar {m['calmar']:>5.2f}  moWin {m['win_rate_monthly']:>4.0%}   SPY {s['cagr']:>+6.1%}")
    oos = evals[lbl]["outsample"]; ins = evals[lbl]["insample"]
    print(f"    insample {ins['cagr']:+.1%}  |  outsample {oos['cagr']:+.1%}")

# ── Stage 3: Monte Carlo on the top two ──────────────────────────────────────
print()
print("=" * 108)
print("3) MONTE CARLO — top 2 configs, 300 block-bootstrap alternate histories")
print("=" * 108)
mc_out = {}
for lbl in top[:2]:
    r = simulate(df, GRID[lbl], W0, W1)
    curve = pd.Series([v for _, v in r["curve"]],
                      index=pd.to_datetime([d for d, _ in r["curve"]]))
    rets = curve.pct_change().dropna().tolist()
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    cagrs, mdds = [], []
    for seed in range(1, 301):
        rng = random.Random(seed)
        path = _block_bootstrap(rets, 15, rng)
        eq = 1e5; pk = eq; dd = 0.0
        for x in path:
            eq *= (1 + x); pk = max(pk, eq); dd = min(dd, (eq - pk) / pk)
        cagrs.append((eq / 1e5) ** (1 / yrs) - 1); mdds.append(dd)
    cs, ms = sorted(cagrs), sorted(mdds)
    mc_out[lbl] = {"cagr_p5_50_95": [_percentile(cs, p) for p in (5, 50, 95)],
                   "mdd_p5_50_95": [_percentile(ms, p) for p in (5, 50, 95)],
                   "p_mdd_70": sum(1 for d in mdds if d < -0.70) / 300,
                   "p_mdd_80": sum(1 for d in mdds if d < -0.80) / 300,
                   "p_beat_spy": sum(1 for c in cagrs if c > spy_m["cagr"]) / 300}
    o = mc_out[lbl]
    print(f"\n  {lbl}")
    print(f"    MC CAGR p5/p50/p95 : {o['cagr_p5_50_95'][0]:+.1%} / {o['cagr_p5_50_95'][1]:+.1%} / {o['cagr_p5_50_95'][2]:+.1%}")
    print(f"    MC MDD  p5/p50/p95 : {o['mdd_p5_50_95'][0]:+.1%} / {o['mdd_p5_50_95'][1]:+.1%} / {o['mdd_p5_50_95'][2]:+.1%}")
    print(f"    P(beat SPY) {o['p_beat_spy']:.0%}   P(MDD<-70%) {o['p_mdd_70']:.0%}   P(MDD<-80%) {o['p_mdd_80']:.0%}")

out = {"screen": {l: {"full": screen[l][0], "early": screen[l][1]} for l in screen},
       "top": top, "evals": {l: evals[l] for l in top}, "mc": mc_out,
       "benchmarks": {"spy": spy_m["cagr"], "qld_bh": [qld_c, qld_d], "tqqq_bh": [tqqq_c, tqqq_d]}}
with open(os.path.join(os.path.dirname(__file__), "maxcagr_results.json"), "w") as fh:
    json.dump(out, fh, indent=2)
print("\n  Saved -> reports/maxcagr_results.json")
