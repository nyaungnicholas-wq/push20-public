"""V4 Turbo Research Harness.

Tests each proposed upgrade in isolation against V4 baseline, then combinations.

Changes under test:
  A. Return-proportional weighting (weight ∝ R_i / sum(R_1+R_2+R_3))
  B. Expanded universe (+GLD, TLT, DBC, EFA, EEM in rotation)
  C. Dynamic defensive sleeve (momentum-rank GLD/TLT/BIL by 3m, top 2)
  D. Leadership persistence (must be top-3 for 2 consecutive months before entry,
     except rank-#1 enters immediately)
  E. Two-way vol targeting at 15%, cap 2.0x, per-sector 2x ETFs

Run:
    cd "/Users/natalienyaung/claude code/stock-trader"
    .venv/bin/python reports/v4_turbo_research.py
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

from trader.config import CONFIG
from trader.engine import PaperBroker
from trader.rotation import (
    _CombinedBook, _close_frame, _rebalance_dates, _arrays,
    _realized_vol, _spy_buy_hold, rebalance_to, CHAMPION_FLAGS,
)

# ---------------------------------------------------------------------------
# Universes
# ---------------------------------------------------------------------------
SECTORS_13 = [
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLB", "XLP", "XLU", "XLRE", "XLC", "SMH", "QQQ",
]
MACRO_ADDS = ["GLD", "TLT", "DBC", "EFA", "EEM"]   # expanded multi-asset universe
UNIVERSE_18 = SECTORS_13 + MACRO_ADDS

DEFENSIVE_POOL = ["GLD", "TLT", "BIL"]   # for dynamic defensive sleeve

# Per-sector 2x ETF map (ProShares Ultra / Direxion 2x series, confirmed in yfinance)
LEV2X = {
    "XLK": "ROM",  "QQQ": "QLD",  "SMH": "USD",
    "XLF": "UYG",  "XLE": "ERX",  "XLV": "RXL",
    "XLI": "UXI",  "XLY": "UCC",  "XLB": "UYM",
    "GLD": "UGL",  "TLT": "UBT",
    "EFA": "EFO",  "EEM": "EET",
    "SPY": "SSO",
}

ALL_FETCH = list(dict.fromkeys(
    UNIVERSE_18 + DEFENSIVE_POOL + ["BIL", "SPY"]
    + list(LEV2X.values())
))

FETCH_START = "2003-01-01"
FETCH_END   = "2024-12-31"

WINDOWS = [
    ("2005-2024", "2005-01-01", "2024-12-31"),
    ("2005-2014", "2005-01-01", "2014-12-31"),
    ("2015-2024", "2015-01-01", "2024-12-31"),
    ("2018-2024", "2018-01-01", "2024-12-31"),
]
PRIMARY = WINDOWS[0]

VOL_TARGET = 0.15   # two-way: lever up when vol < this, de-lever when above
LEV_CAP    = 2.0    # max scale factor (uses 2x ETFs, so effective cap = 2.0)
LEV_FLOOR  = 0.50   # minimum scale (50% invested in worst-vol environment)

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
_CLOSE = None

def load_close() -> pd.DataFrame:
    global _CLOSE
    if _CLOSE is not None:
        return _CLOSE
    from trader.data_source import get_data_source
    data = get_data_source("yfinance")
    raw = data.history(ALL_FETCH, FETCH_START, FETCH_END)
    _CLOSE = _close_frame(raw)
    return _CLOSE


# ---------------------------------------------------------------------------
# Core signal helpers
# ---------------------------------------------------------------------------
def _ret(close: pd.DataFrame, sym: str, asof: pd.Timestamp, lb: int) -> float:
    """Single causal total return. Returns nan if insufficient history."""
    cache = _arrays(close)
    pos = cache["pos_map"].get(asof)
    if pos is None:
        return np.nan
    cs = cache["cols"].get(sym)
    if cs is None:
        return np.nan
    arr, first = cs
    if pos - first < lb or pos - lb + 1 < first:
        return np.nan
    denom = float(arr[pos - lb + 1])
    return float(arr[pos]) / denom - 1.0 if denom > 0 else np.nan


def _momentum_rank(close: pd.DataFrame, universe: list[str],
                   asof: pd.Timestamp, lb: int = 252) -> pd.Series:
    """Cross-sectional 12-month return, sorted descending."""
    scores = {s: _ret(close, s, asof, lb) for s in universe
              if s in close.columns}
    return pd.Series({s: v for s, v in scores.items()
                      if not np.isnan(v)}).sort_values(ascending=False)


def _return_prop_weights(ranked: pd.Series, top_n: int) -> dict[str, float]:
    """Return-proportional weighting: w_i = max(R_i, 0) / sum(positive R)."""
    picks = list(ranked.index[:top_n])
    pos = {s: max(float(ranked[s]), 0.0) for s in picks}
    tot = sum(pos.values())
    if tot <= 0:
        n = len(picks)
        return {s: 1.0 / n for s in picks}
    return {s: pos[s] / tot for s in picks}


def _dynamic_defensive_weights(close: pd.DataFrame, asof: pd.Timestamp,
                                pool: list[str] = DEFENSIVE_POOL,
                                top_n: int = 2) -> dict[str, float]:
    """Pick top-2 of GLD/TLT/BIL by 3-month momentum. Equal weight."""
    avail = [s for s in pool if s in close.columns]
    scores = {s: _ret(close, s, asof, 63) for s in avail}
    scores = {s: v for s, v in scores.items() if not np.isnan(v)}
    if not scores:
        # fallback: equal split of all in pool
        return {s: 1.0 / len(avail) for s in avail} if avail else {}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top = [s for s, _ in ranked[:top_n]]
    return {s: 1.0 / len(top) for s in top}


# ---------------------------------------------------------------------------
# Leadership persistence tracker
# ---------------------------------------------------------------------------
class PersistenceTracker:
    """Tracks consecutive months a symbol has appeared in top-3.
    Entry rule: must be top-3 for 2 consecutive months, UNLESS it's rank #1.
    """
    def __init__(self):
        self.consecutive: dict[str, int] = {}   # symbol → streak count

    def update_and_filter(self, ranked: pd.Series, top_n: int,
                          required: int = 2) -> list[str]:
        """
        Returns the allowed picks after applying the persistence rule.
        Symbols in the top-n update their streak; others reset.
        A symbol qualifies if streak >= required OR it's currently rank #1.
        """
        current_topn = set(ranked.index[:top_n])

        # Update streaks
        new_consec: dict[str, int] = {}
        for s in current_topn:
            new_consec[s] = self.consecutive.get(s, 0) + 1
        self.consecutive = new_consec

        # Filter: must have streak >= required, UNLESS rank #1 (immediate)
        rank1 = ranked.index[0] if len(ranked) > 0 else None
        allowed = []
        for s in ranked.index[:top_n]:
            streak = self.consecutive.get(s, 0)
            if s == rank1 or streak >= required:
                allowed.append(s)
        return allowed


# ---------------------------------------------------------------------------
# Vol scaling helpers
# ---------------------------------------------------------------------------
def _vol_scale(close: pd.DataFrame, asof: pd.Timestamp,
               target: float = VOL_TARGET,
               window: int = 20,
               cap: float = LEV_CAP,
               floor: float = LEV_FLOOR) -> float:
    """Two-way exposure scale: target / realized_vol, clipped to [floor, cap]."""
    rv = _realized_vol(close, "SPY", asof, window)
    if rv <= 0:
        return 1.0
    return float(np.clip(target / rv, floor, cap))


def _apply_scale_weights(sector_weights: dict[str, float],
                         scale: float,
                         close: pd.DataFrame,
                         asof: pd.Timestamp,
                         lev_close: pd.DataFrame,
                         def_weights: dict[str, float]) -> dict[str, float]:
    """
    Convert sector weights + scale into final allocation using 2x ETFs.

    When scale > 1:
      - Use 2x ETF for each sector (where available): capital = w_i * scale / 2
      - This gives: (scale/2) × 2 = scale × w_i effective equity exposure per sector
      - Remaining capital = 1 - sum(scale/2 × w_i) → goes to defensive sleeve
      - If no 2x ETF: cap effective at 1.0 for that sector (hold 1x normally, no lever)
    When scale ≤ 1:
      - Use 1x ETF for each sector: capital = w_i * scale
      - Remaining capital → defensive sleeve
    """
    lev_avail = set(lev_close.columns) if lev_close is not None else set()
    final: dict[str, float] = {}
    capital_to_sectors = 0.0

    if scale > 1.0:
        for s, w in sector_weights.items():
            lev = LEV2X.get(s)
            if lev and lev in lev_avail and asof in lev_close.index:
                v = lev_close.loc[asof, lev]
                if not np.isnan(v):
                    capital = w * scale / 2.0   # 2x ETF: half capital, same exposure
                    final[lev] = final.get(lev, 0.0) + capital
                    capital_to_sectors += capital
                    continue
            # No 2x ETF → hold at 1x (no leveraging, effective = w_i not scale*w_i)
            final[s] = final.get(s, 0.0) + w
            capital_to_sectors += w
    else:
        # scale ≤ 1: de-lever
        for s, w in sector_weights.items():
            alloc = w * scale
            final[s] = final.get(s, 0.0) + alloc
            capital_to_sectors += alloc

    # Remaining capital → dynamic defensive sleeve
    remaining = max(0.0, 1.0 - capital_to_sectors)
    if remaining > 0.01 and def_weights:
        tot = sum(def_weights.values())
        for s, dw in def_weights.items():
            final[s] = final.get(s, 0.0) + remaining * dw / tot

    # Normalize to sum = 1.0
    tot = sum(final.values())
    if tot > 0:
        final = {s: v / tot for s, v in final.items()}
    return final


# ---------------------------------------------------------------------------
# Main backtest engine
# ---------------------------------------------------------------------------
def run_turbo(close: pd.DataFrame,
              lev_close: pd.DataFrame | None,
              start: str,
              end: str,
              universe: list[str],
              top_n: int = 3,
              starting_cash: float = 100_000.0,
              # Feature flags
              return_prop: bool = False,     # A
              dynamic_def: bool = False,     # C
              persistence: bool = False,     # D
              two_way_vol: bool = False,     # E
              ) -> dict:
    """
    Generalised V4-family backtest. Feature flags enable each upgrade independently.
    Vol target is always the de-lever gate (equal to the V4 20% when two_way_vol=False).
    When two_way_vol=True, scale can go above 1.0 (leveraged using 2x ETFs).
    """
    univ = [s for s in universe if s in close.columns]
    dates = close.index[(close.index >= start) & (close.index <= end)]
    tranches = (0, 10, 20)
    n_tr = len(tranches)

    cfg = copy.deepcopy(CONFIG)
    cfg.starting_cash = starting_cash
    for k, v in CHAMPION_FLAGS.items():
        setattr(cfg, k, v)
    cfg.rotation_universe = univ

    brokers: list[PaperBroker] = []
    rebal_sets: list[set] = []
    for offset in tranches:
        b = PaperBroker(cfg)
        b.cash = starting_cash / n_tr
        brokers.append(b)
        rebal_sets.append(set(_rebalance_dates(close.index, start, end, offset)))

    book = _CombinedBook()
    started = [False] * n_tr
    tracker = PersistenceTracker() if persistence else None
    lev_avail = set(lev_close.columns) if lev_close is not None else set()

    for dt in dates:
        # Build price dict: base + 2x overlay
        prices: dict[str, float] = {}
        for s in close.columns:
            v = close.loc[dt, s]
            if not np.isnan(v):
                prices[s] = float(v)
        if lev_close is not None and dt in lev_close.index:
            for s in lev_avail:
                v = lev_close.loc[dt, s]
                if not np.isnan(v):
                    prices[s] = float(v)
        ds = dt.strftime("%Y-%m-%d")

        # Vol scale for this day
        if two_way_vol:
            scale = _vol_scale(close, dt)
        else:
            # One-way: de-lever toward 20% but never lever up
            rv = _realized_vol(close, "SPY", dt, 20)
            scale = min(1.0, 0.20 / rv) if rv > 0 else 1.0

        # Dynamic defensive
        def_weights = _dynamic_defensive_weights(close, dt) if dynamic_def else \
                      {"GLD": 0.5, "TLT": 0.5}

        for i, b in enumerate(brokers):
            if not started[i] or dt in rebal_sets[i]:
                started[i] = True

                # Rank universe
                ranked = _momentum_rank(close, univ, dt)

                # Persistence filter
                if tracker is not None:
                    allowed = tracker.update_and_filter(ranked, top_n)
                    ranked_filtered = ranked[ranked.index.isin(allowed)]
                else:
                    ranked_filtered = ranked

                # Absolute momentum gate (positive only)
                ranked_pos = ranked_filtered[ranked_filtered > 0]
                picks = list(ranked_pos.index[:top_n])

                # Fill empty slots with defensive
                n_empty = top_n - len(picks)

                # Sector weights
                if picks:
                    if return_prop:
                        sector_w = _return_prop_weights(ranked[picks], top_n=top_n)
                    else:
                        sector_w = {s: 1.0 / top_n for s in picks}  # equal weight
                    # Shift empty slot weight to defensive
                    if n_empty > 0:
                        equity_share = len(picks) / top_n
                        sector_w = {s: w * equity_share for s, w in sector_w.items()}
                else:
                    sector_w = {}
                    equity_share = 0.0

                # Apply vol scale + 2x leverage → final allocation
                if two_way_vol and lev_close is not None:
                    final_w = _apply_scale_weights(
                        sector_w, scale, close, dt, lev_close, def_weights)
                else:
                    # Standard one-way: sector_w × scale, remainder → defensive
                    final_w = {}
                    sector_capital = 0.0
                    for s, w in sector_w.items():
                        alloc = w * scale
                        final_w[s] = alloc
                        sector_capital += alloc
                    remaining = max(0.0, 1.0 - sector_capital)
                    if remaining > 0.01 and def_weights:
                        tot = sum(def_weights.values())
                        for s, dw in def_weights.items():
                            final_w[s] = final_w.get(s, 0.0) + remaining * dw / tot
                    # Also add defensive for empty slots when no vol de-lever
                    if n_empty > 0 and picks and scale >= 1.0:
                        empty_share = n_empty / top_n
                        tot = sum(def_weights.values())
                        for s, dw in def_weights.items():
                            final_w[s] = final_w.get(s, 0.0) + empty_share * dw / tot
                    # Full defensive when no picks
                    if not picks:
                        tot = sum(def_weights.values())
                        for s, dw in def_weights.items():
                            final_w[s] = dw / tot

                    # Normalize
                    tot = sum(final_w.values())
                    if tot > 0:
                        final_w = {s: v / tot for s, v in final_w.items()}

                rebalance_to(b, ds, list(final_w.keys()), prices, final_w)

        total_eq = sum(b.equity(prices) for b in brokers)
        book.equity_curve.append((ds, total_eq))

    for b in brokers:
        book.trades.extend(b.trades)

    cfg.backtest_start, cfg.backtest_end = start, end
    return {
        "equity_curve": book.equity_curve,
        "benchmark": _spy_buy_hold(close, cfg),
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _metrics(equity_curve: list[tuple], starting_cash: float) -> dict:
    vals = [v for _, v in equity_curve]
    if not vals:
        return {"cagr": 0, "mdd": 0, "sharpe": 0, "final": 0}
    final = vals[-1]
    years = len(vals) / 252.0
    cagr = (final / starting_cash) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    rets = np.diff(vals) / vals[:-1] if len(vals) > 1 else np.array([])
    sh = (float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(252))
          if len(rets) > 1 and np.std(rets) > 0 else 0.0)
    return {"cagr": cagr, "mdd": mdd, "sharpe": sh, "final": final}


def _spy_m(benchmark: dict) -> dict:
    vals = [v for _, v in benchmark.get("equity_curve", [])]
    rets = np.diff(vals) / vals[:-1] if len(vals) > 1 else np.array([])
    sh = (float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(252))
          if len(rets) > 1 and np.std(rets) > 0 else 0.0)
    return {"cagr": benchmark.get("cagr", 0),
            "mdd": benchmark.get("max_drawdown", 0),
            "sharpe": sh,
            "final": benchmark.get("final_equity", 0)}


def _row(label: str, m: dict, champ: dict, spy: dict, extra: str = "") -> str:
    vs_spy   = (m["cagr"] - spy["cagr"]) * 100
    vs_champ = (m["cagr"] - champ["cagr"]) * 100
    return (f"  {label:<42}  CAGR {m['cagr']*100:5.1f}%  "
            f"MDD {m['mdd']*100:6.1f}%  Sh {m['sharpe']:.2f}  "
            f"vs SPY {vs_spy:+5.1f}pp  vs V4 {vs_champ:+5.1f}pp  {extra}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 90)
    print("V4 Turbo Research Harness")
    print("=" * 90)

    print("\nLoading price data...")
    close = load_close()
    print(f"  {len(close.columns)} symbols  |  {len(close)} trading days")

    # Build the 2x ETF frame
    lev_syms = [s for s in LEV2X.values() if s in close.columns]
    lev_close = close[lev_syms].copy() if lev_syms else None
    print(f"  2x ETFs available: {sorted(lev_syms)}")

    label, start, end = PRIMARY
    SC = 100_000.0

    print(f"\n{'='*90}")
    print(f"  ISOLATION TESTS  |  {label}")
    print(f"{'='*90}")
    print(f"  {'Label':<42}  {'CAGR':>6}   {'MDD':>7}  {'Sh':>5}  "
          f"{'vs SPY':>8}  {'vs V4':>7}")
    print(f"  {'-'*86}")

    results: dict[str, dict] = {}

    # --- V4 baseline (equal weight, 1-way vol 20%, 13-sector universe) ---
    r_v4 = run_turbo(close, None, start, end, SECTORS_13,
                     return_prop=False, dynamic_def=False,
                     persistence=False, two_way_vol=False)
    m_v4 = _metrics(r_v4["equity_curve"], SC)
    m_spy = _spy_m(r_v4["benchmark"])
    results["V4 baseline"] = m_v4

    print(f"  {'V4 baseline (equal wt, vol20, 13 sectors)':<42}  "
          f"CAGR {m_v4['cagr']*100:5.1f}%  MDD {m_v4['mdd']*100:6.1f}%  "
          f"Sh {m_v4['sharpe']:.2f}  vs SPY {(m_v4['cagr']-m_spy['cagr'])*100:+5.1f}pp  vs V4  base")
    print(f"  {'SPY B&H':<42}  CAGR {m_spy['cagr']*100:5.1f}%  "
          f"MDD {m_spy['mdd']*100:6.1f}%  Sh {m_spy['sharpe']:.2f}")
    print()

    def show(tag: str, r: dict, extra: str = ""):
        m = _metrics(r["equity_curve"], SC)
        results[tag] = m
        print(_row(tag, m, m_v4, m_spy, extra))
        return m

    # --- A: Return-proportional weighting ---
    rA = run_turbo(close, None, start, end, SECTORS_13,
                   return_prop=True, dynamic_def=False,
                   persistence=False, two_way_vol=False)
    show("A: return-proportional weight", rA)

    # --- B: Expanded universe (18 assets) ---
    rB = run_turbo(close, None, start, end, UNIVERSE_18,
                   return_prop=False, dynamic_def=False,
                   persistence=False, two_way_vol=False)
    show("B: 18-asset universe (+ macro)", rB)

    # --- C: Dynamic defensive (momentum-rank GLD/TLT/BIL top 2) ---
    rC = run_turbo(close, None, start, end, SECTORS_13,
                   return_prop=False, dynamic_def=True,
                   persistence=False, two_way_vol=False)
    show("C: dynamic defensive sleeve", rC)

    # --- D: Leadership persistence ---
    rD = run_turbo(close, None, start, end, SECTORS_13,
                   return_prop=False, dynamic_def=False,
                   persistence=True, two_way_vol=False)
    show("D: leadership persistence (2mo streak)", rD)

    # --- E: Two-way vol 15% + 2x ETFs ---
    rE = run_turbo(close, lev_close, start, end, SECTORS_13,
                   return_prop=False, dynamic_def=False,
                   persistence=False, two_way_vol=True)
    show("E: two-way vol 15% + 2x ETFs", rE)

    # --- A+B: Return-prop + expanded universe ---
    rAB = run_turbo(close, None, start, end, UNIVERSE_18,
                    return_prop=True, dynamic_def=False,
                    persistence=False, two_way_vol=False)
    show("A+B: ret-prop + 18-asset universe", rAB)

    # --- A+B+C: Add dynamic defensive ---
    rABC = run_turbo(close, None, start, end, UNIVERSE_18,
                     return_prop=True, dynamic_def=True,
                     persistence=False, two_way_vol=False)
    show("A+B+C: + dynamic defensive", rABC)

    # --- A+B+C+D: Add persistence ---
    rABCD = run_turbo(close, None, start, end, UNIVERSE_18,
                      return_prop=True, dynamic_def=True,
                      persistence=True, two_way_vol=False)
    show("A+B+C+D: + persistence", rABCD)

    # --- Full V4 Turbo (all 5 features) ---
    rFULL = run_turbo(close, lev_close, start, end, UNIVERSE_18,
                      return_prop=True, dynamic_def=True,
                      persistence=True, two_way_vol=True)
    show("FULL V4 Turbo (A+B+C+D+E)", rFULL, "★")

    print()

    # ---------------------------------------------------------------------------
    # Multi-window: V4 baseline vs best no-leverage combo vs full turbo
    # ---------------------------------------------------------------------------
    print(f"\n{'='*90}")
    print("  MULTI-WINDOW SUMMARY  |  V4 baseline vs A+B+C+D (no lev) vs Full Turbo")
    print(f"{'='*90}")

    for wlabel, wstart, wend in WINDOWS:
        # V4 baseline
        r_v4w = run_turbo(close, None, wstart, wend, SECTORS_13,
                          return_prop=False, dynamic_def=False,
                          persistence=False, two_way_vol=False)
        m_v4w = _metrics(r_v4w["equity_curve"], SC)
        m_spyw = _spy_m(r_v4w["benchmark"])

        # No-leverage combo
        r_nolev = run_turbo(close, None, wstart, wend, UNIVERSE_18,
                            return_prop=True, dynamic_def=True,
                            persistence=True, two_way_vol=False)
        m_nolev = _metrics(r_nolev["equity_curve"], SC)

        # Full turbo
        r_full = run_turbo(close, lev_close, wstart, wend, UNIVERSE_18,
                           return_prop=True, dynamic_def=True,
                           persistence=True, two_way_vol=True)
        m_full = _metrics(r_full["equity_curve"], SC)

        def fmt(m):
            return (f"CAGR {m['cagr']*100:5.1f}%  MDD {m['mdd']*100:5.1f}%  "
                    f"Sh {m['sharpe']:.2f}  Final ${m['final']:>9,.0f}")

        print(f"\n  {wlabel}")
        print(f"  V4 baseline        {fmt(m_v4w)}  vs SPY {(m_v4w['cagr']-m_spyw['cagr'])*100:+.1f}pp")
        print(f"  A+B+C+D (no lev)   {fmt(m_nolev)}  vs SPY {(m_nolev['cagr']-m_spyw['cagr'])*100:+.1f}pp  vs V4 {(m_nolev['cagr']-m_v4w['cagr'])*100:+.1f}pp")
        print(f"  Full Turbo (A-E)   {fmt(m_full)}  vs SPY {(m_full['cagr']-m_spyw['cagr'])*100:+.1f}pp  vs V4 {(m_full['cagr']-m_v4w['cagr'])*100:+.1f}pp  ★")
        print(f"  SPY B&H            {fmt(m_spyw)}")

    print(f"\n{'='*90}")
    print("Done.")


if __name__ == "__main__":
    main()
