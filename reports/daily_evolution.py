#!/usr/bin/env python
"""
daily_evolution.py — 4-Generation Evolutionary Daily Strategy Optimizer

Run:
  cd ~/claude\ code/stock-trader
  .venv/bin/python reports/daily_evolution.py

Universe  : 13 clean sector ETFs (no survivorship bias, matches monthly champion test)
Period    : 2006-01-01 → 2024-12-31  (same as monthly champion)
Friction  : 5 bps / side (mandatory, applied every rebalance)
Total runs: 200  (4 generations × 50 simulations)

Each generation:
  Gen 1 — Signal quality: lookback × weighting × vol_target
  Gen 2 — Min-hold & smoothing: patch whipsaw / friction-bleed from Gen 1 failures
  Gen 3 — Vol overlay & regime filter: refine risk controls
  Gen 4 — Fine-tune: tight grid around Gen 3 champion

Monthly V4 Turbo champion benchmark: 14.7% CAGR / -31.5% MDD / 0.73 Sharpe
"""
import os, sys, json, random
from itertools import product as iprod
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("pip install yfinance")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
UNIVERSE   = ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLP","XLU","XLRE","XLC","SMH","QQQ"]
DEFENSIVE  = ["GLD","TLT","BIL"]
BENCH      = "SPY"

DL_START   = "2003-01-01"   # warmup for 252-day lookback + 200-day SMA
EVAL_START = "2006-01-01"
EVAL_END   = "2024-12-31"

FRICTION   = 0.0005          # 5 bps per side (10 bps round-trip)

MONTHLY_CHAMPION = {"cagr": 0.147, "mdd": -0.315, "sharpe": 0.73}

CACHE      = ROOT / "data" / "daily_evo_cache.parquet"
REPORT_DIR = ROOT / "reports"
SEED       = 42
random.seed(SEED)
np.random.seed(SEED)


# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    if CACHE.exists():
        df = pd.read_parquet(CACHE)
        if df.index[-1].strftime("%Y-%m") >= "2024-12":
            print("  [data: cache hit]")
            return df
    syms = list(dict.fromkeys(UNIVERSE + DEFENSIVE + [BENCH]))
    print(f"  [data: downloading {len(syms)} symbols from yfinance …]")
    raw = yf.download(syms, start=DL_START, end=EVAL_END,
                      auto_adjust=True, progress=False)
    df = raw["Close"] if "Close" in raw.columns else raw
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)
    df.dropna(how="all", inplace=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Signal pre-computation (memoized by key)
# ─────────────────────────────────────────────────────────────────────────────
_PRE_CACHE: dict = {}

def get_precomputed(df: pd.DataFrame, lb: int, ema_sp: int, vw: int, reg_sma: int) -> dict:
    key = (lb, ema_sp, vw, reg_sma)
    if key in _PRE_CACHE:
        return _PRE_CACHE[key]

    sec_cols = [c for c in UNIVERSE if c in df.columns]
    def_cols = [c for c in DEFENSIVE if c in df.columns]
    spy      = df[BENCH] if BENCH in df.columns else pd.Series(1.0, index=df.index)

    # Momentum matrix
    sec_px  = df[sec_cols]
    mom_raw = sec_px / sec_px.shift(lb) - 1.0
    mom     = mom_raw.ewm(span=ema_sp, adjust=False).mean() if ema_sp > 1 else mom_raw

    # SPY realized vol (annualized)
    spy_rets = spy.pct_change()
    vol_ser  = spy_rets.rolling(vw).std() * np.sqrt(252)

    # Regime SMA (inf = never triggered if reg_sma=0)
    sma_ser  = spy.rolling(reg_sma).mean() if reg_sma > 0 else pd.Series(np.inf, index=df.index)

    pre = {
        "sec_cols" : sec_cols,
        "def_cols" : def_cols,
        "mom"      : mom.values.astype(float),          # (n, n_sec)
        "vol"      : vol_ser.values.astype(float),       # (n,)
        "sma"      : sma_ser.values.astype(float),       # (n,)
        "spy"      : spy.values.astype(float),           # (n,)
        "sec_px"   : sec_px.values.astype(float),        # (n, n_sec)
        "def_px"   : df[def_cols].values.astype(float) if def_cols else np.zeros((len(df), 0)),
        "index"    : df.index,
    }
    _PRE_CACHE[key] = pre
    return pre


# ─────────────────────────────────────────────────────────────────────────────
# Core simulator
# ─────────────────────────────────────────────────────────────────────────────
def simulate(df: pd.DataFrame, p: dict) -> Optional[dict]:
    lb      = p["lookback"]
    ema_sp  = p.get("signal_ema", 1)
    vw      = p.get("vol_window", 20)
    reg_sma = p.get("regime_sma", 0)
    top_n   = p["top_n"]
    wt      = p["weighting"]                # "equal" | "return_prop"
    vt      = p.get("vol_target", 0.15)     # 0 = no vol overlay
    vfloor  = p.get("vol_floor", 0.50)
    vcap    = p.get("vol_cap", 1.0)         # 1.0 = de-lever only
    mhd     = p.get("min_hold_days", 0)
    def_bl  = list(p.get("defensive", ("GLD","TLT")))

    pre = get_precomputed(df, lb, ema_sp, vw, reg_sma)
    idx = pre["index"]
    sec_cols = pre["sec_cols"]
    def_cols = [c for c in def_bl if c in pre["def_cols"]]
    n_sec  = len(sec_cols)
    n_def  = len(def_cols)

    # Eval window: row positions in the full df
    mask     = (idx >= EVAL_START) & (idx <= EVAL_END)
    eval_pos = np.where(mask)[0]
    if len(eval_pos) < 252:
        return None

    mom    = pre["mom"]    # (n_all, n_sec)
    vol    = pre["vol"]    # (n_all,)
    sma    = pre["sma"]    # (n_all,)
    spy    = pre["spy"]    # (n_all,)
    sec_px = pre["sec_px"] # (n_all, n_sec)
    def_px = pre["def_px"] # (n_all, n_def_avail)
    full_def_cols = pre["def_cols"]

    # Map def_bl indices into pre["def_px"] columns
    def_idx_in_pre = [full_def_cols.index(c) for c in def_cols if c in full_def_cols]

    n_eval  = len(eval_pos)
    vals    = np.empty(n_eval, dtype=float)
    turns   = np.zeros(n_eval, dtype=float)

    val      = 1.0
    curr_w   = np.zeros(n_sec + n_def, dtype=float)  # [sectors..., defensives...]
    prev_w   = np.zeros(n_sec + n_def, dtype=float)
    hold_cd  = 0
    prev_pos = -1

    for i, pos in enumerate(eval_pos):
        # ── Mark-to-market ────────────────────────────────────────────
        pnl = 0.0
        if prev_pos >= 0:
            for j in range(n_sec):
                w = curr_w[j]
                if w != 0.0:
                    p0 = sec_px[prev_pos, j]
                    p1 = sec_px[pos, j]
                    if p0 > 0 and np.isfinite(p0) and np.isfinite(p1):
                        pnl += w * (p1 / p0 - 1.0)
            for k, di in enumerate(def_idx_in_pre):
                w = curr_w[n_sec + k]
                if w != 0.0:
                    p0 = def_px[prev_pos, di]
                    p1 = def_px[pos, di]
                    if p0 > 0 and np.isfinite(p0) and np.isfinite(p1):
                        pnl += w * (p1 / p0 - 1.0)

        # ── Rebalance? ────────────────────────────────────────────────
        if hold_cd <= 0:
            mom_row = mom[pos]  # (n_sec,)

            # Regime gate
            risk_off = (np.isfinite(sma[pos]) and np.isfinite(spy[pos])
                        and spy[pos] < sma[pos] and reg_sma > 0)

            # Sector selection
            if risk_off or not np.any(np.isfinite(mom_row)):
                picks = []
            else:
                valid_mask = np.isfinite(mom_row) & (mom_row > 0)
                valid_idx  = np.where(valid_mask)[0]
                if len(valid_idx) == 0:
                    picks = []
                else:
                    sorted_idx = valid_idx[np.argsort(mom_row[valid_idx])[::-1]]
                    picks = list(sorted_idx[:top_n])

            # Vol scale (de-lever only unless vcap > 1.0)
            v = vol[pos]
            if not np.isfinite(v) or v <= 0 or vt <= 0:
                scale = 1.0
            else:
                scale = float(np.clip(vt / v, vfloor, vcap))

            # Sector weights
            new_w = np.zeros(n_sec + n_def, dtype=float)
            n_eq  = len(picks)
            slot  = 1.0 / top_n

            if n_eq > 0:
                if wt == "return_prop":
                    scores = np.maximum(mom_row[picks], 0.0)
                    tot_s  = scores.sum()
                    if tot_s > 0:
                        for k, j in enumerate(picks):
                            new_w[j] = scores[k] / tot_s * n_eq * slot * scale
                    else:
                        for j in picks:
                            new_w[j] = slot * scale
                else:
                    for j in picks:
                        new_w[j] = slot * scale

            # Defensive fills remainder (clipped to [0,1])
            used = new_w[:n_sec].sum()
            rem  = max(0.0, 1.0 - used)
            if n_def > 0 and rem > 1e-4:
                per = rem / n_def
                for k in range(n_def):
                    di = def_idx_in_pre[k]
                    p_val = def_px[pos, di] if def_px.shape[1] > di else np.nan
                    if np.isfinite(p_val) and p_val > 0:
                        new_w[n_sec + k] = per

            # Normalize to 1.0 (no leverage — de-lever only regime)
            tot = new_w.sum()
            if tot > 1e-6:
                new_w /= tot

            # Turnover and friction
            turn = float(np.abs(new_w - prev_w).sum() / 2.0)
            pnl -= turn * FRICTION * 2.0
            turns[i] = turn

            curr_w  = new_w
            prev_w  = new_w.copy()
            hold_cd = mhd
        else:
            hold_cd -= 1

        val     *= (1.0 + pnl)
        vals[i]  = val
        prev_pos = pos

    # ── Metrics ───────────────────────────────────────────────────────────────
    n_years   = (idx[eval_pos[-1]] - idx[eval_pos[0]]).days / 365.25 or 1.0
    cagr      = float(vals[-1] ** (1.0 / n_years) - 1.0)

    peak      = np.maximum.accumulate(vals)
    dd        = (vals - peak) / np.where(peak > 0, peak, 1.0)
    mdd       = float(dd.min())

    log_rets  = np.diff(np.log(np.clip(vals, 1e-9, None)))
    sharpe    = float(log_rets.mean() / (log_rets.std() + 1e-12) * np.sqrt(252))

    ann_turn  = float(turns.sum() / n_years)
    ann_fric  = float((turns * FRICTION * 2.0).sum() / n_years)

    # Crisis-year calendar returns
    eval_dates = idx[eval_pos]
    val_s      = pd.Series(vals, index=eval_dates)
    spy_s      = pd.Series(spy[eval_pos], index=eval_dates)

    def _yr_ret(series, year):
        s = series[series.index.year == year]
        if len(s) < 20 or s.iloc[0] <= 0:
            return None
        return float(s.iloc[-1] / s.iloc[0] - 1.0)

    crisis = {}
    for yr in [2008, 2020, 2022]:
        strat_r = _yr_ret(val_s, yr)
        spy_r   = _yr_ret(spy_s / spy_s.iloc[0], yr)
        crisis[str(yr)] = (strat_r, spy_r)

    # SPY benchmark
    spy_full = spy[eval_pos]
    spy_yrs  = (eval_dates[-1] - eval_dates[0]).days / 365.25 or 1.0
    spy_cagr = float((spy_full[-1] / spy_full[0]) ** (1.0 / spy_yrs) - 1.0)
    spy_norm = spy_full / spy_full[0]
    spy_peak = np.maximum.accumulate(spy_norm)
    spy_mdd  = float(((spy_norm - spy_peak) / spy_peak).min())

    return {
        "cagr"      : round(cagr, 4),
        "mdd"       : round(mdd, 4),
        "sharpe"    : round(sharpe, 3),
        "ann_turn"  : round(ann_turn, 2),
        "ann_fric"  : round(ann_fric, 4),
        "spy_cagr"  : round(spy_cagr, 4),
        "spy_mdd"   : round(spy_mdd, 4),
        "edge"      : round(cagr - spy_cagr, 4),
        "final_val" : round(float(vals[-1]), 4),
        "crisis"    : crisis,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scoring & diagnostics
# ─────────────────────────────────────────────────────────────────────────────
def score(m: Optional[dict]) -> float:
    if m is None:
        return -999.0
    if m["mdd"] < -0.60:     # hard kill: catastrophic drawdown
        return -999.0 + m["cagr"]
    # Primary: Sharpe. Secondary: CAGR. Tertiary: edge over SPY.
    return m["sharpe"] * 10 + m["cagr"] * 5 + m["edge"] * 2


def diagnose(top5: list, bot5: list) -> dict:
    def avg(runs, key):
        vals = [r["metrics"][key] for r in runs if r.get("metrics")]
        return sum(vals) / len(vals) if vals else 0.0

    t_fric = avg(top5, "ann_fric")
    b_fric = avg(bot5, "ann_fric")
    t_turn = avg(top5, "ann_turn")
    b_turn = avg(bot5, "ann_turn")
    t_cagr = avg(top5, "cagr")
    b_cagr = avg(bot5, "cagr")

    friction_bleed = [r for r in bot5 if r.get("metrics") and r["metrics"]["ann_fric"] > 0.02]
    whipsaw        = [r for r in bot5 if r.get("metrics") and r["metrics"]["ann_turn"] > 10.0]

    def crisis_worse(run, yr):
        m = run.get("metrics")
        if not m:
            return False
        pair = m.get("crisis", {}).get(str(yr), (None, None))
        s, b = pair
        return s is not None and b is not None and s < b - 0.05

    def_lag = [r for r in bot5 if any(crisis_worse(r, y) for y in [2008, 2020, 2022])]

    if len(friction_bleed) >= 2:
        issue = "FRICTION_BLEED — too many trades eating alpha"
    elif len(whipsaw) >= 2:
        issue = "WHIPSAW — daily noise causing excessive churn"
    elif len(def_lag) >= 2:
        issue = "DEFENSIVE_LAG — moved to safety too slowly in 2008/2020/2022"
    else:
        issue = "WEAK_SIGNAL — lookback or weighting not capturing real momentum"

    return {
        "top5_cagr"      : round(t_cagr, 4),
        "bot5_cagr"      : round(b_cagr, 4),
        "top5_turn"      : round(t_turn, 2),
        "bot5_turn"      : round(b_turn, 2),
        "top5_fric"      : round(t_fric, 4),
        "bot5_fric"      : round(b_fric, 4),
        "whipsaw_count"  : len(whipsaw),
        "fric_bleed_count": len(friction_bleed),
        "def_lag_count"  : len(def_lag),
        "primary_issue"  : issue,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Parameter grids per generation
# ─────────────────────────────────────────────────────────────────────────────
def _sample(combos, n=50):
    combos = list(combos)
    if len(combos) <= n:
        return combos
    return random.sample(combos, n)


def gen1_params() -> list:
    """Signal exploration: lookback × top_n × weighting × vol_target."""
    base = dict(min_hold_days=0, signal_ema=1,
                defensive=("GLD", "TLT"),
                vol_window=20, vol_cap=1.0, vol_floor=0.50, regime_sma=0)
    grid = list(iprod(
        [20, 42, 63, 126, 252],            # lookback
        [2, 3],                             # top_n
        ["equal", "return_prop"],           # weighting
        [0.0, 0.10, 0.15, 0.20, 0.25],    # vol_target (0 = no overlay)
    ))
    return [dict(base, lookback=lb, top_n=tn, weighting=wt, vol_target=vt)
            for lb, tn, wt, vt in _sample(grid, 50)]


def gen2_params(top3: list) -> list:
    """Min-hold & signal smoothing to patch whipsaw/friction-bleed."""
    combos = []
    for r in top3:
        b = dict(r["params"])
        for mhd, ema, dbl in iprod(
            [0, 1, 2, 3, 5],
            [1, 3, 5, 10],
            [("GLD",), ("TLT",), ("GLD","TLT"), ("GLD","TLT","BIL")],
        ):
            combos.append(dict(b, min_hold_days=mhd, signal_ema=ema, defensive=tuple(dbl)))
    return _sample(combos, 50)


def gen3_params(top3: list) -> list:
    """Vol overlay & regime filter fine-tuning."""
    combos = []
    for r in top3:
        b = dict(r["params"])
        for vw, vcap, reg in iprod(
            [10, 20, 30, 60],    # vol_window
            [1.0, 1.5, 2.0],     # vol_cap (≤2.0: MDD intact; 3x confirmed catastrophic)
            [0, 100, 200],       # regime_sma (0 = off)
        ):
            combos.append(dict(b, vol_window=vw, vol_cap=vcap, regime_sma=reg))
    return _sample(combos, 50)


def gen4_params(champ: dict) -> list:
    """Tight fine-tune around Gen3 champion."""
    p = champ["params"]
    lb   = p["lookback"]
    vt   = p.get("vol_target", 0.15)
    mhd  = p.get("min_hold_days", 0)
    ema  = p.get("signal_ema", 1)
    vw   = p.get("vol_window", 20)
    vcap = p.get("vol_cap", 1.0)
    reg  = p.get("regime_sma", 0)

    combos = []
    for dlb, dvt, dmhd, dema, dvw in iprod(
        sorted(set([max(5, lb-20), max(5, lb-10), lb, lb+10, lb+20])),
        sorted(set([max(0.0, vt-0.03), vt, min(0.30, vt+0.03)])),
        sorted(set([max(0, mhd-1), mhd, mhd+1])),
        sorted(set([max(1, ema-1), ema, min(20, ema+2)])),
        sorted(set([max(5, vw-5), vw, min(60, vw+10)])),
    ):
        combos.append(dict(p, lookback=int(dlb), vol_target=float(dvt),
                           min_hold_days=int(dmhd), signal_ema=int(dema),
                           vol_window=int(dvw)))
    return _sample(combos, 50)


# ─────────────────────────────────────────────────────────────────────────────
# Run one generation
# ─────────────────────────────────────────────────────────────────────────────
def run_generation(df: pd.DataFrame, params_list: list, gen_num: int) -> list:
    results = []
    n = len(params_list)
    for i, p in enumerate(params_list, 1):
        label = (f"lb={p['lookback']:>3} tn={p['top_n']} wt={p['weighting'][:2]} "
                 f"mhd={p.get('min_hold_days',0)} vt={p.get('vol_target',0):.2f} "
                 f"ema={p.get('signal_ema',1):>2} vcap={p.get('vol_cap',1.0):.1f} "
                 f"reg={p.get('regime_sma',0):>3} def={'&'.join(p.get('defensive',('GLD','TLT')))}")
        print(f"  G{gen_num} [{i:>2}/{n}] {label}", end="  → ", flush=True)
        try:
            m = simulate(df, p)
        except Exception as e:
            m = None
            print(f"ERROR: {e}")
        sc = score(m)
        if m:
            verdict = "✓" if m["cagr"] > MONTHLY_CHAMPION["cagr"] else " "
            print(f"{verdict} CAGR {m['cagr']:>+.1%}  MDD {m['mdd']:.1%}  "
                  f"Shp {m['sharpe']:.2f}  Turn {m['ann_turn']:.1f}x/yr  "
                  f"Fric {m['ann_fric']:.2%}/yr")
        else:
            print("(insufficient data)")
        results.append({"params": p, "metrics": m, "score": sc})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────
W = 78

def _bar(char="─", w=W):
    return char * w

def print_gen_summary(results: list, gen_num: int, diag: dict):
    top10 = [r for r in results if r.get("metrics")][:10]
    print(f"\n{'='*W}")
    print(f"  GENERATION {gen_num} RESULTS  (top 10 of {len(results)})")
    print(f"{'='*W}")
    hdr = f"{'#':>3}  {'CAGR':>7}  {'MDD':>7}  {'Sharpe':>6}  {'Turn':>6}  {'Fric':>6}  Parameters"
    print(hdr)
    print(_bar())
    for rank, r in enumerate(top10, 1):
        m = r["metrics"]
        p = r["params"]
        beat = "✓" if m["cagr"] > MONTHLY_CHAMPION["cagr"] else " "
        print(f"{beat}{rank:<2}  {m['cagr']:>+7.1%}  {m['mdd']:>7.1%}  {m['sharpe']:>6.2f}"
              f"  {m['ann_turn']:>5.1f}x  {m['ann_fric']:>6.2%}  "
              f"lb={p['lookback']:>3} tn={p['top_n']} wt={p['weighting'][:2]} "
              f"mhd={p.get('min_hold_days',0)} vt={p.get('vol_target',0):.2f} "
              f"ema={p.get('signal_ema',1)} vcap={p.get('vol_cap',1.0):.1f} "
              f"reg={p.get('regime_sma',0)}")
    spy_cagr = results[0]["metrics"]["spy_cagr"] if results[0]["metrics"] else 0
    spy_mdd  = results[0]["metrics"]["spy_mdd"]  if results[0]["metrics"] else 0
    print(f"\n  SPY benchmark : CAGR {spy_cagr:>+.1%}  MDD {spy_mdd:.1%}")
    print(f"  Monthly champ : CAGR {MONTHLY_CHAMPION['cagr']:>+.1%}  "
          f"MDD {MONTHLY_CHAMPION['mdd']:.1%}  Sharpe {MONTHLY_CHAMPION['sharpe']:.2f}")
    print(f"\n── Failure Diagnostics (bottom 5 vs top 5) {'─'*(W-44)}")
    print(f"  Primary failure mode : {diag['primary_issue']}")
    print(f"  Friction bleed       : {diag['fric_bleed_count']}/5 bottom runs have >2%/yr friction")
    print(f"  Whipsaw              : {diag['whipsaw_count']}/5 bottom runs have >10x/yr turnover")
    print(f"  Defensive lag        : {diag['def_lag_count']}/5 bottom runs lost to SPY in a crisis year")
    print(f"  Top-5 avg turnover   : {diag['top5_turn']:.1f}x/yr   (bot-5: {diag['bot5_turn']:.1f}x/yr)")
    print(f"  Top-5 avg friction   : {diag['top5_fric']:.2%}/yr  (bot-5: {diag['bot5_fric']:.2%}/yr)")


def print_evolution_summary(all_gens: dict):
    print(f"\n{'='*W}")
    print("  EVOLUTIONARY SUMMARY — What changed across generations")
    print(f"{'='*W}")
    for gen_key in ["gen1","gen2","gen3","gen4"]:
        data = all_gens.get(gen_key)
        if not data:
            continue
        best = next((r for r in data["results"] if r.get("metrics")), None)
        diag = data["diagnostic"]
        gnum = gen_key[-1]
        if best:
            m, p = best["metrics"], best["params"]
            print(f"\n  Gen {gnum} best: CAGR {m['cagr']:>+.1%}  MDD {m['mdd']:.1%}  "
                  f"Sharpe {m['sharpe']:.2f}  Turn {m['ann_turn']:.1f}x  Fric {m['ann_fric']:.2%}")
            print(f"        params : lb={p['lookback']} tn={p['top_n']} wt={p['weighting'][:2]} "
                  f"mhd={p.get('min_hold_days',0)} vt={p.get('vol_target',0):.2f} "
                  f"ema={p.get('signal_ema',1)} vcap={p.get('vol_cap',1.0):.1f}")
            print(f"        fixed  : {diag['primary_issue']}")


# ─────────────────────────────────────────────────────────────────────────────
# Final champion output + production code
# ─────────────────────────────────────────────────────────────────────────────
def print_champion(champ: dict):
    m, p = champ["metrics"], champ["params"]
    print(f"\n{'#'*W}")
    print("  DEFINITIVE DAILY CHAMPION")
    print(f"{'#'*W}")
    print(f"\n  Performance ({EVAL_START} → {EVAL_END}, 5 bps/side friction):")
    print(f"    CAGR            : {m['cagr']:>+.2%}   (SPY {m['spy_cagr']:>+.2%}, "
          f"edge {m['edge']:>+.2%})")
    print(f"    Max Drawdown    : {m['mdd']:.2%}    (SPY {m['spy_mdd']:.2%})")
    print(f"    Sharpe          : {m['sharpe']:.3f}")
    print(f"    Annual Turnover : {m['ann_turn']:.1f}x/yr")
    print(f"    Annual Friction : {m['ann_fric']:.2%}/yr")
    print(f"\n  Crisis performance:")
    for yr in ["2008","2020","2022"]:
        strat, bench = m["crisis"].get(yr, (None, None))
        if strat is not None and bench is not None:
            verb = "outperformed" if strat > bench else "underperformed"
            print(f"    {yr}: strategy {strat:>+.1%}  SPY {bench:>+.1%}  ({verb})")
    print(f"\n  Champion parameters:")
    for k, v in sorted(p.items()):
        print(f"    {k:22} = {v!r}")

    print(f"\n── vs Monthly V4 Turbo {'─'*(W-23)}")
    print(f"  {'Metric':22} {'Daily Champ':>14} {'Monthly Champ':>14}")
    print(f"  {'─'*22} {'─'*14} {'─'*14}")
    rows = [
        ("CAGR",           m["cagr"],   MONTHLY_CHAMPION["cagr"],   "{:>14.1%}"),
        ("Max Drawdown",   m["mdd"],    MONTHLY_CHAMPION["mdd"],    "{:>14.1%}"),
        ("Sharpe",         m["sharpe"], MONTHLY_CHAMPION["sharpe"], "{:>14.2f}"),
        ("Ann. Turnover",  m["ann_turn"], None,                      "{:>14.1f}x"),
    ]
    for label, dv, mv, fmt in rows:
        ds = fmt.format(dv)
        ms = fmt.format(mv) if mv is not None else f"{'~4x':>14}"
        print(f"  {label:22} {ds} {ms}")
    if m["cagr"] > MONTHLY_CHAMPION["cagr"]:
        print(f"\n  ✓ DAILY BEATS MONTHLY  +{(m['cagr']-MONTHLY_CHAMPION['cagr'])*100:.1f}pp CAGR")
    else:
        gap = (MONTHLY_CHAMPION["cagr"] - m["cagr"]) * 100
        print(f"\n  ✗ Monthly champion holds  (daily trails by {gap:.1f}pp CAGR)")
        print(f"    Reason: {MONTHLY_CHAMPION['cagr']*100:.1f}% monthly alpha is from low-friction "
              f"monthly rebalancing of a 12-month signal.")
        print(f"    Daily friction cost: {m['ann_fric']:.2%}/yr vs monthly ~0.4%/yr")

    # Production code
    print(f"\n── Production Code (copy-paste ready) {'─'*(W-38)}")
    print("""
DAILY_CHAMPION_CONFIG = {
""" + "".join(f"    {k!r:28}: {v!r},\n" for k, v in sorted(p.items())) + """}

# Usage: drop into turbo_allocation() or run standalone with the
# daily_evolution._simulate() function. Set launchd to fire every
# weekday; remove the monthly self-gate from rotation_live.py.
""")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * W)
    print("  DAILY MULTI-FACTOR EVOLUTIONARY OPTIMIZER  —  4 Gen × 50 Sims = 200 total")
    print(f"  Universe : {len(UNIVERSE)} sector ETFs  |  {EVAL_START} → {EVAL_END}")
    print(f"  Friction : {FRICTION*1e4:.0f} bps/side  (round-trip {FRICTION*2e4:.0f} bps per rebalance)")
    print("=" * W)
    print("""
  CAGR EXPECTATION NOTE
  ─────────────────────
  Monthly V4 Turbo champion: 14.7% CAGR on clean 13-sector ETFs.
  Daily rebalancing from our blind backtest: 11.7% (-2.9pp, 5.7x the trades).

  20-30% CAGR requires one of:
    (a) Survivorship-biased stock universe  [confirmed artificially inflated]
    (b) Leverage  [3x ETFs confirmed -50%+ MDD; 2x available but narrows edge]
    (c) Look-ahead bias

  This optimizer finds the true daily ceiling honestly. If the best daily
  config beats 14.7% monthly, we adopt it. If not, monthly stays champion.
  ✓ marks any run that beats the monthly champion's CAGR.
""")

    print("Loading price data …")
    df = load_data()
    avail = [c for c in UNIVERSE + DEFENSIVE + [BENCH] if c in df.columns]
    print(f"  {len(df)} trading days  |  {len(avail)} of {len(UNIVERSE)+len(DEFENSIVE)+1} symbols available\n")

    all_gens: dict = {}

    # ── Generation 1 ─────────────────────────────────────────────────────────
    print(_bar("─"))
    print("  GENERATION 1 — Signal quality sweep")
    print("  Fixed: min_hold=0, ema=1, defensive=GLD+TLT, vol_window=20, vol_cap=1.0")
    print("  Sweep: lookback [20,42,63,126,252] × top_n [2,3] × weighting × vol_target")
    print(_bar("─"))
    g1p = gen1_params()
    g1r = run_generation(df, g1p, 1)
    g1d = diagnose(g1r[:5], g1r[-5:])
    print_gen_summary(g1r, 1, g1d)
    all_gens["gen1"] = {"results": g1r, "diagnostic": g1d}
    top3_g1 = [r for r in g1r if r.get("metrics")][:3]

    # ── Generation 2 ─────────────────────────────────────────────────────────
    print(f"\n{_bar('─')}")
    print("  GENERATION 2 — Min-hold & signal smoothing (patch whipsaw / friction-bleed)")
    print(f"  Base: top-3 Gen1  "
          f"(lb={[r['params']['lookback'] for r in top3_g1]}  "
          f"wt={[r['params']['weighting'][:2] for r in top3_g1]})")
    print("  Sweep: min_hold [0,1,2,3,5] × signal_ema [1,3,5,10] × defensive blend")
    print(_bar("─"))
    g2p = gen2_params(top3_g1)
    g2r = run_generation(df, g2p, 2)
    g2d = diagnose(g2r[:5], g2r[-5:])
    print_gen_summary(g2r, 2, g2d)
    all_gens["gen2"] = {"results": g2r, "diagnostic": g2d}
    top3_g2 = [r for r in g2r if r.get("metrics")][:3]

    # ── Generation 3 ─────────────────────────────────────────────────────────
    print(f"\n{_bar('─')}")
    print("  GENERATION 3 — Vol overlay & regime filter")
    print(f"  Base: top-3 Gen2")
    print("  Sweep: vol_window [10,20,30,60] × vol_cap [1.0,1.5,2.0] × regime_sma [0,100,200]")
    print("  Note: vol_cap>1.0 = notional leverage (no margin cost modeled, slightly optimistic)")
    print(_bar("─"))
    g3p = gen3_params(top3_g2)
    g3r = run_generation(df, g3p, 3)
    g3d = diagnose(g3r[:5], g3r[-5:])
    print_gen_summary(g3r, 3, g3d)
    all_gens["gen3"] = {"results": g3r, "diagnostic": g3d}
    champ_g3 = next((r for r in g3r if r.get("metrics")), None)

    # ── Generation 4 ─────────────────────────────────────────────────────────
    print(f"\n{_bar('─')}")
    print("  GENERATION 4 — Fine-tune around Gen3 champion")
    if champ_g3:
        p = champ_g3["params"]
        print(f"  Champion: lb={p['lookback']} mhd={p.get('min_hold_days',0)} "
              f"ema={p.get('signal_ema',1)} vt={p.get('vol_target',0):.2f} "
              f"vcap={p.get('vol_cap',1.0):.1f} reg={p.get('regime_sma',0)}")
    print("  Sweep: ±10-20% on all numeric params, tight grid of 50")
    print(_bar("─"))
    g4p = gen4_params(champ_g3) if champ_g3 else gen1_params()
    g4r = run_generation(df, g4p, 4)
    g4d = diagnose(g4r[:5], g4r[-5:])
    print_gen_summary(g4r, 4, g4d)
    all_gens["gen4"] = {"results": g4r, "diagnostic": g4d}
    final_champ = next((r for r in g4r if r.get("metrics")), None)

    # ── Summaries ─────────────────────────────────────────────────────────────
    print_evolution_summary(all_gens)

    if final_champ:
        print_champion(final_champ)

    # ── Save JSON ─────────────────────────────────────────────────────────────
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "daily_evolution_results.json"

    def _serial(obj):
        if isinstance(obj, (np.integer, np.floating)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, tuple):
            return list(obj)
        return str(obj)

    save = {
        "monthly_champion_benchmark": MONTHLY_CHAMPION,
        "champion_params"  : final_champ["params"]   if final_champ else {},
        "champion_metrics" : final_champ["metrics"]  if final_champ else {},
        "generations": {
            gk: {
                "diagnostic": data["diagnostic"],
                "top10": [
                    {"params": r["params"], "metrics": r["metrics"], "score": r["score"]}
                    for r in data["results"][:10] if r.get("metrics")
                ],
            }
            for gk, data in all_gens.items()
        },
    }
    with open(out, "w") as f:
        json.dump(save, f, indent=2, default=_serial)
    print(f"\n  Saved → {out}\n{'='*W}\n")


if __name__ == "__main__":
    main()
