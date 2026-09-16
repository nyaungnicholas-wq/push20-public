"""Combine the changes that survived the plateau, still in-sample only.

Also splits the development window in two eras. A change that only works in one
era is a period effect, not a mechanism, and must not be carried into the holdout.
"""
import os, sys, json, statistics as st, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "reports"))

import opt_harness as H
H.FETCH_END = "2026-09-02"

ERAS = [("crisis 06-12", "2006-01-01", "2012-12-31"),
        ("grind  13-18", "2013-01-01", "2018-12-31"),
        ("full   06-18", "2006-01-01", "2018-12-31")]

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

PLATEAU = [{}, {"vol_target": 0.45}, {"vol_target": 0.55}, {"vol_window": 6}, {"vol_window": 12},
           {"max_weight": 0.50}, {"max_weight": 0.70}, {"ema_span": 7}, {"ema_span": 12},
           {"lookback": 210}, {"lookback": 252}, {"top_n": 2}, {"top_n": 4}]

NOVIX = {"vix_hi_level": 0.0}
EQ = {"weight_scheme": "equal"}
CAP = {"vol_cap_bull": 1.25}
RGA = {"risk_gate_always": 1}

COMBOS = {
    "0 live (honest costs)":            {},
    "1 −VIX35":                         {**NOVIX},
    "2 −VIX35 +equalwt":                {**NOVIX, **EQ},
    "3 −VIX35 +equalwt +cap1.25":       {**NOVIX, **EQ, **CAP},
    "4 −VIX35 +equalwt +riskgate":      {**NOVIX, **EQ, **RGA},
    "5 −VIX35 +equalwt +cap1.25 +rg":   {**NOVIX, **EQ, **CAP, **RGA},
    "6 −VIX35 +cap1.25":                {**NOVIX, **CAP},
}


def med(extra, s, e):
    sh, ca, cg, dd = [], [], [], []
    for pert in PLATEAU:
        m = H.metrics(H.simulate(df, {**BASE, **pert, **extra}, s, e))
        if m:
            sh.append(m["sharpe"]); ca.append(m["calmar"]); cg.append(m["cagr"]); dd.append(m["mdd"])
    return st.median(sh), st.median(ca), st.median(cg), st.median(dd)


print("median across 13 plateau configs, IN-SAMPLE ONLY (holdout 2019+ untouched)\n")
hdr = f"{'combo':<34}"
for n, _, _ in ERAS:
    hdr += f"{n:>26}"
print(hdr)
print(f"{'':<34}" + "".join(f"{'Sharpe  Calmar   CAGR':>26}" for _ in ERAS))
print("-" * 114)

res = {}
for name, extra in COMBOS.items():
    line = f"{name:<34}"
    res[name] = {}
    for en, s, e in ERAS:
        sh, ca, cg, dd = med(extra, s, e)
        res[name][en] = dict(sharpe=sh, calmar=ca, cagr=cg, mdd=dd)
        line += f"{sh:>9.3f}{ca:>8.3f}{cg*100:>8.2f}%"
    print(line); sys.stdout.flush()

print("\nrobustness — how many of the 13 plateau configs beat the live baseline on Calmar, 06-18:")
base_each = [H.metrics(H.simulate(df, {**BASE, **p}, "2006-01-01", "2018-12-31"))["calmar"] for p in PLATEAU]
for name, extra in COMBOS.items():
    if not extra:
        continue
    wins = 0
    for p, b in zip(PLATEAU, base_each):
        m = H.metrics(H.simulate(df, {**BASE, **p, **extra}, "2006-01-01", "2018-12-31"))
        if m and m["calmar"] > b:
            wins += 1
    print(f"  {name:<34} {wins:>2}/13")
    sys.stdout.flush()

json.dump(res, open(os.path.join(HERE, "combo_is.json"), "w"), indent=1)
print("\nwrote combo_is.json")
