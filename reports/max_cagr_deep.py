"""MAX-CAGR deep evaluation of one config: all windows + real-3x-only era +
cost sensitivity + per-year + 300-path Monte Carlo.

Run: .venv/bin/python reports/max_cagr_deep.py --label NAME --config '{"top_n":1,...}'
Prints compact JSON headline; writes reports/max_deep_NAME.json with everything.
"""
from __future__ import annotations
import os, sys, json, random, argparse, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from opt_harness import load_data, simulate, metrics, spy_bh, FETCH_END
from trader.montecarlo import _block_bootstrap, _percentile

ap = argparse.ArgumentParser()
ap.add_argument("--label", type=str, required=True)
ap.add_argument("--config", type=str, required=True)
args = ap.parse_args()
cfg = json.loads(args.config)
df = load_data()

WINS = [
    ("full_06_24",   "2006-01-01", "2024-12-31"),
    ("early_06_14",  "2006-01-01", "2014-12-31"),
    ("modern_15_24", "2015-01-01", "2024-12-31"),
    ("recent_18_24", "2018-01-01", "2024-12-31"),
    ("real3x_11_24", "2011-01-01", "2024-12-31"),   # ALL-REAL leveraged data (no synthesis)
    ("live_25_now",  "2025-01-01", FETCH_END),
    ("full_06_now",  "2006-01-01", FETCH_END),
    ("insample_06_15", "2006-01-01", "2015-12-31"),
    ("outsample_16_24", "2016-01-01", "2024-12-31"),
]
windows = {}
for name, a, b in WINS:
    m = metrics(simulate(df, cfg, a, b))
    s = metrics(spy_bh(df, a, b))
    if m:
        m["spy_cagr"] = s.get("cagr")
        windows[name] = m

# cost sensitivity on the full window
costs = {}
for bps in (10, 20):
    c2 = {**cfg, "cost_bps": bps}
    m = metrics(simulate(df, c2, "2006-01-01", "2024-12-31"))
    costs[f"{bps}bps"] = {"cagr": m["cagr"], "mdd": m["mdd"]}

# Monte Carlo on the full window
real = simulate(df, cfg, "2006-01-01", "2024-12-31")
curve = pd.Series([v for _, v in real["curve"]],
                  index=pd.to_datetime([d for d, _ in real["curve"]]))
rets = curve.pct_change().dropna().tolist()
yrs = (curve.index[-1] - curve.index[0]).days / 365.25
spy_full = windows["full_06_24"]["spy_cagr"]
cagrs, mdds = [], []
for seed in range(1, 301):
    rng = random.Random(seed)
    path = _block_bootstrap(rets, 15, rng)
    eq = 1e5; pk = eq; dd = 0.0
    for r in path:
        eq *= (1 + r); pk = max(pk, eq); dd = min(dd, (eq - pk) / pk)
    cagrs.append((eq / 1e5) ** (1 / yrs) - 1); mdds.append(dd)
cs, ms = sorted(cagrs), sorted(mdds)
mc = {"cagr_p5": _percentile(cs, 5), "cagr_p50": _percentile(cs, 50),
      "cagr_p95": _percentile(cs, 95),
      "mdd_p5": _percentile(ms, 5), "mdd_p50": _percentile(ms, 50),
      "p_beat_spy": sum(1 for c in cagrs if c > spy_full) / 300,
      "p_mdd_lt_50": sum(1 for d in mdds if d < -0.50) / 300,
      "p_mdd_lt_60": sum(1 for d in mdds if d < -0.60) / 300,
      "p_mdd_lt_70": sum(1 for d in mdds if d < -0.70) / 300}

per_year = windows["full_06_now"].get("per_year", {})
worst_year = min(per_year.values()) if per_year else None

out = {"label": args.label, "cfg": cfg, "windows": windows,
       "cost_sensitivity": costs, "mc": mc,
       "per_year": per_year, "worst_year": worst_year}
path = os.path.join(os.path.dirname(__file__), f"max_deep_{args.label}.json")
with open(path, "w") as fh:
    json.dump(out, fh, indent=1)

f = windows["full_06_24"]
print(json.dumps({
    "label": args.label, "file": path,
    "full_cagr": f["cagr"], "full_mdd": f["mdd"], "full_calmar": f["calmar"],
    "modern_cagr": windows["modern_15_24"]["cagr"],
    "real11_cagr": windows["real3x_11_24"]["cagr"],
    "real11_spy": windows["real3x_11_24"]["spy_cagr"],
    "oos_cagr": windows["outsample_16_24"]["cagr"],
    "insample_cagr": windows["insample_06_15"]["cagr"],
    "live25_cagr": windows.get("live_25_now", {}).get("cagr"),
    "cost20_cagr": costs["20bps"]["cagr"],
    "mc_p50_cagr": mc["cagr_p50"], "mc_p5_cagr": mc["cagr_p5"],
    "mc_p_mdd_lt_60": mc["p_mdd_lt_60"],
    "worst_year": worst_year}))
