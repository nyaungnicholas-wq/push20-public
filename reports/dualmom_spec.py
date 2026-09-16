"""Measure the textbook Dual-Momentum Sector Rotation spec, as written.

The spec:
  - universe: liquid US sector ETFs
  - absolute momentum: SPY > 200 SMA -> 100% long; SPY < 200 SMA -> 100% BIL/SHV
  - relative momentum: rank monthly by 0.6 * 3-month return + 0.4 * 12-month return
  - allocate equally to the top 2 or 3, rebalance monthly
  - no leverage

Claimed: 24.6% CAGR, -18.4% MDD, Sharpe 1.22, ~78% time in market, over 20 years.

Expressed in opt_harness terms:
  vol_cap_bull=1.0, vol_target huge    -> scale pinned at 1.0 in risk-on (100% long)
  vol_cap_bear=0.0                     -> scale 0 in risk-off, everything to defensive
  use_lev=False                        -> never touch a 2x fund
  min_hold_days=21                     -> monthly cadence
  ema_span=1                           -> raw returns, no smoothing
  lookback=(63,252), lookback_weights=(0.6,0.4)
"""
from __future__ import annotations
import os, sys, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "reports"))

import opt_harness as H
H.FETCH_END = "2026-09-02"

SPEC = dict(
    lookback=(63, 252), lookback_weights=(0.6, 0.4), ema_span=1,
    top_n=3, min_hold_days=21,
    vol_target=99.0, vol_floor=0.0, vol_cap_bull=1.0, vol_cap_bear=0.0,
    regime_sma=200, use_lev=False, weight_scheme="equal",
    defensive="SHY", defensive_live=1, basket_vol=0,
    vix_gate_level=0.0, vix_hi_level=0.0, cost_bps=9.5,
)

# The config that is live on main as of 2026-09-13, for reference.
from live_checkup import live_sim_config
LIVE, _ = live_sim_config()

WINDOWS = [("full  2006-2026", "2006-01-01", "2026-09-02"),
           ("dev   2006-2018", "2006-01-01", "2018-12-31"),
           ("late  2019-2026", "2019-01-01", "2026-09-02")]


def row(tag, cfg, df, pad=34):
    out = []
    for _, s, e in WINDOWS:
        m = H.metrics(H.simulate(df, cfg, s, e))
        out.append(m)
    line = f"{tag:<{pad}}"
    for m in out:
        line += f"{m['cagr']*100:>7.2f}%{m['mdd']*100:>8.2f}%{m['sharpe']:>7.2f}"
    print(line); sys.stdout.flush()
    return out


def main():
    df = H.load_data()
    print(f"{'':<34}" + "".join(f"{w[0]:>22}" for w in WINDOWS))
    print(f"{'':<34}" + "".join(f"{'CAGR     MDD  Sharpe':>22}" for _ in WINDOWS))
    print("-" * 100)

    spy = []
    for _, s, e in WINDOWS:
        spy.append(H.metrics(H.spy_bh(df, s, e)))
    print(f"{'SPY buy and hold':<34}" + "".join(
        f"{m['cagr']*100:>7.2f}%{m['mdd']*100:>8.2f}%{m['sharpe']:>7.2f}" for m in spy))

    row("dual-mom spec, 9.5bps (as written)", SPEC, df)
    row("  ... top 2 instead of 3", {**SPEC, "top_n": 2}, df)
    row("  ... equal blend, not 0.6/0.4", {**SPEC, "lookback_weights": None}, df)
    row("  ... 12-1 only (skip recent month)", {**SPEC, "lookback": (252,), "lookback_weights": None,
                                                "skip_days": 21}, df)
    row("  ... ZERO costs (the usual claim)", {**SPEC, "cost_bps": 0.0}, df)
    row("  ... zero costs AND top 2", {**SPEC, "cost_bps": 0.0, "top_n": 2}, df)
    print()
    row("PUSH-20 v2 (live on main)", dict(LIVE), df)
    row("  ... v2 with leverage OFF", {**LIVE, "use_lev": False, "vol_cap_bull": 1.0}, df)


if __name__ == "__main__":
    main()
