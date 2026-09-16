"""Research harness for the sector-rotation v2 study.

Fetches the price history ONCE for the full union universe over the widest range,
then runs any number of config variants across multiple date windows entirely
in-process (no per-window re-download). Used by the overnight research loop to
search for a rotation configuration that beats SPY over the full 2005-2024 cycle.

Run:  .venv/bin/python reports/research_harness.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
import warnings
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from trader.config import Config, CONFIG
from trader.data_source import get_data_source
from trader.engine import PaperBroker
from trader.rotation import (
    _CombinedBook, _close_frame, _rebalance_dates, _spy_buy_hold,
    rebalance_weights, select_targets, _defensive_list,
)
from trader.rotation import rebalance_to as _rebalance_to

# ---------------------------------------------------------------------------
# Universe: base sectors + the expanded macro sleeve (bonds/gold/intl/commod).
# ---------------------------------------------------------------------------
BASE_UNIVERSE = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLB", "XLP",
                 "XLU", "XLRE", "XLC", "SMH", "QQQ"]
EXTRA_UNIVERSE = ["IWM", "MDY", "VEA", "VWO", "TLT", "IEF", "GLD", "GDX", "DBA", "USO"]
ALL_SYMBOLS = BASE_UNIVERSE + EXTRA_UNIVERSE + ["BIL", "SHY", "SPY"]

FETCH_START = "2003-01-01"
FETCH_END = "2024-12-31"

WINDOWS: List[Tuple[str, str, str]] = [
    ("2005-2024", "2005-01-01", "2024-12-31"),   # primary 20-year benchmark
    ("2005-2014", "2005-01-01", "2014-12-31"),
    ("2015-2024", "2015-01-01", "2024-12-31"),
    ("2018-2024", "2018-01-01", "2024-12-31"),
]

_CLOSE: Optional[pd.DataFrame] = None


def load_close() -> pd.DataFrame:
    """Fetch (cached) and build the full close frame once."""
    global _CLOSE
    if _CLOSE is not None:
        return _CLOSE
    data = get_data_source("yfinance")
    raw = data.history(ALL_SYMBOLS, FETCH_START, FETCH_END)
    _CLOSE = _close_frame(raw)
    return _CLOSE


def make_cfg(**flags) -> Config:
    """A rotation Config with v2 flags applied. Universe/top_n/lookbacks overridable."""
    cfg = copy.deepcopy(CONFIG)
    cfg.rotation_universe = list(flags.pop("universe", BASE_UNIVERSE))
    cfg.rotation_top_n = flags.pop("top_n", 3)
    cfg.rotation_lookbacks = tuple(flags.pop("lookbacks", (126, 252)))
    cfg.rotation_tranches = tuple(flags.pop("tranches", (0, 10, 20)))
    for k, v in flags.items():
        setattr(cfg, k, v)
    return cfg


def run(cfg: Config, close: pd.DataFrame, start: str, end: str) -> Dict:
    """Tranched monthly rotation over [start, end] using a pre-built close frame."""
    cfg = copy.deepcopy(cfg)
    cfg.backtest_start, cfg.backtest_end = start, end
    # Restrict the universe to symbols we actually have data for.
    cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]

    tranches = list(cfg.rotation_tranches) or [0]
    n = len(tranches)
    brokers: List[PaperBroker] = []
    rebal_sets: List[set] = []
    for offset in tranches:
        b = PaperBroker(cfg)
        b.cash = cfg.starting_cash / n
        brokers.append(b)
        rebal_sets.append(set(_rebalance_dates(close.index, start, end, offset)))

    # Positional numpy iteration — avoids slow per-cell .loc lookups in the hot loop.
    idx = close.index
    values = close.to_numpy()
    cols = list(close.columns)
    pos_list = np.where((idx >= start) & (idx <= end))[0]
    date_strs = {p: idx[p].strftime("%Y-%m-%d") for p in pos_list}

    book = _CombinedBook()
    started = [False] * n
    for pos in pos_list:
        dt = idx[pos]
        row = values[pos]
        prices = {cols[j]: float(row[j]) for j in range(len(cols)) if row[j] == row[j]}
        ds = date_strs[pos]
        for i, b in enumerate(brokers):
            if not started[i] or dt in rebal_sets[i]:
                started[i] = True
                targets = select_targets(close, cfg, dt)
                weights = rebalance_weights(close, cfg, dt, targets)
                _rebalance_to(b, ds, targets, prices, weights)
        book.equity_curve.append((ds, sum(b.equity(prices) for b in brokers)))
    for b in brokers:
        book.trades.extend(b.trades)
    bench = _spy_buy_hold(close, cfg)
    return {"equity_curve": book.equity_curve, "benchmark": bench, "cfg": cfg}


def run_weekly(cfg: Config, close: pd.DataFrame, start: str, end: str) -> Dict:
    """Fixed weekly rebalance: rebalance on the first trading day of each ISO week
    (single book). A calendar rebalance, like monthly but 4x more frequent."""
    cfg = copy.deepcopy(cfg)
    cfg.backtest_start, cfg.backtest_end = start, end
    cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]

    idx = close.index
    values = close.to_numpy()
    cols = list(close.columns)
    pos_list = np.where((idx >= start) & (idx <= end))[0]
    rebal, seen = set(), set()
    for p in pos_list:
        wk = (idx[p].isocalendar()[0], idx[p].isocalendar()[1])
        if wk not in seen:
            seen.add(wk)
            rebal.add(idx[p])

    b = PaperBroker(cfg)
    b.cash = cfg.starting_cash
    book = _CombinedBook()
    started = False
    for pos in pos_list:
        dt = idx[pos]
        row = values[pos]
        prices = {cols[j]: float(row[j]) for j in range(len(cols)) if row[j] == row[j]}
        ds = idx[pos].strftime("%Y-%m-%d")
        if not started or dt in rebal:
            started = True
            targets = select_targets(close, cfg, dt)
            weights = rebalance_weights(close, cfg, dt, targets)
            _rebalance_to(b, ds, targets, prices, weights)
        book.equity_curve.append((ds, b.equity(prices)))
    book.trades = b.trades
    return {"equity_curve": book.equity_curve, "benchmark": _spy_buy_hold(close, cfg),
            "cfg": cfg, "n_trades": len(b.trades)}


def run_event(cfg: Config, close: pd.DataFrame, start: str, end: str,
              vol_band: Optional[float] = None) -> Dict:
    """Event-driven rotation: check every market day, rebalance ONLY when the set
    of selected sectors changes (membership change). If `vol_band` is set, also
    rebalance when the vol-target equity exposure shifts by more than that band
    (so risk control stays responsive between leadership changes). Single book,
    no tranching (tranching is a monthly-timing device).
    """
    cfg = copy.deepcopy(cfg)
    cfg.backtest_start, cfg.backtest_end = start, end
    cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]
    dset = set(_defensive_list(cfg))

    idx = close.index
    values = close.to_numpy()
    cols = list(close.columns)
    pos_list = np.where((idx >= start) & (idx <= end))[0]

    b = PaperBroker(cfg)
    b.cash = cfg.starting_cash
    book = _CombinedBook()
    held = None
    last_expo = None
    for pos in pos_list:
        dt = idx[pos]
        row = values[pos]
        prices = {cols[j]: float(row[j]) for j in range(len(cols)) if row[j] == row[j]}
        ds = idx[pos].strftime("%Y-%m-%d")
        targets = select_targets(close, cfg, dt)
        tset = frozenset(targets)
        weights = rebalance_weights(close, cfg, dt, targets)
        expo = (sum(w for s, w in weights.items() if s not in dset)
                if weights else 1.0)
        trigger = (held is None) or (tset != held)
        if vol_band and last_expo is not None and abs(expo - last_expo) > vol_band:
            trigger = True
        if trigger:
            _rebalance_to(b, ds, targets, prices, weights)
            held = tset
            last_expo = expo
        book.equity_curve.append((ds, b.equity(prices)))
    book.trades = b.trades
    return {"equity_curve": book.equity_curve, "benchmark": _spy_buy_hold(close, cfg),
            "cfg": cfg, "n_trades": len(b.trades)}


def run_event_buffered(cfg: Config, close: pd.DataFrame, start: str, end: str,
                       enter_rank: int = 3, exit_rank: int = 5, min_hold: int = 10,
                       vol_band: Optional[float] = 0.15) -> Dict:
    """Event-driven rotation WITH hysteresis to avoid noise-churn.

    Daily check. A sector is ADDED only when it enters the top `enter_rank`; a held
    sector is DROPPED only when it falls out of the top `exit_rank` (a buffer band)
    AND has been held at least `min_hold` days. This trades on genuine, persistent
    leadership changes instead of daily rank noise. Optional vol_band keeps the
    vol-target risk control responsive between leadership changes.
    """
    from trader.rotation import momentum_scores
    cfg = copy.deepcopy(cfg)
    cfg.backtest_start, cfg.backtest_end = start, end
    cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]
    dl = [s for s in _defensive_list(cfg) if s in close.columns]
    dset = set(dl)
    top_n = cfg.rotation_top_n
    skip = getattr(cfg, "rotation_skip_days", 0)

    idx = close.index
    values = close.to_numpy()
    cols = list(close.columns)
    pos_list = np.where((idx >= start) & (idx <= end))[0]

    b = PaperBroker(cfg)
    b.cash = cfg.starting_cash
    book = _CombinedBook()
    held: List[str] = []
    hold_days: Dict[str, int] = {}
    last_set = None
    last_expo = None
    for pos in pos_list:
        dt = idx[pos]
        row = values[pos]
        prices = {cols[j]: float(row[j]) for j in range(len(cols)) if row[j] == row[j]}
        ds = idx[pos].strftime("%Y-%m-%d")

        ser = momentum_scores(close, cfg.rotation_universe, dt, cfg.rotation_lookbacks, skip)
        ranked_pos = [s for s in ser.index if ser[s] > 0.0]
        top_enter = set(ranked_pos[:enter_rank])
        top_exit = set(ranked_pos[:exit_rank])

        new_held = [s for s in held if (s in top_exit or hold_days.get(s, 0) < min_hold)]
        for s in ranked_pos:
            if len(new_held) >= top_n:
                break
            if s in top_enter and s not in new_held:
                new_held.append(s)
        new_held = new_held[:top_n]

        targets = list(new_held)
        empty = top_n - len(targets)
        if empty > 0 and dl:
            targets += [dl[i % len(dl)] for i in range(empty)]
        weights = rebalance_weights(close, cfg, dt, targets)
        expo = (sum(w for s, w in weights.items() if s not in dset) if weights else 1.0)

        trigger = (last_set is None) or (set(new_held) != set(held))
        if vol_band and last_expo is not None and abs(expo - last_expo) > vol_band:
            trigger = True
        if trigger:
            _rebalance_to(b, ds, targets, prices, weights)
            last_expo = expo
            last_set = frozenset(new_held)

        # update holdings + tenure
        for s in new_held:
            hold_days[s] = hold_days.get(s, 0) + 1
        for s in list(hold_days):
            if s not in new_held:
                hold_days.pop(s, None)
        held = new_held
        book.equity_curve.append((ds, b.equity(prices)))
    book.trades = b.trades
    return {"equity_curve": book.equity_curve, "benchmark": _spy_buy_hold(close, cfg),
            "cfg": cfg, "n_trades": len(b.trades)}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _curve_df(curve) -> pd.Series:
    s = pd.Series({pd.Timestamp(d): e for d, e in curve}).sort_index()
    return s


def metrics(curve, start_cash: float = 100_000.0) -> Dict:
    s = _curve_df(curve)
    if len(s) < 2:
        return {}
    final = float(s.iloc[-1])
    years = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    cagr = (final / start_cash) ** (1.0 / years) - 1.0
    rets = s.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = float(rets.mean() / downside.std() * np.sqrt(252)) if len(downside) and downside.std() > 0 else 0.0
    peak = s.cummax()
    mdd = float(((s - peak) / peak).min())
    # per-year
    per_year = {}
    for y in range(s.index[0].year, s.index[-1].year + 1):
        ys = s[(s.index >= f"{y}-01-01") & (s.index <= f"{y}-12-31")]
        if len(ys) >= 2:
            per_year[y] = float(ys.iloc[-1] / ys.iloc[0] - 1.0)
    worst_year = min(per_year.values()) if per_year else 0.0
    return {"final": final, "cagr": cagr, "sharpe": sharpe, "sortino": sortino,
            "mdd": mdd, "per_year": per_year, "worst_year": worst_year, "years": years}


def rolling_3y_cagr(curve) -> Dict[str, float]:
    s = _curve_df(curve)
    out = {}
    for y in range(s.index[0].year, s.index[-1].year - 1):
        a, b = f"{y}-01-01", f"{y+2}-12-31"
        w = s[(s.index >= a) & (s.index <= b)]
        if len(w) >= 200:
            yrs = (w.index[-1] - w.index[0]).days / 365.25
            out[f"{y}-{y+2}"] = float((w.iloc[-1] / w.iloc[0]) ** (1 / yrs) - 1)
    return out


def objective(m: Dict, spy: Dict) -> float:
    """Higher is better: beat SPY CAGR (weighted) + Sharpe - excess-drawdown penalty."""
    if not m or not spy:
        return -99.0
    excess_dd = max(0.0, abs(m["mdd"]) - abs(spy["mdd"]))
    return m["sharpe"] + 2.0 * (m["cagr"] - spy["cagr"]) - 0.5 * excess_dd


# ---------------------------------------------------------------------------
# Comparison driver
# ---------------------------------------------------------------------------
def evaluate(cfg: Config, close: pd.DataFrame) -> Dict:
    """Run a config across all windows; return metrics + objective vs SPY per window."""
    res = {}
    for name, a, b in WINDOWS:
        r = run(cfg, close, a, b)
        m = metrics(r["equity_curve"])
        spy = metrics(r["benchmark"]["equity_curve"])
        res[name] = {"m": m, "spy": spy, "obj": objective(m, spy),
                     "rolling": rolling_3y_cagr(r["equity_curve"])}
    return res


def fmt_row(label: str, ev: Dict, window: str = "2005-2024") -> str:
    d = ev[window]
    m, spy = d["m"], d["spy"]
    beat = "WIN " if m["cagr"] > spy["cagr"] else "lose"
    return (f"  {label:<22} {window}  "
            f"${m['final']:>10,.0f}  CAGR {m['cagr']:+6.1%}  "
            f"MDD {m['mdd']:6.1%}  Sharpe {m['sharpe']:4.2f}  "
            f"obj {d['obj']:+5.2f}  vs SPY {spy['cagr']:+5.1%} [{beat}]")


def spy_line(ev: Dict, window: str = "2005-2024") -> str:
    spy = ev[window]["spy"]
    return (f"  {'SPY buy & hold':<22} {window}  "
            f"${spy['final']:>10,.0f}  CAGR {spy['cagr']:+6.1%}  "
            f"MDD {spy['mdd']:6.1%}  Sharpe {spy['sharpe']:4.2f}")


if __name__ == "__main__":
    close = load_close()
    print(f"Loaded close frame: {close.shape[0]} rows, {close.shape[1]} symbols "
          f"({close.index[0].date()} → {close.index[-1].date()})")
    base = make_cfg()
    ev = evaluate(base, close)
    print("\nBASELINE (no v2 flags):")
    for w, _, _ in WINDOWS:
        print(fmt_row("baseline", ev, w))
    print()
    print(spy_line(ev))
