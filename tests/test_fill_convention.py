"""A2a — the certifying engine must fill where the live loop can actually transact.

The live loop is the evening/next-open path: it computes the signal from a completed
session's OFFICIAL CLOSING PRINT and queues orders for the next open. A backtest that
transacts at that same close is using a price that only exists once the market is shut.

These run on a synthetic frame (no network, no cache) and assert on the engine's own
trade log, so they fail if the fill plumbing regresses — which the rest of the suite,
having no coverage of the strategy math at all, would not notice.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "reports"))

import opt_harness as H  # noqa: E402

CFG = dict(lookback=20, ema_span=3, top_n=3, min_hold_days=3, cost_bps=0.0,
           use_lev=False, vol_cap_bull=1.0, vol_cap_bear=1.0)
OPEN_FACTOR = 1.07          # opens deliberately far from any close, so a wrong bar shows up


@pytest.fixture
def frame(monkeypatch):
    """Synthetic closes + opens = closes * OPEN_FACTOR. No yfinance, no parquet cache."""
    idx = pd.bdate_range("2010-01-04", periods=400)
    rng = np.random.default_rng(11)
    cols = H.UNIVERSE + ["SPY", "GLD", "TLT"]
    px = pd.DataFrame(
        {c: 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.011, len(idx))) for c in cols},
        index=idx)
    monkeypatch.setattr(H, "_DF", px, raising=False)
    monkeypatch.setattr(H, "_OPENS", px * OPEN_FACTOR, raising=False)
    return px


def _trades(px, **over):
    cfg = {**CFG, "trade_log": [], **over}
    H.simulate(px, cfg, str(px.index[0].date()), str(px.index[-1].date()))
    assert cfg["trade_log"], "the engine logged no trades, so nothing was checked"
    return cfg["trade_log"]


def test_default_is_next_open():
    """A default of 'close' silently re-certifies a fill the live loop cannot get."""
    assert H.FILL_MODE == "next_open"


def test_next_open_fills_at_the_next_session_open(frame):
    px = frame
    for ds, sym, dq, pr in _trades(px, fill_mode="next_open"):
        i = px.index.get_loc(pd.Timestamp(ds))
        assert i + 1 < len(px.index)
        assert pr == pytest.approx(px[sym].iloc[i + 1] * OPEN_FACTOR, rel=1e-9)


def test_close_mode_still_fills_at_the_decision_close(frame):
    """The old convention stays available and unchanged, for reproducing graded runs."""
    px = frame
    for ds, sym, dq, pr in _trades(px, fill_mode="close"):
        i = px.index.get_loc(pd.Timestamp(ds))
        assert pr == pytest.approx(px[sym].iloc[i], rel=1e-9)


def test_blend_prices_partial_session_participation(frame):
    px = frame
    for ds, sym, dq, pr in _trades(px, fill_mode="next_open", fill_blend=0.25):
        i = px.index.get_loc(pd.Timestamp(ds))
        nxt = px[sym].iloc[i + 1]
        assert pr == pytest.approx(0.75 * nxt * OPEN_FACTOR + 0.25 * nxt, rel=1e-9)


def test_unknown_fill_mode_raises(frame):
    with pytest.raises(ValueError):
        H.simulate(frame, {**CFG, "fill_mode": "moc"},
                   str(frame.index[0].date()), str(frame.index[-1].date()))


def test_missing_open_falls_forward_not_backward(frame, monkeypatch):
    """A data hole in the opens is not a licence to trade at the decision close."""
    px = frame
    opens = px * OPEN_FACTOR
    opens.iloc[:, :] = np.nan                       # every open missing
    monkeypatch.setattr(H, "_OPENS", opens, raising=False)
    H.FILL_FALLBACKS = 0
    for ds, sym, dq, pr in _trades(px, fill_mode="next_open"):
        i = px.index.get_loc(pd.Timestamp(ds))
        assert pr == pytest.approx(px[sym].iloc[i + 1], rel=1e-9), "fell back to close(t)"
    assert H.FILL_FALLBACKS > 0, "fallbacks must be counted, not swallowed"
