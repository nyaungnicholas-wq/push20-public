"""Multiple simulations of the NEW system (leveraged daily rotation, cap1.5 family).

Four parts:
  1. Multi-window eval of the refined champion + siblings (robustness across eras).
  2. Leverage-cap sweep 1.0x -> 3.0x (where does raising the cap stop paying?).
  3. Monte Carlo: 300 block-bootstrap alternate histories of the champion.
  4. LIMIT AUDIT: per-symbol real data inception/end vs what the harness assumes
     (the harness bfills prices before ETF inception -> fake flat data).

Run: .venv/bin/python reports/mc_new_system.py
"""
from __future__ import annotations
import os, sys, json, math, random, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from opt_harness import (load_data, simulate, metrics, spy_bh, full_eval,
                         UNIVERSE, DEFENSIVE_ASSETS, SPY_SYM, LEV2X,
                         FETCH_START, FETCH_END, WINDOWS, SYN_DRAG_ANNUAL)
from trader.montecarlo import _block_bootstrap, _percentile
from trader.data_source import get_data_source

CHAMPION = {"vol_cap_bull": 1.5}            # cap1.5_base — best composite from opt_refine
SIBLINGS = {
    "cap1.4":        {"vol_cap_bull": 1.4},
    "cap1.5_vt0.16": {"vol_cap_bull": 1.5, "vol_target": 0.16},
    "V6A_cap2.0":    {"vol_cap_bull": 2.0},
}

df = load_data()

# ── 1. multi-window eval ─────────────────────────────────────────────────────
print("=" * 100)
print("1) NEW SYSTEM — multi-window robustness (champion cap1.5_base + siblings)")
print("=" * 100)
spy_w = {n: metrics(spy_bh(df, a, b)) for n, a, b in WINDOWS}
champ_eval = full_eval(df, CHAMPION)
rows = {"cap1.5_base (CHAMPION)": champ_eval}
for lbl, cfg in SIBLINGS.items():
    rows[lbl] = full_eval(df, cfg)
print(f"\n  {'config':<24} {'window':<14} {'CAGR':>7} {'Sharpe':>7} {'MDD':>7} {'Calmar':>7} {'moWin':>6}   {'SPY cagr':>8}  edge")
for lbl, ev in rows.items():
    for wname, _, _ in [(w[0], w[1], w[2]) for w in WINDOWS]:
        m = ev[wname]; s = spy_w[wname]
        edge = m["cagr"] - s["cagr"]
        print(f"  {lbl:<24} {wname:<14} {m['cagr']:>+7.1%} {m['sharpe']:>7.2f} {m['mdd']:>+7.1%} "
              f"{m['calmar']:>7.2f} {m['win_rate_monthly']:>6.0%}   {s['cagr']:>+8.1%}  {edge:>+6.1%} {'WIN' if edge>0 else 'lose'}")
    oos = ev["outsample"]
    print(f"  {lbl:<24} {'OOS_16_24':<14} {oos['cagr']:>+7.1%} {oos['sharpe']:>7.2f} {oos['mdd']:>+7.1%}")
    print()

# ── 2. leverage-cap sweep ────────────────────────────────────────────────────
print("=" * 100)
print("2) LEVERAGE-CAP SWEEP (full 2006-2024) — is vol_cap_bull the binding limit?")
print("=" * 100)
print(f"\n  {'cap':>5} {'CAGR':>8} {'Sharpe':>7} {'MDD':>8} {'Calmar':>7} {'moWin':>6} {'lev_frac':>9} {'final':>14}")
sweep = {}
for cap in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]:
    r = simulate(df, {"vol_cap_bull": cap}, "2006-01-01", "2024-12-31")
    m = metrics(r)
    sweep[cap] = m
    print(f"  {cap:>4.2f}x {m['cagr']:>+8.1%} {m['sharpe']:>7.2f} {m['mdd']:>+8.1%} "
          f"{m['calmar']:>7.2f} {m['win_rate_monthly']:>6.0%} {m['lev_frac']:>9.0%} ${m['final']:>13,}")

# ── 3. Monte Carlo on the champion ──────────────────────────────────────────
print()
print("=" * 100)
print("3) MONTE CARLO — 300 block-bootstrap alternate histories (champion, 2006-2024)")
print("=" * 100)
real = simulate(df, CHAMPION, "2006-01-01", "2024-12-31")
curve = pd.Series([v for _, v in real["curve"]],
                  index=pd.to_datetime([d for d, _ in real["curve"]]))
rets = curve.pct_change().dropna().tolist()
yrs = (curve.index[-1] - curve.index[0]).days / 365.25
spy_real = metrics(spy_bh(df, "2006-01-01", "2024-12-31"))

N, BLOCK = 300, 15
cagrs, mdds, finals = [], [], []
for seed in range(1, N + 1):
    rng = random.Random(seed)
    path = _block_bootstrap(rets, BLOCK, rng)
    eq = 100000.0; peak = eq; mdd = 0.0
    for r in path:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = min(mdd, (eq - peak) / peak)
    cagrs.append((eq / 100000.0) ** (1 / yrs) - 1)
    mdds.append(mdd)
    finals.append(eq)
cagrs_s, mdds_s = sorted(cagrs), sorted(mdds)
p_beat_spy = sum(1 for c in cagrs if c > spy_real["cagr"]) / N
p_loss     = sum(1 for f in finals if f < 100000) / N
p_dd40     = sum(1 for d in mdds if d < -0.40) / N
real_m = metrics(real)
print(f"\n  Real backtest      : CAGR {real_m['cagr']:+.1%}  MDD {real_m['mdd']:+.1%}  final ${real_m['final']:,}")
print(f"  SPY same window    : CAGR {spy_real['cagr']:+.1%}  MDD {spy_real['mdd']:+.1%}")
print(f"\n  MC CAGR percentiles:  p5 {_percentile(cagrs_s,5):+.1%}   p25 {_percentile(cagrs_s,25):+.1%}   "
      f"p50 {_percentile(cagrs_s,50):+.1%}   p75 {_percentile(cagrs_s,75):+.1%}   p95 {_percentile(cagrs_s,95):+.1%}")
print(f"  MC MDD percentiles :  p5 {_percentile(mdds_s,5):+.1%}   p50 {_percentile(mdds_s,50):+.1%}   p95 {_percentile(mdds_s,95):+.1%}")
print(f"\n  P(beat SPY {spy_real['cagr']:+.1%})       : {p_beat_spy:.0%}")
print(f"  P(lose money over 19y)   : {p_loss:.0%}")
print(f"  P(drawdown worse than -40%): {p_dd40:.0%}")

# ── 4. LIMIT AUDIT — real data inception per symbol (post-fix status) ────────
print()
print("=" * 100)
print("4) LIMIT AUDIT — real inception per symbol (pre-inception now NaN-skipped or synthesized)")
print("=" * 100)
ds = get_data_source("yfinance")
syms = list(dict.fromkeys(UNIVERSE + DEFENSIVE_ASSETS + [SPY_SYM] + list(LEV2X.values())))
raw = ds.history(syms, FETCH_START, FETCH_END)
print(f"\n  Harness fetch window: {FETCH_START} -> {FETCH_END}   ({'STALE' if FETCH_END < '2026' else 'current'})")
print(f"\n  {'symbol':<8} {'first real bar':<15} {'last real bar':<15} note")
sim_start = pd.Timestamp("2006-01-01")
issues = []
for s in syms:
    d = raw.get(s)
    if d is None or d.empty:
        print(f"  {s:<8} {'MISSING':<15}")
        issues.append((s, "missing"))
        continue
    first, last = d.index[0], d.index[-1]
    note = ""
    if first > sim_start:
        gap_y = (first - sim_start).days / 365.25
        how = "synthesized from underlying" if s in LEV2X.values() else "NaN -> correctly skipped"
        note = f"<- starts {gap_y:.1f}y after sim start ({how})"
        issues.append((s, str(first.date())))
    print(f"  {s:<8} {str(first.date()):<15} {str(last.date()):<15} {note}")

lev_syms = [v for v in LEV2X.values()]
lev_starts = [raw[s].index[0] for s in lev_syms if s in raw and not raw[s].empty]
if lev_starts:
    print(f"\n  Last 2x ETF real inception (synth covers before): {max(lev_starts).date()}")
print(f"  Data end (FETCH_END)                            : {FETCH_END}")

# ── 5. SYNTHESIS VALIDATION — model (2x daily - drag) vs REAL lev ETF returns ─
print()
print("=" * 100)
print(f"5) SYNTHESIS VALIDATION — 2x model w/ {SYN_DRAG_ANNUAL:.1%}/yr drag vs real ETF (overlap era)")
print("=" * 100)
print(f"\n  {'pair':<12} {'overlap':<22} {'daily corr':>10} {'real CAGR':>10} {'model CAGR':>11} {'drift/yr':>9}")
for und, lv in [("XLK","ROM"),("QQQ","QLD"),("XLE","DIG"),("TLT","UBT"),("GLD","UGL")]:
    if und not in df.columns or lv not in df.columns: continue
    ru = raw.get(und); rl = raw.get(lv)
    if ru is None or rl is None: continue
    u = ru["close"]; l = rl["close"]
    both = pd.concat([u, l], axis=1, keys=["u","l"]).dropna()
    if len(both) < 252: continue
    r_u = both["u"].pct_change().dropna()
    r_l = both["l"].pct_change().dropna()
    model = 2.0 * r_u - SYN_DRAG_ANNUAL / 252
    corr = float(np.corrcoef(model.values, r_l.values)[0,1])
    yrs_o = len(r_l) / 252
    real_cagr  = float((1+r_l).prod() ** (1/yrs_o) - 1)
    model_cagr = float((1+model).prod() ** (1/yrs_o) - 1)
    print(f"  {und+'->'+lv:<12} {str(both.index[0].date())+' .. '+str(both.index[-1].date()):<22} "
          f"{corr:>10.4f} {real_cagr:>+10.1%} {model_cagr:>+11.1%} {model_cagr-real_cagr:>+9.1%}")

out = {
    "champion_eval": champ_eval,
    "cap_sweep": {str(k): v for k, v in sweep.items()},
    "mc": {"n": N, "block": BLOCK,
           "cagr_p5_50_95": [_percentile(cagrs_s, p) for p in (5, 50, 95)],
           "mdd_p5_50_95": [_percentile(mdds_s, p) for p in (5, 50, 95)],
           "p_beat_spy": p_beat_spy, "p_loss": p_loss, "p_dd_worse_40": p_dd40},
    "data_limits": {s: n for s, n in issues},
}
with open(os.path.join(os.path.dirname(__file__), "mc_new_system_results.json"), "w") as fh:
    json.dump(out, fh, indent=2, default=str)
print("\n  Saved -> reports/mc_new_system_results.json")
