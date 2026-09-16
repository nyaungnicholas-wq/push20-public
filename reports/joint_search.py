"""Global joint search with walk-forward out-of-sample selection.

Fixes the three plateau causes:
  - greedy local optimum  -> sample full configs from the JOINT space (not deltas)
  - in-sample noise        -> select on TRAIN (2005-2016), judge on untouched TEST (2017-2024)
  - single factor          -> the space now includes the new risk-structure factors
                              (invvol / sharpe / rp_blend weighting, sharpe-rank selection)

Honest protocol: pick the best config by TRAIN objective, then report its TEST
performance (true out-of-sample). Also print the TEST leaderboard to see the
ceiling. If nothing beats the current champion out-of-sample, the plateau is real.

Run:  .venv/bin/python reports/joint_search.py
"""

from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_harness as H  # noqa: E402

random.seed(20240605)  # deterministic

TRAIN = ("2005-01-01", "2016-12-31")
TEST = ("2017-01-01", "2024-12-31")
FULL = ("2005-01-01", "2024-12-31")

SPACE = {
    "lookbacks": [(252,), (126, 252), (189,), (210,), (231,), (63, 126, 252)],
    "top_n": [2, 3, 4, 5],
    "rotation_weight_scheme": ["momentum", "invvol", "sharpe", "rp_blend"],
    "rotation_rank_metric": ["mom", "sharpe"],
    "rotation_vol_target": [0.0, 0.10, 0.12, 0.15],
    "rotation_vol_window": [20, 30],
    "rotation_factor_window": [21, 63],
    "rotation_defensive_symbol": ["GLD", "GLD+TLT", "TLT", "BIL"],
    "rotation_cluster_filter": [False, False, True],   # weighted toward off
    "rotation_trend_filter": [False, False, True],
    "rotation_momentum_weight": [True],                # weighting on; scheme decides how
}

N = 110

CHAMPION = dict(rotation_momentum_weight=True, rotation_defensive_symbol="GLD+TLT",
                lookbacks=(252,), rotation_vol_target=0.10, rotation_vol_window=20)


def sample_cfg():
    f = {}
    for k, opts in SPACE.items():
        f[k] = random.choice(opts)
    return f


def evalw(close, flags, win):
    cfg = H.make_cfg(**flags)
    r = H.run(cfg, close, win[0], win[1])
    m = H.metrics(r["equity_curve"])
    spy = H.metrics(r["benchmark"]["equity_curve"])
    return m, spy, H.objective(m, spy)


def main():
    close = H.load_close()

    # Champion reference on all three windows.
    cm = {w: evalw(close, CHAMPION, win) for w, win in (("train", TRAIN), ("test", TEST), ("full", FULL))}
    print("CHAMPION (GLD+TLT blend):")
    for w in ("train", "test", "full"):
        m, spy, o = cm[w]
        print(f"  {w:<5} CAGR {m['cagr']:+6.1%}  MDD {m['mdd']:6.1%}  Sharpe {m['sharpe']:4.2f}  obj {o:+.3f}  (SPY {spy['cagr']:+.1%})")
    champ_test_obj = cm["test"][2]
    champ_test_sharpe = cm["test"][0]["sharpe"]

    results = []
    seen = set()
    for i in range(N):
        flags = sample_cfg()
        sig = json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in flags.items()}, sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        try:
            tr = evalw(close, flags, TRAIN)
            te = evalw(close, flags, TEST)
            fu = evalw(close, flags, FULL)
        except Exception:
            continue
        results.append({"flags": flags, "train_obj": tr[2], "test_obj": te[2], "full_obj": fu[2],
                        "test": te[0], "train": tr[0], "full": fu[0], "test_spy": te[1]["cagr"],
                        "full_spy": fu[1]["cagr"]})
        if (i + 1) % 20 == 0:
            print(f"  ...evaluated {len(results)} configs")

    # --- Honest walk-forward: select by TRAIN, report TEST ---
    by_train = sorted(results, key=lambda r: -r["train_obj"])
    wf_pick = by_train[0]
    # --- Ceiling: best by TEST (selection-on-test, optimistic) ---
    by_test = sorted(results, key=lambda r: -r["test_obj"])

    def desc(r):
        f = r["flags"]
        tag = (f"lb={f['lookbacks']} top={f['top_n']} wt={f['rotation_weight_scheme']} "
               f"rank={f['rotation_rank_metric']} vt={f['rotation_vol_target']} "
               f"def={f['rotation_defensive_symbol']} clu={int(f['rotation_cluster_filter'])} "
               f"trd={int(f['rotation_trend_filter'])}")
        return tag

    print(f"\n{'='*70}\nWALK-FORWARD (select on TRAIN 2005-2016 → report untouched TEST 2017-2024):")
    print(f"  picked: {desc(wf_pick)}")
    m = wf_pick["test"]
    print(f"  TEST  CAGR {m['cagr']:+.1%}  MDD {m['mdd']:.1%}  Sharpe {m['sharpe']:.2f}  obj {wf_pick['test_obj']:+.3f}")
    print(f"  vs CHAMPION TEST obj {champ_test_obj:+.3f}, Sharpe {champ_test_sharpe:.2f}")
    verdict = ("BREAKS the plateau (beats champion out-of-sample)"
               if wf_pick["test_obj"] > champ_test_obj else
               "does NOT beat the champion out-of-sample — plateau confirmed")
    print(f"  --> {verdict}")

    print(f"\nTOP 8 by TEST objective (optimistic ceiling — selection-on-test):")
    for r in by_test[:8]:
        m = r["test"]
        mark = " *BEATS champ*" if r["test_obj"] > champ_test_obj else ""
        print(f"  obj {r['test_obj']:+.3f}  CAGR {m['cagr']:+6.1%}  MDD {m['mdd']:6.1%}  "
              f"Sharpe {m['sharpe']:4.2f} | {desc(r)}{mark}")

    print(f"\nTOP 6 by FULL 2005-2024 objective:")
    for r in sorted(results, key=lambda r: -r["full_obj"])[:6]:
        m = r["full"]
        print(f"  obj {r['full_obj']:+.3f}  CAGR {m['cagr']:+6.1%}  MDD {m['mdd']:6.1%}  "
              f"Sharpe {m['sharpe']:4.2f} | {desc(r)}")

    out = {"n_evaluated": len(results), "champion_test_obj": champ_test_obj,
           "wf_pick": {"flags": {k: (list(v) if isinstance(v, tuple) else v) for k, v in wf_pick["flags"].items()},
                       "test": {k: wf_pick["test"][k] for k in ("cagr", "mdd", "sharpe")},
                       "test_obj": wf_pick["test_obj"]},
           "best_test_obj": by_test[0]["test_obj"],
           "beats_champion_oos": bool(wf_pick["test_obj"] > champ_test_obj)}
    with open(os.path.join(os.path.dirname(__file__), "joint_search_results.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n{len(results)} configs evaluated. Saved → reports/joint_search_results.json")


if __name__ == "__main__":
    main()
