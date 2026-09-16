"""Reusable round driver for the continuous rotation-v2 research loop.

Each invocation loads the current champion from champion_config.json, tries a list
of MOVES (deltas) on top of it, evaluates across 4 standard windows + a 2-window
walk-forward (train 2005-2016 / test 2017-2024), logs every result to
iteration_log.md and all_results.jsonl, and promotes the champion on KEEP.

Edit ROUND and MOVES, then:  .venv/bin/python reports/run_round.py
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_harness as H  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "iteration_log.md")
CHAMP_JSON = os.path.join(HERE, "champion_config.json")
ALL_RESULTS = os.path.join(HERE, "all_results.jsonl")
COUNTER = os.path.join(HERE, ".iter_counter")

PRIMARY = "2005-2024"
SUBWINDOWS = ["2005-2014", "2015-2024", "2018-2024"]
ROBUSTNESS_TOL = 0.015

# 4 standard windows + walk-forward train/test split.
WINDOWS = list(H.WINDOWS) + [
    ("WF-train 2005-2016", "2005-01-01", "2016-12-31"),
    ("WF-test 2017-2024", "2017-01-01", "2024-12-31"),
]

# ---------------------------------------------------------------------------
# ROUND DEFINITION — edit these two between rounds.
# ---------------------------------------------------------------------------
ROUND = 4
MOVES = [
    ("equalweight_vt", "Turn OFF momentum-weighting (keep vol-target) — does equal weight de-risk better?",
     {"rotation_momentum_weight": False}),
    ("regime_plus", "Add SPY-200SMA regime overlay parking in GLD (retest now vol-target is on).",
     {"rotation_regime_filter": True}),
    ("single_tranche", "Single first-of-month rebalance instead of 3 tranches.",
     {"tranches": (0,)}),
    ("four_tranche", "4 tranches (days 0/7/14/21) — smoother timing diversification.",
     {"tranches": (0, 7, 14, 21)}),
    ("drop_QQQ", "Remove QQQ from the universe (redundant with XLK).",
     {"universe": [s for s in ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLP","XLU","XLRE","XLC","SMH"]]}),
    ("drop_SMH", "Remove SMH (semis) — does losing the highest-octane sleg cut drawdown?",
     {"universe": ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLP","XLU","XLRE","XLC","QQQ"]}),
    ("gold_ranked", "Add GLD to the RANKED universe so gold can be an active position too.",
     {"universe": ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLP","XLU","XLRE","XLC","SMH","QQQ","GLD"]}),
    ("lb_189_252", "Blend 9mo+12mo momentum.",
     {"lookbacks": (189, 252)}),
    ("lb_126_189_252", "Blend 6/9/12mo momentum.",
     {"lookbacks": (126, 189, 252)}),
    ("volwin_30", "Vol estimate over 30d (less twitchy de-risking).",
     {"rotation_vol_window": 30}),
    ("volwin_40", "Vol estimate over 40d.",
     {"rotation_vol_window": 40}),
    ("vt_0.09", "Vol-target 0.09 (slightly lower).",
     {"rotation_vol_target": 0.09}),
    ("vt_0.11", "Vol-target 0.11 (slightly higher).",
     {"rotation_vol_target": 0.11}),
    ("floor_0.20", "Momentum-weight floor 0.20 (de-concentrate).",
     {"rotation_weight_floor": 0.20}),
    ("top5", "Hold top 5 sectors.",
     {"top_n": 5}),
]


def load_champion_flags() -> dict:
    if os.path.exists(CHAMP_JSON):
        with open(CHAMP_JSON) as f:
            d = json.load(f)
        flags = dict(d.get("champion_flags", {}))
        if "lookbacks" in flags and isinstance(flags["lookbacks"], list):
            flags["lookbacks"] = tuple(flags["lookbacks"])
        return flags
    return {}


def next_iter() -> int:
    n = 14
    if os.path.exists(COUNTER):
        with open(COUNTER) as f:
            n = int(f.read().strip() or "14")
    n += 1
    with open(COUNTER, "w") as f:
        f.write(str(n))
    return n


def evaluate_windows(cfg, close) -> dict:
    res = {}
    for name, a, b in WINDOWS:
        r = H.run(cfg, close, a, b)
        m = H.metrics(r["equity_curve"])
        spy = H.metrics(r["benchmark"]["equity_curve"])
        res[name] = {"m": m, "spy": spy, "obj": H.objective(m, spy)}
    return res


def robustness_ok(cand, champ) -> tuple:
    for w in SUBWINDOWS:
        dc = cand[w]["m"]["cagr"] - champ[w]["m"]["cagr"]
        if dc < -ROBUSTNESS_TOL:
            return False, f"{w} CAGR {dc:+.1%}"
    return True, "all sub-periods hold"


def row(name, ev):
    out = []
    for w, _, _ in [(x[0], x[1], x[2]) for x in WINDOWS]:
        m, spy = ev[w]["m"], ev[w]["spy"]
        beat = "WIN" if m["cagr"] > spy["cagr"] else "lose"
        out.append(f"| {w} | ${m['final']:,.0f} | {m['cagr']:+.1%} | {m['mdd']:.1%} | "
                   f"{m['sharpe']:.2f} | {ev[w]['obj']:+.2f} | {spy['cagr']:+.1%} | {beat} |")
    return "\n".join(out)


def append_result(it, name, flags, ev, decision):
    rec = {"iter": it, "name": name, "flags": {k: (list(v) if isinstance(v, tuple) else v)
                                               for k, v in flags.items()},
           "decision": decision,
           "windows": {w: {"final": ev[w]["m"]["final"], "cagr": ev[w]["m"]["cagr"],
                           "mdd": ev[w]["m"]["mdd"], "sharpe": ev[w]["m"]["sharpe"],
                           "obj": ev[w]["obj"], "spy_cagr": ev[w]["spy"]["cagr"]}
                       for w, _, _ in WINDOWS}}
    with open(ALL_RESULTS, "a") as f:
        f.write(json.dumps(rec) + "\n")


def main():
    close = H.load_close()
    champ_flags = load_champion_flags()
    champ_cfg = H.make_cfg(**champ_flags)
    champ_ev = evaluate_windows(champ_cfg, close)
    champ_obj = champ_ev[PRIMARY]["obj"]

    with open(LOG, "a") as f:
        f.write(f"\n# ===== ROUND {ROUND} (building on champion `{champ_flags}`) =====\n\n")
        f.write(f"Champion baseline this round — 2005-2024 obj {champ_obj:+.3f}, "
                f"CAGR {champ_ev[PRIMARY]['m']['cagr']:+.1%}, MDD {champ_ev[PRIMARY]['m']['mdd']:.1%}, "
                f"WF-test 2017-2024 CAGR {champ_ev['WF-test 2017-2024']['m']['cagr']:+.1%}.\n\n")

    print(f"ROUND {ROUND} champion obj={champ_obj:+.3f} "
          f"CAGR={champ_ev[PRIMARY]['m']['cagr']:+.1%} MDD={champ_ev[PRIMARY]['m']['mdd']:.1%} "
          f"WFtest={champ_ev['WF-test 2017-2024']['m']['cagr']:+.1%}")
    append_result(next_iter() - 1 if False else 0, f"R{ROUND}-champion", champ_flags, champ_ev, "BASELINE")

    promoted = False
    for name, hypo, delta in MOVES:
        it = next_iter()
        cand_flags = copy.deepcopy(champ_flags)
        cand_flags.update(delta)
        try:
            cand_cfg = H.make_cfg(**cand_flags)
            cand_ev = evaluate_windows(cand_cfg, close)
        except Exception as e:
            with open(LOG, "a") as f:
                f.write(f"## Iteration {it} — {name}\n\n{hypo}\n\nERROR: {e}\n\n**REVERT**\n\n")
            print(f"Iter {it} {name}: ERROR {e}")
            continue

        cand_obj = cand_ev[PRIMARY]["obj"]
        improved = cand_obj > champ_obj + 1e-6
        robust, rmsg = robustness_ok(cand_ev, champ_ev)
        keep = improved and robust
        # Note risk-reduction wins even when objective doesn't improve.
        dmdd = abs(cand_ev[PRIMARY]["m"]["mdd"]) - abs(champ_ev[PRIMARY]["m"]["mdd"])
        dcagr = cand_ev[PRIMARY]["m"]["cagr"] - champ_ev[PRIMARY]["m"]["cagr"]
        risk_win = (dmdd < -0.03 and dcagr > -0.01 and robust)
        decision = "KEEP" if keep else ("NOTE-risk-reduction" if risk_win else "REVERT")

        with open(LOG, "a") as f:
            f.write(f"## Iteration {it} — {name}\n\n{hypo}\n\nDelta: `{delta}`\n\n")
            f.write("| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            f.write(row(name, cand_ev) + "\n\n")
            f.write(f"Obj 2005-2024: {cand_obj:+.3f} vs champ {champ_obj:+.3f}. "
                    f"ΔMDD {dmdd:+.1%}, ΔCAGR {dcagr:+.1%}. Robustness: {rmsg}. "
                    f"WF-test 2017-2024 CAGR {cand_ev['WF-test 2017-2024']['m']['cagr']:+.1%} "
                    f"vs SPY {cand_ev['WF-test 2017-2024']['spy']['cagr']:+.1%}.\n\n")
            f.write(f"Decision: **{decision}**.\n\n")

        append_result(it, name, cand_flags, cand_ev, decision)
        print(f"Iter {it} {name:<16} obj {cand_obj:+.3f} CAGR {cand_ev[PRIMARY]['m']['cagr']:+.1%} "
              f"MDD {cand_ev[PRIMARY]['m']['mdd']:.1%} WFtest {cand_ev['WF-test 2017-2024']['m']['cagr']:+.1%} "
              f"-> {decision}")

        if keep:
            champ_flags, champ_ev, champ_obj = cand_flags, cand_ev, cand_obj
            promoted = True
            with open(CHAMP_JSON, "w") as f:
                json.dump({"champion_flags": {k: (list(v) if isinstance(v, tuple) else v)
                                              for k, v in champ_flags.items()},
                           "objective_2005_2024": champ_obj,
                           "metrics": {w: {k: champ_ev[w]["m"][k]
                                           for k in ("final", "cagr", "mdd", "sharpe", "sortino", "worst_year")}
                                       for w, _, _ in WINDOWS},
                           "spy": {w: {k: champ_ev[w]["spy"][k] for k in ("final", "cagr", "mdd", "sharpe")}
                                   for w, _, _ in WINDOWS}}, f, indent=2)

    print(f"\nROUND {ROUND} done. Champion promoted: {promoted}. Flags: {champ_flags}")
    print(f"  2005-2024 obj {champ_obj:+.3f} CAGR {champ_ev[PRIMARY]['m']['cagr']:+.1%} "
          f"MDD {champ_ev[PRIMARY]['m']['mdd']:.1%}")


if __name__ == "__main__":
    main()
