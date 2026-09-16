"""Indicator-stack experiment — what happens when you pile on TradingView indicators?

Tests the popular belief that stacking many confirmation indicators boosts win rate.
Stacks MACD, Bollinger Bands, RSI, Stochastic, ROC, and a trend filter as ENTRY
GATES on top of the V6A momentum-rotation base, then measures:
  • monthly win rate          (does it go up?)
  • CAGR / Sharpe / MDD       (what does it cost?)
  • # rebalances, time in mkt (why it costs that)

Plus an IN-SAMPLE vs OUT-OF-SAMPLE split to expose overfitting: tune the filter
stack on 2006-2015, then test the SAME settings on 2016-2024.

All indicators are close-based (we only have close prices), which is itself an
honest lesson: half of TradingView's catalogue needs intraday high/low/volume
we don't have, so they can't be faithfully reproduced anyway.

Run: .venv/bin/python reports/indicator_stack.py
"""
from __future__ import annotations
import os, sys, warnings, math
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

from trader.data_source import get_data_source

UNIVERSE  = ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLC","SMH","QQQ"]
DEFENSIVE = ["GLD","TLT"]
SPY_SYM   = "SPY"
LEV2X = {"XLK":"ROM","QQQ":"QLD","SMH":"USD","XLF":"UYG","XLE":"ERX",
         "XLV":"RXL","XLI":"UXI","XLY":"UCC","XLB":"UYM","GLD":"UGL","TLT":"UBT"}

FETCH_START, FETCH_END = "2003-01-01", "2024-12-31"

_DF = None
def load_data():
    global _DF
    if _DF is not None: return _DF
    print("Fetching price data…")
    ds = get_data_source("yfinance")
    syms = list(dict.fromkeys(UNIVERSE + DEFENSIVE + [SPY_SYM] + list(LEV2X.values())))
    raw = ds.history(syms, FETCH_START, FETCH_END)
    frames = {s: d["close"] for s, d in raw.items() if not d.empty}
    _DF = pd.DataFrame(frames).sort_index().ffill().bfill()
    print(f"  {len(_DF)} rows, {len(_DF.columns)} symbols")
    return _DF

# ── Indicators (all close-based) ──────────────────────────────────────────────
def _sma(a, pos, w):
    if pos+1 < w: return None
    v = float(a[pos+1-w:pos+1].mean());  return None if math.isnan(v) else v

def _ema_series(a, pos, span, lookback):
    """EMA value at pos using `lookback` warmup bars."""
    if pos+1 < lookback: return None
    seg = a[pos+1-lookback:pos+1].astype(float)
    alpha = 2.0/(span+1); e = seg[0]
    for v in seg[1:]: e = alpha*v + (1-alpha)*e
    return e

def _rsi(a, pos, period=14):
    need=period+1
    if pos+1<need: return None
    d=np.diff(a[pos+1-need:pos+1].astype(float))
    g=np.where(d>0,d,0.0); l=np.where(d<0,-d,0.0)
    ag,al=g[:period].mean(),l[:period].mean()
    for i in range(period,len(d)):
        ag=(ag*(period-1)+g[i])/period; al=(al*(period-1)+l[i])/period
    return 100.0 if al==0 else 100.0-100.0/(1+ag/al)

def _macd_hist(a, pos):
    """MACD histogram = (EMA12-EMA26) - signal(EMA9 of MACD). Positive = bullish."""
    if pos < 40: return None
    macd_line = []
    for p in range(pos-9, pos+1):
        e12=_ema_series(a,p,12,30); e26=_ema_series(a,p,26,40)
        if e12 is None or e26 is None: return None
        macd_line.append(e12-e26)
    macd = macd_line[-1]
    alpha=2.0/(9+1); sig=macd_line[0]
    for v in macd_line[1:]: sig=alpha*v+(1-alpha)*sig
    return macd - sig

def _bollinger_pctb(a, pos, w=20, k=2.0):
    """%B = (price - lower) / (upper - lower). >0.5 = upper half of band."""
    if pos+1<w: return None
    seg=a[pos+1-w:pos+1].astype(float)
    mid=seg.mean(); sd=seg.std()
    if sd==0: return 0.5
    upper,lower=mid+k*sd, mid-k*sd
    return (a[pos]-lower)/(upper-lower)

def _stoch_close(a, pos, w=14):
    """Close-based stochastic: where is close within its w-day close range."""
    if pos+1<w: return None
    seg=a[pos+1-w:pos+1].astype(float)
    lo,hi=seg.min(),seg.max()
    if hi==lo: return 50.0
    return 100.0*(a[pos]-lo)/(hi-lo)

def _roc(a, pos, w=20):
    if pos<w or a[pos-w]<=0: return None
    return (a[pos]/a[pos-w]-1.0)*100.0

def _williams_r(a, pos, w=14):
    """Close-based Williams %R: 0 = top of range, -100 = bottom."""
    if pos+1<w: return None
    seg=a[pos+1-w:pos+1].astype(float); hi,lo=seg.max(),seg.min()
    if hi==lo: return -50.0
    return -100.0*(hi-a[pos])/(hi-lo)

def _cci(a, pos, w=20):
    """Close-approx CCI (typical price ≈ close). >0 bullish."""
    if pos+1<w: return None
    seg=a[pos+1-w:pos+1].astype(float); sma=seg.mean()
    md=np.mean(np.abs(seg-sma))
    if md==0: return 0.0
    return (a[pos]-sma)/(0.015*md)

def _ema_cross(a, pos):
    """EMA20 > EMA50 → True (bullish trend stack)."""
    e20=_ema_series(a,pos,20,60); e50=_ema_series(a,pos,50,100)
    if e20 is None or e50 is None: return None
    return e20>e50

def _donchian_break(a, pos, w=20):
    """Price within 3% of its 20-day high → near breakout."""
    if pos+1<w: return None
    hi=a[pos+1-w:pos+1].astype(float).max()
    if hi<=0: return None
    return a[pos]>=0.97*hi

def _ema_mom(a, pos, lb, span):
    if pos < lb+span: return None
    raw=[]
    for lag in range(span-1,-1,-1):
        p=pos-lag; base=a[p-lb]
        if base<=0 or math.isnan(base) or math.isnan(a[p]): continue
        raw.append(a[p]/base-1.0)
    if not raw: return None
    if len(raw)==1: return raw[-1]
    alpha=2.0/(span+1); e=raw[0]
    for v in raw[1:]: e=alpha*v+(1-alpha)*e
    return e

def _rvol(spy,pos,w=25):
    if pos<w: return 0.20
    r=np.diff(np.log(spy[pos-w:pos+1].astype(float)))
    v=float(np.std(r)*np.sqrt(252)); return v if v>0 else 0.20

# ── Simulator with toggleable indicator gates ────────────────────────────────
def simulate(df, p, start, end):
    span=9; lb=232; top_n=3
    vt=p.get("vol_target",0.15); vw=25; vfloor=0.5
    vcap_bull=p.get("vol_cap_bull",2.0); vcap_bear=p.get("vol_cap_bear",1.0)
    reg_sma=p.get("regime_sma",200); mhd=3; cost=5/10000; use_lev=True

    # indicator gates (each None = off)
    g_trend = p.get("trend_sma")      # require price > this SMA
    g_rsi   = p.get("rsi_max")        # require RSI < this
    g_macd  = p.get("macd_pos")       # require MACD histogram > 0
    g_boll  = p.get("boll_min")       # require %B > this
    g_stoch = p.get("stoch_max")      # require stochastic < this
    g_roc   = p.get("roc_min")        # require ROC > this
    g_wr    = p.get("wr_min")         # require Williams %R > this
    g_cci   = p.get("cci_min")        # require CCI > this
    g_cross = p.get("ema_cross")      # require EMA20 > EMA50
    g_donch = p.get("donchian")       # require near 20d high breakout

    sec=[s for s in UNIVERSE if s in df.columns]
    dfc=[s for s in DEFENSIVE if s in df.columns]
    lev={s:LEV2X[s] for s in sec+dfc if s in LEV2X and LEV2X[s] in df.columns}
    cols=list(dict.fromkeys(sec+dfc+[SPY_SYM]+list(lev.values())))
    d=df[[c for c in cols if c in df.columns]]
    idx=d.index; px={c:d[c].values.astype(float) for c in d.columns}
    spy=px.get(SPY_SYM)
    i0=int(np.searchsorted(idx.values,np.datetime64(start)))
    i1=int(np.searchsorted(idx.values,np.datetime64(end),side="right"))-1
    need=lb+span+45

    cash=100000.0; shares={}; curve=[]; last=-999; wins=loss=0; prev=100000.0
    days_in=0; days_total=0

    def price(s,pos):
        a=px.get(s);  v=a[pos] if a is not None else float("nan")
        return None if (a is None or math.isnan(v) or v<=0) else v
    def eq(pos):
        e=cash
        for s,q in shares.items():
            pr=price(s,pos)
            if pr: e+=q*pr
        return e

    for pos in range(i0,i1+1):
        e=eq(pos); ds=idx[pos].strftime("%Y-%m-%d"); curve.append((ds,e))
        days_total+=1
        if any(price(s,pos) for s in shares): days_in+=1
        if pos<i0+need or pos-last<mhd: continue

        bull=True
        if reg_sma>0 and spy is not None:
            sv=_sma(spy,pos,reg_sma)
            if sv is not None: bull=spy[pos]>=sv
        vcap=vcap_bull if bull else vcap_bear
        rv=_rvol(spy,pos,vw); scale=float(np.clip(vt/rv if rv>0 else 1.0,vfloor,vcap))

        scores={}
        for s in sec:
            a=px.get(s)
            m=_ema_mom(a,pos,lb,span)
            if m is None or m<=0: continue
            # ── indicator gates ──
            if g_trend:
                sv=_sma(a,pos,g_trend)
                if sv is None or a[pos]<sv: continue
            if g_rsi:
                r=_rsi(a,pos);
                if r is not None and r>g_rsi: continue
            if g_macd:
                h=_macd_hist(a,pos)
                if h is None or h<=0: continue
            if g_boll is not None:
                b=_bollinger_pctb(a,pos)
                if b is None or b<g_boll: continue
            if g_stoch:
                st=_stoch_close(a,pos)
                if st is not None and st>g_stoch: continue
            if g_roc is not None:
                rc=_roc(a,pos)
                if rc is None or rc<g_roc: continue
            if g_wr is not None:
                wr=_williams_r(a,pos)
                if wr is None or wr<g_wr: continue
            if g_cci is not None:
                cc=_cci(a,pos)
                if cc is None or cc<g_cci: continue
            if g_cross:
                xc=_ema_cross(a,pos)
                if not xc: continue
            if g_donch:
                db=_donchian_break(a,pos)
                if not db: continue
            scores[s]=m

        ranked=sorted(scores.items(),key=lambda x:-x[1])
        picks=[s for s,_ in ranked[:top_n]]
        for dd in dfc:
            if len(picks)>=top_n: break
            if dd not in picks: picks.append(dd)
        if not picks: continue

        raw_w={s:(scores[s] if s in scores else 0.01) for s in picks}
        tw=sum(raw_w.values()); weq={s:v/tw for s,v in raw_w.items()}
        e_now=eq(pos)
        if e_now<=0: continue
        tgt={}
        for s,w in weq.items():
            notional=w*scale*e_now
            if scale>1.05 and use_lev and s in lev and price(lev[s],pos):
                tgt[lev[s]]=notional/2.0
            elif price(s,pos): tgt[s]=notional
        inv=sum(tgt.values()); left=e_now-inv
        if left>1 and dfc:
            for dd in dfc:
                if price(dd,pos): tgt[dd]=tgt.get(dd,0)+left/len(dfc)
        inv=sum(tgt.values())
        if inv>e_now*1.001:
            f=e_now/inv; tgt={s:v*f for s,v in tgt.items()}
        tsh={}
        for s,dol in tgt.items():
            pr=price(s,pos)
            if pr: tsh[s]=dol/pr
        for s,oq in list(shares.items()):
            nq=tsh.get(s,0.0)
            if nq>=oq-1e-9: continue
            pr=price(s,pos)
            if pr is None: tsh[s]=oq; continue
            cash+=(oq-nq)*pr - (oq-nq)*pr*cost
        for s,nq in tsh.items():
            oq=shares.get(s,0.0)
            if nq<=oq+1e-9: continue
            pr=price(s,pos)
            if pr is None: tsh[s]=oq; continue
            cash-=(nq-oq)*pr + (nq-oq)*pr*cost
        shares={s:q for s,q in tsh.items() if q>1e-9}
        ea=eq(pos)
        if last>=i0:
            if ea>=prev: wins+=1
            else: loss+=1
        prev=ea; last=pos

    mo={}
    for ds,v in curve: mo[ds[:7]]=v
    k=sorted(mo); mw=sum(1 for i in range(1,len(k)) if mo[k[i]]>mo[k[i-1]])
    return {"curve":curve,"wins":wins,"loss":loss,"mo_wins":mw,"mo_tot":len(k)-1,
            "time_in":days_in/days_total if days_total else 0}

def metrics(r):
    c=[(d,v) for d,v in r["curve"] if not math.isnan(v)]
    if len(c)<2: return {}
    s=pd.Series([x[1] for x in c],index=pd.to_datetime([x[0] for x in c]))
    yrs=(s.index[-1]-s.index[0]).days/365.25
    cagr=(s.iloc[-1]/100000)**(1/yrs)-1
    dr=s.pct_change().dropna()
    sh=float(dr.mean()/dr.std()*np.sqrt(252)) if dr.std()>0 else 0
    mdd=float(((s-s.cummax())/s.cummax()).min())
    n=r["wins"]+r["loss"]
    return {"cagr":cagr,"sharpe":sh,"mdd":mdd,"final":float(s.iloc[-1]),
            "wr_mo":r["mo_wins"]/r["mo_tot"] if r["mo_tot"] else 0,
            "wr_rb":r["wins"]/n if n else 0,"n":n,"time_in":r["time_in"]}

def spy_bh(df,start,end):
    s=df[SPY_SYM][(df.index>=start)&(df.index<=end)].astype(float)
    c=[(d.strftime("%Y-%m-%d"),100000*float(v)/float(s.iloc[0])) for d,v in s.items()]
    mo={}
    for ds,v in c: mo[ds[:7]]=v
    k=sorted(mo); mw=sum(1 for i in range(1,len(k)) if mo[k[i]]>mo[k[i-1]])
    return {"curve":c,"wins":mw,"loss":len(k)-1-mw,"mo_wins":mw,"mo_tot":len(k)-1,"time_in":1.0}

def row(label,m,sm):
    beat="✓" if m["cagr"]>sm["cagr"] else "✗"
    print(f"  {label:<34} CAGR {m['cagr']:+6.1%}  MDD {m['mdd']:6.1%}  Sh {m['sharpe']:.2f}  "
          f"WinRate(mo) {m['wr_mo']:.0%}  InMkt {m['time_in']:.0%}  #{m['n']:>4}  {beat}")

V6A      = dict(vol_target=0.15, vol_cap_bull=2.0, vol_cap_bear=1.0, regime_sma=200)
PLUS1    = {**V6A, "rsi_max":70}
PLUS2    = {**PLUS1, "macd_pos":True}
PLUS3    = {**PLUS2, "trend_sma":100}
PLUS4    = {**PLUS3, "boll_min":0.0}
KITCHEN  = {**V6A, "rsi_max":65, "macd_pos":True, "trend_sma":100,
            "boll_min":0.2, "stoch_max":80, "roc_min":0.0}
# All 10 indicators stacked as AND-gates on entry:
# 1 RSI  2 MACD  3 Trend-SMA  4 Bollinger  5 Stochastic  6 ROC
# 7 Williams%R  8 CCI  9 EMA20>EMA50 cross  10 Donchian breakout
TEN = {**V6A, "rsi_max":65, "macd_pos":True, "trend_sma":100, "boll_min":0.2,
       "stoch_max":80, "roc_min":0.0, "wr_min":-50.0, "cci_min":0.0,
       "ema_cross":True, "donchian":True}

CONFIGS = [
    ("V6A base (no extra indicators)", V6A),
    ("+RSI<70", PLUS1),
    ("+RSI +MACD", PLUS2),
    ("+RSI +MACD +Trend", PLUS3),
    ("+RSI +MACD +Trend +Bollinger", PLUS4),
    ("KITCHEN SINK (all 6 indicators)", KITCHEN),
]

if __name__=="__main__":
    df=load_data()
    print("\n"+"="*104)
    print("INDICATOR-STACK EXPERIMENT — does piling on indicators boost win rate? (2006–2024, 5bps/side)")
    print("="*104)
    sm=metrics(spy_bh(df,"2006-01-01","2024-12-31"))
    print(f"  {'SPY buy & hold':<34} CAGR {sm['cagr']:+6.1%}  MDD {sm['mdd']:6.1%}  Sh {sm['sharpe']:.2f}  "
          f"WinRate(mo) {sm['wr_mo']:.0%}  InMkt 100%")
    print()
    for label,cfg in CONFIGS:
        m=metrics(simulate(df,cfg,"2006-01-01","2024-12-31"))
        if m: row(label,m,sm)

    # ── Overfitting demo: tune in-sample, test out-of-sample ──────────────────
    print("\n"+"="*104)
    print("OVERFITTING DEMO — same kitchen-sink settings, in-sample (2006-15) vs out-of-sample (2016-24)")
    print("="*104)
    for label,cfg in [("V6A base",V6A),("KITCHEN SINK (6 indicators)",KITCHEN)]:
        ins=metrics(simulate(df,cfg,"2006-01-01","2015-12-31"))
        oos=metrics(simulate(df,cfg,"2016-01-01","2024-12-31"))
        sin=metrics(spy_bh(df,"2006-01-01","2015-12-31"))
        sos=metrics(spy_bh(df,"2016-01-01","2024-12-31"))
        print(f"\n  {label}")
        print(f"    IN-SAMPLE  2006-15:  CAGR {ins['cagr']:+6.1%}  WinRate {ins['wr_mo']:.0%}  "
              f"Sharpe {ins['sharpe']:.2f}   (SPY {sin['cagr']:+.1%})")
        print(f"    OUT-SAMPLE 2016-24:  CAGR {oos['cagr']:+6.1%}  WinRate {oos['wr_mo']:.0%}  "
              f"Sharpe {oos['sharpe']:.2f}   (SPY {sos['cagr']:+.1%})")
