"""Regression + honesty check for the patched opt_harness.

Part 1 proves the patch is behaviour-preserving: every historical result in
reports/ must still reproduce with the new flags off.
Part 2 measures the live config on an engine that actually models the live
system (basket vol, both VIX tiers, real cost, risk gates not suspended).
"""
import io, os, sys, json, importlib.util, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# last commit before the HOLDOUT v2 harness work
BASE_REV = "ecbc95d"
SCRATCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "reports"))

import numpy as np

import opt_harness as NEW
# pin to a fetch window that is fully cached, so this is reproducible and offline
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

print("loading data ...", flush=True)
df = NEW.load_data()
OLD._DF = df            # share the exact same frame, so any diff is code, not data
NEW._DF = df
print("data:", df.shape, df.index[0].date(), "->", df.index[-1].date(), flush=True)

from live_checkup import live_sim_config
LIVE, notes = live_sim_config()
print("\nlive config from DAILY_CHAMPION_FLAGS:")
print(json.dumps(LIVE, indent=1))
for n in notes: print("  note:", n)

S, E = "2006-01-01", "2026-09-02"

def show(tag, cfg, mod=NEW):
    r = mod.simulate(df, cfg, S, E)
    m = mod.metrics(r)
    print(f"{tag:<44} CAGR {m['cagr']*100:6.2f}%  MDD {m['mdd']*100:7.2f}%  "
          f"Sharpe {m['sharpe']:.3f}  Calmar {m['calmar']:.3f}  rebals {r['rebals']:5d}"
          + (f"  derisks {r.get('derisks',0)}" if r.get('derisks') else ""))
    return m, r

print("\n=== PART 1 — regression: new flags OFF must equal the old engine ===")
for name, cfg in [("live cfg (5bps, as reports ran it)", dict(LIVE)),
                  ("V6A defaults", {}),
                  ("equal weight / no lev", {"weight_scheme": "equal", "use_lev": False})]:
    ro = OLD.simulate(df, cfg, S, E); rn = NEW.simulate(df, cfg, S, E)
    co = np.array([v for _, v in ro["curve"]]); cn = np.array([v for _, v in rn["curve"]])
    same = len(co) == len(cn) and np.allclose(co, cn, rtol=0, atol=1e-6)
    print(f"  {'IDENTICAL' if same else 'DIFFERENT'}  {name}")
    if not same:
        print("    old final", co[-1], "new final", cn[-1], "maxdiff",
              float(np.max(np.abs(co[:min(len(co), len(cn))] - cn[:min(len(co), len(cn))]))))

print("\n=== PART 2 — what the live system actually is ===")
base, _ = show("A. as reported (SPY vol, 1 VIX tier, 5bps)", dict(LIVE))

c = dict(LIVE); c["cost_bps"] = 9.5
show("B. + realised cost 9.5bps", c)

c = dict(LIVE); c["cost_bps"] = 9.5; c["vix_hi_level"] = 35.0; c["vix_hi_cap"] = 0.5
show("C. + the live VIX>=35 tier", c)

c = dict(LIVE); c["cost_bps"] = 9.5; c["vix_hi_level"] = 35.0; c["vix_hi_cap"] = 0.5
c["basket_vol"] = 1
show("D. + basket-vol sizing (LIVE truth)", c)

c = dict(LIVE); c["cost_bps"] = 9.5; c["vix_hi_level"] = 35.0; c["vix_hi_cap"] = 0.5
c["basket_vol"] = 1; c["risk_gate_always"] = 1
honest, _ = show("E. + risk gates not suspended by min-hold", c)

print("\n  A -> E delta:  CAGR %+.2fpp   MDD %+.2fpp   Sharpe %+.3f   Calmar %+.3f"
      % ((honest["cagr"] - base["cagr"]) * 100, (honest["mdd"] - base["mdd"]) * 100,
         honest["sharpe"] - base["sharpe"], honest["calmar"] - base["calmar"]))

json.dump({"live_cfg": LIVE, "reported": base, "honest": honest},
          open(os.path.join(SCRATCH, "honest_baseline.json"), "w"), indent=1)
print("\nwrote honest_baseline.json")
