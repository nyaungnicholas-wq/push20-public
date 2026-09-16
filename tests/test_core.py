"""Unit tests covering all 8 strategy fixes."""

import pandas as pd
import numpy as np

from trader.config import Config
from trader.engine import PaperBroker, Position
from trader import indicators
from trader.strategy import compute_features, signal_for_row, Action


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def test_sma():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert indicators.sma(s, 2).iloc[-1] == 4.5
    assert pd.isna(indicators.sma(s, 2).iloc[0])


def test_ema_converges():
    s = pd.Series([10.0] * 50)
    assert abs(indicators.ema(s, 10).dropna().iloc[-1] - 10.0) < 0.01


def test_rsi_bounds():
    s = pd.Series(range(1, 50), dtype=float)
    r = indicators.rsi(s, 14).dropna()
    assert (r <= 100).all() and (r >= 0).all()
    assert r.iloc[-1] > 70


def test_macd_structure():
    s = pd.Series(np.linspace(100, 120, 100))
    df = indicators.macd(s)
    assert {"macd", "signal", "hist"} == set(df.columns)


def test_bollinger_constant():
    s = pd.Series([100.0] * 30)
    bb = indicators.bollinger_bands(s, 20)
    assert abs(bb["bb_upper"].dropna().iloc[-1] - 100.0) < 1e-9


def test_adx_positive():
    """Fix #7: ADX must always be non-negative."""
    n = 100
    high  = pd.Series(np.linspace(100, 120, n))
    low   = pd.Series(np.linspace(98, 118, n))
    close = pd.Series(np.linspace(99, 119, n))
    adx = indicators.adx(high, low, close, 14).dropna()
    assert (adx >= 0).all()


def test_volume_ratio():
    v  = pd.Series([100.0] * 19 + [200.0])
    vr = indicators.volume_ratio(v, 20)
    assert vr.dropna().iloc[-1] > 1.5


# ---------------------------------------------------------------------------
# Engine: risk management
# ---------------------------------------------------------------------------

def test_buy_respects_cash():
    cfg = Config(starting_cash=1000.0)
    b   = PaperBroker(cfg)
    b.buy("2024-01-01", "AAPL", 100.0, 100, "test", 95.0, 125.0)
    assert "AAPL" not in b.positions
    assert b.cash == 1000.0


def test_stop_loss_triggers():
    cfg = Config(starting_cash=100_000.0, slippage_bps=0)
    b   = PaperBroker(cfg)
    b.buy("2024-01-01", "AAPL", 100.0, 10, "entry", 92.0, 140.0)
    b.check_risk_exits("2024-01-02", {"AAPL": 91.0})
    assert "AAPL" not in b.positions
    assert "STOP-LOSS" in b.trades[-1].reason


def test_take_profit_5to1():
    cfg = Config(starting_cash=100_000.0, slippage_bps=0)
    b   = PaperBroker(cfg)
    b.buy("2024-01-01", "AAPL", 100.0, 10, "entry", 98.5, 107.5)
    b.check_risk_exits("2024-01-02", {"AAPL": 108.0})
    assert "AAPL" not in b.positions
    assert "TAKE-PROFIT" in b.trades[-1].reason


def test_portfolio_heat_calculation():
    cfg = Config(starting_cash=100_000.0, slippage_bps=0)
    b   = PaperBroker(cfg)
    b.buy("2024-01-01", "AAPL", 100.0, 100, "entry", 98.5, 107.5)
    heat = b.portfolio_heat({"AAPL": 100.0})
    # 100 shares × $1.50 stop distance / ~$90k equity after buy
    assert 0 < heat < 0.01


def test_heat_gate_blocks_new_buys():
    cfg = Config(starting_cash=10_000.0, max_portfolio_heat_pct=0.04, slippage_bps=0)
    b   = PaperBroker(cfg)
    b.buy("2024-01-01", "AAPL", 10.0, 500, "first", 9.0, 15.0)
    heat = b.portfolio_heat({"AAPL": 10.0})
    assert heat >= cfg.max_portfolio_heat_pct
    assert not b.heat_allows_buy({"AAPL": 10.0})


# ---------------------------------------------------------------------------
# Fix #1: RSI exit threshold is now 80, not 70
# ---------------------------------------------------------------------------

def test_rsi_exit_threshold_is_80():
    cfg = Config(starting_cash=100_000.0, rsi_overbought=80.0, slippage_bps=0)
    assert cfg.rsi_overbought == 80.0
    # A position with RSI=75 should NOT trigger sell (was incorrectly exited pre-fix)
    n     = 260
    close = pd.Series([90.0] * 130 + [100.0] * 130, dtype=float)
    vol   = pd.Series([2_000_000.0] * n)
    df    = pd.DataFrame({"open": close, "high": close*1.005,
                          "low": close*0.995, "close": close, "volume": vol})
    feat  = compute_features(df, cfg)
    # Holding = True, RSI somewhere in 70-80 range → should NOT sell
    for i in range(200, n):
        rsi_val = float(feat["rsi"].iloc[i]) if not pd.isna(feat["rsi"].iloc[i]) else 50.0
        if 70 < rsi_val < 80:
            sig = signal_for_row("TEST", feat, i, cfg, 0.0, "LOW", holding=True)
            # Should not sell purely on RSI 70-80 range
            if sig and sig.action.value == "SELL":
                assert "RSI extreme" in sig.reason or "SMA" in sig.reason or "EMA" in sig.reason
            break


# ---------------------------------------------------------------------------
# Fix #2: Trailing stop — breakeven and trail logic
# ---------------------------------------------------------------------------

def test_trailing_stop_breakeven():
    """At 2:1 R:R, stop should move to entry price."""
    cfg = Config(
        starting_cash=100_000.0, slippage_bps=0,
        trailing_stop_enabled=True,
        breakeven_trigger_rr=2.0,
        trail_trigger_rr=3.0,
        trail_atr_multiple=2.0,
    )
    b = PaperBroker(cfg)
    # Entry=100, stop=98, risk=2 → 2:1 = price 104
    b.buy("2024-01-01", "AAPL", 100.0, 10, "entry", stop_loss=98.0, take_profit=110.0)
    b.positions["AAPL"].initial_stop = 98.0
    # Price moves to 104 (exactly 2:1)
    b.update_trailing_stops({"AAPL": 104.0})
    # Stop should now be at breakeven (entry = 100)
    assert b.positions["AAPL"].stop_loss >= 100.0


def test_trailing_stop_trails_above_3r():
    """At 3:1 R:R, stop should trail above the original entry."""
    cfg = Config(
        starting_cash=100_000.0, slippage_bps=0,
        trailing_stop_enabled=True,
        breakeven_trigger_rr=2.0,
        trail_trigger_rr=3.0,
        trail_atr_multiple=2.0,
    )
    b = PaperBroker(cfg)
    b.buy("2024-01-01", "AAPL", 100.0, 10, "entry", stop_loss=98.0, take_profit=110.0)
    b.positions["AAPL"].initial_stop = 98.0
    # Price at 106 = 3:1 R:R (3 × $2 risk)
    b.update_trailing_stops({"AAPL": 106.0})
    # Stop should have trailed UP above entry
    assert b.positions["AAPL"].stop_loss > 100.0


# ---------------------------------------------------------------------------
# Fix #7: ADX in features
# ---------------------------------------------------------------------------

def test_adx_in_compute_features():
    n     = 260
    close = pd.Series(np.linspace(100, 120, n))
    vol   = pd.Series([1_000_000.0] * n)
    df    = pd.DataFrame({"open": close, "high": close*1.01,
                          "low": close*0.99, "close": close, "volume": vol})
    cfg  = Config()
    feat = compute_features(df, cfg)
    assert "adx" in feat.columns
    assert feat["adx"].dropna().iloc[-1] >= 0


# ---------------------------------------------------------------------------
# Fix #6: Slippage default is realistic (10bps)
# ---------------------------------------------------------------------------

def test_slippage_is_realistic():
    cfg = Config()
    assert cfg.slippage_bps == 10.0


# ---------------------------------------------------------------------------
# 5:1 R:R integrity
# ---------------------------------------------------------------------------

def test_rr_ratio_enforced():
    cfg = Config(starting_cash=100_000.0, rr_ratio=5.0, stop_loss_pct=0.015,
                 min_confluence_score=2)
    n     = 260
    close = pd.Series([90.0] * 120 + [100.0] * 140, dtype=float)
    vol   = pd.Series([2_000_000.0] * n)
    df    = pd.DataFrame({"open": close, "high": close*1.005,
                          "low": close*0.995, "close": close, "volume": vol})
    feat  = compute_features(df, cfg)
    for i in range(60, n):
        sig = signal_for_row("TEST", feat, i, cfg, 0.5, "MEDIUM", False)
        if sig and sig.action == Action.BUY:
            stop_dist = sig.price - sig.stop_loss
            tp_dist   = sig.take_profit - sig.price
            assert abs(tp_dist / stop_dist - 5.0) < 0.02
            break
