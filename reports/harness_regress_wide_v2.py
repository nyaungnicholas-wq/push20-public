"""Wider regression: exercise every branch the simulate() restructure touched."""
import io, os, sys, importlib.util, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# last commit before the HOLDOUT v2 harness work
BASE_REV = "ecbc95d"
SCRATCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "reports"))
import numpy as np
import opt_harness as NEW
NEW.FETCH_END = "2026-09-02"
def load_old():
    """Reconstruct the pre-change opt_harness from git, so this script is runnable
    from a clean checkout. It previously imported a scratch copy that was never
    committed, which made the central claim of this file unverifiable."""
    import subprocess, tempfile
    src = subprocess.run(["git", "show", f"{BASE_REV}:reports/opt_harness.py"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    tmp = os.path.join(tempfile.mkdtemp(), "old_harness.py")
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(src)
    spec = importlib.util.spec_from_file_location("old_harness", tmp)
    m = importlib.util.module_from_spec(spec)
    sys.modules["old_harness"] = m
    spec.loader.exec_module(m)
    m.FETCH_END = "2026-09-02"
    return m
OLD = load_old()

df = NEW.load_data(); OLD._DF = df; NEW._DF = df
from live_checkup import live_sim_config
L, _ = live_sim_config()
# the pre-change live config, so the old module can run it at all
NEW_FLAGS = ("cost_bps", "basket_vol", "defensive_live", "vix_hi_level", "vix_hi_cap",
             "risk_gate_always", "skip_days", "vol_ewma_halflife", "dd_brake", "min_score")
L0 = {k: v for k, v in L.items() if k not in NEW_FLAGS}
L0.update(vol_cap_bull=1.5, weight_scheme="return_prop")

CASES = {
    "V6A defaults":            {},
    "pre-change live cfg":     dict(L0),
    "score_gap hysteresis":    {**L0, "min_score_gap": 0.05},
    "defensive_momentum":      {**L0, "defensive_momentum": True},
    "defensive_dynamic+BIL":   {**L0, "defensive": "GLD+TLT+BIL", "defensive_dynamic": 1},
    "lev_only_topk=1":         {**L0, "lev_only_topk": 1},
    "tiered_caps + maxw .4":   {**L0, "tiered_caps": 1, "max_weight": 0.4},
    "use_3x + cap 3.0":        {**L0, "use_3x": 1, "vol_cap_bull": 3.0},
    "risk_adjust_vol=20":      {**L0, "risk_adjust_vol": 20},
    "golden_cross+buffer":     {**L0, "golden_cross": 1, "regime_buffer": 0.01},
    "breaker_sma 50":          {**L0, "breaker_sma": 50, "breaker_level": 0.5},
    "vix_spike 0.3":           {**L0, "vix_spike_pct": 0.3},
    "equal wt, no leverage":   {**L0, "weight_scheme": "equal", "use_lev": False},
    "squared wt, top_n 1":     {**L0, "weight_scheme": "squared", "top_n": 1},
    "min_hold 1 / lookback 90":{**L0, "min_hold_days": 1, "lookback": 90},
}
S, E = "2006-01-01", "2026-09-02"
bad = 0
for name, cfg in CASES.items():
    ro, rn = OLD.simulate(df, cfg, S, E), NEW.simulate(df, cfg, S, E)
    co = np.array([v for _, v in ro["curve"]]); cn = np.array([v for _, v in rn["curve"]])
    ok = len(co) == len(cn) and np.allclose(co, cn, rtol=0, atol=1e-6)
    if not ok:
        bad += 1
        n = min(len(co), len(cn))
        d = np.abs(co[:n] - cn[:n]); i = int(np.argmax(d))
        print(f"  DIFFER  {name:<26} maxdiff {d[i]:.6f} at {ro['curve'][i][0]} "
              f"(old {co[-1]:,.0f} new {cn[-1]:,.0f})")
    else:
        print(f"  same    {name:<26} final {cn[-1]:,.0f}  rebals {rn['rebals']}")
    sys.stdout.flush()
print(f"\n{len(CASES)-bad}/{len(CASES)} identical")
sys.exit(1 if bad else 0)
