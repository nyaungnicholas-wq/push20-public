"""Robustness / stress battery for the converged rotation-v2 champion.

The hill-climb has plateaued, so chasing more objective = overfitting. This script
instead STRESSES the champion: does the edge survive higher costs, a different
risk-off asset, different start dates, and parameter perturbation? It does NOT
promote a champion — it just prints honest sensitivity tables.

Run:  .venv/bin/python reports/stress_test.py
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_harness as H  # noqa: E402

# The SIMPLE, deployable champion (round-3/4 micro-tweaks dropped as likely noise).
SIMPLE = dict(rotation_momentum_weight=True, rotation_defensive_symbol="GLD",
              lookbacks=(252,), rotation_vol_target=0.10, rotation_vol_window=20)
# The fully-tuned champion the hill-climb landed on.
TUNED = dict(rotation_momentum_weight=True, rotation_defensive_symbol="GLD",
             lookbacks=(252,), rotation_vol_target=0.09, rotation_vol_window=30,
             rotation_weight_floor=0.10, rotation_abs_momentum=False)

OUT = os.path.join(os.path.dirname(__file__), "stress_results.md")


def ev(close, start, end, **flags):
    cfg = H.make_cfg(**flags)
    r = H.run(cfg, close, start, end)
    m = H.metrics(r["equity_curve"])
    spy = H.metrics(r["benchmark"]["equity_curve"])
    return m, spy


def line(label, m, spy):
    edge = m["cagr"] - spy["cagr"]
    beat = "WIN " if edge > 0 else "LOSE"
    return (f"  {label:<26} CAGR {m['cagr']:+6.1%}  MDD {m['mdd']:6.1%}  "
            f"Sharpe {m['sharpe']:4.2f}  | SPY {spy['cagr']:+5.1%}  edge {edge:+5.1%} [{beat}]")


def main():
    close = H.load_close()
    L = []
    def pr(s):
        print(s); L.append(s)

    pr("# Rotation-v2 champion — robustness / stress battery\n")

    pr("## 1. Simple vs fully-tuned champion (2005-2024)")
    pr("   (Are the round-3/4 micro-tweaks real, or noise? If simple ≈ tuned, prefer simple.)")
    m, spy = ev(close, "2005-01-01", "2024-12-31", **SIMPLE)
    pr(line("simple champion", m, spy))
    m, spy = ev(close, "2005-01-01", "2024-12-31", **TUNED)
    pr(line("tuned champion", m, spy))
    pr("")

    pr("## 2. Transaction-cost sensitivity (simple champion, 2005-2024)")
    pr("   (10bps is the study default. Does the edge survive realistic / pessimistic costs?)")
    for bps in (5, 10, 20, 30, 50):
        f = dict(SIMPLE); f["slippage_bps"] = bps
        m, spy = ev(close, "2005-01-01", "2024-12-31", **f)
        pr(line(f"slippage {bps}bps/side", m, spy))
    pr("")

    pr("## 3. Risk-off asset dependence (simple champion, 2005-2024)")
    pr("   (How much of the edge rides on the GLD choice specifically?)")
    for d in ("GLD", "TLT", "IEF", "BIL"):
        f = dict(SIMPLE); f["rotation_defensive_symbol"] = d
        m, spy = ev(close, "2005-01-01", "2024-12-31", **f)
        pr(line(f"defensive = {d}", m, spy))
    pr("")

    pr("## 4. Start-date sensitivity (simple champion → 2024-12-31)")
    pr("   (Is the 20-year result an artifact of starting in 2005?)")
    for s in ("2005-01-01", "2006-01-01", "2007-01-01", "2008-01-01", "2010-01-01", "2012-01-01"):
        m, spy = ev(close, s, "2024-12-31", **SIMPLE)
        pr(line(f"start {s[:4]}", m, spy))
    pr("")

    pr("## 5. Parameter perturbation (simple champion, 2005-2024)")
    pr("   (Is the champion on a knife-edge, or a broad plateau? Stable = trustworthy.)")
    for vt in (0.08, 0.10, 0.12, 0.15):
        f = dict(SIMPLE); f["rotation_vol_target"] = vt
        m, spy = ev(close, "2005-01-01", "2024-12-31", **f)
        pr(line(f"vol_target {vt:.2f}", m, spy))
    for lb in (210, 231, 252, 273):
        f = dict(SIMPLE); f["lookbacks"] = (lb,)
        m, spy = ev(close, "2005-01-01", "2024-12-31", **f)
        pr(line(f"lookback {lb}d", m, spy))
    pr("")

    pr("## 6. Cross-check vs SPY on every window (simple champion)")
    for name, a, b in H.WINDOWS:
        m, spy = ev(close, a, b, **SIMPLE)
        pr(line(name, m, spy))

    with open(OUT, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nSaved → {OUT}")


if __name__ == "__main__":
    main()
