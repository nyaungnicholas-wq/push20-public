"""Canonical, fully-parameterized backtest harness — single source of truth.

Every optimization test goes through THIS simulate() so results are comparable.
Reproduces V6A (16.6% CAGR / 58% monthly win rate, 2006-2024) exactly.

Usage:
  # one config, rich diagnostics:
  python reports/opt_harness.py --config '{"vol_cap_bull":2.0,"vol_cap_bear":1.0}'
  # batch (JSON array in, JSON array out):
  python reports/opt_harness.py --batch '[{...},{...}]'
  # named diagnostic decomposition:
  python reports/opt_harness.py --diagnose

Config keys (all optional, V6A defaults shown):
  lookback=232 ema_span=9 top_n=3 min_hold_days=3
  vol_target=0.15 vol_window=25 vol_floor=0.50
  vol_cap_bull=2.0 vol_cap_bear=1.0 regime_sma=200
  breaker_sma=0 breaker_level=1.0          # crash circuit breaker (0=off)
  defensive="GLD+TLT"                       # or "GLD+TLT+BIL" (risk-off cash) / "BIL"
  defensive_momentum=False                  # rank defensive sleeve by momentum, hold best
  use_lev=True lev_only_topk=0              # 0=lever all picks; k=lever only top-k picks
  max_weight=1.0                            # cap any single position weight (1.0=off)
  weight_scheme="return_prop"               # or "equal" / "squared"
  cost_bps=5
"""
from __future__ import annotations
import os, sys, json, math, argparse, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

from trader.data_source import get_data_source

UNIVERSE  = ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLC","SMH","QQQ"]
SPY_SYM   = "SPY"
# All ProShares Ultra 2x. (ERX was wrong here: Direxion, 3x until 2020 — use DIG.)
LEV2X = {"XLK":"ROM","QQQ":"QLD","SMH":"USD","XLF":"UYG","XLE":"DIG",
         "XLV":"RXL","XLI":"UXI","XLY":"UCC","XLB":"UYM","GLD":"UGL","TLT":"UBT"}
# 3x tier — only the liquid names (mirrors live rotation.py LEV3X_MAP).
# TECL/FAS real from 2008-11; TQQQ/SOXL from 2010-02; synthesized before that.
LEV3X = {"XLK":"TECL","QQQ":"TQQQ","SMH":"SOXL","XLF":"FAS"}
DEFENSIVE_ASSETS = ["GLD","TLT","BIL","SHY"]

FETCH_START = "2003-01-01"
FETCH_END   = pd.Timestamp.today().strftime("%Y-%m-%d")   # dynamic — always include latest data

WINDOWS = [
    ("full_06_24","2006-01-01","2024-12-31"),
    ("early_06_14","2006-01-01","2014-12-31"),
    ("modern_15_24","2015-01-01","2024-12-31"),
    ("recent_18_24","2018-01-01","2024-12-31"),
    ("live_25_now","2025-01-01",FETCH_END),
    ("full_06_now","2006-01-01",FETCH_END),
]
INSAMPLE  = ("2006-01-01","2015-12-31")
OUTSAMPLE = ("2016-01-01","2024-12-31")

# Synthetic pre-inception leveraged pricing: mult× underlying daily return minus
# flat drag. 2x: 0.95% ER + ~3% financing on the 1 borrowed leg = 4%/yr.
# 3x: ~1% ER + ~3% financing on EACH of the 2 borrowed legs = 7%/yr.
SYN_DRAG_ANNUAL  = 0.04
SYN_DRAG_3X      = 0.07

# RELEASE TIMING AND WHAT cost_bps MEANS (2026-09-19, research/v3/EXECUTION.md).
# cost_bps is one flat constant for every symbol, size, date and INSTANT. The
# instant matters more than anyone assumed: MEASURED whole-book notional-weighted
# half-spread is 27.63 bps/side at 09:30:00.000 against 7.23 five minutes later
# (806 legs, 150 mornings), and the signed drift across that window is
# indistinguishable from zero at every offset out to an hour.
#
# So this constant is not a property of the strategy, it is a property of WHEN
# the live loop releases. Priced through this engine at each symbol's own
# measured multiple, releasing at the open rather than after it costs
# RELEASE_AT_OPEN_PENALTY_BPS more per side. ROTATION_RELEASE_DELAY_MIN in
# trader/rotation_live.py is the live switch; this is the number it is worth.
#
# NOT wired into the default. Changing cost_bps here would silently restate every
# historical result, and the fill convention stays next_open either way -- the
# drift finding is what licenses that, since mid(+15m) is an unbiased estimate of
# mid(09:30). A true open+15m fill price would need 20 years of intraday bars,
# which do not exist for free.
RELEASE_AT_OPEN_PENALTY_BPS = 10.41   # MEASURED, per side, whole book

def synthesize_lev(df, underlying, lev_sym, drag=SYN_DRAG_ANNUAL, mult=2.0):
    """Extend a leveraged ETF backwards before its inception using the underlying's
    returns: px[t-1] = px[t] / (1 + mult*r_t - drag/252). Stops where the
    underlying itself has no data. Returns count of synthesized bars."""
    if lev_sym not in df.columns or underlying not in df.columns: return 0
    col=df[lev_sym]
    first=col.first_valid_index()
    if first is None: return 0
    fi=df.index.get_loc(first)
    if fi==0: return 0
    und=df[underlying].values.astype(float)
    syn=np.full(fi+1,np.nan); syn[fi]=float(col.iloc[fi])
    d=drag/252.0
    for i in range(fi,0,-1):
        if math.isnan(und[i]) or math.isnan(und[i-1]) or und[i-1]<=0: break
        r=und[i]/und[i-1]-1.0
        syn[i-1]=syn[i]/(1.0+mult*r-d)
    n=int(np.sum(~np.isnan(syn[:fi])))
    df.iloc[:fi, df.columns.get_loc(lev_sym)]=syn[:fi]
    return n

_DF=None
# Fill mode: "close" (execute at the very close the decision was computed from) or
# "next_open" (execute at the next session's open).
#
# DEFAULT IS "next_open" AS OF 2026-09-17 (research/v3/A2a_fill_convention.md).
# The live loop is the evening/next-open path (register-tasks.ps1:93 ->
# `rotation-live --scheduled --session next-open`): rotation_live.run_scheduled
# takes `closed = last_closed_session()`, asserts the price frame is as-of that
# session (require_asof), and therefore consumes session t's OFFICIAL CLOSING
# PRINT. A price that exists only once the market has closed cannot also be the
# price you transact at; the first executable price after it is the t+1 open.
# "close" is retained for reproducing every pre-2026-09-17 number in reports/
# (including the spent holdout) and for a same-session MOC design that decides
# on a pre-close snapshot instead. Override precedence:
#   cfg["fill_mode"]  >  --fill-mode  >  $PUSH20_FILL_MODE  >  this default.
FILL_MODE = os.environ.get("PUSH20_FILL_MODE", "next_open")
HOLD_UNIT = "trading"
# Momentum window convention (A2b). "live" reproduces trader/rotation.py, which is what
# the live loop actually computes and therefore what this certifier must grade by default.
#   "live"      a[p]/a[p-lb+1]-1  -> an lb-BAR window = lb-1 return periods (231 at lb=232)
#   "canonical" a[p]/a[p-lb]-1    -> lb return periods (232 at lb=232) = the correct reading
#                                    of "lb-day return", and what the lb=232 grid actually fit
# Live is the buggy side; see research/v3/A2b_window_skip.md for the proposed live diff.
MOM_WINDOW = "live"
# trader/rotation.py drops rotation_skip_days entirely when rotation_signal_ema>1
# (momentum_scores_ema has no skip parameter). Grading skip>0 under an EMA therefore
# certifies a signal the live loop cannot produce. Refuse it instead of grading it.
SKIP_UNDER_EMA = False
_OPENS = None
FILL_FALLBACKS = 0

def load_opens():
    """Opens are deliberately NOT synthesized for the leveraged sleeves, because synthesize_lev invents pre-inception CLOSES and inventing opens on top of invented closes would measure our own model rather than the market, and that a missing open falls back to that days close with the count reported."""
    global _OPENS
    if _OPENS is not None: return _OPENS
    base=load_data()
    ds=get_data_source("yfinance")
    raw=ds.history(list(base.columns),FETCH_START,FETCH_END)
    frames={k:v["open"] for k,v in raw.items() if not v.empty and "open" in v}
    _OPENS=pd.DataFrame(frames).reindex(base.index)
    return _OPENS


def load_data():
    global _DF
    if _DF is not None: return _DF
    ds=get_data_source("yfinance")
    syms=list(dict.fromkeys(UNIVERSE+DEFENSIVE_ASSETS+[SPY_SYM]
                            +list(LEV2X.values())+list(LEV3X.values())+["^VIX"]))
    raw=ds.history(syms,FETCH_START,FETCH_END)
    frames={s:d["close"] for s,d in raw.items() if not d.empty}
    # ffill ONLY — no bfill. Pre-inception prices stay NaN so the simulator
    # correctly skips assets that did not exist yet (XLC pre-2018, BIL pre-2007).
    df=pd.DataFrame(frames).sort_index().ffill()
    # Leveraged ETFs: synthesize honest pre-inception history from the underlying
    # so the leveraged sleeves are simulatable across the whole window.
    for und,lv in LEV2X.items():
        synthesize_lev(df,und,lv)
    for und,lv in LEV3X.items():
        synthesize_lev(df,und,lv,drag=SYN_DRAG_3X,mult=3.0)
    _DF=df
    return _DF

# ── indicators ────────────────────────────────────────────────────────────────
def _sma(a,pos,w):
    if pos+1<w: return None
    v=float(a[pos+1-w:pos+1].mean()); return None if math.isnan(v) else v

def _ema_mom(a,pos,lb,span,skip=0):
    """EMA-smoothed `lb`-day return, ending `skip` days before `pos`.

    skip>0 implements skip-month momentum: the most recent `skip` trading days
    are excluded from the measurement window, so a 12-1 signal is lb=252,skip=21.
    skip=0 reproduces the original (contaminated) behaviour exactly.

    MOM_WINDOW selects the window convention; see its definition above.
    """
    if skip>0 and span>1 and not SKIP_UNDER_EMA:
        raise ValueError(
            f"skip={skip} with ema_span={span}: trader/rotation.py ignores "
            "rotation_skip_days whenever rotation_signal_ema>1, so this signal is not "
            "executable live. Set opt_harness.SKIP_UNDER_EMA=True to grade it anyway.")
    off = 1 if MOM_WINDOW=="live" else 0
    # keep the ORIGINAL warm-up guard under both conventions so a live-vs-canonical
    # comparison starts on the same bar and measures the formula, not the start date.
    if pos<lb+span+skip: return None
    raw=[]
    for lag in range(span-1,-1,-1):
        p=pos-lag-skip; base=a[p-lb+off]
        if base<=0 or math.isnan(base) or math.isnan(a[p]): continue
        raw.append(a[p]/base-1.0)
    if not raw: return None
    if len(raw)==1: return raw[-1]
    alpha=2.0/(span+1); e=raw[0]
    for v in raw[1:]: e=alpha*v+(1-alpha)*e
    return e

def _rvol(spy,pos,w):
    if pos<w: return 0.20
    r=np.diff(np.log(spy[pos-w:pos+1].astype(float)))
    v=float(np.std(r)*np.sqrt(252)); return v if v>0 else 0.20

def _ewma_vol(a,pos,halflife,span_mult=4):
    """Annualized EWMA vol of log returns, decaying with `halflife` trading days.

    A fixed window drops a large return off a cliff the day it ages out, which is
    what makes an 8-day estimate whipsaw the exposure scale. EWMA retires the same
    return smoothly, so the scale moves without step changes.
    """
    n=int(halflife*span_mult)
    if pos<n+1: return 0.0
    seg=a[pos-n:pos+1].astype(float)
    if np.any(np.isnan(seg)) or np.any(seg<=0): return 0.0
    r=np.diff(np.log(seg))
    lam=0.5**(1.0/halflife)
    w=lam**np.arange(len(r)-1,-1,-1)
    w=w/w.sum()
    var=float(np.sum(w*(r-np.sum(w*r))**2))
    v=math.sqrt(var*252.0)
    return v if v>0 else 0.0

def _basket_vol(px,syms,pos,w,ewma_hl=0):
    """Annualized vol of the EQUAL-WEIGHT basket of `syms`.

    This is what the live system sizes against (rotation.py `_basket_realized_vol`).
    Sizing off SPY instead systematically under-prices a 2x-semis sleeve, which runs
    3-4x SPY's volatility.
    """
    rows=[]
    for s in syms:
        a=px.get(s)
        if a is None: continue
        if ewma_hl>0:
            n=int(ewma_hl*4)
        else:
            n=w
        if pos<n+1: continue
        seg=a[pos-n:pos+1].astype(float)
        if np.any(np.isnan(seg)) or np.any(seg<=0): continue
        rows.append(np.diff(seg)/seg[:-1])
    if not rows: return 0.0
    br=np.vstack(rows).mean(axis=0)
    if len(br)<2: return 0.0
    if ewma_hl>0:
        lam=0.5**(1.0/ewma_hl)
        wt=lam**np.arange(len(br)-1,-1,-1); wt=wt/wt.sum()
        var=float(np.sum(wt*(br-np.sum(wt*br))**2))
        v=math.sqrt(var*252.0)
    else:
        sd=float(np.std(br,ddof=1)); v=sd*math.sqrt(252.0)
    return v if v>0 else 0.0

def _sec_vol(a,pos,w=20):
    """Per-sector annualized realized vol (for risk-adjusted sizing)."""
    if pos<w: return None
    seg=a[pos-w:pos+1].astype(float)
    if np.any(seg<=0) or np.any(np.isnan(seg)): return None
    r=np.diff(np.log(seg)); v=float(np.std(r)*np.sqrt(252))
    return v if v>0 else None

def _blend_mom(a,pos,lbs,span,skip=0,wts=None):
    """Blended EMA momentum over multiple lookbacks. lbs=tuple of ints.

    wts=None means equal weight (the original behaviour). Passing weights lets a
    spec say "0.6 x 3-month + 0.4 x 12-month" literally instead of approximating
    it with an equal blend. Weights are renormalised over whichever lookbacks
    actually have enough history, so an asset near its inception is not silently
    scored on a different formula from the rest of the universe.
    """
    vals,ws=[],[]
    for i,lb in enumerate(lbs):
        m=_ema_mom(a,pos,lb,span,skip)
        if m is not None:
            vals.append(m); ws.append(1.0 if wts is None else float(wts[i]))
    if not vals: return None
    tw=sum(ws)
    return float(sum(v*w for v,w in zip(vals,ws))/tw) if tw>0 else float(np.mean(vals))

# High-beta / cyclical sectors get a stricter cap under tiered_caps.
HIGH_BETA={"SMH","XLK","QQQ","XLE","XLC"}

# Reverse map: levered ticker -> its underlying sector. Needed to answer "which
# sectors am I actually holding" when the book is carrying ROM/QLD/USD/... .
UNLEV={**{v:k for k,v in LEV2X.items()},**{v:k for k,v in LEV3X.items()}}

DEFAULTS=dict(lookback=232,ema_span=9,top_n=3,min_hold_days=3,
    vol_target=0.15,vol_window=25,vol_floor=0.50,vol_cap_bull=2.0,vol_cap_bear=1.0,
    regime_sma=200,breaker_sma=0,breaker_level=1.0,defensive="GLD+TLT",
    defensive_momentum=False,use_lev=True,lev_only_topk=0,max_weight=1.0,
    weight_scheme="return_prop",cost_bps=5,
    warmup_bars=0,          # 0: warm-up = max(lb)+span+skip+5 (start date moves with lb).
                            # >0: pin it, so a lookback sweep is not also a start-date sweep.
    use_3x=0,             # 1: deploy 3x ETFs (TECL/TQQQ/SOXL/FAS) when scale>2 (V5 three-tier)
    # ── V7 upgrade knobs (all default OFF → reproduce V6D exactly) ──
    risk_adjust_vol=0,      # >0: divide momentum score by sector vol over this window (risk-adjusted sizing)
    golden_cross=0,         # 1: require SMA50>SMA200 for full bull leverage, else cap at 1.0
    regime_buffer=0.0,      # SPY must be this fraction above 200SMA to count as bull (hysteresis)
    defensive_dynamic=0,    # 1: swap TLT->BIL when TLT below its own 200SMA (2022 fix)
    tiered_caps=0,          # 1: high-beta sectors capped at max_weight, others at max_weight+0.05
    min_score_gap=0.0,      # hysteresis: incumbent kept unless challenger beats it by this margin
    # ── "news fear" overlay knobs (VIX as backtestable proxy; default OFF) ──
    vix_gate_level=0.0,     # >0: when VIX >= level, cap exposure scale at vix_gate_cap
    vix_gate_cap=1.0,       # the cap applied while the VIX gate is active
    vix_spike_pct=0.0,      # >0: gate also fires when VIX is up this fraction over 5 days
    ext_gate=None,          # optional {date_str: cap} from an external signal (e.g. news sentiment)
    # ── FIDELITY knobs: make the harness match what rotation_live.py actually does. ──
    # All default OFF so every historical result in reports/ still reproduces byte-for-byte.
    vix_hi_level=0.0,       # >0: second VIX tier (live runs 35 -> 0.5x); 0 = tier absent
    vix_hi_cap=0.5,         # cap applied at the upper tier
    basket_vol=0,           # 1: size exposure off the HELD picks' own vol, not SPY's (live behaviour)
    risk_gate_always=0,     # 1: let a FALLING risk cap de-lever even inside the min-hold window
    # ── Structural knobs (each is a mechanism, not a fitted number) ──
    skip_days=0,            # >0: exclude the most recent N days from the momentum window (12-1)
    vol_ewma_halflife=0,    # >0: EWMA vol estimator with this halflife, replacing the fixed window
    dd_brake=None,          # [(depth,cap),...] portfolio-drawdown brake, e.g. [(0.15,1.0),(0.25,0.5)]
    min_score=0.0,          # absolute-momentum gate; 0.0 = the original "> 0" rule
    lookback_weights=None,  # per-lookback weights, e.g. (0.6,0.4); None = equal blend
    fill_mode=None,         # None = module FILL_MODE; "close" / "next_open" per-run override
    fill_blend=0.0,         # next_open only: 0.0 = the t+1 open, 1.0 = the t+1 close.
                            # b in between prices a partial-session participation
                            # (early-session VWAP proxy) as (1-b)*open + b*close.
    unlev_live_size=0,      # A2c. 1: a pick with NO 2x listing (XLC) is booked at the
                            # UNLEVERED weight while the book is levered, as
                            # trader/rotation.py turbo_allocation Tier 1 does, and the
                            # difference falls through to the defensive sleeve. 0 keeps
                            # exposure-preserving sizing (w*scale in cash), which is what
                            # the volatility overlay means and what the account can fund.
    alloc_log=None,         # optional list: (date, scale, cap_used_pre_def, cap_used_post,
                            # [unleverable picks], weights) per acting rebalance.
    defensive_live=0)       # 1: defensive sleeve takes LEFTOVER capital, unlevered, as
                            # trader/rotation.py turbo_allocation does. 0 keeps the old
                            # harness rule where a defensive fill takes a full weighted
                            # slot and can be routed to UGL/UBT.

def simulate(df,cfg,start,end):
    p={**DEFAULTS,**cfg}
    span=int(p["ema_span"]); top_n=int(p["top_n"])
    mhd=int(p["min_hold_days"]); vt=float(p["vol_target"]); vw=int(p["vol_window"])
    vfloor=float(p["vol_floor"]); vcb=float(p["vol_cap_bull"]); vcbear=float(p["vol_cap_bear"])
    reg_sma=int(p["regime_sma"]); brk_sma=int(p["breaker_sma"]); brk_lvl=float(p["breaker_level"])
    use_lev=bool(p["use_lev"]); lev_topk=int(p["lev_only_topk"]); maxw=float(p["max_weight"])
    wscheme=str(p["weight_scheme"]); def_mom=bool(p["defensive_momentum"])
    cost=float(p["cost_bps"])/10000
    # Optional trade ledger. Pass cfg["trade_log"]=[] to collect
    # (date, symbol, delta_shares, per_share_price_net_of_cost). Default None =
    # no list, no appends, no behaviour change: every historical result still
    # reproduces byte-for-byte. Prices are logged AFTER commission because that
    # is the tax quantity (cost raises basis on a buy, cuts proceeds on a sell).
    tlog=p.get("trade_log")
    # V7 knobs
    ra_vol=int(p["risk_adjust_vol"]); golden=int(p["golden_cross"]); reg_buf=float(p["regime_buffer"])
    def_dyn=int(p["defensive_dynamic"]); tiered=int(p["tiered_caps"]); score_gap=float(p["min_score_gap"])
    # fidelity + structural knobs
    vhi=float(p["vix_hi_level"]); vhi_cap=float(p["vix_hi_cap"])
    use_bvol=int(p["basket_vol"]); risk_always=int(p["risk_gate_always"])
    skip=int(p["skip_days"]); ewma_hl=int(p["vol_ewma_halflife"])
    brake=p["dd_brake"] or []; min_score=float(p["min_score"]); def_live=int(p["defensive_live"])
    unlev_live=int(p["unlev_live_size"]); alog=p.get("alloc_log")
    lbw=p["lookback_weights"]
    lbs=tuple(p["lookback"]) if isinstance(p["lookback"],(list,tuple)) else (int(p["lookback"]),)
    dfc=[s for s in p["defensive"].split("+") if s in df.columns]
    # ensure BIL available for dynamic defensive
    if def_dyn and "BIL" in df.columns and "BIL" not in dfc: dfc=dfc+["BIL"]

    vgl=float(p["vix_gate_level"]); vgc=float(p["vix_gate_cap"]); vsp=float(p["vix_spike_pct"])
    use_vix=(vgl>0 or vsp>0 or vhi>0) and "^VIX" in df.columns

    sec=[s for s in UNIVERSE if s in df.columns]
    lev={s:LEV2X[s] for s in sec+dfc if s in LEV2X and LEV2X[s] in df.columns}
    use3=int(p["use_3x"])
    lev3={s:LEV3X[s] for s in sec if s in LEV3X and LEV3X[s] in df.columns} if use3 else {}
    cols=list(dict.fromkeys(sec+dfc+[SPY_SYM]+list(lev.values())+list(lev3.values())
                            +(["^VIX"] if use_vix else [])))
    d=df[[c for c in cols if c in df.columns]]
    idx=d.index; px={c:d[c].values.astype(float) for c in d.columns}
    spy=px.get(SPY_SYM)
    i0=int(np.searchsorted(idx.values,np.datetime64(start)))
    i1=int(np.searchsorted(idx.values,np.datetime64(end),side="right"))-1
    # Warm-up bars burned after `start` before the book may trade. It defaults to
    # max(lbs)+span+skip+5, which makes the SIMULATION START DATE a function of the
    # lookback: sweeping lb=231 vs lb=232 silently shifts the first traded bar by one
    # session, and that shift alone moves CAGR (measured: 0.25pp — see A2b Part 3b).
    # warmup_bars>0 pins it so a lookback sweep compares signals, not start dates.
    need=int(p.get("warmup_bars") or 0) or (max(lbs)+span+skip+5)

    cash=100000.0; shares={}; curve=[]; last=-999; wins=loss=0; prev=100000.0
    days_in=days_tot=0; lev_days=0; rebal_days=0
    last_cap=1e9; peak=100000.0; derisk_days=0

    def price(s,pos):
        a=px.get(s); v=a[pos] if a is not None else float("nan")
        return None if (a is None or math.isnan(v) or v<=0) else v
    def eq(pos):
        e=cash
        for s,q in shares.items():
            pr=price(s,pos)
            if pr: e+=q*pr
        return e

    fmode=str(p["fill_mode"] or FILL_MODE)
    if fmode not in ("close","next_open"):
        raise ValueError(f"fill_mode must be 'close' or 'next_open', got {fmode!r}")
    blend=float(p["fill_blend"])
    _op=None
    if fmode=="next_open":
        _of=load_opens()
        _op={c:_of[c].values.astype(float) for c in _of.columns}

    def fill_px(s,pos):
        """Price a TRADE. Decisions still use the close at pos; only the
        execution price moves. next_open fills at pos+1 open, which is what an
        order queued after the close actually gets.

        NAMED fill_px, not fill: `fill` is already a local list in this
        function (the defensive-sleeve selection at ~:326), and shadowing it
        made every rebalance raise "'list' object is not callable"."""
        global FILL_FALLBACKS
        if fmode=="next_open" and _op is not None and pos+1<len(idx):
            a=_op.get(s)
            if a is not None:
                v=a[pos+1]
                if not math.isnan(v) and v>0:
                    if blend>0:
                        c1=price(s,pos+1)
                        if c1: v=(1.0-blend)*v+blend*c1
                    return v
            # No open for t+1 (pre-inception 2x sleeves carry synthesized CLOSES
            # only). Fall back to the t+1 CLOSE, not the t close: a missing open
            # is a data hole, not a licence to trade at a price the decision
            # already consumed. Only if t+1 has no price at all do we give up.
            FILL_FALLBACKS+=1
            nxt=price(s,pos+1)
            if nxt: return nxt
        return price(s,pos)

    for pos in range(i0,i1+1):
        e=eq(pos); ds=idx[pos].strftime("%Y-%m-%d"); curve.append((ds,e))
        days_tot+=1
        if e>peak: peak=e
        if any(price(s,pos) for s in shares): days_in+=1
        if pos<i0+need: continue

        # ── min-hold brake. NOT a `continue` any more: the risk cap below is a
        # market-level quantity that costs nothing to evaluate, and gating the
        # de-lever path behind the turnover brake means a VIX spike or a 200-SMA
        # break on day 2 of a 3-day hold cannot reduce exposure at all. The brake
        # exists to suppress *rotation* churn, not to suspend risk control.
        blocked=False
        if last>=i0:
            held_d=((idx[pos]-idx[last]).days if HOLD_UNIT=="calendar" else pos-last)
            if held_d<mhd: blocked=True

        # regime + cap  (market-level: independent of which sectors we pick)
        bull=True
        if reg_sma>0 and spy is not None:
            sv=_sma(spy,pos,reg_sma)
            if sv is not None: bull=spy[pos]>=sv*(1.0+reg_buf)   # buffer = hysteresis around 200SMA
        cap=vcb if bull else vcbear
        # Golden-cross confirmation: only unlock full bull leverage if SMA50>SMA200
        if golden and bull and spy is not None:
            s50=_sma(spy,pos,50); s200=_sma(spy,pos,200)
            if s50 is not None and s200 is not None and s50<=s200:
                cap=min(cap,1.0)
        if brk_sma>0 and spy is not None:
            sv=_sma(spy,pos,brk_sma)
            if sv is not None and spy[pos]<sv: cap=min(cap,brk_lvl)
        # "news fear" gate — VIX as the backtestable proxy for bad-news regimes.
        # Two tiers, high first so the tighter cap wins when both are breached.
        if use_vix:
            vix=px.get("^VIX"); v=vix[pos] if vix is not None else float("nan")
            if not math.isnan(v):
                if vhi>0 and v>=vhi: cap=min(cap,vhi_cap)
                elif vgl>0 and v>=vgl: cap=min(cap,vgc)
                if vsp>0 and pos>=5 and not math.isnan(vix[pos-5]) and vix[pos-5]>0 \
                        and v/vix[pos-5]-1.0>=vsp: cap=min(cap,vgc)
        # external signal gate (e.g. dated news-sentiment series; in-process only)
        if p["ext_gate"]:
            g=p["ext_gate"].get(ds)
            if g is not None: cap=min(cap,float(g))
        # portfolio drawdown brake: a circuit breaker, not a timing signal. It can
        # only lower the cap, and it responds to OUR equity, which the market-level
        # gates above cannot see.
        if brake and peak>0:
            dd=e/peak-1.0
            for depth,lvl in brake:
                if dd<=-abs(depth): cap=min(cap,float(lvl))

        if blocked:
            # Inside the hold window the book may de-risk but may not rotate.
            if not (risk_always and cap<last_cap-1e-9): continue
            derisk_days+=1
        floor_=min(vfloor,cap)

        # score sectors (blended lookback; optionally risk-adjusted by sector vol)
        scores={}        # selection/weight score
        for s in sec:
            m=_blend_mom(px[s],pos,lbs,span,skip,lbw)
            if m is None or m<=min_score: continue
            if ra_vol>0:
                sv_=_sec_vol(px[s],pos,ra_vol)
                if sv_ is None: continue
                scores[s]=m/sv_          # risk-adjusted momentum
            else:
                scores[s]=m
        ranked=sorted(scores.items(),key=lambda x:-x[1])

        held_sec=[s for s in (UNLEV.get(k,k) for k in shares) if s in sec]
        if blocked:
            # de-lever in place: keep what we hold, cut exposure. Re-ranking here
            # would let the hold window be bypassed by any risk event.
            picks=[s for s in held_sec if s in scores][:top_n]
            if not picks: picks=[s for s,_ in ranked[:top_n]]
        elif score_gap>0 and shares:
            # hysteresis: keep current holdings unless a challenger beats them by score_gap
            held=[s for s in scores if any(k==s or LEV2X.get(s)==k for k in shares)]
            picks=[]
            for s,_ in ranked:
                if len(picks)>=top_n: break
                picks.append(s)
            cutoff=scores[picks[-1]] if picks else 0
            # promote incumbents within score_gap of the cutoff
            for s in held:
                if s not in picks and scores.get(s,0)>=cutoff*(1-score_gap):
                    if picks: picks[-1]=s
        else:
            picks=[s for s,_ in ranked[:top_n]]

        # defensive fill (dynamic: swap TLT->BIL when TLT below its 200SMA)
        active_def=list(dfc)
        if def_dyn and "TLT" in active_def:
            tlt=px.get("TLT"); s200=_sma(tlt,pos,200) if tlt is not None else None
            if s200 is not None and tlt[pos]<s200:
                active_def=[("BIL" if x=="TLT" else x) for x in active_def]
            active_def=[x for x in active_def if x!="BIL" or "BIL" in px]
        fill=active_def
        if def_mom and active_def:
            dm=sorted(((s,_blend_mom(px[s],pos,lbs,span,skip,lbw) or -9) for s in active_def),key=lambda x:-x[1])
            fill=[dm[0][0]]
        for dd_ in fill:
            if len(picks)>=top_n: break
            if dd_ not in picks: picks.append(dd_)
        if not picks: continue

        # ── exposure scale. Computed AFTER the picks are known, because the live
        # system sizes against the held basket's own volatility, not SPY's.
        eq_picks=[s for s in picks if s in sec]
        if use_bvol and eq_picks:
            rv=_basket_vol(px,eq_picks,pos,vw,ewma_hl)
            if rv<=0:
                rv=_ewma_vol(spy,pos,ewma_hl) if ewma_hl>0 else _rvol(spy,pos,vw)
        else:
            rv=_ewma_vol(spy,pos,ewma_hl) if ewma_hl>0 else _rvol(spy,pos,vw)
        if rv<=0: rv=0.20
        scale=float(np.clip(vt/rv,floor_,cap))

        # weights (scores already risk-adjusted if ra_vol>0)
        #
        # def_live mirrors trader/rotation.py turbo_allocation: only the EQUITY picks
        # are weighted, and they share an equity budget of n_equity/top_n. The
        # defensive sleeve is not a weighted holding at all — it absorbs whatever
        # capital is left after the equity legs are sized, always unlevered.
        # Without this the harness gives a defensive fill a full equal slot AND can
        # route it to UGL/UBT, which the live allocator has no code path to do.
        w_syms=(eq_picks if (def_live and eq_picks) else picks)
        if wscheme=="equal":
            raw_w={s:1.0 for s in w_syms}
        elif wscheme=="squared":
            raw_w={s:(scores[s]**2 if s in scores else 0.0001) for s in w_syms}
        else:  # return_prop
            raw_w={s:(scores[s] if s in scores else 0.01) for s in w_syms}
        tw=sum(raw_w.values()); weq={s:v/tw for s,v in raw_w.items()}
        budget=1.0
        if def_live and eq_picks and len(picks)>0:
            budget=len(eq_picks)/float(max(top_n,len(picks)))
            weq={s:v*budget for s,v in weq.items()}
        if maxw<1.0:
            # tiered cap: high-beta sectors get maxw, others get maxw+0.05 (iterative renorm)
            def _cap_of(s): return maxw if (tiered and s in HIGH_BETA) else (maxw+0.05 if tiered else maxw)
            for _ in range(20):
                over={s:w for s,w in weq.items() if w>_cap_of(s)+1e-9}
                if not over: break
                for s in over: weq[s]=_cap_of(s)
                excess=1.0-sum(weq.values())
                under={s:w for s,w in weq.items() if w<_cap_of(s)-1e-9}
                pool=sum(under.values())
                if pool<=0: break
                for s in under: weq[s]+=excess*(under[s]/pool)
            _t=sum(weq.values())
            if _t>0: weq={s:v/_t*budget for s,v in weq.items()}

        rebal_days+=1
        if scale>1.05: lev_days+=1

        e_now=eq(pos)
        if e_now<=0: continue
        # rank picks for lev_only_topk
        pick_rank={s:i for i,(s,_) in enumerate(ranked[:top_n])}
        tgt={}; unlev_hit=[]
        for s,w in weq.items():
            notional=w*scale*e_now
            _leverable = (not def_live) or (s in sec)   # live never levers the defensive sleeve
            do_lev3 = (_leverable and scale>2.05 and use_lev and s in lev3 and price(lev3[s],pos))
            do_lev  = (_leverable and scale>1.05 and use_lev and s in lev and price(lev[s],pos))
            if lev_topk>0:
                in_topk = pick_rank.get(s,99) < lev_topk
                do_lev3 = do_lev3 and in_topk
                do_lev  = do_lev and in_topk
            if do_lev3:
                tgt[lev3[s]]=notional/3.0
            elif do_lev:
                tgt[lev[s]]=notional/2.0
            elif price(s,pos):
                # A2c: no 2x listing for this pick (XLC is the only such sector).
                unlev_hit.append(s)
                tgt[s]=(w*e_now if (unlev_live and scale>1.05) else notional)
        inv=sum(tgt.values()); left=e_now-inv
        if left>1 and dfc:
            for dd_ in dfc:
                if price(dd_,pos): tgt[dd_]=tgt.get(dd_,0)+left/len(dfc)
        inv0=inv
        inv=sum(tgt.values())
        if alog is not None:
            alog.append((ds,float(scale),inv0/e_now,inv/e_now,list(unlev_hit),dict(weq)))
        if inv>e_now*1.001:
            f=e_now/inv; tgt={s:v*f for s,v in tgt.items()}
        tsh={}
        for s,dol in tgt.items():
            pr=fill_px(s,pos)
            if pr: tsh[s]=dol/pr
        for s,oq in list(shares.items()):
            nq=tsh.get(s,0.0)
            if nq>=oq-1e-9: continue
            pr=fill_px(s,pos)
            if pr is None: tsh[s]=oq; continue
            cash+=(oq-nq)*pr-(oq-nq)*pr*cost
            if tlog is not None: tlog.append((ds,s,-(oq-nq),pr*(1.0-cost)))
        for s,nq in tsh.items():
            oq=shares.get(s,0.0)
            if nq<=oq+1e-9: continue
            pr=fill_px(s,pos)
            if pr is None: tsh[s]=oq; continue
            cash-=(nq-oq)*pr+(nq-oq)*pr*cost
            if tlog is not None: tlog.append((ds,s,(nq-oq),pr*(1.0+cost)))
        shares={s:q for s,q in tsh.items() if q>1e-9}
        ea=eq(pos)
        if last>=i0:
            if ea>=prev: wins+=1
            else: loss+=1
        prev=ea; last=pos; last_cap=cap

    return {"curve":curve,"wins":wins,"loss":loss,
            "time_in":days_in/days_tot if days_tot else 0,
            "lev_frac":lev_days/rebal_days if rebal_days else 0,
            "rebals":rebal_days,"derisks":derisk_days}

def metrics(r):
    c=[(d,v) for d,v in r["curve"] if not math.isnan(v)]
    if len(c)<2: return {}
    s=pd.Series([x[1] for x in c],index=pd.to_datetime([x[0] for x in c]))
    yrs=(s.index[-1]-s.index[0]).days/365.25
    cagr=(s.iloc[-1]/100000)**(1/yrs)-1
    dr=s.pct_change().dropna()
    sh=float(dr.mean()/dr.std()*np.sqrt(252)) if dr.std()>0 else 0
    dn=dr[dr<0]
    sortino=float(dr.mean()/dn.std()*np.sqrt(252)) if len(dn) and dn.std()>0 else 0
    mdd=float(((s-s.cummax())/s.cummax()).min())
    # monthly win rate
    mo={}
    for ds,v in c: mo[ds[:7]]=v
    k=sorted(mo); mw=sum(1 for i in range(1,len(k)) if mo[k[i]]>mo[k[i-1]])
    motot=len(k)-1
    # per-year
    py={}
    for y in range(s.index[0].year,s.index[-1].year+1):
        ys=s[s.index.year==y]
        if len(ys)>=2: py[y]=float(ys.iloc[-1]/ys.iloc[0]-1)
    pos_yr=sum(1 for v in py.values() if v>0)
    n=r["wins"]+r["loss"]
    calmar=cagr/abs(mdd) if mdd<0 else 0
    return {"cagr":round(cagr,4),"sharpe":round(sh,3),"sortino":round(sortino,3),
            "mdd":round(mdd,4),"calmar":round(calmar,3),"final":round(float(s.iloc[-1])),
            "win_rate_monthly":round(mw/motot if motot else 0,3),
            "win_rate_rebal":round(r["wins"]/n if n else 0,3),
            "win_rate_yearly":round(pos_yr/len(py) if py else 0,3),
            "n_rebals":r["rebals"],"time_in":round(r["time_in"],3),
            "lev_frac":round(r["lev_frac"],3),
            "per_year":{str(y):round(v,4) for y,v in py.items()}}

def spy_bh(df,start,end):
    s=df[SPY_SYM][(df.index>=start)&(df.index<=end)].astype(float)
    c=[(d.strftime("%Y-%m-%d"),100000*float(v)/float(s.iloc[0])) for d,v in s.items()]
    mo={}
    for ds,v in c: mo[ds[:7]]=v
    k=sorted(mo); mw=sum(1 for i in range(1,len(k)) if mo[k[i]]>mo[k[i-1]])
    return {"curve":c,"wins":mw,"loss":len(k)-1-mw,"time_in":1.0,"lev_frac":0,"rebals":0}

def full_eval(df,cfg):
    """All windows + OOS split for a config."""
    out={}
    for name,a,b in WINDOWS:
        out[name]=metrics(simulate(df,cfg,a,b))
    out["insample"]=metrics(simulate(df,cfg,*INSAMPLE))
    out["outsample"]=metrics(simulate(df,cfg,*OUTSAMPLE))
    return out

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",type=str,default="{}")
    ap.add_argument("--batch",type=str,default="")
    ap.add_argument("--label",type=str,default="cfg")
    ap.add_argument("--diagnose",action="store_true")
    ap.add_argument("--fill-mode",dest="fill_mode",choices=["close","next_open"],default=None,
                    help="execution price convention (default: next_open)")
    args=ap.parse_args()
    if args.fill_mode: FILL_MODE=args.fill_mode
    print(f"# FILL_MODE={FILL_MODE}",file=sys.stderr)
    df=load_data()

    if args.diagnose:
        spy={n:metrics(spy_bh(df,a,b)) for n,a,b in WINDOWS}
        print(json.dumps({"spy":spy},indent=2)); sys.exit(0)

    if args.batch:
        configs=json.loads(args.batch)
        res=[]
        for c in configs:
            label=c.pop("_label","cfg")
            res.append({"label":label,"eval":full_eval(df,c)})
        print(json.dumps(res,indent=2))
    else:
        cfg=json.loads(args.config)
        print(json.dumps({"label":args.label,"eval":full_eval(df,cfg)},indent=2))
