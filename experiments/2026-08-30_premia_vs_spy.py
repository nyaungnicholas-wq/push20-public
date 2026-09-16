"""Do any classic, published allocation strategies actually beat SPY here?

WHY THIS SHAPE. This repo's history is 1,414 configs with no replicating
survivor, and its own README concludes "the tradeable edge on operating
companies is not established". Searching the same engine again reproduces that.
So this tests a **pre-registered** list of strategies that each carry decades of
published out-of-sample evidence from outside this dataset. The list is fixed
before the first run and NO parameter is tuned. A strategy either clears the bar
as specified or it does not.

A BACKTEST IS NOT A LIVE RESULT.

THREE CORRECTNESS RULES, because each was got wrong in the first draft:
  * Decisions use data up to and including the rebalance close; the weights take
    effect the NEXT session. Earning the rebalance day's return on weights
    chosen from that same day's close is look-ahead, and it flatters trend
    strategies most.
  * Rebalances are MONTHLY, on the last session of each month. Emitting weights
    every day silently turns these into daily strategies -- a 200-day trend
    filter whipsaws far more daily than monthly, so that is a different
    strategy, not a detail.
  * Weights DRIFT with returns between rebalances, as a real portfolio does.
    Holding fixed weights daily is a hidden daily rebalance, which quietly pays
    a mean-reversion bonus nobody authorised.

    .venv/Scripts/python.exe experiments/2026-08-30_premia_vs_spy.py
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from trader.data_source import get_data_source  # noqa: E402

SYMBOLS = ["SPY", "EFA", "IEF", "TLT", "GLD", "DBC", "VNQ", "QQQ", "IWM", "BIL"]
START = "2005-01-01"
COST_BPS = 10.0          # charged on turnover at each rebalance
MOM_LB = 252             # 12 months
VOL_LB = 60


def load_close():
    ds = get_data_source("yfinance")
    end = pd.Timestamp.today().strftime("%Y-%m-%d")
    raw = ds.history(SYMBOLS, START, end)
    df = pd.DataFrame({s: d["close"] for s, d in raw.items() if not d.empty})
    return df.sort_index().ffill()


def month_ends(idx):
    return [g.index[-1] for _, g in pd.Series(1, index=idx).groupby(
        [idx.year, idx.month])]


# ---- pre-registered strategies. Each returns {rebalance_date: {sym: weight}} --

def _mom(close, syms, d, lb=MOM_LB):
    out = {}
    for s in syms:
        if s not in close.columns:
            continue
        hist = close[s].loc[:d].dropna()
        if len(hist) > lb:
            out[s] = hist.iloc[-1] / hist.iloc[-1 - lb] - 1
    return out


def s_spy(close, dates):
    return {d: {"SPY": 1.0} for d in dates}


def s_6040(close, dates):
    return {d: {"SPY": 0.6, "IEF": 0.4} for d in dates}


def s_trend200(close, dates):
    sma = close["SPY"].rolling(200).mean()
    out = {}
    for d in dates:
        px, s = close["SPY"].loc[d], sma.loc[d]
        out[d] = {"SPY": 1.0} if pd.notna(s) and px > s else {"BIL": 1.0}
    return out


def s_gem(close, dates):
    out = {}
    for d in dates:
        m = _mom(close, ["SPY", "EFA", "BIL"], d)
        if "SPY" not in m or "EFA" not in m:
            out[d] = {"BIL": 1.0}
            continue
        best = "SPY" if m["SPY"] >= m["EFA"] else "EFA"
        out[d] = ({best: 1.0} if m[best] > m.get("BIL", 0.0)
                  else {"IEF": 1.0})
    return out


def s_ew5(close, dates):
    w = {s: 0.2 for s in ("SPY", "EFA", "IEF", "GLD", "DBC")}
    return {d: dict(w) for d in dates}


def s_invvol4(close, dates):
    syms = ["SPY", "TLT", "GLD", "DBC"]
    rets = close[syms].pct_change()
    out = {}
    for d in dates:
        v = rets.loc[:d].tail(VOL_LB).std()
        inv = {s: 1.0 / v[s] for s in syms if pd.notna(v.get(s)) and v[s] > 0}
        tot = sum(inv.values())
        out[d] = {s: x / tot for s, x in inv.items()} if tot > 0 else {"BIL": 1.0}
    return out


def s_top3(close, dates):
    syms = ["SPY", "EFA", "QQQ", "IWM", "VNQ", "GLD", "DBC", "TLT"]
    out = {}
    for d in dates:
        m = _mom(close, syms, d)
        if not m:
            out[d] = {"BIL": 1.0}
            continue
        top = sorted(m, key=m.get, reverse=True)[:3]
        w = {}
        for s in top:
            # absolute-momentum filter: a negative 12m return goes to cash
            k = s if m[s] >= 0 else "BIL"
            w[k] = w.get(k, 0.0) + 1.0 / 3.0
        out[d] = w
    return out


def s_voltarget(close, dates):
    r = close["SPY"].pct_change()
    out = {}
    for d in dates:
        v = r.loc[:d].tail(VOL_LB).std() * np.sqrt(252)
        if pd.isna(v) or v <= 0:
            out[d] = {"BIL": 1.0}
            continue
        w = min(1.0, 0.15 / v)          # no leverage
        out[d] = {"SPY": w, "BIL": 1.0 - w}
    return out


STRATEGIES = [
    ("SPY buy-and-hold", s_spy),
    ("60/40 SPY-IEF", s_6040),
    ("SPY 200d trend", s_trend200),
    ("Dual momentum GEM", s_gem),
    ("Equal-weight 5", s_ew5),
    ("Inverse-vol 4", s_invvol4),
    ("Top-3 momentum", s_top3),
    ("Vol-targeted SPY", s_voltarget),
]


def simulate(close, targets):
    """Daily equity curve with drifting weights and monthly rebalances.

    Weights set on rebalance date d take effect from the NEXT session.
    """
    rets = close.pct_change().fillna(0.0)
    idx = close.index
    w = pd.Series(0.0, index=close.columns)
    eq, curve = 100000.0, []
    pending = None

    for i, d in enumerate(idx):
        if pending is not None:                     # applies from today
            tgt = pd.Series(0.0, index=close.columns)
            for s, x in pending.items():
                if s in tgt.index and pd.notna(close[s].loc[d]):
                    tgt[s] = x
            if tgt.sum() > 0:
                tgt /= tgt.sum()
                eq *= (1 - (tgt - w).abs().sum() * COST_BPS / 1e4)
                w = tgt
            pending = None

        if i > 0:
            r = float((w * rets.loc[d]).sum())
            eq *= (1 + r)
            if w.sum() > 0 and (1 + r) != 0:        # let the weights drift
                w = w * (1 + rets.loc[d]) / (1 + r)
        curve.append(eq)

        if d in targets:
            pending = targets[d]

    return pd.Series(curve, index=idx)


def metrics(eq):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    r = eq.pct_change().dropna()
    mdd = float((eq / eq.cummax() - 1).min())
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
    return {"cagr": cagr, "mdd": mdd, "sharpe": sharpe,
            "calmar": cagr / abs(mdd) if mdd < 0 else np.nan}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "RESULTS_premia.md"))
    a = ap.parse_args()

    close = load_close()
    close = close.dropna(subset=["SPY"])
    dates = [d for d in month_ends(close.index)]
    print(f"data {close.index[0].date()} -> {close.index[-1].date()}  "
          f"{len(close)} sessions, {len(dates)} rebalances, "
          f"symbols {[c for c in close.columns]}")

    curves = {}
    for name, fn in STRATEGIES:
        curves[name] = simulate(close, fn(close, dates))

    # warm-up: the momentum rules need 12m of history, so grade everyone from
    # the same later start rather than crediting the ones that could act sooner
    start = close.index[MOM_LB + 5]
    curves = {k: (v.loc[start:] / v.loc[start] * 100000.0) for k, v in curves.items()}

    m = {k: metrics(v) for k, v in curves.items()}
    spy = m["SPY buy-and-hold"]["cagr"]

    print(f"\n## Full period  {start.date()} -> {close.index[-1].date()}\n")
    print("| Strategy | CAGR | MaxDD | Sharpe | Calmar | vs SPY |")
    print("|---|---|---|---|---|---|")
    rows = sorted(m.items(), key=lambda kv: -kv[1]["cagr"])
    lines = []
    for name, x in rows:
        vs = (x["cagr"] - spy) * 100
        print(f"| {name} | {x['cagr']*100:.1f}% | {x['mdd']*100:.1f}% | "
              f"{x['sharpe']:.2f} | {x['calmar']:.2f} | {vs:+.2f}pp |")
        lines.append(f"- {date.today().isoformat()} premia-vs-spy {name}: "
                     f"CAGR {x['cagr']*100:.1f}% MDD {x['mdd']*100:.1f}% "
                     f"Sharpe {x['sharpe']:.2f} vs SPY {vs:+.2f}pp")

    # ---- sub-period stability -------------------------------------------
    bounds = pd.date_range(start, close.index[-1], periods=5)
    print(f"\n## Sub-period CAGR minus SPY (pp) -- the stability check\n")
    hdr = " | ".join(f"{bounds[i].date()}..{bounds[i+1].date()}" for i in range(4))
    print(f"| Strategy | {hdr} | beat SPY in |")
    print("|---|" + "---|" * 5)
    winners = []
    for name, _ in rows:
        cells, wins = [], 0
        for i in range(4):
            seg = curves[name].loc[bounds[i]:bounds[i + 1]]
            sseg = curves["SPY buy-and-hold"].loc[bounds[i]:bounds[i + 1]]
            if len(seg) < 20:
                cells.append("n/a")
                continue
            d = (metrics(seg)["cagr"] - metrics(sseg)["cagr"]) * 100
            wins += d > 0
            cells.append(f"{d:+.1f}")
        print(f"| {name} | " + " | ".join(cells) + f" | {wins}/4 |")
        if name != "SPY buy-and-hold" and m[name]["cagr"] > spy and wins >= 3:
            winners.append((name, m[name], wins))

    print("\n## Verdict\n")
    if winners:
        for n, x, w in winners:
            print(f"BEATS SPY: {n} -- CAGR {x['cagr']*100:.1f}% vs "
                  f"{spy*100:.1f}%, MDD {x['mdd']*100:.1f}%, "
                  f"won {w}/4 sub-periods")
    else:
        print("NONE of the pre-registered strategies beat SPY on CAGR over the "
              "full period AND in at least 3 of 4 sub-periods.")
        best = max((k for k in m if k != "SPY buy-and-hold"),
                   key=lambda k: m[k]["sharpe"])
        print(f"Best RISK-ADJUSTED was {best}: Sharpe {m[best]['sharpe']:.2f} "
              f"vs SPY {m['SPY buy-and-hold']['sharpe']:.2f}, "
              f"MDD {m[best]['mdd']*100:.1f}% vs {m['SPY buy-and-hold']['mdd']*100:.1f}%")

    with open(a.out, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nappended {len(lines)} line(s) to {a.out}")


if __name__ == "__main__":
    main()
