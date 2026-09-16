"""Sequential hill-climbing research loop for the rotation-v2 study.

Starts from the baseline champion and tries one change per iteration. A change is
KEPT only if it (a) improves the objective on the primary 2005-2024 window AND
(b) passes the robustness gate (no sub-period CAGR drops more than 1.5pp vs the
current champion). Every iteration is logged to reports/iteration_log.md and the
running champion is saved to reports/champion_config.json.

Run:  .venv/bin/python reports/run_iterations.py
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research_harness as H  # noqa: E402  (reports/ is on sys.path via __file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_harness as H   # noqa

PRIMARY = "2005-2024"
SUBWINDOWS = ["2005-2014", "2015-2024", "2018-2024"]
ROBUSTNESS_TOL = 0.015  # a sub-period CAGR may not fall more than 1.5pp vs champion

LOG = os.path.join(os.path.dirname(__file__), "iteration_log.md")
CHAMP_JSON = os.path.join(os.path.dirname(__file__), "champion_config.json")

# Ordered candidate moves: each is (name, hypothesis, {flag deltas to merge into champion}).
MOVES = [
    ("trend_filter", "Drop sectors below their own 200-day SMA — should cut bear-market drawdown.",
     {"rotation_trend_filter": True}),
    ("cluster_filter", "Hold at most one ETF per correlation cluster — kills the SMH+XLK+QQQ triple-tech bet.",
     {"rotation_cluster_filter": True}),
    ("skip_month", "12-1 skip-month momentum (skip last 21d) — avoids short-term reversal whipsaw.",
     {"rotation_skip_days": 21}),
    ("dual_momentum", "Require each sector to beat SPY's own momentum, not merely be >0 — stronger risk-off.",
     {"rotation_dual_momentum": True}),
    ("regime_filter", "When SPY < its 200-SMA, go fully defensive — should dodge 2008/2022.",
     {"rotation_regime_filter": True}),
    ("momentum_weight", "Size positions by momentum strength (floor 15%) instead of equal weight.",
     {"rotation_momentum_weight": True}),
    ("defensive_TLT", "Use long treasuries (TLT) as the risk-off asset instead of T-bills (BIL).",
     {"rotation_defensive_symbol": "TLT"}),
    ("defensive_GLD", "Use gold (GLD) as the risk-off asset instead of T-bills.",
     {"rotation_defensive_symbol": "GLD"}),
    ("defensive_IEF", "Use intermediate treasuries (IEF) as the risk-off asset.",
     {"rotation_defensive_symbol": "IEF"}),
    ("expanded_universe", "Add bonds/gold/intl/commodities so momentum can rotate beyond equities.",
     {"universe": H.BASE_UNIVERSE + H.EXTRA_UNIVERSE}),
    ("top_n_2", "Concentrate into the top 2 sectors instead of 3.",
     {"top_n": 2}),
    ("top_n_4", "Diversify into the top 4 sectors instead of 3.",
     {"top_n": 4}),
    ("lookback_3blend", "Blend 3/6/12-month momentum instead of 6/12.",
     {"lookbacks": (63, 126, 252)}),
    ("lookback_12only", "Use 12-month momentum only (slower, less whipsaw).",
     {"lookbacks": (252,)}),
]


def champ_flags_to_kwargs(flags: dict) -> dict:
    return dict(flags)


def evaluate_flags(close, flags: dict):
    cfg = H.make_cfg(**champ_flags_to_kwargs(flags))
    return H.evaluate(cfg, close), cfg


def robustness_ok(cand_ev, champ_ev) -> tuple:
    """No sub-period CAGR may fall more than ROBUSTNESS_TOL vs the champion."""
    for w in SUBWINDOWS:
        dc = cand_ev[w]["m"]["cagr"] - champ_ev[w]["m"]["cagr"]
        if dc < -ROBUSTNESS_TOL:
            return False, f"{w} CAGR {dc:+.1%} (< -1.5pp)"
    return True, "all sub-periods hold"


def metrics_block(ev) -> str:
    lines = []
    for w, _, _ in H.WINDOWS:
        m, spy = ev[w]["m"], ev[w]["spy"]
        beat = "WIN" if m["cagr"] > spy["cagr"] else "lose"
        lines.append(
            f"| {w} | ${m['final']:,.0f} | {m['cagr']:+.1%} | {m['mdd']:.1%} | "
            f"{m['sharpe']:.2f} | {m['worst_year']:+.1%} | {ev[w]['obj']:+.2f} | "
            f"{spy['cagr']:+.1%} | {beat} |")
    return "\n".join(lines)


def main():
    close = H.load_close()
    champ_flags: dict = {}  # baseline
    champ_ev, champ_cfg = evaluate_flags(close, champ_flags)

    with open(LOG, "w") as f:
        f.write("# Rotation-v2 — Iterative Research Log (20-year study)\n\n")
        f.write("Primary benchmark: SPY buy-and-hold over 2005-2024 "
                f"(${champ_ev[PRIMARY]['spy']['final']:,.0f}, "
                f"{champ_ev[PRIMARY]['spy']['cagr']:+.1%} CAGR, "
                f"{champ_ev[PRIMARY]['spy']['mdd']:.1%} MDD).\n\n")
        f.write("Objective = Sharpe + 2·(CAGR − SPY_CAGR) − 0.5·max(0, |MDD|−|SPY_MDD|), "
                "on 2005-2024. A change is KEPT only if it raises the objective on "
                "2005-2024 AND no sub-period CAGR drops > 1.5pp vs the champion.\n\n")
        f.write("## Iteration 0 — Baseline (no v2 flags)\n\n")
        f.write("Hypothesis: establish the honest 20-year baseline.\n\n")
        f.write("| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        f.write(metrics_block(champ_ev) + "\n\n")
        f.write(f"**Champion objective (2005-2024): {champ_ev[PRIMARY]['obj']:+.3f}** — "
                f"baseline {'BEATS' if champ_ev[PRIMARY]['m']['cagr'] > champ_ev[PRIMARY]['spy']['cagr'] else 'loses to'} SPY.\n\n")

    print(f"Iter 0 baseline: obj={champ_ev[PRIMARY]['obj']:+.3f} "
          f"CAGR={champ_ev[PRIMARY]['m']['cagr']:+.1%} MDD={champ_ev[PRIMARY]['m']['mdd']:.1%}")

    history = [("baseline", "KEEP", champ_ev[PRIMARY]['obj'], dict(champ_flags))]

    for i, (name, hypo, delta) in enumerate(MOVES, start=1):
        cand_flags = copy.deepcopy(champ_flags)
        cand_flags.update(delta)
        try:
            cand_ev, cand_cfg = evaluate_flags(close, cand_flags)
        except Exception as e:
            with open(LOG, "a") as f:
                f.write(f"## Iteration {i} — {name}\n\nHypothesis: {hypo}\n\n"
                        f"ERROR: {e}\n\nDecision: **REVERT** (crashed).\n\n")
            history.append((name, "ERROR", None, dict(delta)))
            print(f"Iter {i} {name}: ERROR {e}")
            continue

        cand_obj = cand_ev[PRIMARY]["obj"]
        champ_obj = champ_ev[PRIMARY]["obj"]
        improved = cand_obj > champ_obj + 1e-6
        robust, robust_msg = robustness_ok(cand_ev, champ_ev)
        keep = improved and robust
        decision = "KEEP" if keep else "REVERT"

        with open(LOG, "a") as f:
            f.write(f"## Iteration {i} — {name}\n\n")
            f.write(f"Hypothesis: {hypo}\n\n")
            f.write(f"Change (delta vs champion): `{delta}`\n\n")
            f.write("| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |\n")
            f.write("|---|---|---|---|---|---|---|---|---|\n")
            f.write(metrics_block(cand_ev) + "\n\n")
            f.write(f"Objective 2005-2024: candidate {cand_obj:+.3f} vs champion {champ_obj:+.3f} "
                    f"→ {'improved' if improved else 'no improvement'}. Robustness: {robust_msg}.\n\n")
            f.write(f"Decision: **{decision}**.\n\n")

        print(f"Iter {i} {name:<18} obj {cand_obj:+.3f} (champ {champ_obj:+.3f}) "
              f"CAGR {cand_ev[PRIMARY]['m']['cagr']:+.1%} MDD {cand_ev[PRIMARY]['m']['mdd']:.1%} "
              f"-> {decision} ({robust_msg if not keep else 'kept'})")

        history.append((name, decision, cand_obj, dict(delta)))
        if keep:
            champ_flags, champ_ev, champ_cfg = cand_flags, cand_ev, cand_cfg
            with open(CHAMP_JSON, "w") as f:
                json.dump({
                    "champion_flags": champ_flags,
                    "objective_2005_2024": champ_ev[PRIMARY]["obj"],
                    "metrics": {w: {k: champ_ev[w]["m"][k]
                                    for k in ("final", "cagr", "mdd", "sharpe", "sortino", "worst_year")}
                                for w, _, _ in H.WINDOWS},
                    "spy": {w: {k: champ_ev[w]["spy"][k] for k in ("final", "cagr", "mdd", "sharpe")}
                            for w, _, _ in H.WINDOWS},
                    "rolling_3y_2005_2024": champ_ev[PRIMARY]["rolling"],
                }, f, indent=2)

    # Final summary
    with open(LOG, "a") as f:
        f.write("## Final Champion\n\n")
        f.write(f"Champion flags: `{champ_flags}`\n\n")
        f.write("| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        f.write(metrics_block(champ_ev) + "\n\n")
        m, spy = champ_ev[PRIMARY]["m"], champ_ev[PRIMARY]["spy"]
        verdict = "BEATS" if m["cagr"] > spy["cagr"] else "LOSES TO"
        f.write(f"**Verdict (2005-2024): champion {verdict} SPY** — "
                f"{m['cagr']:+.1%} vs {spy['cagr']:+.1%} CAGR, "
                f"{m['mdd']:.1%} vs {spy['mdd']:.1%} MDD, "
                f"Sharpe {m['sharpe']:.2f} vs {spy['sharpe']:.2f}.\n\n")
        f.write("### Iteration outcomes\n\n")
        for name, dec, obj, delta in history:
            ostr = f"{obj:+.3f}" if obj is not None else "n/a"
            f.write(f"- {name}: **{dec}** (obj {ostr}) — `{delta}`\n")

    print("\n=== FINAL CHAMPION ===")
    print(f"flags: {champ_flags}")
    for w, _, _ in H.WINDOWS:
        print(H.fmt_row("champion", champ_ev, w))
    print(H.spy_line(champ_ev))
    print(f"\nLog: {LOG}\nChampion: {CHAMP_JSON}")


if __name__ == "__main__":
    main()
