"""Find the least-risky config that clears >=20% CAGR — honest frontier search.

Stage 1: screen ~24 aggression variants on full_06_24 + modern_15_24.
Stage 2: full multi-window eval of the top candidates (incl. OOS + live era).
Stage 3: Monte Carlo tail-risk check on the winner.

Run: .venv/bin/python reports/push20_search.py
"""
from __future__ import annotations
import os, sys, json, random, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from opt_harness import load_data, simulate, metrics, spy_bh, full_eval, WINDOWS
from trader.montecarlo import _block_bootstrap, _percentile

df = load_data()

GRID = {
    # vol-target ladder on the proven cap1.5 base
    "c1.5_vt16":          {"vol_cap_bull":1.5,"vol_target":0.16},
    "c1.5_vt18":          {"vol_cap_bull":1.5,"vol_target":0.18},
    "c1.5_vt20":          {"vol_cap_bull":1.5,"vol_target":0.20},
    "c1.5_vt22":          {"vol_cap_bull":1.5,"vol_target":0.22},
    "c1.5_vt25":          {"vol_cap_bull":1.5,"vol_target":0.25},
    # concentration
    "c1.5_top2":          {"vol_cap_bull":1.5,"top_n":2},
    "c1.5_top2_vt16":     {"vol_cap_bull":1.5,"top_n":2,"vol_target":0.16},
    "c1.5_top2_vt18":     {"vol_cap_bull":1.5,"top_n":2,"vol_target":0.18},
    "c1.5_top2_vt20":     {"vol_cap_bull":1.5,"top_n":2,"vol_target":0.20},
    # weight scheme
    "c1.5_vt18_sq":       {"vol_cap_bull":1.5,"vol_target":0.18,"weight_scheme":"squared"},
    "c1.5_vt20_sq":       {"vol_cap_bull":1.5,"vol_target":0.20,"weight_scheme":"squared"},
    "c1.5_top2_vt18_sq":  {"vol_cap_bull":1.5,"top_n":2,"vol_target":0.18,"weight_scheme":"squared"},
    # higher caps (sweep said worse at vt0.15 — but higher vt changes the bind)
    "c1.75_vt18":         {"vol_cap_bull":1.75,"vol_target":0.18},
    "c1.75_vt20":         {"vol_cap_bull":1.75,"vol_target":0.20},
    "c2.0_vt20":          {"vol_cap_bull":2.0,"vol_target":0.20},
    "c2.0_vt22":          {"vol_cap_bull":2.0,"vol_target":0.22},
    "c2.0_vt25":          {"vol_cap_bull":2.0,"vol_target":0.25},
    # diversified family (user's V6D shape, pushed)
    "V6D_live":           {"vol_cap_bull":2.0,"vol_target":0.20,"top_n":5,"max_weight":0.30},
    "V6D_vt22":           {"vol_cap_bull":2.0,"vol_target":0.22,"top_n":5,"max_weight":0.30},
    "V6D_vt25":           {"vol_cap_bull":2.0,"vol_target":0.25,"top_n":5,"max_weight":0.30},
    "top4_vt20_mw35":     {"vol_cap_bull":1.5,"vol_target":0.20,"top_n":4,"max_weight":0.35},
    "top4_vt18_mw35":     {"vol_cap_bull":1.5,"vol_target":0.18,"top_n":4,"max_weight":0.35},
    # defensive sleeve ranked by momentum (hold best of GLD/TLT instead of 50/50)
    "c1.5_vt18_defmom":   {"vol_cap_bull":1.5,"vol_target":0.18,"defensive_momentum":True},
    "c1.5_vt20_defmom":   {"vol_cap_bull":1.5,"vol_target":0.20,"defensive_momentum":True},
}

spy_full = metrics(spy_bh(df, "2006-01-01", "2024-12-31"))
spy_mod  = metrics(spy_bh(df, "2015-01-01", "2024-12-31"))

print("=" * 110)
print(f"STAGE 1 — screen {len(GRID)} configs   (SPY full {spy_full['cagr']:+.1%} / modern {spy_mod['cagr']:+.1%})")
print("=" * 110)
print(f"\n  {'config':<20} {'FULL cagr':>9} {'mdd':>7} {'calmar':>7} {'sharpe':>7} {'moWin':>6}   {'MODERN cagr':>11} {'mdd':>7}   20%?")
screen = {}
for lbl, cfg in GRID.items():
    mf = metrics(simulate(df, cfg, "2006-01-01", "2024-12-31"))
    mm = metrics(simulate(df, cfg, "2015-01-01", "2024-12-31"))
    screen[lbl] = (mf, mm)
    ok = "  <<<" if (mf["cagr"] >= 0.20 and mm["cagr"] >= 0.20) else ""
    print(f"  {lbl:<20} {mf['cagr']:>+9.1%} {mf['mdd']:>+7.0%} {mf['calmar']:>7.2f} {mf['sharpe']:>7.2f} {mf['win_rate_monthly']:>6.0%}"
          f"   {mm['cagr']:>+11.1%} {mm['mdd']:>+7.0%}{ok}")

# rank: must clear 20% on BOTH full and modern; then maximize full-window Calmar
qual = [l for l in screen if screen[l][0]["cagr"] >= 0.20 and screen[l][1]["cagr"] >= 0.20]
if not qual:   # fall back: closest to 20
    qual = sorted(screen, key=lambda l: -screen[l][0]["cagr"])[:5]
top = sorted(qual, key=lambda l: -screen[l][0]["calmar"])[:5]

print()
print("=" * 110)
print(f"STAGE 2 — full multi-window eval of top {len(top)} candidates")
print("=" * 110)
evals = {}
for lbl in top:
    evals[lbl] = full_eval(df, GRID[lbl])
spy_w = {n: metrics(spy_bh(df, a, b)) for n, a, b in WINDOWS}
for lbl in top:
    print(f"\n  {lbl}   {json.dumps(GRID[lbl])}")
    for wname, a, b in WINDOWS:
        m = evals[lbl][wname]; s = spy_w[wname]
        if not m: continue
        print(f"    {wname:<14} CAGR {m['cagr']:>+6.1%}  Sharpe {m['sharpe']:>5.2f}  MDD {m['mdd']:>+6.1%}  "
              f"Calmar {m['calmar']:>5.2f}  moWin {m['win_rate_monthly']:>4.0%}   SPY {s['cagr']:>+6.1%}  "
              f"{'WIN' if m['cagr']>s['cagr'] else 'lose'}")
    oos = evals[lbl]["outsample"]; ins = evals[lbl]["insample"]
    print(f"    {'insample':<14} CAGR {ins['cagr']:>+6.1%}   |   outsample CAGR {oos['cagr']:>+6.1%}  (overfit check)")

# winner: highest full-window calmar among qualifiers
winner = top[0]
wcfg = GRID[winner]

print()
print("=" * 110)
print(f"STAGE 3 — Monte Carlo tail risk on WINNER: {winner}  {json.dumps(wcfg)}")
print("=" * 110)
real = simulate(df, wcfg, "2006-01-01", "2024-12-31")
curve = pd.Series([v for _, v in real["curve"]],
                  index=pd.to_datetime([d for d, _ in real["curve"]]))
rets = curve.pct_change().dropna().tolist()
yrs = (curve.index[-1] - curve.index[0]).days / 365.25
N, BLOCK = 300, 15
cagrs, mdds = [], []
for seed in range(1, N + 1):
    rng = random.Random(seed)
    path = _block_bootstrap(rets, BLOCK, rng)
    eq = 100000.0; peak = eq; mdd = 0.0
    for r in path:
        eq *= (1.0 + r); peak = max(peak, eq); mdd = min(mdd, (eq - peak) / peak)
    cagrs.append((eq / 100000.0) ** (1 / yrs) - 1); mdds.append(mdd)
cs, ms = sorted(cagrs), sorted(mdds)
print(f"\n  MC CAGR:  p5 {_percentile(cs,5):+.1%}   p25 {_percentile(cs,25):+.1%}   p50 {_percentile(cs,50):+.1%}   p95 {_percentile(cs,95):+.1%}")
print(f"  MC MDD :  p5 {_percentile(ms,5):+.1%}   p50 {_percentile(ms,50):+.1%}   p95 {_percentile(ms,95):+.1%}")
print(f"  P(CAGR >= 20%) : {sum(1 for c in cagrs if c>=0.20)/N:.0%}")
print(f"  P(beat SPY)    : {sum(1 for c in cagrs if c>spy_full['cagr'])/N:.0%}")
print(f"  P(MDD < -40%)  : {sum(1 for d in mdds if d<-0.40)/N:.0%}")
print(f"  P(MDD < -50%)  : {sum(1 for d in mdds if d<-0.50)/N:.0%}")

out = {"screen": {l: {"full": screen[l][0], "modern": screen[l][1]} for l in screen},
       "finalists": {l: evals[l] for l in top}, "winner": winner, "winner_cfg": wcfg,
       "mc": {"cagr_p5_50_95": [_percentile(cs,p) for p in (5,50,95)],
              "mdd_p5_50_95": [_percentile(ms,p) for p in (5,50,95)],
              "p_cagr_ge_20": sum(1 for c in cagrs if c>=0.20)/N,
              "p_mdd_lt_40": sum(1 for d in mdds if d<-0.40)/N}}
with open(os.path.join(os.path.dirname(__file__), "push20_results.json"), "w") as fh:
    json.dump(out, fh, indent=2)
print("\n  Saved -> reports/push20_results.json")
