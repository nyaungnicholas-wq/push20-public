"""The monthly checkup must validate the config that is actually trading.

This is the bug that hid for three months. `live_checkup.py` enforced
EXPECT_CAGR = 0.197 / EXPECT_MDD = -0.355, a pair produced by
`run_rotation_backtest`, which rebalances MONTHLY across tranched trading days.
The live system rebalances DAILY under a 3-day minimum hold. The checkup was
reporting "within expectations" about a strategy that was not trading, so it
could never have seen real degradation.

Re-grading is a multi-minute backtest and needs network, so these tests do not
re-run it. They pin the two things that must not drift silently:

  1. the config the checkup derives is exactly the config the live flags produce
  2. that config is the one the recorded expectation was measured on

If you change a DAILY_CHAMPION_FLAGS value, one of these fails. That is the
point: a flag change invalidates the expectation, and the expectation must be
re-measured (reports/holdout_v2.py) rather than quietly left stale.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "reports"))


# The config graded on the sealed 2019-2026 holdout (reports/HOLDOUT_RESULT_v2.json,
# prereg sha256 c5bdee5e26537b1f). Full window 2006-01-01..2026-09-02 measured
# 18.52% CAGR / -28.37% MDD / Sharpe 0.892 / Calmar 0.653.
GRADED = {
    "lookback": 232,
    "ema_span": 9,
    "top_n": 3,
    "min_hold_days": 3,
    "vol_target": 0.5,
    "vol_window": 8,
    "vol_floor": 0.5,
    "vol_cap_bull": 1.25,
    "vol_cap_bear": 1.0,
    "regime_sma": 200,
    "max_weight": 0.6,
    "defensive": "GLD+TLT",
    "weight_scheme": "equal",
    "use_3x": 0,
    "cost_bps": 9.5,
    "vix_gate_level": 25.0,
    "vix_gate_cap": 1.0,
    "basket_vol": 1,
    "defensive_live": 1,
}


def test_checkup_config_is_the_graded_config():
    from live_checkup import live_sim_config

    cfg, _ = live_sim_config()
    assert cfg == GRADED, (
        "live_sim_config() no longer matches the config graded on the holdout.\n"
        f"  added/changed: { {k: v for k, v in cfg.items() if GRADED.get(k) != v} }\n"
        f"  dropped:       { {k: v for k, v in GRADED.items() if k not in cfg} }\n"
        "Re-grade with reports/holdout_v2.py and update GRADED + EXPECT_* together."
    )


def test_expectations_describe_that_config():
    import live_checkup as lc

    # 18.63% / -28.37%, rounded as stored.
    assert lc.EXPECT_CAGR == pytest.approx(0.185, abs=5e-4)
    assert lc.EXPECT_MDD == pytest.approx(-0.284, abs=5e-4)
    # The account fills at ~9.5bps. A 5bps expectation flatters the backtest by
    # roughly a full point of CAGR over this window.
    assert lc.SLIPPAGE_ASSUMED_BPS == pytest.approx(9.5)


def _weighted(rows, key):
    """Notional-weighted mean of `key`. The fields are ALREADY signed at write
    time (positive = worse for us) — flipping sells again here was a real bug on
    2026-09-13, and it read 8.49bps on a book whose drift was 5.41."""
    g = [(r[key], abs(float(r.get("qty", 0)) * float(r.get("fill", 0))))
         for r in rows if key in r]
    w = sum(n for _, n in g)
    return (sum(v * n for v, n in g) / w, len(g)) if w > 0 else (None, 0)


def _slippage_rows():
    import json

    path = os.path.join(ROOT, "data", "slippage_log.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def test_cost_assumption_is_not_optimistic_vs_measured_execution():
    """The assumed cost must not sit below what the book actually pays to execute.

    `slippage_bps` is fill vs the OPEN of the session the order filled in — real
    execution cost. It is NOT `drift_bps`, which is fill vs the decision close and
    therefore carries an overnight gap (a whole weekend on a Friday decision).
    Charging drift as cost is what made this assumption look like ~9.8bps when
    measured execution is ~3.5.
    """
    import live_checkup as lc

    rows = _slippage_rows()
    avg, n = _weighted(rows, "slippage_bps")
    if n < 20:
        pytest.skip(f"only {n} fills with a measured open; too few to judge")
    assert lc.SLIPPAGE_ASSUMED_BPS >= avg, (
        f"assumed {lc.SLIPPAGE_ASSUMED_BPS}bps but {n} live fills executed at "
        f"{avg:.2f}bps notional-weighted — the backtest is flattering itself"
    )


def test_drift_is_recorded_separately_and_not_charged_as_cost():
    """Queue drift must stay visible but must never become the cost assumption.

    It is symmetric market movement across the decision-to-fill gap, so charging
    it to the backtest would penalise the strategy for noise that averages out.
    """
    rows = _slippage_rows()
    drift, n_drift = _weighted(rows, "drift_bps")
    exec_bps, n_exec = _weighted(rows, "slippage_bps")
    if n_drift < 20:
        pytest.skip(f"only {n_drift} rows carry drift_bps")
    assert drift is not None and exec_bps is not None, (
        "both fields must exist; run reports/backfill_slippage.py --apply"
    )
    # They measure different things, so they must not be the same number.
    assert abs(drift - exec_bps) > 0.5, (
        f"drift {drift:.2f} and execution {exec_bps:.2f} are indistinguishable — "
        "the split has probably collapsed back to one metric"
    )


def test_live_flags_produce_the_graded_behaviour():
    """Guard the three flags the holdout actually graded."""
    from trader.rotation import DAILY_CHAMPION_FLAGS as F

    assert F["rotation_return_prop"] is False, "equal weight was the graded choice"
    assert F["rotation_vol_cap"] == 1.25, "1.25x ceiling was the graded choice"
    # The VIX>=35 tier was retired by setting its cap equal to the >=25 cap.
    # Anything lower re-introduces the tier that measurably increased drawdown.
    assert F["rotation_vix_hi_cap"] >= F["rotation_vix_lo_cap"], (
        "the VIX>=35 tier de-levered into the bottom and missed the rebound: "
        "in-sample it cost 1.51pp CAGR and made drawdown worse"
    )
