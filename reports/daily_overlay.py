"""Daily exposure overlay on the monthly sector-rotation champion.

Design
------
The champion rebalances sector picks once a month (12-mo momentum).  This
overlay DOES NOT change sector picks.  It adjusts total portfolio *exposure*
(equity fraction) daily using VIX as a fast risk signal:

    exposure = clip(1.0 - max(0, VIX - 20) / 60, 0.5, 1.0)

  VIX ≤ 20  → 100 % in sectors
  VIX = 40  → 66 % sectors, 34 % GLD+TLT
  VIX ≥ 50  → 50 % sectors, 50 % GLD+TLT (floor)

Adjustment only fires when the target changes by >TRIGGER_PCT so we avoid
micro-trading on normal VIX noise.  On monthly rebalance days the sector pick
runs first, then VIX exposure is applied on top.

Run:
    cd "/Users/natalienyaung/claude code/stock-trader"
    .venv/bin/python reports/daily_overlay.py
"""

from __future__ import annotations

import copy
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from trader.config import Config, CONFIG
from trader.data_source import get_data_source
from trader.engine import PaperBroker
from trader.rotation import (
    _CombinedBook,
    _close_frame,
    _defensive_list,
    _rebalance_dates,
    _realized_vol,
    _spy_buy_hold,
    rebalance_to,
    rebalance_weights,
    select_targets,
    CHAMPION_FLAGS,
)

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
VIX_CALM     = 20.0   # below this → 100 % exposure
VIX_PANIC    = 50.0   # at/above this → EXPOSURE_FLOOR
EXPOSURE_FLOOR = 0.50  # minimum equity fraction
TRIGGER_PCT  = 0.03   # only rebalance exposure when shift > 3 %

# VIX z-score variant: compare VIX to its own 60-day rolling mean/std.
# Fires only when VIX is an unusual spike above recent baseline.
VIX_ZSCORE_WINDOW = 60   # rolling window for z-score baseline
VIX_ZSCORE_CALM   = 0.0  # z < this → full exposure
VIX_ZSCORE_PANIC  = 2.0  # z ≥ this → floor exposure

FETCH_START = "2003-01-01"
FETCH_END   = "2024-12-31"

WINDOWS = [
    ("2005-2024", "2005-01-01", "2024-12-31"),
    ("2005-2014", "2005-01-01", "2014-12-31"),
    ("2015-2024", "2015-01-01", "2024-12-31"),
    ("2018-2024", "2018-01-01", "2024-12-31"),
]


# ---------------------------------------------------------------------------
# VIX fetch
# ---------------------------------------------------------------------------
def _fetch_vix(start: str, end: str) -> pd.Series:
    """Daily VIX close from yfinance.  Returns a series indexed by Timestamp."""
    import yfinance as yf
    v = yf.download("^VIX", start=start, end=end, auto_adjust=True, progress=False)
    if v.empty:
        return pd.Series(dtype=float)
    # yfinance v0.2+ may return MultiIndex columns
    if isinstance(v.columns, pd.MultiIndex):
        v.columns = v.columns.get_level_values(0)
    s = v["Close"].squeeze()
    if isinstance(s.index, pd.DatetimeIndex):
        s.index = s.index.tz_localize(None)
    return s.sort_index()


def _vix_exposure(vix_val: float) -> float:
    """Map a VIX level to a target equity exposure fraction [FLOOR, 1.0]."""
    if vix_val <= VIX_CALM:
        return 1.0
    if vix_val >= VIX_PANIC:
        return EXPOSURE_FLOOR
    return 1.0 - (vix_val - VIX_CALM) / (VIX_PANIC - VIX_CALM) * (1.0 - EXPOSURE_FLOOR)


def _build_vix_zscore(vix: pd.Series) -> pd.Series:
    """Rolling z-score of VIX vs its own 60-day mean/std (causal)."""
    roll_mean = vix.rolling(VIX_ZSCORE_WINDOW, min_periods=10).mean()
    roll_std  = vix.rolling(VIX_ZSCORE_WINDOW, min_periods=10).std(ddof=1)
    z = (vix - roll_mean) / roll_std.clip(lower=0.1)
    return z


def _vix_zscore_exposure(z: float) -> float:
    """Map VIX z-score to exposure: calm (z≤0) → 1.0, spike (z≥2) → FLOOR."""
    if z <= VIX_ZSCORE_CALM:
        return 1.0
    if z >= VIX_ZSCORE_PANIC:
        return EXPOSURE_FLOOR
    return 1.0 - (z - VIX_ZSCORE_CALM) / (VIX_ZSCORE_PANIC - VIX_ZSCORE_CALM) * (1.0 - EXPOSURE_FLOOR)


# ---------------------------------------------------------------------------
# Core: adjust exposure on a single broker without changing sector picks
# ---------------------------------------------------------------------------
def _adjust_exposure(broker: PaperBroker, date_str: str,
                     prices: dict, defensive: list[str],
                     target_expo: float, current_expo: float) -> None:
    """Scale total equity position toward `target_expo` by trimming/adding
    the defensive sleeve.  Works symmetrically in both directions.

    `current_expo` is the fraction currently in non-defensive holdings.
    `target_expo`  is where we want to be.
    `defensive`    is the list of defensive symbols (e.g. ["GLD", "TLT"]).
    """
    equity = broker.equity(prices)
    if equity <= 0:
        return

    # Current dollar value in equity (non-defensive) positions
    def_set = set(defensive)
    equity_val = sum(
        p.market_value(prices.get(s, p.avg_price))
        for s, p in broker.positions.items()
        if s not in def_set
    )
    def_val = sum(
        p.market_value(prices.get(s, p.avg_price))
        for s, p in broker.positions.items()
        if s in def_set
    )

    target_equity_val = equity * target_expo
    diff = target_equity_val - equity_val   # positive = need more equity, negative = trim

    if abs(diff) < equity * 0.01:           # sub-1% adjustment → skip
        return

    if diff < 0:
        # Need to reduce equity holdings → sell proportionally from all equity positions
        trim_frac = abs(diff) / max(equity_val, 1.0)
        for s, pos in list(broker.positions.items()):
            if s in def_set:
                continue
            px = prices.get(s)
            if px is None or px <= 0:
                continue
            sell_shares = pos.shares * trim_frac
            if sell_shares > 0.001:
                broker.sell(date_str, s, px, "VIX-overlay trim",
                            shares=sell_shares)
        # Park freed cash into defensive sleeve (equal split)
        cash_to_deploy = abs(diff) * 0.995
        if defensive:
            per_def = cash_to_deploy / len(defensive)
            for d in defensive:
                px = prices.get(d)
                if px and px > 0:
                    shares = per_def / px
                    if shares > 0.001:
                        broker.buy(date_str, d, px, shares,
                                   "VIX-overlay park", 0.0, 1e12, "High")
    else:
        # Need more equity → sell defensive, buy back equity proportionally
        avail = min(diff, def_val) * 0.995
        if avail <= 0:
            return
        # Sell from defensive
        def_positions = {s: p for s, p in broker.positions.items() if s in def_set}
        def_total = sum(p.market_value(prices.get(s, p.avg_price))
                        for s, p in def_positions.items())
        if def_total > 0:
            sell_frac = min(1.0, avail / def_total)
            for s, pos in def_positions.items():
                px = prices.get(s)
                if px is None or px <= 0:
                    continue
                sell_shares = pos.shares * sell_frac
                if sell_shares > 0.001:
                    broker.sell(date_str, s, px, "VIX-overlay unpark",
                                shares=sell_shares)
        # Buy back equity positions proportionally
        eq_positions = {s: p for s, p in broker.positions.items()
                        if s not in def_set}
        eq_total = sum(p.market_value(prices.get(s, p.avg_price))
                       for s, p in eq_positions.items())
        if eq_total > 0 and avail > 0:
            for s, pos in eq_positions.items():
                px = prices.get(s)
                if px is None or px <= 0:
                    continue
                frac = pos.market_value(prices.get(s, pos.avg_price)) / eq_total
                buy_shares = (avail * frac) / px
                if buy_shares > 0.001:
                    broker.buy(date_str, s, px, buy_shares,
                               "VIX-overlay restore", 0.0, 1e12, "High")


# ---------------------------------------------------------------------------
# Backtest: champion + daily VIX overlay
# ---------------------------------------------------------------------------
def run_with_overlay(cfg: Config, close: pd.DataFrame,
                     vix: pd.Series, start: str, end: str,
                     use_zscore: bool = False) -> dict:
    """Run the champion backtest augmented with the daily VIX exposure overlay.

    Returns the same dict shape as run_rotation_backtest:
    {broker, cfg, benchmark, tranche_brokers, overlay_stats}
    """
    dates = close.index[(close.index >= start) & (close.index <= end)]
    defensive = [s for s in _defensive_list(cfg) if s in close.columns]

    tranches = list(cfg.rotation_tranches) or [0]
    n = len(tranches)
    brokers: list[PaperBroker] = []
    rebal_sets: list[set] = []
    for offset in tranches:
        b = PaperBroker(cfg)
        b.cash = cfg.starting_cash / n
        brokers.append(b)
        rebal_sets.append(set(_rebalance_dates(close.index, start, end, offset)))

    book = _CombinedBook()
    started = [False] * n

    # Track current exposure per tranche (starts at 1.0 — fully invested)
    current_expo = [1.0] * n

    overlay_fires = 0
    vix_values_used = []

    # Pre-build VIX z-score series if requested
    vix_zscore = _build_vix_zscore(vix) if use_zscore else None

    def _get_vix_signal(dt) -> float:
        """Return VIX value or z-score for `dt`, using last known if missing."""
        series = vix_zscore if use_zscore else vix
        if dt in series.index:
            val = float(series.loc[dt])
        else:
            prior = series.index[series.index < dt]
            val = float(series.iloc[series.index.get_loc(prior[-1])]) if len(prior) else (0.0 if use_zscore else 20.0)
        if np.isnan(val):
            val = 0.0 if use_zscore else 20.0
        return val

    for dt in dates:
        prices = {s: float(close.loc[dt, s]) for s in close.columns
                  if not pd.isna(close.loc[dt, s])}
        ds = dt.strftime("%Y-%m-%d")

        signal = _get_vix_signal(dt)
        raw_vix = float(vix.loc[dt]) if dt in vix.index else (vix_values_used[-1] if vix_values_used else 20.0)
        vix_values_used.append(raw_vix)
        target_expo = _vix_zscore_exposure(signal) if use_zscore else _vix_exposure(signal)

        for i, b in enumerate(brokers):
            # Step 1: monthly sector rotation (same as champion)
            if not started[i] or dt in rebal_sets[i]:
                started[i] = True
                targets = select_targets(close, cfg, dt)
                weights = rebalance_weights(close, cfg, dt, targets)
                # Apply vol-target from champion FIRST, then we'll scale via VIX
                rebalance_to(b, ds, targets, prices, weights)
                current_expo[i] = 1.0  # just rebalanced to full

            # Step 2: daily VIX overlay — adjust exposure if target moved enough
            if abs(target_expo - current_expo[i]) > TRIGGER_PCT:
                _adjust_exposure(b, ds, prices, defensive,
                                 target_expo, current_expo[i])
                if abs(target_expo - current_expo[i]) > TRIGGER_PCT:
                    overlay_fires += 1
                current_expo[i] = target_expo

        total_eq = sum(b.equity(prices) for b in brokers)
        book.equity_curve.append((ds, total_eq))

    for b in brokers:
        book.trades.extend(b.trades)

    benchmark = _spy_buy_hold(close, cfg)
    overlay_stats = {
        "overlay_fires": overlay_fires,
        "avg_vix": float(np.mean(vix_values_used)) if vix_values_used else None,
        "pct_days_below_full": float(np.mean([v > VIX_CALM for v in vix_values_used])),
    }
    return {
        "broker": book,
        "cfg": cfg,
        "benchmark": benchmark,
        "tranche_brokers": brokers,
        "overlay_stats": overlay_stats,
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _metrics(equity_curve: list[tuple], starting_cash: float) -> dict:
    vals = [v for _, v in equity_curve]
    if not vals:
        return {}
    final = vals[-1]
    years = len(vals) / 252.0
    cagr = (final / starting_cash) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    rets = np.diff(vals) / vals[:-1] if len(vals) > 1 else np.array([])
    sharpe = (float(np.mean(rets)) / float(np.std(rets, ddof=1)) * np.sqrt(252)
              if len(rets) > 1 and np.std(rets) > 0 else 0.0)
    return {"cagr": cagr, "mdd": mdd, "sharpe": sharpe,
            "final": final, "total_return": final / starting_cash - 1.0}


def _fmt(m: dict) -> str:
    return (f"CAGR {m['cagr']*100:.1f}%  MDD {m['mdd']*100:.1f}%  "
            f"Sharpe {m['sharpe']:.2f}  Final ${m['final']:,.0f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 72)
    print("Daily VIX Overlay Research")
    print(f"  VIX ≤ {VIX_CALM:.0f} → 100% exposure")
    print(f"  VIX ≥ {VIX_PANIC:.0f} → {EXPOSURE_FLOOR*100:.0f}% exposure  (linear interpolation)")
    print(f"  Trigger threshold: >{TRIGGER_PCT*100:.0f}% change to fire")
    print("=" * 72)

    # Build champion config
    cfg_base = copy.deepcopy(CONFIG)
    cfg_base.starting_cash = 100_000.0
    for k, v in CHAMPION_FLAGS.items():
        setattr(cfg_base, k, v)
    cfg_base.rotation_lookbacks = (252,)
    cfg_base.rotation_tranches  = (0, 10, 20)

    from reports.research_harness import load_close, BASE_UNIVERSE, ALL_SYMBOLS
    print("\nFetching price data...")
    close = load_close()
    cfg_base.rotation_universe = [s for s in BASE_UNIVERSE if s in close.columns]

    print("Fetching VIX...")
    vix = _fetch_vix(FETCH_START, FETCH_END)
    print(f"  VIX loaded: {len(vix)} trading days")
    print(f"  VIX range: {vix.min():.1f} – {vix.max():.1f}  mean {vix.mean():.1f}")

    print()

    # Also import champion runner for comparison
    from reports.research_harness import run as run_champion, make_cfg

    for label, start, end in WINDOWS:
        cfg = copy.deepcopy(cfg_base)
        cfg.backtest_start = start
        cfg.backtest_end   = end
        cfg.rotation_universe = [s for s in BASE_UNIVERSE if s in close.columns]

        # Champion (no overlay) — run_champion returns {"equity_curve": [...], "benchmark": ...}
        champ = run_champion(cfg, close, start, end)
        m_champ = _metrics(champ["equity_curve"], cfg.starting_cash)
        spy    = champ["benchmark"]

        # SPY metrics
        spy_vals = [v for _, v in spy["equity_curve"]]
        spy_rets = np.diff(spy_vals) / spy_vals[:-1]
        spy_sharpe = (float(np.mean(spy_rets) / np.std(spy_rets, ddof=1) * np.sqrt(252))
                      if len(spy_rets) > 1 and np.std(spy_rets) > 0 else 0.0)
        spy_m = {"cagr": spy["cagr"], "mdd": spy["max_drawdown"],
                 "sharpe": spy_sharpe, "final": spy["final_equity"],
                 "total_return": spy["total_return"]}

        # VIX-level overlay
        cfg2 = copy.deepcopy(cfg)
        ov_lvl = run_with_overlay(cfg2, close, vix, start, end, use_zscore=False)
        m_lvl  = _metrics(ov_lvl["broker"].equity_curve, cfg2.starting_cash)
        st_lvl = ov_lvl["overlay_stats"]

        # VIX z-score overlay
        cfg3 = copy.deepcopy(cfg)
        ov_z = run_with_overlay(cfg3, close, vix, start, end, use_zscore=True)
        m_z  = _metrics(ov_z["broker"].equity_curve, cfg3.starting_cash)
        st_z = ov_z["overlay_stats"]

        def _edge(m): return m["cagr"] - spy_m["cagr"]
        def _vc(m):   return m["cagr"] - m_champ["cagr"]

        print(f"\n{'='*72}")
        print(f"  {label}")
        print(f"{'='*72}")
        print(f"  Champion    : {_fmt(m_champ)}   vs SPY {_edge(m_champ)*100:+.1f}pp")
        print(f"  VIX level   : {_fmt(m_lvl)}   vs SPY {_edge(m_lvl)*100:+.1f}pp  vs Champ {_vc(m_lvl)*100:+.2f}pp  fires={st_lvl['overlay_fires']}")
        print(f"  VIX z-score : {_fmt(m_z)}   vs SPY {_edge(m_z)*100:+.1f}pp  vs Champ {_vc(m_z)*100:+.2f}pp  fires={st_z['overlay_fires']}")
        print(f"  SPY B&H     : CAGR {spy_m['cagr']*100:.1f}%  MDD {spy_m['mdd']*100:.1f}%  "
              f"Sharpe {spy_m['sharpe']:.2f}  Final ${spy_m['final']:,.0f}")

    print("\n" + "=" * 72)
    print("Done.")


if __name__ == "__main__":
    main()
