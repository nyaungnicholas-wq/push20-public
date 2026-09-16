"""Measure what evening mode costs: next-open fills, and a calendar-day hold.

A BACKTEST IS NOT A LIVE RESULT. This measures two mismatches between the
harness that certified the Daily Champion rotation and what the live system
actually does, on the same periods, so each is a number rather than an
assumption:

  * FILLS. The certified backtest fills at the DECISION DAY'S CLOSE. Evening
    mode queues orders after the close, so they fill at the NEXT OPEN and carry
    an overnight gap the backtest never modelled.
  * HOLD UNIT. The backtest counts min_hold in TRADING days
    (`opt_harness.py`, `pos-last<mhd` over trading-day indices). `rotation_live`
    counted CALENDAR days. Friday to Monday is 3 calendar days but 1 trading
    day, so live rebalanced where the backtest would not.

Both knobs default to the certified behaviour, and a defaults run reproduces
the unpatched harness byte-identically -- verified 2026-08-30.

    .venv/Scripts/python.exe experiments/2026-08-30_f8_fill_and_hold.py
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pandas as pd  # noqa: E402
from reports import opt_harness as H  # noqa: E402

PERIODS = [
    ("live_25_now", "2025-01-01", None),
    ("full_06_now", "2006-01-01", None),
]

VARIANTS = [
    ("close/trading", "close", "trading"),          # the baseline that certified it
    ("next_open/trading", "next_open", "trading"),
    ("close/calendar", "close", "calendar"),
    ("next_open/calendar", "next_open", "calendar"),  # what live would actually do
]


def run_variant(df, cfg, start, end, fill_mode, hold_unit):
    """One variant, with the globals always restored."""
    orig = (H.FILL_MODE, H.HOLD_UNIT)
    try:
        H.FILL_MODE, H.HOLD_UNIT = fill_mode, hold_unit
        H.FILL_FALLBACKS = 0
        m = dict(H.metrics(H.simulate(df, cfg, start, end)) or {})
        m["fill_fallbacks"] = H.FILL_FALLBACKS
        return m
    finally:
        H.FILL_MODE, H.HOLD_UNIT = orig


def pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def num(x):
    return "n/a" if x is None else f"{x:.2f}"


def pp(a, b):
    """Difference in percentage points, or None if either side is missing."""
    return None if (a is None or b is None) else (a - b) * 100


def fmt_pp(x):
    return "n/a" if x is None else f"{x:+.2f}pp"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="{}")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "RESULTS.md"))
    a = ap.parse_args()

    cfg = json.loads(a.config)
    df = H.load_data()
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    lines = []

    for name, start, end in PERIODS:
        end = end or today
        print(f"\n## {name}  ({start} -> {end})\n")

        spy = dict(H.metrics(H.spy_bh(df, start, end)) or {})
        spy_cagr = spy.get("cagr")
        res = {lbl: run_variant(df, cfg, start, end, f, h)
               for lbl, f, h in VARIANTS}

        print("| Config | CAGR | MaxDD | Calmar | Sharpe | Win% | Rebals | FillFallbacks | vs SPY |")
        print("|---|---|---|---|---|---|---|---|---|")
        print(f"| SPY buy-and-hold | {pct(spy_cagr)} | {pct(spy.get('mdd'))} | "
              f"{num(spy.get('calmar'))} | {num(spy.get('sharpe'))} | "
              f"{pct(spy.get('win_rate_monthly'))} | - | - | - |")
        for lbl, _f, _h in VARIANTS:
            m = res[lbl]
            print(f"| {lbl} | {pct(m.get('cagr'))} | {pct(m.get('mdd'))} | "
                  f"{num(m.get('calmar'))} | {num(m.get('sharpe'))} | "
                  f"{pct(m.get('win_rate_monthly'))} | {m.get('n_rebals', 'n/a')} | "
                  f"{m.get('fill_fallbacks', 'n/a')} | "
                  f"{fmt_pp(pp(m.get('cagr'), spy_cagr))} |")

        base, nxt, cal = (res["close/trading"], res["next_open/trading"],
                          res["close/calendar"])
        d_fill = (pp(nxt.get("cagr"), base.get("cagr")),
                  pp(nxt.get("mdd"), base.get("mdd")))
        d_hold = (pp(cal.get("cagr"), base.get("cagr")),
                  pp(cal.get("mdd"), base.get("mdd")))

        print("\n### deltas vs the certified baseline (close/trading)")
        print(f"cost of next-open fills : CAGR {fmt_pp(d_fill[0])}  MDD {fmt_pp(d_fill[1])}")
        print(f"cost of calendar-day hold: CAGR {fmt_pp(d_hold[0])}  MDD {fmt_pp(d_hold[1])}")
        if nxt.get("fill_fallbacks"):
            print(f"NOTE: {nxt['fill_fallbacks']} next-open fills had no open and "
                  f"used that day's close -- opens are not synthesized for the "
                  f"leveraged sleeves, so treat the fill delta as a floor.")

        lines.append(
            f"- {date.today().isoformat()} f8 fill/hold measurement {name}: "
            f"baseline CAGR {pct(base.get('cagr'))} MDD {pct(base.get('mdd'))}; "
            f"next-open delta CAGR {fmt_pp(d_fill[0])} MDD {fmt_pp(d_fill[1])}; "
            f"calendar-hold delta CAGR {fmt_pp(d_hold[0])} MDD {fmt_pp(d_hold[1])}; "
            f"script experiments/{os.path.basename(__file__)}")

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nappended {len(lines)} line(s) to {a.out}")


if __name__ == "__main__":
    main()
