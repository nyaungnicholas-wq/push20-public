"""Stage-2 refinement around the cap=1.5x optimum found in opt_search.py.

Finds the best frontier point: pushes win-rate up and MDD down while holding
CAGR near 20%. Tests fine cap tuning + the win-rate levers (partial leverage,
top_n, holding period) layered on the 1.5x base.

Run: .venv/bin/python reports/opt_refine.py
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from opt_harness import load_data, simulate, metrics, spy_bh, full_eval

df=load_data()

CANDIDATES={
    "cap1.5_base":            {"vol_cap_bull":1.5},
    "cap1.4":                 {"vol_cap_bull":1.4},
    "cap1.6":                 {"vol_cap_bull":1.6},
    "cap1.5_levtop2":         {"vol_cap_bull":1.5,"lev_only_topk":2},
    "cap1.5_levtop1":         {"vol_cap_bull":1.5,"lev_only_topk":1},
    "cap1.5_mhd7":            {"vol_cap_bull":1.5,"min_hold_days":7},
    "cap1.5_top2":            {"vol_cap_bull":1.5,"top_n":2},
    "cap1.5_vt0.16":          {"vol_cap_bull":1.5,"vol_target":0.16},
    "cap1.5_vt0.18":          {"vol_cap_bull":1.5,"vol_target":0.18},
    "cap1.5_levtop2_mhd7":    {"vol_cap_bull":1.5,"lev_only_topk":2,"min_hold_days":7},
    "cap1.5_top2_mhd7":       {"vol_cap_bull":1.5,"top_n":2,"min_hold_days":7},
    "cap1.5_levtop2_vt0.17":  {"vol_cap_bull":1.5,"lev_only_topk":2,"vol_target":0.17},
    "cap1.6_levtop2":         {"vol_cap_bull":1.6,"lev_only_topk":2},
    "cap1.5_top2_levtop2":    {"vol_cap_bull":1.5,"top_n":2,"lev_only_topk":2},
    "cap1.5_mhd7_vt0.17":     {"vol_cap_bull":1.5,"min_hold_days":7,"vol_target":0.17},
}

res={l:full_eval(df,c) for l,c in CANDIDATES.items()}
spy_full=metrics(spy_bh(df,"2006-01-01","2024-12-31"))
spy_mod=metrics(spy_bh(df,"2015-01-01","2024-12-31"))

# score: balance CAGR + win rate + drawdown control. Composite = modern Calmar + win_rate bonus.
def score(l):
    mo=res[l]["modern_15_24"]
    return mo["calmar"]+ (mo["win_rate_monthly"]-0.55)*2  # reward win rate over 55%

rows=sorted(CANDIDATES,key=lambda l:-score(l))

print("="*108)
print("STAGE-2 REFINEMENT around cap=1.5x  (sorted by composite: modern Calmar + win-rate bonus)")
print("="*108)
print(f"  SPY  FULL {spy_full['cagr']:+.1%}/{spy_full['win_rate_monthly']:.0%}win/-55%   "
      f"MOD {spy_mod['cagr']:+.1%}/{spy_mod['win_rate_monthly']:.0%}win")
print(f"\n  {'candidate':<24} {'FULL cagr/win/mdd/calmar':<30} {'MODERN cagr/win/mdd/sharpe':<32} {'OOS cagr'}")
print("  "+"-"*104)
for l in rows:
    f=res[l]["full_06_24"]; mo=res[l]["modern_15_24"]; oos=res[l]["outsample"]
    print(f"  {l:<24} {f['cagr']:+5.1%}/{f['win_rate_monthly']:.0%}/{f['mdd']:+.0%}/{f['calmar']:.2f}        "
          f"{mo['cagr']:+5.1%}/{mo['win_rate_monthly']:.0%}/{mo['mdd']:+.0%}/{mo['sharpe']:.2f}         "
          f"{oos['cagr']:+.1%}")

out={l:res[l] for l in CANDIDATES}
out["_spy"]={"full":spy_full,"modern":spy_mod}
with open(os.path.join(os.path.dirname(__file__),"opt_refine_results.json"),"w") as fh:
    json.dump(out,fh,indent=2)
print("\n  Saved → reports/opt_refine_results.json")
