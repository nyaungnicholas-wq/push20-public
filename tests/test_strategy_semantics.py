"""Strategy semantics, on a synthetic frame with hand-computed answers.

Why this file exists
--------------------
Audited 2026-09-17: the 86-test suite had ZERO strategy coverage. Seven separate
mutations to the momentum comparator, selection, weighting, leverage-tier routing
and the position cap all left it green. Everything that was tested was plumbing --
ledger rows, risk gates, slippage bookkeeping -- and nothing asserted what the
strategy actually computes.

The frames here are synthetic and the expected numbers are written out as closed
forms, not captured from a run. A snapshot of current behaviour would only pin
the bugs in place; a closed form fails when the behaviour moves, whichever
direction it moves in.

Where a test pins behaviour that is KNOWN to be defective, the docstring says so
explicitly and names the defect. Those are tripwires, not endorsements: if
someone fixes the defect the test fails and points at the note.

Conventions used throughout:
  * `lookbacks=(L,)` measures close[pos] / close[pos-L+1] - 1, i.e. L closes and
    L-1 compounding steps. See test_lookback_window_spans_L_closes_not_L_returns.
  * realized vol is std(daily arithmetic returns, ddof=1) * sqrt(252) over a
    window of `rotation_vol_window` returns (window+1 closes).
"""
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "reports"))

from trader import rotation as R
from trader.config import Config

ANN = math.sqrt(252.0)


@pytest.fixture(autouse=True)
def _clear_frame_cache():
    """`rotation._arrays` caches on `id(close)` and only revalidates on row count.

    Two frames of the same length in one process can therefore collide if the
    first is garbage-collected and the allocator reuses its address. That is a
    real latent bug in the hot path, not a test artifact; here it would make
    results depend on test ordering, so the cache is cleared around every test.
    """
    R._ARR_CACHE.clear()
    yield
    R._ARR_CACHE.clear()


# ---------------------------------------------------------------------------
# synthetic frames
# ---------------------------------------------------------------------------
def _series(rets, p0=100.0):
    """Price path from a per-day return sequence. len == len(rets) + 1."""
    out = [float(p0)]
    for r in rets:
        out.append(out[-1] * (1.0 + r))
    return out


def _frame(spec, n):
    """Close frame of `n` business days.

    `spec` maps symbol -> a constant daily return (float) or an explicit
    sequence of n-1 returns.
    """
    idx = pd.bdate_range("2019-01-02", periods=n)
    data = {}
    for sym, r in spec.items():
        rets = [float(r)] * (n - 1) if np.isscalar(r) else list(r)
        assert len(rets) == n - 1, f"{sym}: need {n-1} returns, got {len(rets)}"
        data[sym] = _series(rets)
    return pd.DataFrame(data, index=idx)


def _cfg(**kw):
    c = Config()
    c.rotation_lookbacks = (5,)
    c.rotation_top_n = 3
    c.rotation_abs_momentum = True
    c.rotation_signal_ema = 1
    c.rotation_defensive_symbol = "GLD+TLT"
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ===========================================================================
# 1. MOMENTUM RANKING
# ===========================================================================
def test_momentum_ranking_order_and_values_are_exact():
    """Four constant-growth sectors: the ordering is unambiguous by construction.

    With lookbacks=(5,) the score is close[pos]/close[pos-4] - 1 = (1+g)^4 - 1,
    which is strictly increasing in g, so the correct ranking is g-descending.
    """
    g = {"XLK": 0.010, "XLF": 0.005, "XLE": 0.000, "XLV": -0.005}
    close = _frame(g, 40)
    asof = close.index[-1]

    ranked = R.momentum_scores(close, list(g), asof, (5,))

    assert list(ranked.index) == ["XLK", "XLF", "XLE", "XLV"]
    for s, gi in g.items():
        assert ranked[s] == pytest.approx((1.0 + gi) ** 4 - 1.0, rel=1e-12)
    # hand-computed, spelled out so a silent formula change is visible
    assert ranked["XLK"] == pytest.approx(0.04060401, abs=1e-9)
    assert ranked["XLV"] == pytest.approx(-0.019850499375, abs=1e-9)


def test_lookback_window_spans_L_closes_not_L_returns():
    """`lookbacks=(L,)` uses L closes => L-1 returns. Pinned deliberately.

    KNOWN FIDELITY BREAK (ROUND1: WINDOW BREAK). At the live
    rotation_lookbacks=(232,) this computes a 231-day return, while
    reports/opt_harness.py computes a 232-day return. The certifying engine and
    the live book therefore measure different windows. This test pins the LIVE
    convention so the break cannot widen unnoticed; it is not a statement that
    231 is right.
    """
    # flat, then one +10% day: the score is 0.10 exactly while the jump is
    # inside the window and 0.0 once it has fallen out of it.
    n = 30
    rets = [0.0] * (n - 1)
    rets[19] = 0.10                       # close[20] is the first post-jump close
    close = _frame({"XLK": rets}, n)

    # pos=24, L=5 -> close[24]/close[20]: the jump is NOT inside the window
    assert R.momentum_scores(close, ["XLK"], close.index[24], (5,))["XLK"] == pytest.approx(0.0)
    # pos=24, L=6 -> close[24]/close[19] = 110/100 - 1
    assert R.momentum_scores(close, ["XLK"], close.index[24], (6,))["XLK"] == pytest.approx(0.10)
    # boundary: L=5 at pos=23 -> close[23]/close[19] = 0.10
    assert R.momentum_scores(close, ["XLK"], close.index[23], (5,))["XLK"] == pytest.approx(0.10)


def test_blended_lookbacks_are_the_arithmetic_mean_of_the_legs():
    close = _frame({"XLK": 0.01}, 40)
    asof = close.index[-1]
    a = R.momentum_scores(close, ["XLK"], asof, (5,))["XLK"]
    b = R.momentum_scores(close, ["XLK"], asof, (11,))["XLK"]
    blend = R.momentum_scores(close, ["XLK"], asof, (5, 11))["XLK"]
    assert a == pytest.approx(1.01 ** 4 - 1.0)
    assert b == pytest.approx(1.01 ** 10 - 1.0)
    assert blend == pytest.approx((a + b) / 2.0, rel=1e-12)


def test_ema_span_1_is_exactly_the_unsmoothed_score():
    """Documented contract of momentum_scores_ema: span=1 reproduces the raw score."""
    close = _frame({"XLK": 0.01, "XLF": 0.002}, 40)
    asof = close.index[-1]
    raw = R.momentum_scores(close, ["XLK", "XLF"], asof, (5,))
    ema = R.momentum_scores_ema(close, ["XLK", "XLF"], asof, (5,), ema_span=1)
    for s in ("XLK", "XLF"):
        assert ema[s] == pytest.approx(raw[s], rel=1e-12)


def test_ema_smoothing_is_the_hand_computed_recursion():
    """Three known daily momentum readings -> one hand-computed EMA.

    Prices are flat at 100 then +10% on three consecutive days, so with
    lookbacks=(5,) the last three readings are exactly 0.10, 0.21 and 0.331
    (each divides by a close that is still 100). alpha = 2/(3+1) = 0.5, seeded
    on the oldest reading:
        e0 = 0.10
        e1 = 0.5*0.21  + 0.5*0.10  = 0.155
        e2 = 0.5*0.331 + 0.5*0.155 = 0.243
    """
    n = 30
    rets = [0.0] * (n - 1)
    rets[19] = rets[20] = rets[21] = 0.10     # closes 20, 21, 22 are 110/121/133.1
    close = _frame({"XLK": rets}, n)
    asof = close.index[22]

    raw = [R.momentum_scores(close, ["XLK"], close.index[i], (5,))["XLK"] for i in (20, 21, 22)]
    assert raw == pytest.approx([0.10, 0.21, 0.331], abs=1e-12)

    ema = R.momentum_scores_ema(close, ["XLK"], asof, (5,), ema_span=3)["XLK"]
    assert ema == pytest.approx(0.243, abs=1e-12)


# ===========================================================================
# 2. SELECTION
# ===========================================================================
def _sel_frame():
    return _frame({
        "XLK": 0.010, "XLF": 0.006, "XLE": 0.003,      # positive momentum
        "XLV": -0.002, "XLI": -0.006,                  # negative momentum
        "GLD": 0.001, "TLT": 0.001,                    # defensive sleeve
    }, 40)


def test_select_targets_takes_top_n_in_rank_order():
    close = _sel_frame()
    cfg = _cfg(rotation_universe=["XLK", "XLF", "XLE", "XLV", "XLI"], rotation_top_n=3)
    assert R.select_targets(close, cfg, close.index[-1]) == ["XLK", "XLF", "XLE"]

    cfg.rotation_top_n = 2
    assert R.select_targets(close, cfg, close.index[-1]) == ["XLK", "XLF"]


def test_absolute_momentum_gate_fires_and_fills_with_the_defensive_sleeve():
    """Only two names are positive, so slot 3 must go to the defensive sleeve.

    `_defensive_list` splits "GLD+TLT" and empty slots are filled round-robin
    from the head, so one empty slot takes GLD.
    """
    close = _sel_frame()
    cfg = _cfg(rotation_universe=["XLK", "XLF", "XLV", "XLI"],
               rotation_top_n=3, rotation_abs_momentum=True)
    assert R.select_targets(close, cfg, close.index[-1]) == ["XLK", "XLF", "GLD"]

    # three empty slots -> GLD, TLT, GLD (round robin over a 2-name sleeve)
    cfg2 = _cfg(rotation_universe=["XLV", "XLI"], rotation_top_n=3,
                rotation_abs_momentum=True)
    assert R.select_targets(close, cfg2, close.index[-1]) == ["GLD", "TLT", "GLD"]


def test_absolute_momentum_gate_not_firing_admits_a_negative_sector():
    close = _sel_frame()
    cfg = _cfg(rotation_universe=["XLK", "XLF", "XLV", "XLI"],
               rotation_top_n=3, rotation_abs_momentum=False)
    picks = R.select_targets(close, cfg, close.index[-1])
    assert picks == ["XLK", "XLF", "XLV"], "gate off must admit the negative name"
    # and the gate is the ONLY difference between the two runs
    cfg.rotation_abs_momentum = True
    assert R.select_targets(close, cfg, close.index[-1]) == ["XLK", "XLF", "GLD"]


def test_gate_is_strictly_greater_than_zero():
    """A sector with exactly zero momentum must NOT qualify (`> 0.0`, not `>=`)."""
    close = _frame({"XLK": 0.010, "XLF": 0.000, "GLD": 0.001, "TLT": 0.001}, 40)
    cfg = _cfg(rotation_universe=["XLK", "XLF"], rotation_top_n=2,
               rotation_abs_momentum=True)
    assert R.momentum_scores(close, ["XLF"], close.index[-1], (5,))["XLF"] == pytest.approx(0.0)
    assert R.select_targets(close, cfg, close.index[-1]) == ["XLK", "GLD"]


def test_tie_at_the_selection_boundary_is_resolved_deterministically():
    """Two exactly-equal scores straddle the top_n cut.

    The sort is `Series.sort_values()`, whose default kind is not documented as
    stable, so WHICH of the tied names wins is not a guarantee this test can
    make. What it can and must guarantee is that the answer is the same every
    time: a book that flip-flops between two equal names on repeated runs would
    churn commissions for nothing.
    """
    close = _frame({"XLK": 0.010, "XLF": 0.004, "XLE": 0.004,
                    "GLD": 0.001, "TLT": 0.001}, 40)
    cfg = _cfg(rotation_universe=["XLK", "XLF", "XLE"], rotation_top_n=2)
    asof = close.index[-1]

    ranked = R.momentum_scores(close, ["XLF", "XLE"], asof, (5,))
    assert ranked["XLF"] == ranked["XLE"], "frame must actually tie"

    runs = [R.select_targets(close, cfg, asof) for _ in range(5)]
    assert all(r == runs[0] for r in runs), f"tie-break is not deterministic: {runs}"
    assert runs[0][0] == "XLK"
    assert len(runs[0]) == 2 and runs[0][1] in ("XLF", "XLE")


# ===========================================================================
# 3. WEIGHTING
# ===========================================================================
def test_rebalance_weights_returns_none_when_no_weighting_feature_is_active():
    """None is the sentinel for plain equal weight; it is not an error path."""
    close = _sel_frame()
    cfg = _cfg(rotation_momentum_weight=False, rotation_weight_scheme="momentum",
               rotation_position_cap=1.0, rotation_vol_target=0.0)
    assert R.rebalance_weights(close, cfg, close.index[-1], ["XLK", "XLF"]) is None


def test_equal_weight_with_a_cap_that_does_not_bind():
    close = _sel_frame()
    cfg = _cfg(rotation_position_cap=0.30, rotation_vol_target=0.0)
    w = R.rebalance_weights(close, cfg, close.index[-1], ["XLK", "XLF", "XLE", "XLV"])
    assert set(w) == {"XLK", "XLF", "XLE", "XLV"}
    for s in w:
        assert w[s] == pytest.approx(0.25)


def test_cap_binds_in_rebalance_weights_and_the_result_no_longer_sums_to_one():
    """top_n=2 -> 0.50 each, capped at 0.40, and this path does NOT renormalise.

    Hand-computed: both names are over the cap, both are set to 0.40, there is
    no under-cap name to absorb the 0.20 of excess, so the dict sums to 0.80.
    That is a real property of `rebalance_weights` (unlike `turbo_allocation`,
    which renormalises the cap away -- see the next test).
    """
    close = _sel_frame()
    cfg = _cfg(rotation_position_cap=0.40, rotation_vol_target=0.0)
    w = R.rebalance_weights(close, cfg, close.index[-1], ["XLK", "XLF"])
    assert w["XLK"] == pytest.approx(0.40)
    assert w["XLF"] == pytest.approx(0.40)
    assert sum(w.values()) == pytest.approx(0.80)


def test_cap_weights_redistributes_excess_pro_rata():
    """{0.7, 0.2, 0.1} at cap 0.5: 0.2 of excess is shared 2:1, not 1:1.

    under = {B: 0.2, C: 0.1}, pool 0.3
      B -> 0.2 + 0.2*(0.2/0.3) = 0.33333...
      C -> 0.1 + 0.2*(0.1/0.3) = 0.16666...
    """
    out = R._cap_weights({"A": 0.7, "B": 0.2, "C": 0.1}, 0.5)
    assert out["A"] == pytest.approx(0.5)
    assert out["B"] == pytest.approx(1.0 / 3.0, abs=1e-12)
    assert out["C"] == pytest.approx(1.0 / 6.0, abs=1e-12)
    assert sum(out.values()) == pytest.approx(1.0)
    assert max(out.values()) <= 0.5 + 1e-9


def test_cap_weights_is_a_noop_when_the_cap_cannot_bind():
    w = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    assert R._cap_weights(dict(w), 0.60) == w
    assert R._cap_weights(dict(w), 1.0) == w


def test_momentum_weighting_applies_the_floor_then_renormalises():
    """Hand-computed floor + renormalisation.

    XLF has exactly zero momentum, so raw weights are {XLK: 1.0, XLF: 0.0};
    the 0.15 floor lifts XLF to 0.15, the sum becomes 1.15, and the pair
    renormalises to 1/1.15 and 0.15/1.15.
    """
    close = _frame({"XLK": 0.010, "XLF": 0.000, "GLD": 0.001, "TLT": 0.001}, 40)
    cfg = _cfg(rotation_momentum_weight=True, rotation_weight_floor=0.15,
               rotation_position_cap=1.0, rotation_vol_target=0.0)
    w = R.rebalance_weights(close, cfg, close.index[-1], ["XLK", "XLF"])
    assert w["XLK"] == pytest.approx(1.0 / 1.15, abs=1e-12)
    assert w["XLF"] == pytest.approx(0.15 / 1.15, abs=1e-12)
    assert sum(w.values()) == pytest.approx(1.0)


def test_position_cap_is_cancelled_by_the_renormalisation_in_turbo_allocation():
    """TRIPWIRE for a KNOWN DEFECT: `rotation_position_cap` is inert in the live book.

    `turbo_allocation` caps `base_equity_w` and then divides by `raw_sum`.
    At top_n=2 both names are over the cap, both land on it, and the subsequent
    `v / raw_sum * equity_budget` restores exactly the uncapped weights. At the
    live top_n=3 the cap (0.60) cannot bind at all, because equal weighting
    gives 0.3333 per slot.

    This test asserts the defect, deliberately. Proposed fix (NOT applied --
    trader/rotation.py is frozen for this work): renormalise BEFORE capping, or
    drop the `/ raw_sum` term, so the cap survives. If someone fixes it, this
    test fails and this note is the explanation.
    """
    close, lev = _lev_frames()
    cfg = _cfg(rotation_top_n=2, rotation_two_way_vol=False, rotation_vol_target=0.0)
    asof = close.index[-1]

    cfg.rotation_position_cap = 1.0
    uncapped = R.turbo_allocation(close, cfg, asof, ["XLK", "XLC"], lev_close=lev)
    cfg.rotation_position_cap = 0.40
    capped = R.turbo_allocation(close, cfg, asof, ["XLK", "XLC"], lev_close=lev)

    assert capped == uncapped, "the cap is supposed to be inert here -- see docstring"
    assert capped["XLK"] == pytest.approx(0.5)
    assert capped["XLC"] == pytest.approx(0.5)


# ===========================================================================
# 4. LEVERAGE-TIER ROUTING
# ===========================================================================
# The vol proxy is built from alternating +/- c daily returns. Over an even
# window that is exactly W/2 up moves and W/2 down moves, so
#     mean = 0,  sd(ddof=1) = c*sqrt(W/(W-1)),  rv = c*sqrt(W/(W-1))*sqrt(252)
# and choosing c = target / (S * sqrt(W/(W-1)) * sqrt(252)) makes the two-way
# scale come out at exactly S.
VOL_W = 8
VOL_TARGET = 0.50


def _c_for_scale(s, window=VOL_W, target=VOL_TARGET):
    return target / (s * math.sqrt(window / (window - 1.0)) * ANN)


def _alt(c, n):
    return [c if i % 2 == 0 else -c for i in range(n - 1)]


def _lev_frames(scale=1.0, n=60):
    """(close, lev2x) where SPY's realized vol puts the two-way scale at `scale`."""
    close = _frame({"XLK": 0.004, "XLC": 0.003, "SPY": _alt(_c_for_scale(scale), n),
                    "GLD": 0.001, "TLT": 0.001}, n)
    lev = _frame({"ROM": 0.008}, n)
    return close, lev


def _lev3_frame(n=60):
    return _frame({"TECL": 0.012}, n)


def _lev_cfg(**kw):
    base = dict(rotation_top_n=2, rotation_two_way_vol=True,
                rotation_vol_target=VOL_TARGET, rotation_vol_window=VOL_W,
                rotation_vol_floor=0.5, rotation_basket_vol=False,
                rotation_vix_gate=False)
    base.update(kw)
    return _cfg(**base)


def test_scale_at_or_below_one_holds_the_1x_sectors_and_never_touches_leverage():
    close, lev = _lev_frames()
    cfg = _cfg(rotation_top_n=2, rotation_two_way_vol=False, rotation_vol_target=0.0)
    out = R.turbo_allocation(close, cfg, close.index[-1], ["XLK", "XLC"],
                             lev_close=lev, lev3x_close=_lev3_frame())
    assert set(out) == {"XLK", "XLC"}
    assert out["XLK"] == pytest.approx(0.5)
    assert out["XLC"] == pytest.approx(0.5)


def test_tier2_routes_to_the_2x_etf_at_half_the_capital():
    """scale = 1.5, two equity slots, and XLC has no 2x fund. Fully hand-computed.

    slot_w = 1/2, equity_budget = 1.0, equal sector weights 0.5 / 0.5.
      XLK -> ROM   capital 0.5 * 1.5 / 2 = 0.375   (exposure 0.75)
      XLC -> XLC   capital 0.5           = 0.5     (exposure 0.50)  <-- 1x fallback
      used 0.875, defensive absorbs 0.125 split over GLD+TLT = 0.0625 each
      total 1.0, so the final normalisation is a no-op.
    """
    close, lev = _lev_frames(scale=1.5)
    cfg = _lev_cfg(rotation_vol_cap=2.0)
    asof = close.index[-1]

    assert R._two_way_vol_scale(close, asof, cfg) == pytest.approx(1.5, rel=1e-9)

    out = R.turbo_allocation(close, cfg, asof, ["XLK", "XLC"], lev_close=lev)
    assert out["ROM"] == pytest.approx(0.375, abs=1e-9)
    assert out["XLC"] == pytest.approx(0.500, abs=1e-9)
    assert out["GLD"] == pytest.approx(0.0625, abs=1e-9)
    assert out["TLT"] == pytest.approx(0.0625, abs=1e-9)
    assert "XLK" not in out
    assert sum(out.values()) == pytest.approx(1.0)


def test_xlc_1x_fallback_is_sized_at_unlevered_weight_not_levered_notional():
    """TRIPWIRE for a KNOWN FIDELITY BREAK (ROUND1: XLC BREAK).

    XLC is the only traded sector with no 2x fund. `turbo_allocation` gives the
    1x leg its plain weight `w`, so its EXPOSURE is w -- while a levered sibling
    at the same weight carries w*scale. reports/opt_harness.py sizes that same
    fallback at LEVERED notional, so the two engines disagree by exactly the
    scale factor on the XLC leg.

    This pins the LIVE side. The harness is the side that should move: the live
    allocator cannot buy 2x exposure in a fund that does not exist, so its
    number is the physically achievable one. The fix belongs in the harness.
    """
    close, lev = _lev_frames(scale=1.5)
    cfg = _lev_cfg(rotation_vol_cap=2.0)
    out = R.turbo_allocation(close, cfg, close.index[-1], ["XLK", "XLC"], lev_close=lev)

    xlk_exposure = out["ROM"] * 2.0
    xlc_exposure = out["XLC"] * 1.0
    assert xlc_exposure == pytest.approx(0.5, abs=1e-9)
    assert xlk_exposure / xlc_exposure == pytest.approx(1.5, rel=1e-9), (
        "the 1x fallback lags its levered siblings by exactly the vol scale"
    )


def test_tier3_routes_to_the_3x_etf_at_one_third_the_capital():
    """scale = 2.5 with use_3x on.

      XLK -> TECL  capital 0.5 * 2.5 / 3 = 0.4166667  (exposure 1.25)
      XLC -> XLC   capital 0.5
      used 0.9166667, defensive 0.0833333 -> 0.0416667 each.
    """
    close, lev = _lev_frames(scale=2.5)
    cfg = _lev_cfg(rotation_use_3x=True, rotation_vol_cap=3.0)
    asof = close.index[-1]
    assert R._two_way_vol_scale(close, asof, cfg) == pytest.approx(2.5, rel=1e-9)

    out = R.turbo_allocation(close, cfg, asof, ["XLK", "XLC"],
                             lev_close=lev, lev3x_close=_lev3_frame())
    assert out["TECL"] == pytest.approx(0.5 * 2.5 / 3.0, abs=1e-9)
    assert out["XLC"] == pytest.approx(0.5, abs=1e-9)
    assert out["GLD"] == pytest.approx(0.5 / 12.0, abs=1e-9)
    assert "ROM" not in out


def test_tier3_falls_back_to_tier2_when_the_3x_fund_is_unavailable():
    """Same scale 2.5, but no 3x frame: XLK must route to ROM at scale/2.

      XLK -> ROM  capital 0.5*2.5/2 = 0.625 ; XLC 0.5 ; used 1.125 > 1 so the
      defensive sleeve gets nothing and the final normalisation divides by 1.125:
        ROM 0.625/1.125 = 0.5555556,  XLC 0.5/1.125 = 0.4444444.
    """
    close, lev = _lev_frames(scale=2.5)
    cfg = _lev_cfg(rotation_use_3x=True, rotation_vol_cap=3.0)
    out = R.turbo_allocation(close, cfg, close.index[-1], ["XLK", "XLC"],
                             lev_close=lev, lev3x_close=None)
    assert out["ROM"] == pytest.approx(0.625 / 1.125, abs=1e-9)
    assert out["XLC"] == pytest.approx(0.500 / 1.125, abs=1e-9)
    assert "GLD" not in out and "TLT" not in out
    assert sum(out.values()) == pytest.approx(1.0)


def test_use_3x_off_keeps_tier2_even_above_scale_two():
    close, lev = _lev_frames(scale=2.5)
    cfg = _lev_cfg(rotation_use_3x=False, rotation_vol_cap=3.0)
    out = R.turbo_allocation(close, cfg, close.index[-1], ["XLK", "XLC"],
                             lev_close=lev, lev3x_close=_lev3_frame())
    assert "TECL" not in out, "use_3x=False must never reach tier 3"
    assert out["ROM"] == pytest.approx(0.625 / 1.125, abs=1e-9)


def test_defensive_picks_are_never_routed_to_a_leveraged_fund():
    """GLD has a 2x fund (UGL) in LEV2X_MAP, but a defensive SLOT must stay 1x."""
    assert R.LEV2X_MAP["GLD"] == "UGL"
    close, _ = _lev_frames(scale=1.5)
    lev = _frame({"ROM": 0.008, "UGL": 0.002}, len(close.index))
    cfg = _lev_cfg(rotation_vol_cap=2.0)
    out = R.turbo_allocation(close, cfg, close.index[-1], ["XLK", "GLD"], lev_close=lev)
    assert "UGL" not in out
    assert out["GLD"] > 0


# ===========================================================================
# 5. VOLATILITY SCALING
# ===========================================================================
def test_realized_vol_matches_the_closed_form_on_a_known_path():
    """Alternating +/- c returns: sd(ddof=1) = c*sqrt(W/(W-1)), annualised by sqrt(252)."""
    c, n = 0.01, 60
    close = _frame({"SPY": _alt(c, n)}, n)
    expected = c * math.sqrt(VOL_W / (VOL_W - 1.0)) * ANN
    got = R._realized_vol(close, "SPY", close.index[-1], VOL_W)
    assert got == pytest.approx(expected, rel=1e-9)
    # ddof=1 matters: the population sd would be c*sqrt(252), ~6.5% lower here
    assert abs(got - c * ANN) > 0.005


def test_realized_vol_is_zero_when_history_is_too_short():
    """Needs window+2 closes; short history returns 0.0, which callers read as
    'unavailable' and fall back rather than dividing by it."""
    close = _frame({"SPY": _alt(0.01, 9)}, 9)
    assert R._realized_vol(close, "SPY", close.index[-1], VOL_W) == 0.0
    assert R._realized_vol(close, "NOPE", close.index[-1], VOL_W) == 0.0


def test_two_way_scale_is_target_over_realized_vol():
    close, _ = _lev_frames(scale=1.5)
    cfg = _lev_cfg(rotation_vol_cap=3.0)
    assert R._two_way_vol_scale(close, close.index[-1], cfg) == pytest.approx(1.5, rel=1e-9)


def test_two_way_scale_is_clipped_by_the_bull_cap_and_by_the_floor():
    hi, _ = _lev_frames(scale=3.0)              # would be 3.0, cap says 1.25
    assert R._two_way_vol_scale(hi, hi.index[-1], _lev_cfg(rotation_vol_cap=1.25)) \
        == pytest.approx(1.25)

    lo, _ = _lev_frames(scale=0.20)             # would be 0.20, floor says 0.50
    assert R._two_way_vol_scale(lo, lo.index[-1], _lev_cfg(rotation_vol_cap=1.25)) \
        == pytest.approx(0.50)


def test_bear_cap_binds_only_when_the_proxy_is_below_its_200_day_sma():
    """Same realized vol, two regimes: bull cap 2.0 vs bear cap 1.0.

    The bear frame carries a long -0.3%/day drift before the alternating tail,
    so SPY sits below its 200-day SMA; the bull frame drifts up. The uncapped
    scale is 1.5 in both, so any difference is the regime and nothing else.
    """
    n = 280
    tail = _alt(_c_for_scale(1.5), 10)          # 9 returns
    cfg = _lev_cfg(rotation_vol_cap=2.0, rotation_vol_cap_bear=1.0,
                   rotation_vol_cap_sma=200)

    bear = _frame({"SPY": [-0.003] * (n - 1 - len(tail)) + tail}, n)
    bull = _frame({"SPY": [+0.003] * (n - 1 - len(tail)) + tail}, n)

    assert R._realized_vol(bear, "SPY", bear.index[-1], VOL_W) == pytest.approx(
        R._realized_vol(bull, "SPY", bull.index[-1], VOL_W), rel=1e-9)
    assert R._two_way_vol_scale(bull, bull.index[-1], cfg) == pytest.approx(1.5, rel=1e-9)
    assert R._two_way_vol_scale(bear, bear.index[-1], cfg) == pytest.approx(1.0)


def test_vix_gate_caps_exposure_at_the_configured_tiers():
    n = 60
    alt = _alt(_c_for_scale(1.5), n)

    def _scale(vix_level, hi_cap=0.5):
        R._ARR_CACHE.clear()
        close = _frame({"SPY": alt, "^VIX": 0.0}, n)
        close["^VIX"] = float(vix_level)
        cfg = _lev_cfg(rotation_vol_cap=2.0, rotation_vix_gate=True,
                       rotation_vix_lo=25.0, rotation_vix_lo_cap=1.0,
                       rotation_vix_hi=35.0, rotation_vix_hi_cap=hi_cap)
        return R._two_way_vol_scale(close, close.index[-1], cfg)

    assert _scale(15.0) == pytest.approx(1.5, rel=1e-9)   # calm: gate idle
    assert _scale(30.0) == pytest.approx(1.0)             # >= lo: 1x
    assert _scale(40.0) == pytest.approx(0.5)             # >= hi: hi cap
    # the live book retires the hi tier by setting hi_cap == lo_cap
    assert _scale(40.0, hi_cap=1.0) == pytest.approx(1.0)


def test_basket_vol_sizes_on_the_held_picks_not_on_the_proxy():
    """rotation_basket_vol=True must ignore SPY and use the equal-weight basket."""
    n = 60
    close = _frame({"SPY": _alt(_c_for_scale(1.5), n),
                    "XLK": _alt(_c_for_scale(0.75), n),
                    "XLC": _alt(_c_for_scale(0.75), n),
                    "GLD": 0.001, "TLT": 0.001}, n)
    asof = close.index[-1]

    on = R._two_way_vol_scale(close, asof, _lev_cfg(rotation_vol_cap=2.0,
                                                    rotation_vol_floor=0.10,
                                                    rotation_basket_vol=True),
                              basket=["XLK", "XLC"])
    off = R._two_way_vol_scale(close, asof, _lev_cfg(rotation_vol_cap=2.0,
                                                     rotation_vol_floor=0.10,
                                                     rotation_basket_vol=False),
                               basket=["XLK", "XLC"])
    assert off == pytest.approx(1.5, rel=1e-9)
    assert on == pytest.approx(0.75, rel=1e-9)


# ===========================================================================
# 6. MIN HOLD DAYS
# ===========================================================================
class _FakeBroker:
    """Enough of AlpacaBroker to reach gate 3 of run_scheduled."""

    def __init__(self, *a, **kw):
        pass

    def connected(self):
        return True

    def is_market_open(self):
        return True

    def get_account(self):
        return {}

    def get_positions(self):
        return []


@pytest.fixture
def live(tmp_path, monkeypatch):
    """rotation_live with its state file redirected and its side effects stubbed."""
    from trader import rotation_live as RL
    from trader import risk_gate

    monkeypatch.setattr(RL, "_STATE_PATH", str(tmp_path / "rotation_state.json"))
    monkeypatch.setattr(RL, "_LOG_PATH", str(tmp_path / "rotation.log"))
    monkeypatch.setattr(RL, "AlpacaBroker", _FakeBroker)
    monkeypatch.setattr(RL, "reconcile_fills", lambda cfg: None)
    monkeypatch.setattr(RL, "_notify", lambda *a, **kw: None)
    monkeypatch.setattr(RL, "_ledger", lambda *a, **kw: None)

    class _Allowed:
        allowed = True
        metrics = {}

        def blocked_summary(self):
            return ""

    monkeypatch.setattr(risk_gate, "evaluate", lambda acct, pos: _Allowed())
    return RL


def _cfg_with_keys():
    c = Config()
    c.alpaca_key, c.alpaca_secret = "PKTEST", "SECRET"
    return c


def test_rebalance_inside_the_min_hold_window_is_suppressed(live, monkeypatch):
    """min_hold_days = 3 and 2 days held -> skip, and the broker is never reached."""
    from trader.rotation import DAILY_CHAMPION_FLAGS
    assert DAILY_CHAMPION_FLAGS["rotation_min_hold_days"] == 3

    calls = []
    monkeypatch.setattr(live, "run_rotation_cycle",
                        lambda *a, **kw: calls.append(kw) or {"action": "rebalance"})
    monkeypatch.setattr(live, "_days_since_last_rebalance", lambda *a, **kw: 2)

    out = live.run_scheduled(_cfg_with_keys(), dry_run=True)
    assert out == {"action": "skip", "reason": "min_hold_days"}
    assert calls == [], "a suppressed rebalance must not reach the broker"


def test_rebalance_outside_the_min_hold_window_proceeds(live, monkeypatch):
    calls = []
    monkeypatch.setattr(live, "run_rotation_cycle",
                        lambda *a, **kw: calls.append(kw) or {"action": "rebalance",
                                                              "sent": []})
    monkeypatch.setattr(live, "_days_since_last_rebalance", lambda *a, **kw: 3)

    out = live.run_scheduled(_cfg_with_keys(), dry_run=True)
    assert out.get("reason") != "min_hold_days"
    assert len(calls) == 1, "outside the window the cycle must actually run"


def test_min_hold_boundary_trades_at_exactly_min_hold_days(live, monkeypatch):
    """held == min_hold must TRADE. `<` not `<=` is the difference between a
    3-day hold and a 4-day hold, i.e. roughly 25% of the rebalance count."""
    seen = []
    monkeypatch.setattr(live, "run_rotation_cycle",
                        lambda *a, **kw: seen.append(1) or {"action": "rebalance",
                                                            "sent": []})
    for held, should_trade in ((0, False), (1, False), (2, False), (3, True), (4, True)):
        seen.clear()
        monkeypatch.setattr(live, "_days_since_last_rebalance",
                            lambda *a, _h=held, **kw: _h)
        out = live.run_scheduled(_cfg_with_keys(), dry_run=True)
        traded = out.get("reason") != "min_hold_days"
        assert traded is should_trade, f"held={held}: expected trade={should_trade}"
        assert bool(seen) is should_trade


def test_days_since_last_rebalance_counts_sessions_not_calendar_days(live):
    """Friday -> Monday is 3 calendar days but 1 TRADING day.

    The certifying harness indexes trading days, so evening mode counts sessions.
    Conflating the two is what made live rebalance where the backtest would not.
    """
    live._save_state({"last_rebalance_date": "2026-09-11"})       # a Friday
    monday = date(2026, 9, 14)
    assert live._days_since_last_rebalance(asof=monday) == 3
    assert live._days_since_last_rebalance(trading_days=True, asof=monday) == 1

    thursday = date(2026, 9, 17)
    assert live._days_since_last_rebalance(asof=thursday) == 6
    assert live._days_since_last_rebalance(trading_days=True, asof=thursday) == 4


def test_days_since_last_rebalance_is_999_when_never_rebalanced(live):
    live._save_state({})
    assert live._days_since_last_rebalance() == 999
    live._save_state({"last_rebalance_date": "not-a-date"})
    assert live._days_since_last_rebalance() == 999
