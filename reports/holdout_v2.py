"""Open the holdout. Criteria fixed in PREREG_v2.md before the first run.

Graded twice: once on the engine as it stood, and again after an adversarial
review found a defensive-sleeve fidelity bug in it. Same criteria both times,
both passed. See PREREG_v2_ADDENDUM.md."""
import os, sys, json, hashlib, statistics as st, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "reports"))

import opt_harness as H
H.FETCH_END = "2026-09-02"

prereg = open(os.path.join(HERE, "PREREG_v2.md"), "rb").read()
print("PREREG sha256:", hashlib.sha256(prereg).hexdigest()[:16], f"({len(prereg)} bytes)\n")

df = H.load_data()
# The PRE-SWITCH live config, pinned literally. Do NOT derive this from
# live_sim_config(): that function returns whatever is live NOW, so after the v2
# switch shipped it returned the candidate, and BASE and CAND collapsed onto each
# other. A baseline that moves with the thing it is measuring is not a baseline.
BASE = dict(lookback=232, ema_span=9, top_n=3, min_hold_days=3, vol_target=0.5,
            vol_window=8, vol_floor=0.5, vol_cap_bull=1.5, vol_cap_bear=1.0,
            regime_sma=200, max_weight=0.6, defensive="GLD+TLT",
            weight_scheme="return_prop", use_3x=0, cost_bps=9.5,
            vix_gate_level=25.0, vix_gate_cap=1.0, vix_hi_level=35.0, vix_hi_cap=0.5,
            basket_vol=1, defensive_live=1)
CAND = {**BASE, "vix_hi_level": 0.0, "weight_scheme": "equal", "vol_cap_bull": 1.25}

S, E = "2019-01-01", "2026-09-02"
PLATEAU = [{}, {"vol_target": 0.45}, {"vol_target": 0.55}, {"vol_window": 6}, {"vol_window": 12},
           {"max_weight": 0.50}, {"max_weight": 0.70}, {"ema_span": 7}, {"ema_span": 12},
           {"lookback": 210}, {"lookback": 252}, {"top_n": 2}, {"top_n": 4}]

def one(cfg, s=S, e=E):
    return H.metrics(H.simulate(df, cfg, s, e))

b, c = one(BASE), one(CAND)
print(f"HOLDOUT {S} .. {E}\n")
print(f"{'':<12}{'CAGR':>9}{'MDD':>10}{'Sharpe':>9}{'Calmar':>9}")
for tag, m in (("baseline", b), ("candidate", c)):
    print(f"{tag:<12}{m['cagr']*100:>8.2f}%{m['mdd']*100:>9.2f}%{m['sharpe']:>9.3f}{m['calmar']:>9.3f}")

g1 = c["calmar"] > b["calmar"]
g2 = c["sharpe"] >= b["sharpe"] - 0.05
g3 = c["mdd"] >= b["mdd"] - 0.02
print(f"\n  1 Calmar {c['calmar']:.3f} > {b['calmar']:.3f}            {'PASS' if g1 else 'FAIL'}")
print(f"  2 Sharpe {c['sharpe']:.3f} >= {b['sharpe']-0.05:.3f}           {'PASS' if g2 else 'FAIL'}")
print(f"  3 MDD    {c['mdd']*100:.2f}% >= {(b['mdd']-0.02)*100:.2f}%        {'PASS' if g3 else 'FAIL'}")
print(f"\n  VERDICT: {'SWITCH' if (g1 and g2 and g3) else 'DO NOT SWITCH'}")

# how broadly does it hold on the holdout — not a criterion, but it says whether
# the result is a mechanism or one lucky point
wins = 0
for p in PLATEAU:
    mb, mc = one({**BASE, **p}), one({**CAND, **p})
    if mb and mc and mc["calmar"] > mb["calmar"]:
        wins += 1
print(f"  candidate beats baseline on Calmar in {wins}/13 plateau configs (holdout)")

# full-window figure for the checkup to enforce afterwards
full_b, full_c = one(BASE, "2006-01-01", E), one(CAND, "2006-01-01", E)
print(f"\nFULL 2006-01-01 .. {E} (for live_checkup.py)")
for tag, m in (("baseline", full_b), ("candidate", full_c)):
    print(f"  {tag:<10} CAGR {m['cagr']*100:6.2f}%  MDD {m['mdd']*100:7.2f}%  "
          f"Sharpe {m['sharpe']:.3f}  Calmar {m['calmar']:.3f}")

json.dump({"prereg_sha256": hashlib.sha256(prereg).hexdigest(), "window": [S, E],
           "baseline": b, "candidate": c, "plateau_wins": wins,
           "full_baseline": full_b, "full_candidate": full_c,
           "verdict": "SWITCH" if (g1 and g2 and g3) else "DO NOT SWITCH",
           "gates": {"calmar": bool(g1), "sharpe": bool(g2), "mdd": bool(g3)}},
          open(os.path.join(HERE, "HOLDOUT_RESULT_v2.json"), "w"), indent=1)
print("\nwrote HOLDOUT_RESULT.json — this window is now spent")
