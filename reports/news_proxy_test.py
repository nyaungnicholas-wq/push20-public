"""Does PUSH-20 improve if it reacts to news? — honest proxy experiment.

Real 20-year headline-sentiment data does not exist, so we use the VIX (the
market's fear index — it IS the price of news risk: 2008, 2020, 2022 all show
as VIX spikes) as the backtestable stand-in. Variants gate/cut leverage when
fear is elevated, exactly what a news feed would be used for.

Run: .venv/bin/python reports/news_proxy_test.py
"""
from __future__ import annotations
import os, sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from opt_harness import load_data, simulate, metrics, spy_bh, FETCH_END

PUSH20 = {"vol_cap_bull": 1.5, "vol_target": 0.22, "max_weight": 0.50}

VARIANTS = {
    "PUSH-20 baseline (no news)":      {},
    "mild: VIX>35 -> no leverage":     {"vix_gate_level": 35, "vix_gate_cap": 1.0},
    "moderate: VIX>30 -> no leverage": {"vix_gate_level": 30, "vix_gate_cap": 1.0},
    "strong: VIX>25 -> no leverage":   {"vix_gate_level": 25, "vix_gate_cap": 1.0},
    "panic-cut: VIX>30 -> half size":  {"vix_gate_level": 30, "vix_gate_cap": 0.5},
    "extreme-only: VIX>45 -> half":    {"vix_gate_level": 45, "vix_gate_cap": 0.5},
    "spike: VIX +30% in 5d -> no lev": {"vix_spike_pct": 0.30, "vix_gate_cap": 1.0},
}

CRISES = ["2008", "2020", "2022", "2025"]

df = load_data()
print("=" * 112)
print("NEWS-REACTION EXPERIMENT — PUSH-20 with VIX fear gates (2006 -> %s, 5bps)" % FETCH_END)
print("=" * 112)
spy = metrics(spy_bh(df, "2006-01-01", "2024-12-31"))
print(f"  SPY benchmark: CAGR {spy['cagr']:+.1%}  MDD {spy['mdd']:+.1%}\n")
hdr = f"  {'variant':<34} {'CAGR':>7} {'MDD':>7} {'Calmar':>7} {'Sharpe':>7} {'moWin':>6}"
hdr += "".join(f" {('yr'+c):>8}" for c in CRISES)
print(hdr)
print("  " + "-" * 108)

out = {}
base_m = None
for name, knobs in VARIANTS.items():
    cfg = {**PUSH20, **knobs}
    m = metrics(simulate(df, cfg, "2006-01-01", "2024-12-31"))
    mn = metrics(simulate(df, cfg, "2006-01-01", FETCH_END))   # incl. 2025-26
    py = mn.get("per_year", {})
    row = (f"  {name:<34} {m['cagr']:>+7.1%} {m['mdd']:>+7.1%} {m['calmar']:>7.2f} "
           f"{m['sharpe']:>7.2f} {m['win_rate_monthly']:>6.0%}")
    row += "".join(f" {py.get(c, float('nan')):>+8.1%}" if c in py else f" {'—':>8}" for c in CRISES)
    if base_m is None:
        base_m = m
    else:
        row += f"   | dCAGR {m['cagr']-base_m['cagr']:+.1%}  dMDD {m['mdd']-base_m['mdd']:+.1%}"
    print(row)
    out[name] = {"full_06_24": m, "per_year_to_now": py}

with open(os.path.join(os.path.dirname(__file__), "news_proxy_results.json"), "w") as fh:
    json.dump(out, fh, indent=1, default=str)
print("\n  Saved -> reports/news_proxy_results.json")
