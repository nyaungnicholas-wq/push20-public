"""Follow-up: can a HIGHER-SHARPE strategy, levered to SPY's risk, beat SPY?

PRE-REGISTERED BEFORE RUNNING. The prior run
(2026-08-30_premia_vs_spy.py) found that no classic allocation beats SPY on raw
CAGR 2006-2026 -- every one of them beat SPY in 2006-2011 and lost in all three
periods since, which is what defensive diversification does in a bull market.
But three beat SPY on RISK-ADJUSTED terms, and Inverse-vol 4 did so by a lot:
Sharpe 0.83 vs 0.64, max drawdown -20.5% vs -55.2%.

That is the textbook risk-parity claim: a higher Sharpe at lower risk can be
levered up to the SAME risk and should then deliver a higher return. This tests
it, and it is the ONE transformation being applied -- not a search.

THE TEST IS ONLY HONEST WITH FINANCING COSTS. Leverage without a borrow rate is
free money and every levered curve wins. Financing is charged on the borrowed
fraction at the cash rate implied by BIL, plus a broker spread.

WHAT WOULD MAKE THIS FAIL, stated in advance:
  * levered drawdown blowing past SPY's -55.2% (risk-parity's 2022 problem:
    stocks and bonds fell together, so the vol estimate understated the risk)
  * the CAGR edge living in one sub-period, like the unlevered version did
  * needing leverage above 3x, which is not fundable at these costs

A BACKTEST IS NOT A LIVE RESULT.

    .venv/Scripts/python.exe experiments/2026-08-30_levered_riskparity.py
"""
import argparse
import importlib.util
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# reuse the prior script's data loading, strategies and simulator verbatim --
# re-implementing them would let the two runs quietly diverge.
_spec = importlib.util.spec_from_file_location(
    "premia", os.path.join(HERE, "2026-08-30_premia_vs_spy.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

BROKER_SPREAD_BPS = 150.0     # over cash, a realistic retail margin spread
MAX_LEVERAGE = 3.0
P_COST_BPS = 10.0        # cost charged on each unit of leverage change


def cash_rate(close):
    """Annualised short rate implied by BIL's own total return."""
    r = close["BIL"].pct_change().fillna(0.0)
    return r.rolling(252).mean() * 252


def lever(curve, close, target_vol=None, fixed=None, lb=60):
    """Lever a strategy's daily returns, charging financing on the borrowed part.

    Leverage is set from TRAILING volatility only -- using the full-sample vol
    would be look-ahead, and would be the single easiest way to fake this result.
    """
    r = curve.pct_change().fillna(0.0)
    rf = cash_rate(close).reindex(r.index).ffill().fillna(0.0)
    vol = r.rolling(lb).std() * np.sqrt(252)

    if fixed is not None:
        L = pd.Series(float(fixed), index=r.index)
    else:
        L = (target_vol / vol).clip(upper=MAX_LEVERAGE)
    L = L.shift(1).fillna(1.0).clip(lower=0.0)      # yesterday's estimate only

    borrowed = (L - 1.0).clip(lower=0.0)
    fin = borrowed * (rf + BROKER_SPREAD_BPS / 1e4) / 252
    # Changing leverage is a TRADE and costs money. Omitting this was a real
    # error in the first run: the vol-matched variant re-levers every single
    # day, so a free leverage knob quietly handed it a daily rebalance nobody
    # paid for. The fixed-leverage variants are unaffected (|dL| = 0).
    lev_turnover = L.diff().abs().fillna(0.0)
    lr = L * r - fin - lev_turnover * P_COST_BPS / 1e4
    return (1 + lr).cumprod() * 100000.0, L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "RESULTS_premia.md"))
    a = ap.parse_args()

    close = P.load_close().dropna(subset=["SPY"])
    dates = P.month_ends(close.index)
    curves = {n: P.simulate(close, f(close, dates)) for n, f in P.STRATEGIES}
    start = close.index[P.MOM_LB + 5]
    curves = {k: v.loc[start:] / v.loc[start] * 100000.0 for k, v in curves.items()}

    spy_curve = curves["SPY buy-and-hold"]
    spy_m = P.metrics(spy_curve)
    spy_vol = spy_curve.pct_change().std() * np.sqrt(252)
    print(f"period {start.date()} -> {close.index[-1].date()}")
    print(f"SPY: CAGR {spy_m['cagr']*100:.1f}%  MDD {spy_m['mdd']*100:.1f}%  "
          f"Sharpe {spy_m['sharpe']:.2f}  realised vol {spy_vol*100:.1f}%")
    print(f"financing: BIL-implied cash + {BROKER_SPREAD_BPS:.0f}bps, "
          f"leverage capped at {MAX_LEVERAGE}x\n")

    # only the strategies that actually beat SPY's Sharpe unlevered qualify
    cands = [n for n in curves
             if n != "SPY buy-and-hold" and P.metrics(curves[n])["sharpe"] > spy_m["sharpe"]]
    print(f"qualifying (unlevered Sharpe > SPY's {spy_m['sharpe']:.2f}): "
          f"{', '.join(cands) or 'NONE'}\n")

    print("| Strategy | Leverage | CAGR | MaxDD | Sharpe | avg L | vs SPY |")
    print("|---|---|---|---|---|---|---|")
    print(f"| SPY buy-and-hold | 1.0x | {spy_m['cagr']*100:.1f}% | "
          f"{spy_m['mdd']*100:.1f}% | {spy_m['sharpe']:.2f} | 1.00 | +0.00pp |")

    lines, results = [], []
    for n in cands:
        for tag, kw in (("vol-matched", {"target_vol": spy_vol}),
                        ("2.0x fixed", {"fixed": 2.0})):
            lc, L = lever(curves[n], close, **kw)
            m = P.metrics(lc)
            vs = (m["cagr"] - spy_m["cagr"]) * 100
            print(f"| {n} | {tag} | {m['cagr']*100:.1f}% | {m['mdd']*100:.1f}% | "
                  f"{m['sharpe']:.2f} | {L.mean():.2f} | {vs:+.2f}pp |")
            results.append((n, tag, m, L.mean(), vs))
            lines.append(
                f"- {date.today().isoformat()} levered-premia {n} [{tag}]: "
                f"CAGR {m['cagr']*100:.1f}% MDD {m['mdd']*100:.1f}% "
                f"Sharpe {m['sharpe']:.2f} avgL {L.mean():.2f} vs SPY {vs:+.2f}pp")

    # sub-period stability, the check the unlevered run failed
    bounds = pd.date_range(start, close.index[-1], periods=5)
    print(f"\n## Sub-period CAGR minus SPY (pp)\n")
    print("| Strategy | " + " | ".join(
        f"{bounds[i].date()}..{bounds[i+1].date()}" for i in range(4)) + " | beat SPY in |")
    print("|---|" + "---|" * 5)
    passed = []
    for n, tag, m, avgL, vs in results:
        lc, _ = lever(curves[n], close,
                      **({"target_vol": spy_vol} if tag == "vol-matched" else {"fixed": 2.0}))
        cells, wins = [], 0
        for i in range(4):
            seg, sseg = lc.loc[bounds[i]:bounds[i+1]], spy_curve.loc[bounds[i]:bounds[i+1]]
            if len(seg) < 20:
                cells.append("n/a"); continue
            d = (P.metrics(seg)["cagr"] - P.metrics(sseg)["cagr"]) * 100
            wins += d > 0
            cells.append(f"{d:+.1f}")
        print(f"| {n} [{tag}] | " + " | ".join(cells) + f" | {wins}/4 |")
        if vs > 0 and wins >= 3 and m["mdd"] > spy_m["mdd"]:
            passed.append((n, tag, m, wins))

    print("\n## Verdict\n")
    if passed:
        for n, tag, m, w in passed:
            print(f"BEATS SPY: {n} [{tag}] -- CAGR {m['cagr']*100:.1f}% vs "
                  f"{spy_m['cagr']*100:.1f}%, MDD {m['mdd']*100:.1f}% vs "
                  f"{spy_m['mdd']*100:.1f}% (shallower), {w}/4 sub-periods")
    else:
        print("NO levered variant clears all three bars: higher CAGR than SPY, "
              "a drawdown no worse than SPY's, and 3 of 4 sub-periods.")

    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nappended {len(lines)} line(s) to {a.out}")


if __name__ == "__main__":
    main()
