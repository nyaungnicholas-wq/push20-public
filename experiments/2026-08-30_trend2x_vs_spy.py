"""SPY 200-day trend at 2x: the one thing found that actually beats SPY.

HOW IT WAS FOUND, in order, because the order is what makes it credible:
  1. A PRE-REGISTERED list of 8 classic published allocations was tested once
     (2026-08-30_premia_vs_spy.py). NONE beat SPY on raw CAGR 2006-2026. Every
     one beat SPY in 2006-2011 and lost in all three periods since -- which is
     what defensive diversification does in a bull market.
  2. Three beat SPY on RISK-ADJUSTED terms, so the single textbook
     transformation was applied: lever the higher-Sharpe ones to SPY-equivalent
     risk, WITH financing charged (2026-08-30_levered_riskparity.py).
  3. Two cleared all three bars. This is the stronger of them.
  4. It was then tested on 1993-2005 -- a window used for NONE of the above.

THE OUT-OF-SAMPLE RESULT IS THE POINT. The edge is LARGER on the held-out
period (+6.36pp) than on the period used to select it (+3.43pp). Overfitting
produces the opposite signature: a strong in-sample number that collapses out of
sample. This repo has seen that collapse repeatedly (1.06 -> 0.72 -> not
significant), which is exactly why the held-out test was run.

THE RULE, in full:
  * On the last session of each month, compare SPY's close to its 200-day SMA.
  * Above  -> hold SPY at 2.0x from the next session.
  * Below  -> hold cash at the T-bill rate.
  * Financing on the borrowed half at the T-bill rate + 150bps.
  * 10bps charged on every unit of position change.
  * No parameter is fitted: 200 days, monthly, and 2x were all fixed in advance.

WHAT IS WRONG WITH IT, stated plainly:
  * It LOSES the most recent sub-period (2021-2026, roughly -7pp) -- the 2022
    bear and its sharp recovery is exactly the whipsaw trend-following pays for.
  * The drawdown is still about -46%. Shallower than SPY's -55%, but a levered
    -46% invites a margin call at the worst possible moment.
  * Trend following has well-documented multi-year droughts. Two of the four
    sub-periods here are droughts.
  * A backtest is not a live result.

    .venv/Scripts/python.exe experiments/2026-08-30_trend2x_vs_spy.py
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from trader.data_source import get_data_source  # noqa: E402

SMA_DAYS = 200
LEVERAGE = 2.0
SPREAD = 0.015          # broker margin spread over the bill rate
COST_BPS = 10.0


def run(start, end):
    ds = get_data_source("yfinance")
    raw = ds.history(["SPY", "^IRX"], start, end)
    spy = raw["SPY"]["close"].dropna()
    # ^IRX is the 13-week bill yield in percent; it has the history BIL lacks.
    rf = (raw["^IRX"]["close"].reindex(spy.index).ffill().bfill() / 100.0)
    r = spy.pct_change().fillna(0.0)
    sma = spy.rolling(SMA_DAYS).mean()

    month_end = {g.index[-1] for _, g in
                 pd.Series(1, index=spy.index).groupby([spy.index.year, spy.index.month])}

    # The signal decided at a month-end close applies from the NEXT session.
    # Writing today's position before updating the signal is what enforces it.
    pos, sig = pd.Series(0.0, index=spy.index), None
    for d in spy.index:
        if sig is not None:
            pos.loc[d] = sig * LEVERAGE
        if d in month_end and pd.notna(sma.loc[d]):
            sig = 1.0 if spy.loc[d] > sma.loc[d] else 0.0

    fin = (pos - 1).clip(lower=0) * (rf + SPREAD) / 252
    cash = (1 - pos).clip(lower=0) * rf / 252
    turn = pos.diff().abs().fillna(0.0) * COST_BPS / 1e4
    strat = (1 + pos * r + cash - fin - turn).cumprod()
    return strat, (1 + r).cumprod()


def met(c):
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    rr = c.pct_change().dropna()
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1
    mdd = float((c / c.cummax() - 1).min())
    return {"cagr": cagr, "mdd": mdd,
            "sharpe": float(rr.mean() / rr.std() * np.sqrt(252)),
            "calmar": cagr / abs(mdd) if mdd < 0 else np.nan}


def report(label, s, b):
    a, x = met(s), met(b)
    print(f"\n## {label}   ({s.index[0].date()} -> {s.index[-1].date()})\n")
    print("| strategy | CAGR | MaxDD | Sharpe | Calmar |")
    print("|---|---|---|---|---|")
    print(f"| SPY buy-and-hold | {x['cagr']*100:.1f}% | {x['mdd']*100:.1f}% | "
          f"{x['sharpe']:.2f} | {x['calmar']:.2f} |")
    print(f"| SPY 200d trend {LEVERAGE}x | {a['cagr']*100:.1f}% | {a['mdd']*100:.1f}% | "
          f"{a['sharpe']:.2f} | {a['calmar']:.2f} |")
    print(f"\n=> {(a['cagr']-x['cagr'])*100:+.2f}pp CAGR, drawdown "
          f"{'SHALLOWER' if a['mdd'] > x['mdd'] else 'DEEPER'} "
          f"({a['mdd']*100:.1f}% vs {x['mdd']*100:.1f}%)")
    return a, x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "RESULTS_premia.md"))
    a = ap.parse_args()
    today = pd.Timestamp.today().strftime("%Y-%m-%d")

    s_oos, b_oos = run("1993-01-29", "2005-12-31")
    m_oos, x_oos = report("HELD OUT 1993-2005 (used for NOTHING above)", s_oos, b_oos)

    s_is, b_is = run("2006-01-01", today)
    m_is, x_is = report("IN SAMPLE 2006-now (used to select)", s_is, b_is)

    s_all, b_all = run("1993-01-29", today)
    m_all, x_all = report("FULL 1993-now", s_all, b_all)

    print("\n## Sub-period stability across the whole 33 years\n")
    bounds = pd.date_range(s_all.index[0], s_all.index[-1], periods=7)
    print("| window | strategy CAGR | SPY CAGR | diff |")
    print("|---|---|---|---|")
    wins = 0
    for i in range(6):
        ss, bb = s_all.loc[bounds[i]:bounds[i+1]], b_all.loc[bounds[i]:bounds[i+1]]
        if len(ss) < 60:
            continue
        d = (met(ss)["cagr"] - met(bb)["cagr"]) * 100
        wins += d > 0
        print(f"| {bounds[i].date()}..{bounds[i+1].date()} | "
              f"{met(ss)['cagr']*100:.1f}% | {met(bb)['cagr']*100:.1f}% | {d:+.1f}pp |")
    print(f"\nbeat SPY in {wins}/6 windows")

    print("\n## Verdict\n")
    ok = (m_oos["cagr"] > x_oos["cagr"] and m_is["cagr"] > x_is["cagr"]
          and m_oos["mdd"] > x_oos["mdd"] and m_is["mdd"] > x_is["mdd"])
    print("BEATS SPY on BOTH return and drawdown, in sample AND out of sample."
          if ok else "Does NOT clear the bar in both samples.")
    print(f"Full 1993-now: {m_all['cagr']*100:.1f}% vs SPY {x_all['cagr']*100:.1f}% "
          f"({(m_all['cagr']-x_all['cagr'])*100:+.2f}pp), "
          f"MDD {m_all['mdd']*100:.1f}% vs {x_all['mdd']*100:.1f}%")

    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write(f"- {date.today().isoformat()} trend2x-vs-spy FULL 1993-now: "
                 f"CAGR {m_all['cagr']*100:.1f}% vs SPY {x_all['cagr']*100:.1f}% "
                 f"({(m_all['cagr']-x_all['cagr'])*100:+.2f}pp), "
                 f"MDD {m_all['mdd']*100:.1f}% vs {x_all['mdd']*100:.1f}%, "
                 f"OOS 1993-2005 {(m_oos['cagr']-x_oos['cagr'])*100:+.2f}pp; "
                 f"script experiments/{os.path.basename(__file__)}\n")


if __name__ == "__main__":
    main()
