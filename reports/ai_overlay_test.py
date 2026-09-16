"""Minimal honest AI-overlay experiment: can a walk-forward ML model boost win rate?

Lowest-possible version:
  - Model: logistic regression (numpy, no deps), predicts P(next month SPY up).
  - Features (all known at month-end t, NO look-ahead): 1m/3m/12m SPY momentum,
    21d realized vol, distance from 200-day SMA.
  - Walk-forward: retrain each year on ALL prior months only, predict that year's
    months. Standardization uses train stats only (no leakage).
  - Overlay: when model says "down" (P<0.5), that month goes flat (cash) instead
    of holding V6D. Costs charged for entering/exiting the overlay.
  - Compares: V6D baseline vs AI-gated V6D, in-sample fit vs out-of-sample reality.

Run: .venv/bin/python reports/ai_overlay_test.py
"""
from __future__ import annotations
import os, sys, math, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from opt_harness import load_data, simulate

df = load_data()
spy = df["SPY"].astype(float)

# ── Build month-end feature rows (no look-ahead: all features use data <= month-end) ──
me = spy.resample("ME").last()
mret = me.pct_change()                       # this month's return
rows = []
for i in range(len(me)):
    dt = me.index[i]
    px_hist = spy[spy.index <= dt]
    if len(px_hist) < 260:
        rows.append(None); continue
    p = px_hist.values
    f_mom1  = p[-1]/p[-21]-1
    f_mom3  = p[-1]/p[-63]-1
    f_mom12 = p[-1]/p[-252]-1
    f_vol   = float(np.std(np.diff(np.log(p[-21:])))*np.sqrt(252))
    f_dist  = p[-1]/np.mean(p[-200:])-1
    rows.append([f_mom1, f_mom3, f_mom12, f_vol, f_dist])

# target: NEXT month's SPY direction (shift -1)
feat, targ, dates = [], [], []
for i in range(len(me)-1):
    if rows[i] is None: continue
    nxt = mret.iloc[i+1]
    if pd.isna(nxt): continue
    feat.append(rows[i]); targ.append(1 if nxt > 0 else 0); dates.append(me.index[i+1])
feat = np.array(feat); targ = np.array(targ); dates = pd.to_datetime(dates)

# ── numpy logistic regression ──
def fit_logreg(X, y, iters=2000, lr=0.1, l2=0.01):
    Xb = np.c_[np.ones(len(X)), X]
    w = np.zeros(Xb.shape[1])
    for _ in range(iters):
        z = Xb @ w
        p = 1/(1+np.exp(-z))
        g = Xb.T @ (p-y)/len(y) + l2*np.r_[0, w[1:]]
        w -= lr*g
    return w
def predict(w, X):
    return 1/(1+np.exp(-(np.c_[np.ones(len(X)), X] @ w)))

# ── walk-forward: train on < year Y, predict year Y ──
years = sorted(set(dates.year))
preds = np.full(len(dates), np.nan)
for Y in years:
    tr = dates.year < Y
    te = dates.year == Y
    if tr.sum() < 36:    # need >=3y history before predicting
        continue
    mu, sd = feat[tr].mean(0), feat[tr].std(0)+1e-9   # train-only standardization
    w = fit_logreg((feat[tr]-mu)/sd, targ[tr])
    preds[te] = predict(w, (feat[te]-mu)/sd)

valid = ~np.isnan(preds)
acc = ((preds[valid] > 0.5).astype(int) == targ[valid]).mean()
base_up = targ[valid].mean()    # base rate: % months SPY is up (always-long accuracy)

# ── overlay on V6D monthly returns ──
V6D = {"top_n":5,"vol_cap_bull":2.0,"vol_target":0.20,"max_weight":0.30}
res = simulate(df, V6D, "2006-01-01", "2024-12-31")
eq = pd.Series([v for _,v in res["curve"]], index=pd.to_datetime([d for d,_ in res["curve"]]))
v6d_m = eq.resample("ME").last().pct_change().dropna()

# align overlay predictions to V6D monthly returns
pred_s = pd.Series(preds, index=dates)
COST = 0.0005
base_rets, ai_rets = [], []
prev_in = True
for dt, r in v6d_m.items():
    key = pred_s.index[(pred_s.index.year==dt.year)&(pred_s.index.month==dt.month)]
    base_rets.append(r)
    if len(key) and not np.isnan(pred_s[key[0]]):
        in_mkt = pred_s[key[0]] > 0.5
    else:
        in_mkt = True
    rr = r if in_mkt else 0.0
    if in_mkt != prev_in: rr -= COST*2      # entering/exiting the overlay
    ai_rets.append(rr); prev_in = in_mkt

def stats(rets):
    rets = pd.Series(rets)
    eq = (1+rets).cumprod()
    yrs = len(rets)/12
    cagr = eq.iloc[-1]**(1/yrs)-1
    wr = (rets > 0).mean()
    dd = ((eq-eq.cummax())/eq.cummax()).min()
    return cagr, wr, dd

b_cagr,b_wr,b_dd = stats(base_rets)
a_cagr,a_wr,a_dd = stats(ai_rets)

# in-sample sanity: fit on ALL data, predict ALL (the overfit/leakage trap)
mu,sd = feat.mean(0), feat.std(0)+1e-9
w_all = fit_logreg((feat-mu)/sd, targ)
is_acc = ((predict(w_all,(feat-mu)/sd)>0.5).astype(int)==targ).mean()

print("="*78)
print("MINIMAL AI-OVERLAY EXPERIMENT (walk-forward logistic regression, SPY timing)")
print("="*78)
print(f"\nDirection-prediction accuracy:")
print(f"  Base rate (always predict UP)     : {base_up:.1%}   <- the number to beat")
print(f"  AI walk-forward (honest, no leak)  : {acc:.1%}")
print(f"  AI in-sample (overfit/leaky trap)  : {is_acc:.1%}   <- looks great, is fake")
print(f"\nStrategy overlay (V6D 2006-2024, costs included):")
print(f"  {'':<22}{'CAGR':>8}{'WinRate':>9}{'MaxDD':>8}")
print(f"  {'V6D baseline':<22}{b_cagr:>+8.1%}{b_wr:>9.0%}{b_dd:>+8.0%}")
print(f"  {'V6D + AI overlay':<22}{a_cagr:>+8.1%}{a_wr:>9.0%}{a_dd:>+8.0%}")
print(f"\n  Win rate change : {a_wr-b_wr:+.1%}")
print(f"  CAGR change     : {a_cagr-b_cagr:+.1%}")
