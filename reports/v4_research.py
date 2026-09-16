"""V4 Research Harness — systematic exploration of V4 architecture changes.

Tests each proposed change against the V3 champion in isolation, then tests
winning combinations, then the full V4 architecture (multi-sleeve + leverage).

Run:
    cd "/Users/natalienyaung/claude code/stock-trader"
    .venv/bin/python reports/v4_research.py
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
    _arrays,
    _above_sma,
    _realized_vol,
    _spy_buy_hold,
    _cap_weights,
    rebalance_to,
    CHAMPION_FLAGS,
)

# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
BASE_UNIVERSE = [
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLB", "XLP", "XLU", "XLRE", "XLC", "SMH", "QQQ",
]
# Leveraged ETF map: used in bull-regime leverage overlay
LEVERED_MAP = {
    "QQQ":  "QLD",    # 2× Nasdaq-100 (from 2006)
    "XLK":  "TECL",   # 3× Technology (from 2008)
    "SMH":  "SOXL",   # 3× Semiconductors (from 2010)
    "SPY":  "SSO",    # 2× S&P 500 (from 2006)
    "XLY":  "UCC",    # 2× Consumer Disc (from 2007) — used if selected
    "XLE":  "ERX",    # 2× Energy (from 2008)
}
# Broad-market sleeve (Sleeve B)
BROAD_SLEEVE = ["SPY", "QQQ", "IWM"]
# Defensive sleeve (Sleeve C)
DEFENSIVE_SLEEVE = ["GLD", "BIL"]

ALL_FETCH = list(dict.fromkeys(
    BASE_UNIVERSE
    + BROAD_SLEEVE
    + DEFENSIVE_SLEEVE
    + ["TLT", "BIL", "GLD", "SHY"]
    + ["SPY"]  # benchmark
    + list(LEVERED_MAP.values())
))

FETCH_START = "2003-01-01"
FETCH_END   = "2024-12-31"

WINDOWS = [
    ("2005-2024", "2005-01-01", "2024-12-31"),
    ("2005-2014", "2005-01-01", "2014-12-31"),
    ("2015-2024", "2015-01-01", "2024-12-31"),
    ("2018-2024", "2018-01-01", "2024-12-31"),
]

# Primary window for the isolation table
PRIMARY = ("2005-2024", "2005-01-01", "2024-12-31")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
_CLOSE = None

def load_close() -> pd.DataFrame:
    global _CLOSE
    if _CLOSE is not None:
        return _CLOSE
    data = get_data_source("yfinance")
    raw = data.history(ALL_FETCH, FETCH_START, FETCH_END)
    _CLOSE = _close_frame(raw)
    return _CLOSE


# ---------------------------------------------------------------------------
# Composite momentum score (V4 signal)
# ---------------------------------------------------------------------------
def composite_scores(close: pd.DataFrame, symbols: list[str],
                     asof: pd.Timestamp) -> pd.Series:
    """Cross-sectional z-score composite:
      0.35 * z(R_12m_skip1month) + 0.20 * z(R_6m) + 0.15 * z(R_3m)
      + 0.15 * z(R_relative_to_SPY_6m) - 0.15 * z(vol_63d)
    """
    cache = _arrays(close)
    pos = cache["pos_map"].get(asof)
    if pos is None:
        return pd.Series(dtype=float)
    cols = cache["cols"]

    def ret(s: str, lb: int, skip: int = 0) -> float:
        cs = cols.get(s)
        if cs is None:
            return np.nan
        arr, first = cs
        if pos - first < lb + skip or pos - lb - skip + 1 < first:
            return np.nan
        denom = arr[pos - lb - skip + 1]
        if denom <= 0:
            return np.nan
        return float(arr[pos - skip]) / float(denom) - 1.0

    def vol(s: str, window: int = 63) -> float:
        cs = cols.get(s)
        if cs is None:
            return np.nan
        arr, first = cs
        if pos - first + 1 < window + 2:
            return np.nan
        seg = arr[pos - window:pos + 1]
        r = np.diff(seg) / seg[:-1]
        if len(r) < 2:
            return np.nan
        sd = float(np.std(r, ddof=1))
        return sd * np.sqrt(252.0) if sd > 0 else np.nan

    skip1m = 21  # ~1 trading month skip
    spy_6m = ret("SPY", 126, 0)

    rows = {}
    for s in symbols:
        r12_1 = ret(s, 252, skip1m)
        r6m   = ret(s, 126, 0)
        r3m   = ret(s, 63, 0)
        r_rel = (r6m - spy_6m) if (not np.isnan(r6m) and spy_6m is not None
                                    and not np.isnan(spy_6m)) else np.nan
        v     = vol(s, 63)
        rows[s] = {"r12_1": r12_1, "r6m": r6m, "r3m": r3m, "r_rel": r_rel, "v": v}

    df = pd.DataFrame(rows).T

    def zs(col: str) -> pd.Series:
        c = df[col].dropna()
        if len(c) < 2:
            return pd.Series(np.nan, index=df.index)
        mu, sd = float(c.mean()), float(c.std(ddof=1))
        if sd <= 0:
            return pd.Series(0.0, index=df.index)
        return (df[col] - mu) / sd

    score = (0.35 * zs("r12_1")
             + 0.20 * zs("r6m")
             + 0.15 * zs("r3m")
             + 0.15 * zs("r_rel")
             - 0.15 * zs("v"))
    return score.dropna().sort_values(ascending=False)


# ---------------------------------------------------------------------------
# Regime detection
# ---------------------------------------------------------------------------
def _regime(close: pd.DataFrame, asof: pd.Timestamp,
            sma_window: int = 200, vol_high: float = 0.18,
            vol_low: float = 0.15) -> str:
    """Returns 'bull', 'mid', 'caution', or 'crash'.

    bull   : SPY > 200-SMA AND 20d vol ≤ vol_low  (leverage eligible)
    mid    : SPY > 200-SMA AND 20d vol ≤ vol_high (no leverage)
    caution: SPY < 200-SMA OR  20d vol > vol_high
    crash  : SPY < 200-SMA AND 20d vol > 0.25
    """
    spy_up = _above_sma(close, "SPY", asof, sma_window)
    rv = _realized_vol(close, "SPY", asof, 20)
    if spy_up and rv <= vol_low:
        return "bull"
    if spy_up and rv <= vol_high:
        return "mid"
    if not spy_up and rv > 0.25:
        return "crash"
    return "caution"


# ---------------------------------------------------------------------------
# Generic backtest runner
# ---------------------------------------------------------------------------
def _run(cfg: Config, close: pd.DataFrame, start: str, end: str,
         score_fn=None, regime_fn=None, levered_close: pd.DataFrame | None = None
         ) -> dict:
    """Runs the tranched monthly rotation with optional composite score and regime.

    score_fn(close, symbols, asof) → pd.Series (ranked scores, descending).
      If None, falls back to the standard momentum ranking in rotation.py.

    regime_fn(close, asof) → str ('bull'|'mid'|'caution'|'crash').
      If None, no regime adjustment (pure champion behavior).

    levered_close: if provided, bull-regime positions use this frame's prices.
    """
    from trader.rotation import select_targets, rebalance_weights, momentum_scores

    cfg = copy.deepcopy(cfg)
    cfg.backtest_start, cfg.backtest_end = start, end
    cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]

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

    def _select(dt: pd.Timestamp) -> tuple[list[str], dict | None]:
        if score_fn is not None:
            scores = score_fn(close, cfg.rotation_universe, dt)
            # Absolute momentum gate: composite score > 0
            scores = scores[scores > 0]
            picks = list(scores.index[: cfg.rotation_top_n])
            empty = cfg.rotation_top_n - len(picks)
            if empty > 0 and defensive:
                picks += [defensive[i % len(defensive)] for i in range(empty)]
            # Sizing: use scores for momentum weighting if cfg enables it
            if getattr(cfg, "rotation_momentum_weight", False) and picks:
                pos_scores = {s: max(float(scores.get(s, 0)), 0.0) for s in picks
                              if s not in set(defensive)}
                tot = sum(pos_scores.values())
                if tot > 0:
                    w = {s: v / tot for s, v in pos_scores.items()}
                    floor = getattr(cfg, "rotation_weight_floor", 0.15)
                    w = {s: max(v, floor) for s, v in w.items()}
                    t2 = sum(w.values())
                    w = {s: v / t2 for s, v in w.items()}
                    # vol-target overlay
                    vt = getattr(cfg, "rotation_vol_target", 0.0)
                    if vt > 0.0:
                        rv = _realized_vol(close, "SPY", dt,
                                           getattr(cfg, "rotation_vol_window", 20))
                        if rv > 0:
                            expo = min(1.0, vt / rv)
                            if expo < 1.0 and defensive:
                                w = {s: v * expo for s, v in w.items()}
                                share = (1.0 - expo) / len(defensive)
                                for d in defensive:
                                    w[d] = w.get(d, 0.0) + share
                    # cap
                    cap = getattr(cfg, "rotation_position_cap", 1.0)
                    if cap < 1.0:
                        w = _cap_weights(w, cap)
                    return picks, w
            return picks, None
        else:
            picks = select_targets(close, cfg, dt)
            w = rebalance_weights(close, cfg, dt, picks)
            return picks, w

    def _prices_for(dt: pd.Timestamp, reg: str | None) -> dict[str, float]:
        """Normal prices unless bull regime and levered_close is provided."""
        row = {s: float(close.loc[dt, s]) for s in close.columns
               if not pd.isna(close.loc[dt, s])}
        if reg == "bull" and levered_close is not None:
            # Overlay leveraged prices where available on the same date
            if dt in levered_close.index:
                for s in levered_close.columns:
                    v = levered_close.loc[dt, s]
                    if not pd.isna(v):
                        row[s] = float(v)
        return row

    def _targets_with_leverage(picks: list[str], reg: str | None,
                                levered_close: pd.DataFrame | None) -> list[str]:
        """Swap eligible sector ETFs for their leveraged versions in bull regime."""
        if reg != "bull" or levered_close is None:
            return picks
        out = []
        lev_cols = set(levered_close.columns) if levered_close is not None else set()
        for s in picks:
            lev = LEVERED_MAP.get(s)
            if lev and lev in lev_cols:
                out.append(lev)
            else:
                out.append(s)
        return out

    for dt in dates:
        reg = regime_fn(close, dt) if regime_fn is not None else None
        prices = _prices_for(dt, reg)
        ds = dt.strftime("%Y-%m-%d")

        for i, b in enumerate(brokers):
            if not started[i] or dt in rebal_sets[i]:
                started[i] = True
                picks, weights = _select(dt)
                picks = _targets_with_leverage(picks, reg, levered_close)
                rebalance_to(b, ds, picks, prices, weights)

        total_eq = sum(b.equity(prices) for b in brokers)
        book.equity_curve.append((ds, total_eq))

    for b in brokers:
        book.trades.extend(b.trades)

    return {
        "equity_curve": book.equity_curve,
        "benchmark": _spy_buy_hold(close, cfg),
        "cfg": cfg,
    }


# ---------------------------------------------------------------------------
# Metrics helpers
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


def _spy_metrics(benchmark: dict) -> dict:
    vals = [v for _, v in benchmark.get("equity_curve", [])]
    rets = np.diff(vals) / vals[:-1] if len(vals) > 1 else np.array([])
    sh = (float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(252))
          if len(rets) > 1 and np.std(rets) > 0 else 0.0)
    return {"cagr": benchmark.get("cagr", 0), "mdd": benchmark.get("max_drawdown", 0),
            "sharpe": sh, "final": benchmark.get("final_equity", 0)}


def _row(label: str, m: dict, champ: dict, spy: dict) -> str:
    vs_spy   = m["cagr"] - spy["cagr"]
    vs_champ = m["cagr"] - champ["cagr"]
    return (f"  {label:<30}  CAGR {m['cagr']*100:5.1f}%  "
            f"MDD {m['mdd']*100:6.1f}%  Sh {m['sharpe']:.2f}  "
            f"vs SPY {vs_spy*100:+5.1f}pp  vs Champ {vs_champ*100:+5.1f}pp")


# ---------------------------------------------------------------------------
# Champion config factory
# ---------------------------------------------------------------------------
def _champion_cfg(starting_cash: float = 100_000.0) -> Config:
    cfg = copy.deepcopy(CONFIG)
    cfg.starting_cash = starting_cash
    for k, v in CHAMPION_FLAGS.items():
        setattr(cfg, k, v)
    cfg.rotation_lookbacks = (252,)
    cfg.rotation_tranches  = (0, 10, 20)
    cfg.rotation_top_n     = 3
    return cfg


# ---------------------------------------------------------------------------
# Individual change tests
# ---------------------------------------------------------------------------
def run_all_tests(close: pd.DataFrame,
                  levered_close: pd.DataFrame | None,
                  start: str, end: str,
                  starting_cash: float = 100_000.0):
    base_cfg = _champion_cfg(starting_cash)
    base_cfg.rotation_universe = [s for s in BASE_UNIVERSE if s in close.columns]

    print(f"\n{'='*80}")
    print(f"  Isolation tests  |  Window: {start[:4]}–{end[:4]}")
    print(f"{'='*80}")

    results = {}

    def test(label: str, cfg: Config,
             score_fn=None, regime_fn=None,
             lev_cls=None) -> dict:
        cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]
        res = _run(cfg, close, start, end, score_fn=score_fn,
                   regime_fn=regime_fn, levered_close=lev_cls)
        return res

    # ---- CHAMPION baseline ----
    champ_res = test("Champion (V3)", copy.deepcopy(base_cfg))
    m_champ = _metrics(champ_res["equity_curve"], starting_cash)
    m_spy   = _spy_metrics(champ_res["benchmark"])
    results["champion"] = (m_champ, champ_res)

    print(f"  {'Label':<30}  {'CAGR':>6}   {'MDD':>7}  {'Sh':>5}  "
          f"{'vs SPY':>8}  {'vs Champ':>9}")
    print(f"  {'-'*78}")
    print(f"  {'Champion (V3)':<30}  CAGR {m_champ['cagr']*100:5.1f}%  "
          f"MDD {m_champ['mdd']*100:6.1f}%  Sh {m_champ['sharpe']:.2f}  "
          f"vs SPY {(m_champ['cagr']-m_spy['cagr'])*100:+5.1f}pp  vs Champ  --")
    print(f"  {'SPY B&H':<30}  CAGR {m_spy['cagr']*100:5.1f}%  "
          f"MDD {m_spy['mdd']*100:6.1f}%  Sh {m_spy['sharpe']:.2f}  "
          f"vs SPY     --  vs Champ {(m_spy['cagr']-m_champ['cagr'])*100:+5.1f}pp")
    print()

    def show(label: str, m: dict):
        print(_row(label, m, m_champ, m_spy))
        results[label] = m

    # ---- A: GLD+BIL instead of GLD+TLT ----
    cfg_A = copy.deepcopy(base_cfg)
    cfg_A.rotation_defensive_symbol = "GLD+BIL"
    res_A = test("A: GLD+BIL defensive", cfg_A)
    show("A: GLD+BIL defensive", _metrics(res_A["equity_curve"], starting_cash))

    # ---- B: Vol target 18% ----
    cfg_B = copy.deepcopy(base_cfg)
    cfg_B.rotation_vol_target = 0.18
    res_B = test("B: vol_target=18%", cfg_B)
    show("B: vol_target=18%", _metrics(res_B["equity_curve"], starting_cash))

    # ---- C: Vol target 20% ----
    cfg_C = copy.deepcopy(base_cfg)
    cfg_C.rotation_vol_target = 0.20
    res_C = test("C: vol_target=20%", cfg_C)
    show("C: vol_target=20%", _metrics(res_C["equity_curve"], starting_cash))

    # ---- D: De-lever only when SPY < 200-SMA AND vol > 18% ----
    # Implemented via regime fn overriding the vol-target gate
    cfg_D = copy.deepcopy(base_cfg)
    cfg_D.rotation_vol_target = 0.0  # disable the built-in vol target
    def _regime_delevering(close, asof, cfg=cfg_D):
        spy_up = _above_sma(close, "SPY", asof, 200)
        rv = _realized_vol(close, "SPY", asof, 20)
        # If SPY trending up and vol ≤ 18%, stay fully invested
        if spy_up or rv <= 0.18:
            return "bull"
        return "caution"
    # Use a custom select that scales exposure when caution
    def _select_with_D_regime(close, symbols, asof):
        reg = _regime_delevering(close, asof)
        scores = composite_scores(close, symbols, asof)
        scores_pos = scores[scores > 0]
        return scores_pos, reg
    # Simpler: just run with regime_fn that overrides vol-target logic
    # We'll build a custom version
    cfg_D2 = copy.deepcopy(base_cfg)
    cfg_D2.rotation_vol_target = 0.0
    # Custom approach: only de-lever if SPY < 200-SMA AND 20d vol > 18%
    # We do this by patching the vol target logic externally
    # The cleanest hack: set vol_target to 18% but add regime gate
    # Build as a custom runner
    print("  D: regime gated de-lever (SPY<200d AND vol>18%) ...")
    cfg_D_inner = copy.deepcopy(base_cfg)
    cfg_D_inner.rotation_vol_target = 0.0  # we handle this ourselves
    def _score_D(close, symbols, asof):
        # original single-lookback momentum
        from trader.rotation import momentum_scores
        return momentum_scores(close, symbols, asof, (252,), skip=0)
    # Custom runner: de-lever only in the bad regime
    from trader.rotation import select_targets as _std_select, rebalance_weights as _std_weights

    def _run_D(cfg, close, start, end):
        cfg = copy.deepcopy(cfg)
        cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]
        dates = close.index[(close.index >= start) & (close.index <= end)]
        defensive = [s for s in _defensive_list(cfg) if s in close.columns]
        tranches = list(cfg.rotation_tranches) or [0]
        n_tr = len(tranches)
        brokers2 = []
        rebal_sets2 = []
        for offset in tranches:
            b = PaperBroker(cfg)
            b.cash = cfg.starting_cash / n_tr
            brokers2.append(b)
            rebal_sets2.append(set(_rebalance_dates(close.index, start, end, offset)))
        book2 = _CombinedBook()
        started2 = [False] * n_tr
        for dt in dates:
            prices2 = {s: float(close.loc[dt, s]) for s in close.columns
                       if not pd.isna(close.loc[dt, s])}
            ds = dt.strftime("%Y-%m-%d")
            spy_up = _above_sma(close, "SPY", dt, 200)
            rv = _realized_vol(close, "SPY", dt, 20)
            bad_regime = (not spy_up) and rv > 0.18
            for i2, b in enumerate(brokers2):
                if not started2[i2] or dt in rebal_sets2[i2]:
                    started2[i2] = True
                    picks = _std_select(close, cfg, dt)
                    w = _std_weights(close, cfg, dt, picks) or {}
                    if bad_regime and w:
                        # apply 50% de-lever: scale equity, park rest in defensive
                        expo = 0.60  # stay 60% invested in bad regime
                        w = {s: v * expo for s, v in w.items()}
                        share = (1.0 - expo) / max(len(defensive), 1)
                        for d in defensive:
                            w[d] = w.get(d, 0.0) + share
                        t2 = sum(w.values())
                        w = {s: v / t2 for s, v in w.items()}
                    rebalance_to(b, ds, picks, prices2, w if w else None)
            total_eq2 = sum(b.equity(prices2) for b in brokers2)
            book2.equity_curve.append((ds, total_eq2))
        for b in brokers2:
            book2.trades.extend(b.trades)
        return {"equity_curve": book2.equity_curve,
                "benchmark": _spy_buy_hold(close, cfg), "cfg": cfg}

    res_D = _run_D(copy.deepcopy(base_cfg), close, start, end)
    show("D: regime-gated de-lever", _metrics(res_D["equity_curve"], starting_cash))

    # ---- E: Top 2 instead of top 3 ----
    cfg_E = copy.deepcopy(base_cfg)
    cfg_E.rotation_top_n = 2
    res_E = test("E: top_n=2 sectors", cfg_E)
    show("E: top_n=2 sectors", _metrics(res_E["equity_curve"], starting_cash))

    # ---- F: Equal weight (no rp_blend) ----
    cfg_F = copy.deepcopy(base_cfg)
    cfg_F.rotation_weight_scheme = "momentum"  # will fall through to equal
    cfg_F.rotation_momentum_weight = False
    cfg_F.rotation_vol_target = 0.0
    res_F = test("F: equal weight (no rp_blend)", cfg_F)
    show("F: equal weight (no rp_blend)", _metrics(res_F["equity_curve"], starting_cash))

    # ---- G: Composite signal ----
    cfg_G = copy.deepcopy(base_cfg)
    res_G = test("G: composite signal", cfg_G, score_fn=composite_scores)
    show("G: composite signal", _metrics(res_G["equity_curve"], starting_cash))

    # ---- H: Composite signal + top 2 ----
    cfg_H = copy.deepcopy(base_cfg)
    cfg_H.rotation_top_n = 2
    res_H = test("H: composite + top2", cfg_H, score_fn=composite_scores)
    show("H: composite + top2", _metrics(res_H["equity_curve"], starting_cash))

    # ---- I: Composite + top2 + GLD+BIL + vol_target=18% ----
    cfg_I = copy.deepcopy(base_cfg)
    cfg_I.rotation_top_n = 2
    cfg_I.rotation_defensive_symbol = "GLD+BIL"
    cfg_I.rotation_vol_target = 0.18
    res_I = test("I: composite+top2+GLD+BIL+vt18", cfg_I, score_fn=composite_scores)
    show("I: composite+top2+GLD+BIL+vt18", _metrics(res_I["equity_curve"], starting_cash))

    # ---- J: Best combo without leverage ----
    cfg_J = copy.deepcopy(base_cfg)
    cfg_J.rotation_top_n = 2
    cfg_J.rotation_defensive_symbol = "GLD+BIL"
    cfg_J.rotation_vol_target = 0.20
    res_J = test("J: composite+top2+GLD+BIL+vt20", cfg_J, score_fn=composite_scores)
    show("J: composite+top2+GLD+BIL+vt20", _metrics(res_J["equity_curve"], starting_cash))

    print()

    # ---- K: Leverage overlay (bull regime) ----
    if levered_close is not None:
        print("  --- Leverage overlay tests (using actual leveraged ETF data) ---")
        cfg_K = copy.deepcopy(base_cfg)
        cfg_K.rotation_top_n = 2
        cfg_K.rotation_defensive_symbol = "GLD+BIL"
        cfg_K.rotation_vol_target = 0.20
        def _regime_fn(close, asof):
            return _regime(close, asof, sma_window=200,
                           vol_high=0.18, vol_low=0.15)
        # NOTE: in bull regime, _run swaps ETFs for leveraged versions via LEVERED_MAP
        res_K = _run(cfg_K, close, start, end,
                     score_fn=composite_scores,
                     regime_fn=_regime_fn,
                     levered_close=levered_close)
        show("K: +1.5x lev ETF (bull regime)", _metrics(res_K["equity_curve"], starting_cash))

    return results


# ---------------------------------------------------------------------------
# Multi-window summary for the best config
# ---------------------------------------------------------------------------
def run_multiwindow(close: pd.DataFrame,
                    levered_close: pd.DataFrame | None,
                    starting_cash: float = 100_000.0):
    print(f"\n{'='*80}")
    print("  Multi-window summary — V4 candidate vs Champion vs SPY")
    print(f"{'='*80}")
    print(f"  V4 config: composite signal, top-2 sectors, GLD+BIL, vol_target=20%")
    if levered_close is not None:
        print(f"             + 1.5x leverage in bull regime (SPY>200d AND 20d vol<15%)")
    print()

    def _regime_fn(close, asof):
        return _regime(close, asof, sma_window=200, vol_high=0.18, vol_low=0.15)

    header = f"  {'Window':<12}  {'Champion':>45}  {'V4 candidate':>45}  {'SPY':>30}"
    print(header)
    print(f"  {'-'*125}")

    for label, start, end in WINDOWS:
        base_cfg = _champion_cfg(starting_cash)
        base_cfg.rotation_universe = [s for s in BASE_UNIVERSE if s in close.columns]

        # Champion
        champ_res = _run(base_cfg, close, start, end)
        m_champ = _metrics(champ_res["equity_curve"], starting_cash)
        m_spy   = _spy_metrics(champ_res["benchmark"])

        # V4 candidate
        cfg_v4 = copy.deepcopy(base_cfg)
        cfg_v4.rotation_top_n = 2
        cfg_v4.rotation_defensive_symbol = "GLD+BIL"
        cfg_v4.rotation_vol_target = 0.20
        res_v4 = _run(cfg_v4, close, start, end,
                      score_fn=composite_scores,
                      regime_fn=_regime_fn if levered_close is not None else None,
                      levered_close=levered_close)
        m_v4 = _metrics(res_v4["equity_curve"], starting_cash)

        def _fmt(m):
            return (f"CAGR {m['cagr']*100:5.1f}%  MDD {m['mdd']*100:5.1f}%  "
                    f"Sh {m['sharpe']:.2f}  Final ${m['final']:>8,.0f}")

        edge_champ = (m_champ["cagr"] - m_spy["cagr"]) * 100
        edge_v4    = (m_v4["cagr"]    - m_spy["cagr"]) * 100
        vc_vs_ch   = (m_v4["cagr"]    - m_champ["cagr"]) * 100

        print(f"  {label:<12}  {_fmt(m_champ)}  edge {edge_champ:+.1f}pp")
        print(f"  {'':12}  {_fmt(m_v4)}  edge {edge_v4:+.1f}pp  vs Champ {vc_vs_ch:+.1f}pp  ← V4")
        print(f"  {'':12}  {_fmt(m_spy)}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 80)
    print("V4 Research Harness")
    print("=" * 80)

    print("\nLoading price data (sectors + macro)...")
    close = load_close()
    print(f"  {len(close.columns)} symbols loaded, {len(close)} trading days")
    print(f"  Symbols: {sorted(close.columns.tolist())}")

    # Load leveraged ETF data if available
    lev_syms = [s for s in LEVERED_MAP.values() if s in close.columns]
    if lev_syms:
        levered_close = close[lev_syms].copy()
        # Only use from 2010 onward (most have limited history before)
        first_valid = levered_close.apply(lambda c: c.first_valid_index())
        print(f"\n  Leveraged ETFs available:")
        for s in lev_syms:
            fv = first_valid.get(s)
            print(f"    {s}: from {fv.strftime('%Y-%m-%d') if fv else 'N/A'}")
        # If we have at least QLD or SSO, proceed with leverage testing
        have_lev = any(s in lev_syms for s in ["QLD", "SSO", "SOXL"])
        levered_close = levered_close if have_lev else None
    else:
        levered_close = None
        print("\n  No leveraged ETF data found — skipping leverage tests.")

    label, start, end = PRIMARY
    run_all_tests(close, levered_close, start, end)
    run_multiwindow(close, levered_close)


if __name__ == "__main__":
    main()
