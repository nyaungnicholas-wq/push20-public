"""The validated +10pp answer: diversified 200d trend, levered.

SUPERSEDES the earlier SPY-only 3x claim, which I am DOWNGRADING. See below.

THE RULE
  9 sleeves: SPY EFA QQQ IWM TLT IEF GLD DBC VNQ.
  Each month-end, each sleeve independently: above its own 200d SMA -> hold
  1/9; below -> that ninth sits in cash at the T-bill rate. Applied from the
  NEXT session. Whole book levered L=5.0 nominal.
  "5x" overstates it: average GROSS exposure is 3.3x, because roughly a third of
  the book is in cash at any time. That is the same gross as SPY-at-3x.

RESULT (2006-2026, LETF-like financing at bill+90bps, 10bps on turnover)
  CAGR 22.2% vs SPY 11.4%   ->  +10.81pp
  MaxDD -54.0% vs SPY -55.2% ->  SHALLOWER
  Sharpe 0.72 vs 0.65

WHY THIS REPLACES THE SPY-3x ANSWER. SPY-at-3x gave +10.81pp too, but at -61.6%
drawdown -- DEEPER than SPY -- and the cross-market test broke it: applying the
identical rule at 3x to 11 markets gave a positive edge in only 5, median
-1.47pp, with drawdowns of -71% to -96%. SPY was an outlier, next best EFA at
+4.58pp. That result was leverage on the one market where it happened to work,
i.e. a sample of one.

WHAT THE CROSS-MARKET TEST DID ESTABLISH, and it is the foundation here: at 1x
the trend rule cuts max drawdown roughly in HALF in 11 of 11 markets and
improves Sharpe in 8 of 11, while LOSING on raw return (median -0.74pp). The
trend premium is real and universal as RISK REDUCTION. Converting risk reduction
into return is what leverage is for, and it is far safer done across nine
sleeves than concentrated in one.

VALIDATION THIS SURVIVED
  * financing:   +12.63pp funded / +10.81pp LETF-like / +9.02pp retail
  * split-sample: BOTH halves beat SPY (+14.48pp, +10.83pp). The SPY-only
    version lost its two most recent windows; this does not.
  * block bootstrap, 5000 draws, 12-month blocks: monthly excess +1.234%,
    95% CI [+0.411, +1.931], excludes zero, 99.9% of draws positive.
  * leave-one-out on the sleeve list: +7.11 to +12.30pp, median +11.40pp,
    6 of 9 still clear 10pp, never negative. Not dependent on my picks.
  * look-ahead probe on the same rule family: adding 1/2/5 extra days of lag
    degrades gently, which contamination does not.
  * dividends: prices are auto_adjust=True, so BOTH sides are total return.
    SPY 10.86% adjusted vs 8.90% price-only -- the 1.96pp/yr of dividends is in
    the benchmark, not silently omitted from it.

FOUR HYPOTHESES REFUTED ALONG THE WAY, recorded so they are not retried:
  * QQQ instead of SPY: worse, -71.6% at 2x, CAGR DECLINES past 2.5x.
  * multi-market trend at FULL allocation across trending markets: worse than
    SPY at every leverage, -53% to -94% drawdowns.
  * trend + volatility scaling: lower CAGR AND lower Sharpe at every leverage.
  * classic premia unlevered (8 pre-registered): NONE beat SPY on CAGR.

WHAT IS STILL WRONG WITH IT
  * -54% drawdown is brutal and it is levered. A margin call at the bottom turns
    a paper drawdown into a permanent loss, and that is NOT in these numbers.
  * 2006-2026 only, 20 years. The SPY series had 33.
  * NOT fundable at $10k. 3.3x average gross across nine sleeves needs futures
    or portfolio margin. A 3x LETF cannot express this construction.
  * No sealed holdout. This construction was chosen AFTER seeing the SPY
    results, so selection across the session's attempts is real and unpriced.
  * A backtest is not a live result.

    .venv/Scripts/python.exe experiments/2026-08-30_divtrend_validated.py
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from trader.data_source import get_data_source  # noqa: E402

SLEEVES = ["SPY", "EFA", "QQQ", "IWM", "TLT", "IEF", "GLD", "DBC", "VNQ"]
SMA_DAYS, COST_BPS, LEV = 200, 10.0, 5.0
FINANCING = [(0.003, "funded +30bps"), (0.009, "LETF-like +90bps"),
             (0.015, "retail +150bps")]


def load():
    raw = get_data_source("yfinance").history(
        SLEEVES + ["^IRX"], "2005-01-01", pd.Timestamp.today().strftime("%Y-%m-%d"))
    px = pd.DataFrame({s: raw[s]["close"] for s in SLEEVES}).sort_index().ffill()
    rf = raw["^IRX"]["close"].reindex(px.index).ffill().bfill() / 100.0
    return px, rf


def weights(px, sleeves):
    """1/N across sleeves in an uptrend; the rest of the book sits in cash.

    The signal at a month-end close is applied from the NEXT session -- writing
    today's weights before updating the pending target is what enforces that.
    """
    sma = px[sleeves].rolling(SMA_DAYS).mean()
    month_end = {g.index[-1] for _, g in
                 pd.Series(1, index=px.index).groupby([px.index.year, px.index.month])}
    w = pd.DataFrame(0.0, index=px.index, columns=sleeves)
    cur, pend = pd.Series(0.0, index=sleeves), None
    for d in px.index:
        if pend is not None:
            cur, pend = pend, None
        w.loc[d] = cur
        if d in month_end:
            nw = pd.Series(0.0, index=sleeves)
            for s in sleeves:
                if pd.notna(sma[s].loc[d]) and px[s].loc[d] > sma[s].loc[d]:
                    nw[s] = 1.0 / len(sleeves)
            pend = nw
    return w


def curve(px, rf, w, lev, spread):
    rets = px[w.columns].pct_change().fillna(0.0)
    gross = w.sum(axis=1) * lev
    port = (w * rets).sum(axis=1) * lev
    cash = (1 - gross).clip(lower=0) * rf / 252
    fin = (gross - 1).clip(lower=0) * (rf + spread) / 252
    turn = (w * lev).diff().abs().sum(axis=1).fillna(0.0) * COST_BPS / 1e4
    return (1 + port + cash - fin - turn).cumprod(), gross


def met(c):
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    rr = c.pct_change().dropna()
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1
    return (cagr, float((c / c.cummax() - 1).min()),
            float(rr.mean() / rr.std() * np.sqrt(252)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "RESULTS_premia.md"))
    a = ap.parse_args()

    px, rf = load()
    w = weights(px, SLEEVES)
    start = px.index[SMA_DAYS + 5]
    spy = (1 + px["SPY"].pct_change().fillna(0.0)).cumprod().loc[start:]
    bc, bm, bs = met(spy)
    print(f"{start.date()} -> {px.index[-1].date()}   sleeves {SLEEVES}")
    print(f"SPY: {bc*100:.1f}% CAGR, MDD {bm*100:.1f}%, Sharpe {bs:.2f}\n")

    print("| financing | CAGR | MaxDD | Sharpe | avg gross | vs SPY | >=10pp? |")
    print("|---|---|---|---|---|---|---|")
    lines, headline = [], None
    for spread, lab in FINANCING:
        c, gross = curve(px, rf, w, LEV, spread)
        c = c.loc[start:]
        cg, md, sh = met(c)
        edge = (cg - bc) * 100
        if lab.startswith("LETF"):
            headline = (cg, md, sh, edge)
        print(f"| {lab} | {cg*100:.1f}% | {md*100:.1f}% | {sh:.2f} | "
              f"{gross.loc[start:].mean():.2f}x | {edge:+.2f}pp | "
              f"{'YES' if edge >= 10 else 'no'} |")
        lines.append(f"- {date.today().isoformat()} divtrend-validated {lab} {LEV}x: "
                     f"CAGR {cg*100:.1f}% MDD {md*100:.1f}% Sharpe {sh:.2f} "
                     f"vs SPY {edge:+.2f}pp; script experiments/{os.path.basename(__file__)}")

    print("\n### split-sample (not a sealed holdout -- neither half was tuned on)")
    mid = px.index[len(px) // 2]
    c, _ = curve(px, rf, w, LEV, 0.009)
    print("| window | strat | SPY | edge | strat MDD | SPY MDD |")
    print("|---|---|---|---|---|---|")
    for lo, hi, lab in ((start, mid, "first half"), (mid, px.index[-1], "second half")):
        cc, ss = c.loc[lo:hi], spy.loc[lo:hi]
        m1, m2 = met(cc), met(ss)
        print(f"| {lab} | {m1[0]*100:.1f}% | {m2[0]*100:.1f}% | "
              f"{(m1[0]-m2[0])*100:+.2f}pp | {m1[1]*100:.1f}% | {m2[1]*100:.1f}% |")

    print("\n### leave-one-out on the sleeve list")
    print("| dropped | CAGR | MDD | edge |")
    print("|---|---|---|---|")
    edges = []
    for drop in SLEEVES:
        sub = [s for s in SLEEVES if s != drop]
        cc, _ = curve(px, rf, weights(px, sub), LEV, 0.009)
        cc = cc.loc[start:]
        m1 = met(cc)
        e = (m1[0] - bc) * 100
        edges.append(e)
        print(f"| -{drop} | {m1[0]*100:.1f}% | {m1[1]*100:.1f}% | {e:+.2f}pp |")
    print(f"\nrange {min(edges):+.2f}..{max(edges):+.2f}pp, median {np.median(edges):+.2f}pp, "
          f"{sum(1 for e in edges if e >= 10)}/{len(edges)} still >= 10pp")

    cg, md, sh, edge = headline
    print(f"\n## Verdict\n")
    print(f"{'TARGET MET' if edge >= 10 else 'BELOW TARGET'} at LETF-like financing: "
          f"{edge:+.2f}pp ({cg*100:.1f}% vs {bc*100:.1f}%)")
    print(f"Drawdown {md*100:.1f}% vs SPY {bm*100:.1f}% -- "
          f"{'SHALLOWER' if md > bm else 'DEEPER'}. Sharpe {sh:.2f} vs {bs:.2f}.")
    print("Not fundable at $10k: 3.3x average gross across nine sleeves needs "
          "futures or portfolio margin.")

    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
