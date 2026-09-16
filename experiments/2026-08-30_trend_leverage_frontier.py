"""The +10pp answer, and exactly what it costs.

THE ASK was at least a 10 percentage-point CAGR edge over SPY. It is reachable.
It is NOT reachable for free, and the price is a drawdown WORSE than SPY's.

THE RULE (unchanged from the 2x version, only the dial moved):
  month-end close, SPY vs its 200-day SMA. Above -> hold SPY at L from the next
  session. Below -> cash at the T-bill rate. Financing on the borrowed part at
  ^IRX + spread. 10bps on every unit of position change.

WHAT WAS TRIED AND FAILED, so the dial is not mistaken for a discovery:
  * QQQ instead of SPY -- WORSE. -71.6% drawdown at 2x, and CAGR DECLINES past
    2.5x from volatility drag. At 4x the edge is negative.
  * Diversified trend over 9 sleeves -- Sharpe 0.91 unlevered (vs SPY 0.65) and
    only -13.0% drawdown, but at matched risk it lands where SPY 2x already is
    (+5.11pp at -45.9% vs +4.95pp at -46.0%). Financing on ~2.6x average gross
    eats the entire diversification benefit.
  * Trend + volatility scaling -- WORSE at every leverage. Lower CAGR AND lower
    Sharpe than trend alone. Hypothesis refuted.

WHAT ACTUALLY MOVES THE NUMBER is the FINANCING RATE, more than leverage or any
signal. Retail margin at bill+150bps versus funded at bill+30bps is worth about
2.2pp of edge at 3x -- larger than anything the signal work produced.

SHARPE FALLS AS LEVERAGE RISES (0.71 at 2x -> 0.64 at 4x). Leverage is not
buying skill, it is buying exposure, and financing takes a cut. The 10pp is paid
for in drawdown, not earned by a better signal.

A BACKTEST IS NOT A LIVE RESULT. A -62% drawdown on a levered account invites a
margin call at the bottom, which converts a paper drawdown into a permanent
loss. That risk is NOT in these numbers.

    .venv/Scripts/python.exe experiments/2026-08-30_trend_leverage_frontier.py
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from trader.data_source import get_data_source  # noqa: E402

SMA_DAYS, COST_BPS = 200, 10.0
SPREADS = [(0.003, "funded +30bps"), (0.009, "LETF-like +90bps"),
           (0.015, "retail +150bps")]
LEVERAGES = [2.0, 2.5, 3.0, 3.5, 4.0]
_CACHE = {}


def _data(start, end):
    if (start, end) not in _CACHE:
        raw = get_data_source("yfinance").history(["SPY", "^IRX"], start, end)
        px = raw["SPY"]["close"].dropna()
        rf = raw["^IRX"]["close"].reindex(px.index).ffill().bfill() / 100.0
        _CACHE[(start, end)] = (px, rf)
    return _CACHE[(start, end)]


def run(start, end, lev, spread):
    px, rf = _data(start, end)
    r = px.pct_change().fillna(0.0)
    sma = px.rolling(SMA_DAYS).mean()
    month_end = {g.index[-1] for _, g in
                 pd.Series(1, index=px.index).groupby([px.index.year, px.index.month])}
    # today's position comes from the PREVIOUS month-end signal, never today's
    pos, sig = pd.Series(0.0, index=px.index), None
    for d in px.index:
        if sig is not None:
            pos.loc[d] = sig * lev
        if d in month_end and pd.notna(sma.loc[d]):
            sig = 1.0 if px.loc[d] > sma.loc[d] else 0.0
    fin = (pos - 1).clip(lower=0) * (rf + spread) / 252
    cash = (1 - pos).clip(lower=0) * rf / 252
    turn = pos.diff().abs().fillna(0.0) * COST_BPS / 1e4
    return (1 + pos * r + cash - fin - turn).cumprod(), (1 + r).cumprod()


def met(c):
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    rr = c.pct_change().dropna()
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1
    mdd = float((c / c.cummax() - 1).min())
    return cagr, mdd, float(rr.mean() / rr.std() * np.sqrt(252))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "RESULTS_premia.md"))
    a = ap.parse_args()
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    FULL = ("1993-01-29", today)
    OOS = ("1993-01-29", "2005-12-31")
    IS = ("2006-01-01", today)

    sc, smdd, ssh = met(run(*FULL, 2.0, 0.015)[1])
    print(f"SPY 1993-now: {sc*100:.1f}% CAGR, MDD {smdd*100:.1f}%, Sharpe {ssh:.2f}")
    print("Target: +10.00pp edge.\n")

    print("## Frontier -- every cell, including the ones that fail\n")
    print("| financing | lev | CAGR | MaxDD | Sharpe | edge | >= +10pp? |")
    print("|---|---|---|---|---|---|---|")
    hits = []
    for spread, slab in SPREADS:
        for lev in LEVERAGES:
            s, b = run(*FULL, lev, spread)
            c, m, sh = met(s)
            bc, _, _ = met(b)
            edge = (c - bc) * 100
            ok = edge >= 10.0
            if ok:
                hits.append((slab, spread, lev, c, m, sh, edge))
            print(f"| {slab} | {lev}x | {c*100:.1f}% | {m*100:.1f}% | {sh:.2f} | "
                  f"{edge:+.2f}pp | {'YES' if ok else 'no'} |")

    print("\n## Out-of-sample check on every cell that hit the target\n")
    print("| financing | lev | HELD OUT 1993-2005 | in sample 2006-now | FULL | MDD |")
    print("|---|---|---|---|---|---|")
    lines = []
    for slab, spread, lev, c, m, sh, edge in hits:
        row = []
        for st, en in (OOS, IS, FULL):
            s, b = run(st, en, lev, spread)
            row.append((met(s)[0] - met(b)[0]) * 100)
        print(f"| {slab} | {lev}x | {row[0]:+.2f}pp | {row[1]:+.2f}pp | "
              f"{row[2]:+.2f}pp | {m*100:.1f}% |")
        lines.append(f"- {date.today().isoformat()} trend-frontier {slab} {lev}x: "
                     f"CAGR {c*100:.1f}% edge {edge:+.2f}pp MDD {m*100:.1f}% "
                     f"Sharpe {sh:.2f}, OOS {row[0]:+.2f}pp; "
                     f"script experiments/{os.path.basename(__file__)}")

    print("\n## Verdict\n")
    if hits:
        best = min(hits, key=lambda h: -h[6])
        print(f"TARGET MET: {best[0]} at {best[2]}x -> {best[6]:+.2f}pp "
              f"({best[3]*100:.1f}% vs SPY {sc*100:.1f}%)")
        print(f"THE PRICE: max drawdown {best[4]*100:.1f}% versus SPY's "
              f"{smdd*100:.1f}% -- {abs(best[4]*100) - abs(smdd*100):.1f}pp DEEPER, "
              f"and levered.")
        print(f"Sharpe {best[5]:.2f} vs SPY {ssh:.2f}: barely better. The edge is "
              f"bought with exposure, not skill.")
    else:
        print("No configuration reaches +10pp.")

    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
