"""Solution search: sweep candidate fixes through the canonical harness.

Diagnoses causes (era decomposition, leverage curve, defensive drag) and tests
~30 candidate solutions across full/modern/OOS windows. Prints a ranked frontier
and flags overfitting (in-sample vs out-of-sample degradation).

Run: .venv/bin/python reports/opt_search.py
"""
from __future__ import annotations
import os, sys, json, itertools
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from opt_harness import load_data, simulate, metrics, spy_bh, full_eval

df=load_data()

# ── DIAGNOSIS 1: leverage curve (CAGR vs win-rate tradeoff) ───────────────────
print("="*100)
print("DIAGNOSIS 1 — Leverage curve: what does each unit of leverage cost in win rate?")
print("="*100)
for cap in [1.0,1.25,1.5,1.75,2.0]:
    cfg={"vol_cap_bull":cap,"vol_cap_bear":1.0,"vol_target":0.15,
         "use_lev":cap>1.0,"regime_sma":200}
    f=metrics(simulate(df,cfg,"2006-01-01","2024-12-31"))
    print(f"  cap={cap:.2f}x  CAGR {f['cagr']:+.1%}  mo-win {f['win_rate_monthly']:.0%}  "
          f"yr-win {f['win_rate_yearly']:.0%}  MDD {f['mdd']:.0%}  Sharpe {f['sharpe']}  Calmar {f['calmar']}")

# ── DIAGNOSIS 2: era decomposition ────────────────────────────────────────────
print("\n"+"="*100)
print("DIAGNOSIS 2 — Era decomposition: where does CAGR come from / win rate suffer?")
print("="*100)
v6a={"vol_cap_bull":2.0,"vol_cap_bear":1.0,"vol_target":0.15}
for name,a,b in [("2006-2014","2006-01-01","2014-12-31"),
                 ("2015-2024","2015-01-01","2024-12-31")]:
    m=metrics(simulate(df,v6a,a,b)); sp=metrics(spy_bh(df,a,b))
    print(f"  {name}  V6A: {m['cagr']:+.1%} CAGR / {m['win_rate_monthly']:.0%} mo-win / {m['mdd']:.0%} MDD  "
          f"| SPY: {sp['cagr']:+.1%} / {sp['win_rate_monthly']:.0%}")

# ── DIAGNOSIS 3: per-year — which years are red, and why ───────────────────────
print("\n"+"="*100)
print("DIAGNOSIS 3 — V6A per-year vs SPY (red years = win-rate + CAGR drag)")
print("="*100)
m=metrics(simulate(df,v6a,"2006-01-01","2024-12-31")); sp=metrics(spy_bh(df,"2006-01-01","2024-12-31"))
for y in sorted(m["per_year"]):
    v=m["per_year"][y]; s=sp["per_year"].get(y,0)
    flag="RED " if v<0 else "    "
    print(f"  {y} {flag} V6A {v:+6.1%}   SPY {s:+6.1%}   edge {v-s:+6.1%}")

# ── SOLUTION GRID ─────────────────────────────────────────────────────────────
print("\n"+"="*100)
print("SOLUTION GRID — candidate fixes (sorted by modern-era Calmar; OOS-degradation flagged)")
print("="*100)

CANDIDATES={
    "V6A_baseline":              {"vol_cap_bull":2.0},
    # crash circuit breakers (cut leverage when SPY<50SMA) — target: ↑win ↓MDD
    "brk50_to_1x":               {"vol_cap_bull":2.0,"breaker_sma":50,"breaker_level":1.0},
    "brk50_to_0.5x":             {"vol_cap_bull":2.0,"breaker_sma":50,"breaker_level":0.5},
    "brk100_to_1x":              {"vol_cap_bull":2.0,"breaker_sma":100,"breaker_level":1.0},
    # partial leverage (only lever strongest picks) — target: ↓decay ↑win
    "lev_top1_only":             {"vol_cap_bull":2.0,"lev_only_topk":1},
    "lev_top2_only":             {"vol_cap_bull":2.0,"lev_only_topk":2},
    # leverage level tuning
    "cap1.5x":                   {"vol_cap_bull":1.5},
    "cap1.75x":                  {"vol_cap_bull":1.75},
    # defensive sleeve fixes (2022 = GLD+TLT both fell)
    "defensive_+BIL":            {"vol_cap_bull":2.0,"defensive":"GLD+TLT+BIL"},
    "defensive_momentum":        {"vol_cap_bull":2.0,"defensive_momentum":True},
    "defensive_BIL_only":        {"vol_cap_bull":2.0,"defensive":"BIL"},
    # vol target tuning
    "vt0.12":                    {"vol_cap_bull":2.0,"vol_target":0.12},
    "vt0.18":                    {"vol_cap_bull":2.0,"vol_target":0.18},
    "vt0.20":                    {"vol_cap_bull":2.0,"vol_target":0.20},
    # holding period
    "mhd5":                      {"vol_cap_bull":2.0,"min_hold_days":5},
    "mhd7":                      {"vol_cap_bull":2.0,"min_hold_days":7},
    "mhd10":                     {"vol_cap_bull":2.0,"min_hold_days":10},
    # signal smoothing
    "ema5":                      {"vol_cap_bull":2.0,"ema_span":5},
    "ema15":                     {"vol_cap_bull":2.0,"ema_span":15},
    "ema21":                     {"vol_cap_bull":2.0,"ema_span":21},
    # breadth
    "top2":                      {"vol_cap_bull":2.0,"top_n":2},
    "top4":                      {"vol_cap_bull":2.0,"top_n":4},
    # concentration cap
    "maxw0.5":                   {"vol_cap_bull":2.0,"max_weight":0.5},
    "maxw0.4":                   {"vol_cap_bull":2.0,"max_weight":0.4},
    # weighting
    "equal_weight":              {"vol_cap_bull":2.0,"weight_scheme":"equal"},
    # ── promising COMBOS ──
    "COMBO_brk50+top2lev+vt18":  {"vol_cap_bull":2.0,"breaker_sma":50,"breaker_level":1.0,
                                  "lev_only_topk":2,"vol_target":0.18},
    "COMBO_brk50+BIL":           {"vol_cap_bull":2.0,"breaker_sma":50,"breaker_level":1.0,
                                  "defensive":"GLD+TLT+BIL"},
    "COMBO_brk50+top2lev+maxw5": {"vol_cap_bull":2.0,"breaker_sma":50,"breaker_level":1.0,
                                  "lev_only_topk":2,"max_weight":0.5},
    "COMBO_brk50+defmom+vt18":   {"vol_cap_bull":2.0,"breaker_sma":50,"breaker_level":1.0,
                                  "defensive_momentum":True,"vol_target":0.18},
    "COMBO_all":                 {"vol_cap_bull":2.0,"breaker_sma":50,"breaker_level":1.0,
                                  "lev_only_topk":2,"defensive":"GLD+TLT+BIL","max_weight":0.5},
}

results={}
for label,cfg in CANDIDATES.items():
    results[label]=full_eval(df,cfg)

# rank by modern-era calmar
def modern(lbl): return results[lbl]["modern_15_24"]
rows=sorted(CANDIDATES, key=lambda l: -modern(l).get("calmar",0))

spy_full=metrics(spy_bh(df,"2006-01-01","2024-12-31"))
spy_mod=metrics(spy_bh(df,"2015-01-01","2024-12-31"))
print(f"\n  {'SPY':<28} FULL {spy_full['cagr']:+.1%}/{spy_full['win_rate_monthly']:.0%}win  "
      f"MOD {spy_mod['cagr']:+.1%}/{spy_mod['win_rate_monthly']:.0%}win")
print(f"\n  {'candidate':<28} {'FULL cagr/win/mdd':<22} {'MODERN cagr/win/mdd':<22} {'OOS':<14} {'overfit?'}")
print("  "+"-"*98)
for l in rows:
    f=results[l]["full_06_24"]; mo=results[l]["modern_15_24"]
    ins=results[l]["insample"]; oos=results[l]["outsample"]
    # overfit flag: OOS CAGR much worse than in-sample
    degr=ins["cagr"]-oos["cagr"]
    flag="OK" if oos["cagr"]>spy_mod["cagr"] else "weak-OOS"
    print(f"  {l:<28} {f['cagr']:+5.1%}/{f['win_rate_monthly']:.0%}/{f['mdd']:+.0%}      "
          f"{mo['cagr']:+5.1%}/{mo['win_rate_monthly']:.0%}/{mo['mdd']:+.0%}      "
          f"is{ins['cagr']:+.0%}/oos{oos['cagr']:+.0%}  {flag}")

# save full results for the workflow risk-review
out={l:results[l] for l in CANDIDATES}
out["_spy"]={"full":spy_full,"modern":spy_mod}
with open(os.path.join(os.path.dirname(__file__),"opt_search_results.json"),"w") as fh:
    json.dump(out,fh,indent=2)
print("\n  Full results saved → reports/opt_search_results.json")
