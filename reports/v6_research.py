"""V6 Enhanced Daily Strategy — research script.

Target: ~20% CAGR, 60-70% monthly win rate.
Base: Daily Champion (14.1% CAGR, 0.83 Sharpe, -23% MDD).

Additions tested:
  A. Regime-aware 2x leverage (bull + calm → 2x ETFs)
  B. Trend filter: sector above 100-day SMA
  C. RSI(14) gate: avoid overbought entries (RSI > 72)
  D. Multi-timeframe blend: 63d + 126d + 232d momentum

Run: .venv/bin/python reports/v6_research.py
"""
from __future__ import annotations
import os, sys, warnings, math
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

from trader.data_source import get_data_source

# ── Universe ─────────────────────────────────────────────────────────────────
UNIVERSE  = ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLC","SMH","QQQ"]
DEFENSIVE = ["GLD","TLT"]
SPY_SYM   = "SPY"

# 2x ETF map — all launched 2007 or earlier; ffill handles pre-launch NaN
LEV2X: Dict[str,str] = {
    "XLK":"ROM","QQQ":"QLD","SMH":"USD","XLF":"UYG",
    "XLE":"ERX","XLV":"RXL","XLI":"UXI","XLY":"UCC",
    "XLB":"UYM","GLD":"UGL","TLT":"UBT",
}

FETCH_START = "2003-01-01"
FETCH_END   = "2024-12-31"

WINDOWS = [
    ("2006-2024","2006-01-01","2024-12-31"),
    ("2006-2014","2006-01-01","2014-12-31"),
    ("2015-2024","2015-01-01","2024-12-31"),
    ("2018-2024","2018-01-01","2024-12-31"),
]

_DF: Optional[pd.DataFrame] = None
def load_data() -> pd.DataFrame:
    global _DF
    if _DF is not None: return _DF
    print("Fetching price data…")
    ds  = get_data_source("yfinance")
    syms = list(dict.fromkeys(UNIVERSE + DEFENSIVE + [SPY_SYM] + list(LEV2X.values())))
    raw  = ds.history(syms, FETCH_START, FETCH_END)
    frames = {s: df["close"] for s, df in raw.items() if not df.empty}
    df = pd.DataFrame(frames).sort_index().ffill().bfill()  # fill any residual NaN
    _DF = df
    print(f"  {len(df)} rows, {len(df.columns)} symbols "
          f"({df.index[0].date()} → {df.index[-1].date()})")
    return df


# ── Indicators ───────────────────────────────────────────────────────────────
def _sma(arr: np.ndarray, pos: int, w: int) -> Optional[float]:
    if pos + 1 < w: return None
    v = float(arr[pos+1-w:pos+1].mean())
    return None if math.isnan(v) else v

def _rsi(arr: np.ndarray, pos: int, period: int = 14) -> Optional[float]:
    need = period + 1
    if pos + 1 < need: return None
    d = np.diff(arr[pos+1-need:pos+1].astype(float))
    gains  = np.where(d > 0, d, 0.0)
    losses = np.where(d < 0,-d, 0.0)
    ag = gains[:period].mean()
    al = losses[:period].mean()
    for i in range(period, len(d)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    return 100.0 if al == 0 else 100.0 - 100.0/(1+ag/al)

def _ema_mom(arr: np.ndarray, pos: int, lb: int, span: int) -> Optional[float]:
    """EMA-smoothed lb-day momentum ending at pos."""
    if pos < lb + span: return None
    raw = []
    for lag in range(span-1, -1, -1):
        p = pos - lag
        base = arr[p - lb]
        if base <= 0 or math.isnan(base) or math.isnan(arr[p]): continue
        raw.append(arr[p]/base - 1.0)
    if not raw: return None
    if len(raw) == 1 or span <= 1: return raw[-1]
    alpha = 2.0/(span+1)
    ema = raw[0]
    for v in raw[1:]: ema = alpha*v + (1-alpha)*ema
    return ema

def _blend(arr, pos, lbs, wts, span):
    tot = wsum = 0.0
    for lb, w in zip(lbs, wts):
        m = _ema_mom(arr, pos, lb, span)
        if m is not None: tot += w*m; wsum += w
    return tot/wsum if wsum > 0 else None

def _rvol(spy: np.ndarray, pos: int, w: int = 25) -> float:
    if pos < w: return 0.20
    r = np.diff(np.log(spy[pos-w:pos+1].astype(float)))
    v = float(np.std(r)*np.sqrt(252))
    return v if v > 0 else 0.20


# ── Simulator ────────────────────────────────────────────────────────────────
def simulate(df: pd.DataFrame, p: dict, start: str, end: str) -> dict:
    """
    Shares-based simulator.  Cash + shares.  All NaN prices handled safely:
      • A holding whose price is NaN on a rebalance day is CARRIED FORWARD
        (not liquidated at 0) until price recovers.
      • Target allocation is never purchased if the price is NaN.
    """
    lbs       = tuple(p.get("lbs",        (232,)))
    lb_wts    = tuple(p.get("lb_weights", (1.0,)))
    span      = int  (p.get("ema_span",    9))
    top_n     = int  (p.get("top_n",       3))
    vt        = float(p.get("vol_target",  0.15))
    vw        = int  (p.get("vol_window",  25))
    vfloor    = float(p.get("vol_floor",   0.50))
    vcap_bull = float(p.get("vol_cap_bull",2.0))
    vcap_bear = float(p.get("vol_cap_bear",1.0))
    reg_sma   = int  (p.get("regime_sma",  200))
    trend_sma = int  (p.get("trend_sma",   0))
    rsi_per   = int  (p.get("rsi_period",  0))
    rsi_max   = float(p.get("rsi_max",     72.0))
    mhd       = int  (p.get("min_hold_days",3))
    use_lev   = bool (p.get("use_lev",     False))
    cost      = float(p.get("cost_bps",    5.0)) / 10_000

    sec_cols = [s for s in UNIVERSE  if s in df.columns]
    def_cols = [s for s in DEFENSIVE if s in df.columns]
    spy_col  = SPY_SYM if SPY_SYM in df.columns else None

    lev_map: Dict[str,str] = {}
    if use_lev:
        for s in sec_cols + def_cols:
            lv = LEV2X.get(s)
            if lv and lv in df.columns: lev_map[s] = lv

    all_cols = list(dict.fromkeys(
        sec_cols + def_cols + ([spy_col] if spy_col else []) + list(lev_map.values())
    ))
    df_w = df[[c for c in all_cols if c in df.columns]].copy()

    idx = df_w.index
    n   = len(idx)
    px: Dict[str, np.ndarray] = {c: df_w[c].values.astype(float) for c in df_w.columns}
    spy_arr = px.get(spy_col) if spy_col else None

    i_start = int(np.searchsorted(idx.values, np.datetime64(start)))
    i_end   = int(np.searchsorted(idx.values, np.datetime64(end), side="right")) - 1
    need    = max(lbs) + span + 5

    START_CASH = 100_000.0
    cash   = START_CASH
    shares: Dict[str, float] = {}

    equity_curve: List[Tuple[str,float]] = []
    last_rebal = i_start - 999
    wins = losses = 0
    prev_eq = START_CASH

    # monthly tracking
    mo_starts: Dict[str,float] = {}

    def price_of(sym: str, pos: int) -> Optional[float]:
        arr = px.get(sym)
        if arr is None: return None
        v = arr[pos]
        return None if (math.isnan(v) or v <= 0) else v

    def equity_now(pos: int) -> float:
        e = cash
        for sym, q in shares.items():
            p = price_of(sym, pos)
            if p is not None: e += q * p
        return e

    for pos in range(i_start, i_end + 1):
        dt = idx[pos]
        eq = equity_now(pos)
        ds = dt.strftime("%Y-%m-%d")
        equity_curve.append((ds, eq))
        mo = ds[:7]
        if mo not in mo_starts: mo_starts[mo] = eq

        if pos < i_start + need:
            continue

        if pos - last_rebal < mhd:
            continue

        # ── Regime ───────────────────────────────────────────────────────────
        bull = True
        if reg_sma > 0 and spy_arr is not None:
            sv = _sma(spy_arr, pos, reg_sma)
            if sv is not None: bull = spy_arr[pos] >= sv
        vcap = vcap_bull if bull else vcap_bear

        # ── Vol scale ─────────────────────────────────────────────────────────
        rv    = _rvol(spy_arr, pos, vw) if spy_arr is not None else vt
        scale = float(np.clip(vt/rv if rv > 0 else 1.0, vfloor, vcap))

        # ── Score sectors ─────────────────────────────────────────────────────
        scores: Dict[str,float] = {}
        for s in sec_cols:
            arr = px.get(s)
            if arr is None: continue
            m = _blend(arr, pos, lbs, lb_wts, span)
            if m is None or m <= 0: continue
            if trend_sma > 0:
                sv = _sma(arr, pos, trend_sma)
                if sv is None or arr[pos] < sv: continue
            if rsi_per > 0:
                r = _rsi(arr, pos, rsi_per)
                if r is not None and r > rsi_max: continue
            scores[s] = m

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        picks  = [s for s, _ in ranked[:top_n]]
        for d in def_cols:
            if len(picks) >= top_n: break
            if d not in picks: picks.append(d)
        if not picks: continue

        # ── Weights ──────────────────────────────────────────────────────────
        raw_w = {s: (scores[s] if s in scores else 0.01) for s in picks}
        tot_w = sum(raw_w.values())
        w_eq  = {s: v/tot_w for s, v in raw_w.items()}

        # ── Target dollars ────────────────────────────────────────────────────
        # For 2x ETF: invest (w * scale * eq / 2) in the 2x ETF
        # → effectively w * scale * eq of notional exposure.
        # Normalise total so invested ≤ eq_now.
        eq_now = equity_now(pos)
        if eq_now <= 0: continue

        raw_target: Dict[str,float] = {}
        for s, w in w_eq.items():
            notional = w * scale * eq_now
            if scale > 1.05 and use_lev:
                lv = lev_map.get(s)
                lv_p = price_of(lv, pos) if lv else None
                if lv and lv_p:
                    raw_target[lv] = notional / 2.0
                    continue
            p_s = price_of(s, pos)
            if p_s: raw_target[s] = notional

        # Defensive sleeve for leftover (de-levering or if scale < 1)
        total_invested = sum(raw_target.values())
        leftover = eq_now - total_invested
        if leftover > 1.0:
            for d in def_cols:
                if price_of(d, pos): raw_target[d] = raw_target.get(d, 0.0) + leftover/len(def_cols)

        # Normalise: hard cap at eq_now to prevent going short on cash
        total_invested = sum(raw_target.values())
        if total_invested > eq_now * 1.001:
            factor = eq_now / total_invested
            raw_target = {s: v*factor for s, v in raw_target.items()}

        # ── Convert to shares ─────────────────────────────────────────────────
        target_sh: Dict[str,float] = {}
        for sym, dollars in raw_target.items():
            p = price_of(sym, pos)
            if p: target_sh[sym] = dollars / p

        # ── Execute rebalance (sells first, then buys) ────────────────────────
        # SELLS — reduce/exit positions not in target or being trimmed
        for sym, old_q in list(shares.items()):
            new_q = target_sh.get(sym, 0.0)
            if new_q >= old_q - 1e-9: continue          # no sell needed
            p = price_of(sym, pos)
            if p is None:                                # can't price → carry forward
                target_sh[sym] = old_q
                continue
            sell_q = old_q - new_q
            cash  += sell_q * p
            cash  -= sell_q * p * cost

        # BUYS — add new or increased positions
        for sym, new_q in target_sh.items():
            old_q = shares.get(sym, 0.0)
            if new_q <= old_q + 1e-9: continue          # no buy needed
            p = price_of(sym, pos)
            if p is None:                                # can't price → skip buy
                target_sh[sym] = old_q
                continue
            buy_q = new_q - old_q
            cash  -= buy_q * p
            cash  -= buy_q * p * cost

        shares = {s: q for s, q in target_sh.items() if q > 1e-9}

        # ── Win/loss ──────────────────────────────────────────────────────────
        eq_after = equity_now(pos)
        if last_rebal >= i_start:
            if eq_after >= prev_eq: wins += 1
            else: losses += 1
        prev_eq = eq_after
        last_rebal = pos

    # ── Monthly win rate ──────────────────────────────────────────────────────
    mo_ends: Dict[str,float] = {}
    for ds, v in equity_curve:
        mo_ends[ds[:7]] = v                             # last entry per month wins

    mo_keys  = sorted(mo_ends.keys())
    mo_wins  = sum(1 for i in range(1, len(mo_keys))
                   if not math.isnan(mo_ends[mo_keys[i]])
                   and not math.isnan(mo_ends[mo_keys[i-1]])
                   and mo_ends[mo_keys[i]] > mo_ends[mo_keys[i-1]])
    mo_total = sum(1 for i in range(1, len(mo_keys))
                   if not math.isnan(mo_ends[mo_keys[i]])
                   and not math.isnan(mo_ends[mo_keys[i-1]]))

    return {"equity_curve": equity_curve,
            "wins": wins, "losses": losses,
            "mo_wins": mo_wins, "mo_total": mo_total}


# ── Metrics ──────────────────────────────────────────────────────────────────
def metrics(r: dict, start_cash: float = 100_000.0) -> dict:
    curve = [(ds, v) for ds, v in r["equity_curve"] if not math.isnan(v)]
    if len(curve) < 2: return {}
    dates = pd.to_datetime([c[0] for c in curve])
    vals  = np.array([c[1] for c in curve])
    s     = pd.Series(vals, index=dates)

    final = float(s.iloc[-1])
    years = (s.index[-1] - s.index[0]).days / 365.25
    cagr  = (final/start_cash)**(1/years) - 1

    dr = s.pct_change().dropna()
    sharpe = float(dr.mean()/dr.std()*np.sqrt(252)) if dr.std() > 0 else 0.0
    mdd    = float(((s - s.cummax())/s.cummax()).min())

    per_year: Dict[int,float] = {}
    for y in range(s.index[0].year, s.index[-1].year+1):
        ys = s[s.index.year == y]
        if len(ys) >= 2:
            per_year[y] = float(ys.iloc[-1]/ys.iloc[0] - 1.0)

    n = r["wins"]+r["losses"]
    return {
        "final": final, "cagr": cagr, "sharpe": sharpe, "mdd": mdd,
        "per_year": per_year,
        "win_rate_rebal": r["wins"]/n if n else 0.0,
        "win_rate_monthly": r["mo_wins"]/r["mo_total"] if r["mo_total"] else 0.0,
        "n_rebalances": n,
    }

def spy_bh(df: pd.DataFrame, start: str, end: str) -> dict:
    s    = df[SPY_SYM][(df.index >= start) & (df.index <= end)].astype(float)
    v0   = float(s.iloc[0])
    curve = [(d.strftime("%Y-%m-%d"), 100_000.0*float(v)/v0) for d, v in s.items()]
    mo_ends: Dict[str,float] = {}
    for ds, v in curve: mo_ends[ds[:7]] = v
    keys = sorted(mo_ends.keys())
    mw   = sum(1 for i in range(1,len(keys)) if mo_ends[keys[i]] > mo_ends[keys[i-1]])
    return {"equity_curve": curve, "wins": mw, "losses": len(keys)-1-mw,
            "mo_wins": mw, "mo_total": len(keys)-1}

def fmt(label, m, sm):
    beat = "✓ WIN" if m["cagr"] > sm["cagr"] else "✗ lose"
    print(f"  {label:<38}  CAGR {m['cagr']:+6.1%}  MDD {m['mdd']:6.1%}  "
          f"Sh {m['sharpe']:.2f}  WinRate(mo) {m['win_rate_monthly']:.0%}  "
          f"vs SPY {sm['cagr']:+.1%}  {beat}")


# ── Configs ───────────────────────────────────────────────────────────────────
BASE = dict(lbs=(232,), lb_weights=(1.0,), ema_span=9, top_n=3,
            vol_target=0.12, vol_window=25, vol_floor=0.50,
            vol_cap_bull=1.0, vol_cap_bear=1.0, regime_sma=0,
            trend_sma=0, rsi_period=0, rsi_max=72, min_hold_days=3,
            use_lev=False, cost_bps=5)

CONFIGS = [
    ("Daily Champion (baseline, 1x)",  {**BASE}),
    ("V6A: +2x leverage (bull regime)",{**BASE, "vol_target":0.15,
                                        "vol_cap_bull":2.0,"vol_cap_bear":1.0,
                                        "regime_sma":200,"use_lev":True}),
    ("V6B: +100d trend filter",        {**BASE, "vol_target":0.15,
                                        "vol_cap_bull":2.0,"vol_cap_bear":1.0,
                                        "regime_sma":200,"trend_sma":100,
                                        "use_lev":True}),
    ("V6C: +RSI<72 gate",              {**BASE, "vol_target":0.15,
                                        "vol_cap_bull":2.0,"vol_cap_bear":1.0,
                                        "regime_sma":200,"trend_sma":100,
                                        "rsi_period":14,"rsi_max":72,
                                        "use_lev":True}),
    ("V6D: +multi-tf (63+126+232)",    {**BASE, "vol_target":0.15,
                                        "lbs":(63,126,232),"lb_weights":(0.2,0.3,0.5),
                                        "vol_cap_bull":2.0,"vol_cap_bear":1.0,
                                        "regime_sma":200,"trend_sma":100,
                                        "rsi_period":14,"rsi_max":72,
                                        "use_lev":True}),
    ("V6E: full+top4+RSI<68",          {**BASE, "vol_target":0.15,
                                        "lbs":(63,126,232),"lb_weights":(0.2,0.3,0.5),
                                        "top_n":4,
                                        "vol_cap_bull":2.0,"vol_cap_bear":1.0,
                                        "regime_sma":200,"trend_sma":100,
                                        "rsi_period":14,"rsi_max":68,
                                        "use_lev":True}),
]


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    df = load_data()

    print("\n" + "="*100)
    print("V6 ENHANCED DAILY STRATEGY — BACKTEST RESULTS (2006–2024, 5bps/side)")
    print("="*100)

    for win_name, w_start, w_end in WINDOWS:
        yrs = (pd.Timestamp(w_end)-pd.Timestamp(w_start)).days/365.25
        print(f"\n── {win_name}  ({yrs:.0f} yrs) ──")
        spy_r = spy_bh(df, w_start, w_end)
        spy_m = metrics(spy_r)
        print(f"  {'SPY buy & hold':<38}  CAGR {spy_m['cagr']:+6.1%}  MDD {spy_m['mdd']:6.1%}  "
              f"Sh {spy_m['sharpe']:.2f}  WinRate(mo) {spy_m['win_rate_monthly']:.0%}")
        for label, cfg in CONFIGS:
            r = simulate(df, cfg, w_start, w_end)
            m = metrics(r)
            if m: fmt(label, m, spy_m)

    # ── Per-year detail on V6D ────────────────────────────────────────────────
    _, best_cfg = CONFIGS[4]
    print(f"\n{'='*100}")
    print(f"PER-YEAR BREAKDOWN — V6D (multi-timeframe + all indicators + 2x bull leverage)")
    print(f"{'='*100}")

    r_best = simulate(df, best_cfg, "2006-01-01", "2024-12-31")
    m_best = metrics(r_best)
    spy_r  = spy_bh(df, "2006-01-01", "2024-12-31")
    spy_m  = metrics(spy_r)

    print(f"\n  {'Year':<6}  {'V6D':>8}  {'SPY':>8}  {'Edge':>8}  Status")
    print("  " + "-"*50)
    pos_yr = 0
    for y in sorted(m_best["per_year"]):
        v = m_best["per_year"][y]
        s = spy_m["per_year"].get(y, 0.0)
        flag = "✓" if v > 0 else "✗"
        if v > 0: pos_yr += 1
        print(f"  {y}    {v:+8.1%}  {s:+8.1%}  {v-s:+8.1%}  {flag}")

    n_yr = len(m_best["per_year"])
    print(f"\n  ── Full-period summary ──")
    print(f"  CAGR               : {m_best['cagr']:+.1%}")
    print(f"  Sharpe             : {m_best['sharpe']:.2f}")
    print(f"  Max Drawdown       : {m_best['mdd']:.1%}")
    print(f"  Monthly Win Rate   : {m_best['win_rate_monthly']:.0%}")
    print(f"  Yearly Win Rate    : {pos_yr}/{n_yr} = {pos_yr/n_yr:.0%}")
    print(f"  Rebalance Win Rate : {m_best['win_rate_rebal']:.0%}")
    print(f"  # Rebalances/year  : {m_best['n_rebalances']/19:.0f}")
    print(f"  $100k → ${m_best['final']:,.0f}  (19 yrs)")

    import json
    out = {
        "configs": {},
        "spy": {k: (v if k != "per_year" else {str(k2):v2 for k2,v2 in v.items()})
                for k, v in spy_m.items()},
    }
    for label, cfg in CONFIGS:
        r = simulate(df, cfg, "2006-01-01", "2024-12-31")
        m = metrics(r)
        out["configs"][label] = {k: (v if k != "per_year" else {str(k2):v2 for k2,v2 in v.items()})
                                  for k, v in m.items()}
    path = os.path.join(os.path.dirname(__file__), "v6_results.json")
    with open(path, "w") as f: json.dump(out, f, indent=2)
    print(f"\n  Results → reports/v6_results.json")
