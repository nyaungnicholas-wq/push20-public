"""Test each proposed change across the CONFIG PLATEAU, in-sample only.

The audit's finding is that 72 of 135 configs sit within 0.05 Sharpe of each
other. So a change measured at one point tells you nothing: it has to move the
whole plateau, or it is noise. Every candidate below is therefore evaluated at
13 perturbations of the live config, and scored on the MEDIAN.

IN-SAMPLE 2006-2018 only. 2019+ is sealed and is not read by this script.
"""
import os, sys, json, statistics as st, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "reports"))

import opt_harness as H
H.FETCH_END = "2026-09-02"

IS_START, IS_END = "2006-01-01", "2018-12-31"      # development window
SEAL_START = "2019-01-01"                           # NOT READ HERE

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

# The plateau: perturbations along every axis the audit calls unidentifiable.
PLATEAU = [
    {}, {"vol_target": 0.45}, {"vol_target": 0.55},
    {"vol_window": 6}, {"vol_window": 12},
    {"max_weight": 0.50}, {"max_weight": 0.70},
    {"ema_span": 7}, {"ema_span": 12},
    {"lookback": 210}, {"lookback": 252},
    {"top_n": 2}, {"top_n": 4},
]

# Each candidate is a MECHANISM, not a fitted number.
CANDIDATES = {
    "baseline (live, honest costs)":      {},
    "skip-month (21d) momentum":          {"skip_days": 21},
    "multi-lookback 63/126/252":          {"lookback": (63, 126, 252)},
    "multi-lookback + skip-month":        {"lookback": (63, 126, 252), "skip_days": 21},
    "risk-adjusted rank (mom/vol 63d)":   {"risk_adjust_vol": 63},
    "EWMA vol, halflife 10d":             {"vol_ewma_halflife": 10},
    "EWMA vol, halflife 21d":             {"vol_ewma_halflife": 21},
    "dd brake 15%->1.0x, 25%->0.5x":      {"dd_brake": [(0.15, 1.0), (0.25, 0.5)]},
    "dd brake 20%->1.0x, 30%->0.5x":      {"dd_brake": [(0.20, 1.0), (0.30, 0.5)]},
    "equal weight (drop return-prop)":    {"weight_scheme": "equal"},
    "abs-momentum gate at +2%":           {"min_score": 0.02},
    "TLT trend filter (dynamic def)":     {"defensive_dynamic": 1},
    "drop VIX>=35 tier":                  {"vix_hi_level": 0.0},
    "leverage cap 1.25x":                 {"vol_cap_bull": 1.25},
    "risk gates during min-hold":         {"risk_gate_always": 1},
}


def run(extra):
    sh, ca, cg, dd = [], [], [], []
    for pert in PLATEAU:
        cfg = {**BASE, **pert, **extra}
        m = H.metrics(H.simulate(df, cfg, IS_START, IS_END))
        if not m:
            continue
        sh.append(m["sharpe"]); ca.append(m["calmar"])
        cg.append(m["cagr"]); dd.append(m["mdd"])
    return (st.median(sh), st.median(ca), st.median(cg), st.median(dd),
            sum(1 for x in sh if x > 0))


print(f"in-sample {IS_START} .. {IS_END}   |   {len(PLATEAU)} configs per row   "
      f"|   holdout {SEAL_START}+ NOT READ\n")
print(f"{'candidate':<36}{'Sharpe':>8}{'Calmar':>8}{'CAGR':>9}{'MDD':>9}   vs base")
print("-" * 82)

rows = {}
base_sh = base_ca = None
for name, extra in CANDIDATES.items():
    sh, ca, cg, dd, n = run(extra)
    rows[name] = dict(sharpe=sh, calmar=ca, cagr=cg, mdd=dd, extra={k: str(v) for k, v in extra.items()})
    if base_sh is None:
        base_sh, base_ca = sh, ca
        delta = ""
    else:
        delta = f"  Sharpe {sh-base_sh:+.3f}  Calmar {ca-base_ca:+.3f}"
    print(f"{name:<36}{sh:>8.3f}{ca:>8.3f}{cg*100:>8.2f}%{dd*100:>8.2f}%{delta}")
    sys.stdout.flush()

json.dump(rows, open(os.path.join(HERE, "plateau_is.json"), "w"), indent=1)
print("\nwrote plateau_is.json")
