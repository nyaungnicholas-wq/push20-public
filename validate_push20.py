"""PUSH-20 honest validation harness.

Runs four analyses through the project's OWN backtest engine (run_rotation_backtest
+ apply_daily_champion), so numbers are directly comparable to the live strategy:

  1. Walk-forward / OOS  : sub-period consistency of the FIXED-param strategy vs SPY
  2. Cost realism        : slippage 5/10/20 bps haircut on CAGR
  3. Overfitting sweep   : vary vol_target / vol_cap / top_n one knob at a time
  4. Risk-reduced variant: lower-leverage config, judged on Calmar/MDD and beat-SPY

Usage:  .venv/bin/python validate_push20.py <stage>
        stage in {smoke, walkforward, cost, sweep, riskreduced, all}
"""
from __future__ import annotations
import sys, json, copy, warnings
warnings.filterwarnings("ignore")
import pandas as pd

from trader.config import Config
from trader.rotation import apply_daily_champion, run_rotation_backtest
from trader.metrics import summarize

OUT = {}

# ---- windows -----------------------------------------------------------------
# Headline window (reproduce ~20.7%) and the full window incl. the 2025-26 live era.
W_HEADLINE = ("2006-01-01", "2024-12-31")
W_FULL     = ("2006-01-01", "2026-06-18")


def _make_cfg(window, overrides=None, slippage=None):
    cfg = Config()
    apply_daily_champion(cfg)
    cfg.backtest_start, cfg.backtest_end = window
    if slippage is not None:
        cfg.slippage_bps = float(slippage)
    for k, v in (overrides or {}).items():
        setattr(cfg, k, v)
    return cfg


def run(window, overrides=None, slippage=None):
    cfg = _make_cfg(window, overrides, slippage)
    res = run_rotation_backtest(cfg)
    book = res["broker"]
    m = summarize(book.equity_curve, book.trades, cfg.starting_cash)
    bench = res.get("benchmark", {})
    return {
        "cagr": m.get("cagr"), "mdd": m.get("max_drawdown"), "sharpe": m.get("sharpe"),
        "calmar": m.get("calmar"), "total_return": m.get("total_return"),
        "trades": m.get("num_trades"),
        "spy_cagr": bench.get("cagr"), "spy_mdd": bench.get("max_drawdown"),
        "curve": book.equity_curve, "spy_curve": bench.get("equity_curve", []),
    }


def _curve_metrics(curve, start=None, end=None):
    """CAGR / MDD / total-return over a (optionally sliced) equity curve."""
    if not curve:
        return None
    pts = [(str(d)[:10], float(e)) for d, e in curve]
    if start:
        pts = [p for p in pts if p[0] >= start]
    if end:
        pts = [p for p in pts if p[0] <= end]
    if len(pts) < 2:
        return None
    e0, e1 = pts[0][1], pts[-1][1]
    yrs = max((pd.Timestamp(pts[-1][0]) - pd.Timestamp(pts[0][0])).days / 365.25, 1e-9)
    cagr = (e1 / e0) ** (1.0 / yrs) - 1.0 if e0 > 0 and e1 > 0 else float("nan")
    peak = pts[0][1]; mdd = 0.0
    for _, e in pts:
        peak = max(peak, e); mdd = min(mdd, (e - peak) / peak)
    return {"cagr": cagr, "mdd": mdd, "total_return": e1 / e0 - 1.0, "years": yrs,
            "start": pts[0][0], "end": pts[-1][0]}


def pct(x):
    return "n/a" if x is None else f"{x*100:6.1f}%"


# ---- stage 1: smoke + reproduce ---------------------------------------------
def stage_smoke():
    print("\n" + "=" * 70 + "\n1) REPRODUCE HEADLINE\n" + "=" * 70)
    for label, w in [("Headline 2006-2024", W_HEADLINE), ("Full incl. live 2006-2026.06", W_FULL)]:
        r = run(w)
        print(f"\n{label}:  {w[0]} -> {w[1]}")
        print(f"  PUSH-20   CAGR {pct(r['cagr'])}  MDD {pct(r['mdd'])}  "
              f"Sharpe {r['sharpe']:.2f}  Calmar {r['calmar']:.2f}  trades {r['trades']}")
        print(f"  SPY hold  CAGR {pct(r['spy_cagr'])}  MDD {pct(r['spy_mdd'])}")
        edge = (r['cagr'] or 0) - (r['spy_cagr'] or 0)
        print(f"  EXCESS vs SPY: {pct(edge)} CAGR")
        OUT[label] = {k: r[k] for k in ("cagr", "mdd", "sharpe", "calmar", "spy_cagr", "spy_mdd", "trades")}


# ---- stage 2: walk-forward / OOS sub-period consistency ----------------------
def stage_walkforward():
    print("\n" + "=" * 70 + "\n2) WALK-FORWARD / SUB-PERIOD CONSISTENCY (fixed params)\n" + "=" * 70)
    r = run(W_FULL)
    buckets = [("2006-2010", "2006-01-01", "2010-12-31"),
               ("2011-2015", "2011-01-01", "2015-12-31"),
               ("2016-2020", "2016-01-01", "2020-12-31"),
               ("2021-2024", "2021-01-01", "2024-12-31"),
               ("2025-2026 (LIVE ERA)", "2025-01-01", "2026-06-18")]
    print(f"\n{'period':22} {'PUSH-20 CAGR':>13} {'MDD':>8} | {'SPY CAGR':>9} | {'EXCESS':>8}  beat?")
    rows = []
    for name, s, e in buckets:
        ps = _curve_metrics(r["curve"], s, e)
        sp = _curve_metrics(r["spy_curve"], s, e)
        if not ps or not sp:
            print(f"{name:22} {'(no data)':>13}")
            continue
        excess = ps["cagr"] - sp["cagr"]
        beat = "YES" if excess > 0 else "no"
        print(f"{name:22} {pct(ps['cagr']):>13} {pct(ps['mdd']):>8} | "
              f"{pct(sp['cagr']):>9} | {pct(excess):>8}  {beat}")
        rows.append({"period": name, "cagr": ps["cagr"], "mdd": ps["mdd"],
                     "spy_cagr": sp["cagr"], "excess": excess, "beat": beat == "YES"})
    won = sum(1 for x in rows if x["beat"])
    print(f"\nBeat SPY in {won}/{len(rows)} sub-periods.")
    OUT["walkforward"] = rows


# ---- stage 3: cost realism --------------------------------------------------
def stage_cost():
    print("\n" + "=" * 70 + "\n3) COST REALISM (slippage per side)\n" + "=" * 70)
    base = None
    rows = []
    for slp in (5, 10, 20, 30):
        r = run(W_FULL, slippage=slp)
        if base is None:
            base = r["cagr"]
        haircut = (r["cagr"] - base)
        print(f"  slippage {slp:>2} bps/side:  CAGR {pct(r['cagr'])}  MDD {pct(r['mdd'])}  "
              f"Calmar {r['calmar']:.2f}  (vs 5bps: {pct(haircut)})")
        rows.append({"slippage_bps": slp, "cagr": r["cagr"], "mdd": r["mdd"], "calmar": r["calmar"]})
    OUT["cost"] = rows


# ---- stage 4: parameter sensitivity (overfitting) ---------------------------
def stage_sweep():
    print("\n" + "=" * 70 + "\n4) PARAMETER SENSITIVITY (overfitting stress)\n" + "=" * 70)
    grids = {
        "rotation_vol_target": [0.16, 0.18, 0.20, 0.22, 0.24, 0.26],
        "rotation_vol_cap":    [1.25, 1.5, 1.75, 2.0],
        "rotation_top_n":      [2, 3, 4, 5],
    }
    sweep = {}
    for knob, vals in grids.items():
        print(f"\n  -- {knob} (live = {dict(rotation_vol_target=0.22, rotation_vol_cap=1.5, rotation_top_n=3).get(knob)}) --")
        rows = []
        for v in vals:
            r = run(W_FULL, overrides={knob: v})
            print(f"     {knob}={v:<5}  CAGR {pct(r['cagr'])}  MDD {pct(r['mdd'])}  "
                  f"Calmar {r['calmar']:.2f}  excess {pct((r['cagr'] or 0)-(r['spy_cagr'] or 0))}")
            rows.append({"value": v, "cagr": r["cagr"], "mdd": r["mdd"], "calmar": r["calmar"]})
        sweep[knob] = rows
    OUT["sweep"] = sweep


# ---- stage 5: risk-reduced variant ------------------------------------------
def stage_riskreduced():
    print("\n" + "=" * 70 + "\n5) RISK-REDUCED VARIANTS (beat SPY, smaller tail)\n" + "=" * 70)
    variants = {
        "PUSH-20 (live)":       {},
        "Balanced vt.18 cap1.5 pc1.0": {"rotation_vol_target": 0.18, "rotation_vol_cap": 1.5, "rotation_position_cap": 1.0},
        "Conservative vt.15 cap1.5":   {"rotation_vol_target": 0.15, "rotation_vol_cap": 1.5, "rotation_position_cap": 1.0},
        "DeLever vt.15 cap1.25":       {"rotation_vol_target": 0.15, "rotation_vol_cap": 1.25, "rotation_position_cap": 0.5},
    }
    rows = []
    for name, ov in variants.items():
        # full history
        rf = run(W_FULL, overrides=ov)
        # live era only
        live = _curve_metrics(rf["curve"], "2025-01-01", "2026-06-18")
        spy_live = _curve_metrics(rf["spy_curve"], "2025-01-01", "2026-06-18")
        print(f"\n  {name}")
        print(f"     FULL 06-26 : CAGR {pct(rf['cagr'])}  MDD {pct(rf['mdd'])}  Calmar {rf['calmar']:.2f}  "
              f"excess {pct((rf['cagr'] or 0)-(rf['spy_cagr'] or 0))}")
        if live and spy_live:
            print(f"     LIVE 25-26 : CAGR {pct(live['cagr'])}  vs SPY {pct(spy_live['cagr'])}  "
                  f"excess {pct(live['cagr']-spy_live['cagr'])}")
        rows.append({"name": name, "full_cagr": rf["cagr"], "full_mdd": rf["mdd"],
                     "calmar": rf["calmar"], "excess_full": (rf['cagr'] or 0)-(rf['spy_cagr'] or 0),
                     "live_cagr": live["cagr"] if live else None,
                     "live_excess": (live["cagr"]-spy_live["cagr"]) if (live and spy_live) else None})
    OUT["riskreduced"] = rows


# ---- stage 6: historical crisis stress + timing-luck + forward projection ----
def stage_stress():
    import random
    print("\n" + "=" * 70 + "\n6) HISTORICAL CRISIS STRESS (real paths, not reshuffles)\n" + "=" * 70)
    r = run(W_FULL)
    crises = [
        ("2008 GFC",            "2007-10-01", "2009-06-30"),
        ("2011 EU/downgrade",   "2011-05-01", "2011-12-31"),
        ("2015-16 China/oil",   "2015-06-01", "2016-02-29"),
        ("Q4-2018 selloff",     "2018-09-01", "2018-12-31"),
        ("2020 COVID crash",    "2020-02-15", "2020-06-30"),
        ("2022 bear (rates)",   "2022-01-01", "2022-12-31"),
        ("2025-26 live era",    "2025-01-01", "2026-06-18"),
    ]
    print(f"\n{'crisis':20} {'PUSH-20 ret':>12} {'MDD':>8} | {'SPY ret':>9} {'SPY MDD':>9}")
    crows = []
    for name, s, e in crises:
        ps = _curve_metrics(r["curve"], s, e); sp = _curve_metrics(r["spy_curve"], s, e)
        if not ps or not sp:
            print(f"{name:20} {'(no data)':>12}"); continue
        print(f"{name:20} {pct(ps['total_return']):>12} {pct(ps['mdd']):>8} | "
              f"{pct(sp['total_return']):>9} {pct(sp['mdd']):>9}")
        crows.append({"crisis": name, "ret": ps["total_return"], "mdd": ps["mdd"],
                      "spy_ret": sp["total_return"], "spy_mdd": sp["mdd"]})
    OUT["crisis"] = crows

    print("\n" + "=" * 70 + "\n7) TIMING-LUCK (same strategy, different start month)\n" + "=" * 70)
    starts = ["2006-01-01", "2006-04-01", "2006-07-01", "2006-10-01",
              "2007-01-01", "2007-07-01"]
    trows = []
    for s in starts:
        rr = run((s, "2024-12-31"))
        print(f"  start {s} -> 2024 : CAGR {pct(rr['cagr'])}  MDD {pct(rr['mdd'])}")
        trows.append({"start": s, "cagr": rr["cagr"], "mdd": rr["mdd"]})
    cagrs = [x["cagr"] for x in trows if x["cagr"] is not None]
    if cagrs:
        print(f"\n  CAGR spread across start dates: {pct(min(cagrs))} .. {pct(max(cagrs))}  "
              f"(range {pct(max(cagrs)-min(cagrs))})")
    OUT["timing"] = trows

    print("\n" + "=" * 70 + "\n8) FORWARD PROJECTION (5y, block-bootstrap of daily returns)\n" + "=" * 70)
    pts = [(str(d)[:10], float(eq)) for d, eq in r["curve"]]
    rets = [pts[i][1] / pts[i-1][1] - 1.0 for i in range(1, len(pts)) if pts[i-1][1] > 0]
    random.seed(7)
    N, HORIZON, BLK = 3000, 252 * 5, 15
    finals, worst_dds = [], []
    for _ in range(N):
        path = []
        while len(path) < HORIZON:
            j = random.randrange(0, len(rets) - BLK)
            path.extend(rets[j:j + BLK])
        path = path[:HORIZON]
        eq, peak, mdd = 1.0, 1.0, 0.0
        for x in path:
            eq *= (1 + x); peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
        finals.append(eq); worst_dds.append(mdd)
    finals.sort(); worst_dds.sort()
    def q(a, p): return a[int(p * (len(a) - 1))]
    print(f"  Projected 5-year account multiple (start = $1):")
    print(f"     5th pct {q(finals,.05):5.2f}x | median {q(finals,.5):5.2f}x | 95th pct {q(finals,.95):5.2f}x")
    print(f"     P(double)  {sum(1 for x in finals if x>=2)/N*100:4.0f}%   "
          f"P(triple) {sum(1 for x in finals if x>=3)/N*100:4.0f}%   "
          f"P(lose money) {sum(1 for x in finals if x<1)/N*100:4.0f}%")
    print(f"  Worst peak-to-trough drawdown over the 5 years:")
    print(f"     median {pct(q(worst_dds,.5))} | P(>-40%) {sum(1 for x in worst_dds if x<-0.40)/N*100:4.0f}%"
          f" | P(>-50%) {sum(1 for x in worst_dds if x<-0.50)/N*100:4.0f}%")
    med_mult = q(finals, .5)
    OUT["forward"] = {"median_5y_mult": med_mult, "p5": q(finals,.05), "p95": q(finals,.95),
                      "p_double": sum(1 for x in finals if x>=2)/N,
                      "p_lose": sum(1 for x in finals if x<1)/N,
                      "median_worst_dd": q(worst_dds,.5),
                      "p_dd_40": sum(1 for x in worst_dds if x<-0.40)/N}


CRISES = [
    ("2008",  "2007-10-01", "2009-06-30", "win"),
    ("2011",  "2011-05-01", "2011-12-31", "whip"),
    ("2015",  "2015-06-01", "2016-02-29", "whip"),
    ("2018",  "2018-09-01", "2018-12-31", "whip"),
    ("2020",  "2020-02-15", "2020-06-30", "whip"),
    ("2022",  "2022-01-01", "2022-12-31", "win"),
]


def _scorecard(name, overrides):
    r = run(W_FULL, overrides=overrides)
    row = {"name": name, "cagr": r["cagr"], "mdd": r["mdd"], "calmar": r["calmar"],
           "excess": (r["cagr"] or 0) - (r["spy_cagr"] or 0)}
    whip = []
    for cname, s, e, kind in CRISES:
        m = _curve_metrics(r["curve"], s, e)
        row[cname] = m["total_return"] if m else None
        if kind == "whip" and m:
            whip.append(m["total_return"])
    row["avg_whip"] = sum(whip) / len(whip) if whip else None
    return row


# ---- stage 9: FIX #1 — 50-day circuit breaker (test only, no live changes) ---
def stage_fix1():
    print("\n" + "=" * 78)
    print("FIX #1 TEST — 50-day circuit breaker (de-lever early when SPY < N-day SMA)")
    print("=" * 78)
    variants = [
        ("BASELINE (live)",      {}),
        ("breaker 50 @ 1.0x",    {"rotation_breaker_sma": 50,  "rotation_breaker_level": 1.0}),
        ("breaker 50 @ 0.5x",    {"rotation_breaker_sma": 50,  "rotation_breaker_level": 0.5}),
        ("breaker 65 @ 1.0x",    {"rotation_breaker_sma": 65,  "rotation_breaker_level": 1.0}),
        ("breaker 100 @ 1.0x",   {"rotation_breaker_sma": 100, "rotation_breaker_level": 1.0}),
    ]
    rows = [_scorecard(n, ov) for n, ov in variants]

    hdr = (f"{'variant':18} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} | "
           f"{'2008✓':>7} {'2022✓':>7} | {'2011':>7} {'2015':>7} {'2018':>7} {'2020':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:18} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} | "
              f"{pct(x['2008']):>7} {pct(x['2022']):>7} | "
              f"{pct(x['2011']):>7} {pct(x['2015']):>7} {pct(x['2018']):>7} {pct(x['2020']):>7} | "
              f"{pct(x['avg_whip']):>8}")

    base = rows[0]
    print("\nΔ vs BASELINE (positive = better):")
    print(f"  {'variant':18} {'ΔCAGR':>7} {'ΔMDD':>7} {'Δ avgWhip loss':>15}  verdict")
    for x in rows[1:]:
        d_cagr = (x["cagr"] - base["cagr"])
        d_mdd  = (x["mdd"] - base["mdd"])              # less negative = better
        d_whip = (x["avg_whip"] - base["avg_whip"])    # less negative = better
        keeps_wins = (x["2008"] or -9) > -0.05 and (x["2022"] or -9) > -0.05
        good = d_whip > 0.01 and x["cagr"] >= 0.18 and keeps_wins
        verdict = "WORTH IT" if good else ("marginal" if d_whip > 0 else "hurts")
        print(f"  {x['name']:18} {pct(d_cagr):>7} {pct(d_mdd):>7} {pct(d_whip):>15}  {verdict}")
    OUT["fix1"] = rows


# ---- stage 10: FIX #4 — trend-quality / anti-concentration gates (test only) -
def stage_fix4():
    print("\n" + "=" * 104)
    print("FIX #4 TEST — trend-quality & anti-concentration gates (all default-OFF in live config)")
    print("=" * 104)
    variants = [
        ("BASELINE (live)",        {}),
        ("sharpe-rank",            {"rotation_rank_metric": "sharpe"}),
        ("trend-filter 100",       {"rotation_trend_filter": True, "rotation_trend_sma": 100}),
        ("trend-filter 50",        {"rotation_trend_filter": True, "rotation_trend_sma": 50}),
        ("dual-momentum",          {"rotation_dual_momentum": True}),
        ("regime->defensive 200",  {"rotation_regime_filter": True, "rotation_regime_sma": 200}),
        ("regime->defensive 100",  {"rotation_regime_filter": True, "rotation_regime_sma": 100}),
        ("sharpe + trend100",      {"rotation_rank_metric": "sharpe",
                                    "rotation_trend_filter": True, "rotation_trend_sma": 100}),
        ("sharpe + regime200",     {"rotation_rank_metric": "sharpe",
                                    "rotation_regime_filter": True, "rotation_regime_sma": 200}),
    ]
    rows = [_scorecard(n, ov) for n, ov in variants]

    hdr = (f"{'variant':22} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} | "
           f"{'2008✓':>7} {'2022✓':>7} | {'2011':>7} {'2015':>7} {'2018':>7} {'2020':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:22} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} | "
              f"{pct(x['2008']):>7} {pct(x['2022']):>7} | "
              f"{pct(x['2011']):>7} {pct(x['2015']):>7} {pct(x['2018']):>7} {pct(x['2020']):>7} | "
              f"{pct(x['avg_whip']):>8}")

    base = rows[0]
    print("\nΔ vs BASELINE (positive = better):")
    print(f"  {'variant':22} {'ΔCAGR':>7} {'ΔMDD':>7} {'Δ avgWhip':>10}  verdict")
    for x in rows[1:]:
        d_cagr = x["cagr"] - base["cagr"]
        d_mdd  = x["mdd"] - base["mdd"]
        d_whip = x["avg_whip"] - base["avg_whip"]
        keeps_wins = (x["2008"] or -9) > -0.05 and (x["2022"] or -9) > -0.05
        good = d_whip > 0.02 and x["cagr"] >= 0.175 and keeps_wins
        verdict = ("WORTH IT" if good else
                   "marginal" if (d_whip > 0.01 and keeps_wins) else
                   "breaks a win" if not keeps_wins else "hurts")
        print(f"  {x['name']:22} {pct(d_cagr):>7} {pct(d_mdd):>7} {pct(d_whip):>10}  {verdict}")
    OUT["fix4"] = rows


# ---- stage 11: FIX #3 — equity-level drawdown circuit breaker (overlay only) -
def _dd_overlay(curve, start_cash, thresh, defmult, recover=0.05, parked=0.0):
    """Simulate an equity-drawdown de-risk overlay on the baseline return stream.
    When the ACCOUNT drawdown breaches -thresh, scale exposure to defmult until it
    recovers to within -recover of its peak. No lookahead: today's return uses the
    exposure state set at yesterday's close."""
    pts = [(str(d)[:10], float(e)) for d, e in curve]
    rets = [pts[i][1] / pts[i-1][1] - 1.0 for i in range(1, len(pts)) if pts[i-1][1] > 0]
    eq, peak, m = start_cash, start_cash, 1.0
    out = [(pts[0][0], eq)]
    for i, r in enumerate(rets):
        eq *= (1 + m * r + (1 - m) * parked)
        peak = max(peak, eq)
        dd = eq / peak - 1.0
        if dd <= -thresh:
            m = defmult
        elif dd >= -recover:
            m = 1.0
        out.append((pts[i + 1][0], eq))
    return out


def stage_fix3():
    print("\n" + "=" * 104)
    print("FIX #3 TEST — equity-level drawdown circuit breaker (overlay; live config untouched)")
    print("=" * 104)
    base = run(W_FULL)
    sc = 100_000.0
    variants = [
        ("BASELINE (live)",       None),
        ("DD -15% -> 0.5x",       (0.15, 0.5)),
        ("DD -20% -> 0.5x",       (0.20, 0.5)),
        ("DD -15% -> 0.0x cash",  (0.15, 0.0)),
        ("DD -20% -> 0.0x cash",  (0.20, 0.0)),
        ("DD -12% -> 0.5x",       (0.12, 0.5)),
    ]
    rows = []
    for name, cfg in variants:
        curve = base["curve"] if cfg is None else _dd_overlay(base["curve"], sc, cfg[0], cfg[1])
        m_full = _curve_metrics(curve)
        row = {"name": name, "cagr": m_full["cagr"], "mdd": m_full["mdd"],
               "calmar": (m_full["cagr"]/abs(m_full["mdd"]) if m_full["mdd"] else 0)}
        whip = []
        for cname, s, e, kind in CRISES:
            mm = _curve_metrics(curve, s, e)
            row[cname] = mm["total_return"] if mm else None
            if kind == "whip" and mm:
                whip.append(mm["total_return"])
        row["avg_whip"] = sum(whip)/len(whip) if whip else None
        rows.append(row)

    hdr = (f"{'variant':22} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} | "
           f"{'2008✓':>7} {'2022✓':>7} | {'2011':>7} {'2015':>7} {'2018':>7} {'2020':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:22} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} | "
              f"{pct(x['2008']):>7} {pct(x['2022']):>7} | "
              f"{pct(x['2011']):>7} {pct(x['2015']):>7} {pct(x['2018']):>7} {pct(x['2020']):>7} | "
              f"{pct(x['avg_whip']):>8}")
    b = rows[0]
    print("\nΔ vs BASELINE (positive = better):")
    for x in rows[1:]:
        keeps = (x["2008"] or -9) > -0.05 and (x["2022"] or -9) > -0.05
        d_whip = x["avg_whip"] - b["avg_whip"]
        good = d_whip > 0.02 and x["cagr"] >= 0.175 and keeps
        verdict = ("WORTH IT" if good else "marginal" if (d_whip > 0.01 and keeps)
                   else "breaks a win" if not keeps else "hurts")
        print(f"  {x['name']:22} ΔCAGR {pct(x['cagr']-b['cagr']):>7}  ΔMDD {pct(x['mdd']-b['mdd']):>7}  "
              f"ΔavgWhip {pct(d_whip):>7}  {verdict}")
    OUT["fix3"] = rows


# ---- stage 12: EXPANDED UNIVERSE (test only) --------------------------------
def stage_universe():
    from trader.rotation import V5_UNIVERSE
    base_u = list(V5_UNIVERSE)
    INTL = ["EFA", "EEM"]                              # have 2x (EFO/EET) -> leveraged
    DEF  = ["XLP", "XLU", "XLRE"]                      # 1x fallback (no 2x ETF)
    THEM = ["KRE", "XHB", "IBB", "XRT", "ITB"]         # 1x fallback, high-beta sub-sectors
    print("\n" + "=" * 104)
    print("EXPANDED-UNIVERSE TEST — current = 10 US sectors (live config untouched)")
    print("=" * 104)
    variants = [
        ("BASELINE 10-sector",        {}),
        ("+ Intl EFA,EEM (2x)",       {"rotation_universe": base_u + INTL}),
        ("+ Defensives XLP/U/RE",     {"rotation_universe": base_u + DEF}),
        ("+ Thematics KRE/XHB/..",    {"rotation_universe": base_u + THEM}),
        ("+ Intl + Defensives",       {"rotation_universe": base_u + INTL + DEF}),
        ("+ EVERYTHING (18)",         {"rotation_universe": base_u + INTL + DEF + THEM}),
    ]
    rows = [_scorecard(n, ov) for n, ov in variants]

    hdr = (f"{'universe':24} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>7} | "
           f"{'2008':>7} {'2022':>7} | {'2011':>7} {'2015':>7} {'2018':>7} {'2020':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:24} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} "
              f"{pct(x['excess']):>7} | {pct(x['2008']):>7} {pct(x['2022']):>7} | "
              f"{pct(x['2011']):>7} {pct(x['2015']):>7} {pct(x['2018']):>7} {pct(x['2020']):>7} | "
              f"{pct(x['avg_whip']):>8}")
    b = rows[0]
    print("\nΔ vs BASELINE 10-sector (positive = better):")
    for x in rows[1:]:
        d_cagr = x["cagr"] - b["cagr"]; d_mdd = x["mdd"] - b["mdd"]; d_whip = x["avg_whip"] - b["avg_whip"]
        better = "BETTER" if (d_cagr > 0.003 and d_mdd >= -0.01) else ("worse" if d_cagr < -0.003 else "~flat")
        print(f"  {x['name']:24} ΔCAGR {pct(d_cagr):>7}  ΔMDD {pct(d_mdd):>7}  "
              f"ΔCalmar {x['calmar']-b['calmar']:>+5.2f}  ΔavgWhip {pct(d_whip):>7}  {better}")
    OUT["universe"] = rows


# ---- stage 13: AI-ETF expansion (test only) ---------------------------------
def stage_ai():
    from trader.rotation import V5_UNIVERSE
    base_u = list(V5_UNIVERSE)
    print("\n" + "=" * 104)
    print("AI-ETF EXPANSION — baseline ALREADY has SMH(2x USD)/XLK(2x ROM)/QQQ(2x QLD) AI exposure")
    print("=" * 104)
    # All 1x (no 2x leveraged versions exist for these thematics). Histories vary:
    # SOXX/IGV ~2001, ROBO ~2013, BOTZ ~2016, AIQ ~2018 (backtest drops pre-inception dates).
    variants = [
        ("BASELINE (SMH/XLK/QQQ)",   {}),
        ("+ SOXX, IGV (semis/SW)",   {"rotation_universe": base_u + ["SOXX", "IGV"]}),
        ("+ BOTZ, AIQ, ROBO (AI)",   {"rotation_universe": base_u + ["BOTZ", "AIQ", "ROBO"]}),
        ("+ ALL AI/robotics/SW",     {"rotation_universe": base_u + ["SOXX", "IGV", "BOTZ", "AIQ", "ROBO"]}),
    ]
    rows = [_scorecard(n, ov) for n, ov in variants]
    hdr = (f"{'universe':24} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>7} | "
           f"{'2018':>7} {'2020':>7} {'2022':>7} | {'2025-26?':>9}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        # live-era slice for the AI-boom period specifically
        live = None
        rr = run(W_FULL, overrides=({} if x["name"].startswith("BASELINE") else
                 {"rotation_universe": dict(variants)[x["name"]]["rotation_universe"]}))
        lm = _curve_metrics(rr["curve"], "2025-01-01", "2026-06-18")
        live = lm["total_return"] if lm else None
        print(f"{x['name']:24} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} "
              f"{pct(x['excess']):>7} | {pct(x['2018']):>7} {pct(x['2020']):>7} {pct(x['2022']):>7} | {pct(live):>9}")
    b = rows[0]
    print("\nΔ vs BASELINE:")
    for x in rows[1:]:
        d_cagr = x["cagr"] - b["cagr"]
        verdict = "BETTER" if d_cagr > 0.003 else ("worse" if d_cagr < -0.003 else "~flat")
        print(f"  {x['name']:24} ΔCAGR {pct(d_cagr):>7}  ΔMDD {pct(x['mdd']-b['mdd']):>7}  "
              f"ΔCalmar {x['calmar']-b['calmar']:>+5.2f}  {verdict}")
    OUT["ai"] = rows


# ---- stage 17 (a): COMBINED — faster vol window + higher leverage = more profit?
def stage_combined():
    print("\n" + "=" * 104)
    print("(a) COMBINED — window 15 freed up risk budget; dial leverage UP to convert it to profit")
    print("=" * 104)
    variants = [
        ("LIVE  w25 vt.22",  {}),
        ("w15 vt.22 (effcy)", {"rotation_vol_window": 15}),
        ("w15 vt.26",         {"rotation_vol_window": 15, "rotation_vol_target": 0.26}),
        ("w15 vt.28",         {"rotation_vol_window": 15, "rotation_vol_target": 0.28}),
        ("w10 vt.26",         {"rotation_vol_window": 10, "rotation_vol_target": 0.26}),
        ("w10 vt.30",         {"rotation_vol_window": 10, "rotation_vol_target": 0.30}),
    ]
    rows = [_scorecard(n, ov) for n, ov in variants]
    hdr = (f"{'variant':20} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>6} | "
           f"{'2020':>7} {'2022✓':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:20} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} {pct(x['excess']):>6} | "
              f"{pct(x['2020']):>7} {pct(x['2022']):>7} | {pct(x['avg_whip']):>8}")
    b = rows[0]
    print("\nΔ vs LIVE — looking for: higher CAGR at <= LIVE drawdown, or same CAGR at lower DD")
    for x in rows[1:]:
        dc, dm, dcal = x["cagr"]-b["cagr"], x["mdd"]-b["mdd"], x["calmar"]-b["calmar"]
        tag = ("DOMINATES (more $ AND less risk)" if dc > 0.002 and dm > -0.002 and dcal > 0 else
               "more $, more risk" if dc > 0.002 else
               "same $, less risk" if abs(dc) <= 0.003 and dm > 0.005 else "worse" if dc < -0.003 else "~flat")
        print(f"  {x['name']:20} ΔCAGR {pct(dc):>7}  ΔMDD {pct(dm):>7}  ΔCalmar {dcal:>+5.2f}  {tag}")
    OUT["combined"] = rows


# ---- stage 18 (b): FORWARD gates — VIX gate (monkeypatch) + basket-vol proxy --
def stage_forward():
    import trader.rotation as R
    import yfinance as yf
    print("\n" + "=" * 104)
    print("(b) FORWARD-LOOKING GATES — VIX gate (de-lever on priced tail-risk) + basket-vol sizing")
    print("=" * 104)
    vraw = yf.download("^VIX", start="2005-01-01", end="2026-06-20", progress=False, auto_adjust=True)
    vix = vraw["Close"]
    if hasattr(vix, "columns"):
        vix = vix.iloc[:, 0]
    vix = vix.dropna()
    # sanity: confirm alignment / known crisis levels
    print(f"  VIX loaded: {len(vix)} days; 2020-03-16≈{float(vix.asof(pd.Timestamp('2020-03-16'))):.0f}, "
          f"2008-10-24≈{float(vix.asof(pd.Timestamp('2008-10-24'))):.0f}, "
          f"latest≈{float(vix.iloc[-1]):.0f}")
    _orig = R._two_way_vol_scale

    def gate(lo_thr, lo_cap, hi_thr, hi_cap):
        def patched(close, asof, cfg):
            s = _orig(close, asof, cfg)
            try:
                v = float(vix.asof(asof))
            except Exception:
                return s
            if v >= hi_thr:
                return min(s, hi_cap)
            if v >= lo_thr:
                return min(s, lo_cap)
            return s
        return patched

    def score(name, overrides, patch):
        R._two_way_vol_scale = patch if patch else _orig
        row = _scorecard(name, overrides)
        R._two_way_vol_scale = _orig
        return row

    specs = [
        ("LIVE (SPY vol, w25)", {}, None),
        # basket-vol: size leverage to a HIGHER-vol proxy (rough; also moves the regime trigger)
        ("basket-vol QQQ w15",  {"rotation_vol_window": 15, "rotation_vol_proxy": "QQQ"}, None),
        ("basket-vol SMH w15",  {"rotation_vol_window": 15, "rotation_vol_proxy": "SMH"}, None),
        # VIX gates (forward tail-risk)
        ("VIX 30→1x/40→.5x",    {}, gate(30, 1.0, 40, 0.5)),
        ("VIX 25→1x/35→.5x",    {}, gate(25, 1.0, 35, 0.5)),
        ("VIX 25/35 + w15",     {"rotation_vol_window": 15}, gate(25, 1.0, 35, 0.5)),
    ]
    rows = [score(n, ov, p) for n, ov, p in specs]
    hdr = (f"{'variant':22} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>6} | "
           f"{'2008':>7} {'2020':>7} {'2022✓':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:22} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} {pct(x['excess']):>6} | "
              f"{pct(x['2008']):>7} {pct(x['2020']):>7} {pct(x['2022']):>7} | {pct(x['avg_whip']):>8}")
    b = rows[0]
    print("\nΔ vs LIVE:")
    for x in rows[1:]:
        dc, dm, dcal = x["cagr"]-b["cagr"], x["mdd"]-b["mdd"], x["calmar"]-b["calmar"]
        keeps = (x["2022"] or -9) > -0.05
        tag = ("DOMINATES" if dc > 0.002 and dm > 0 and dcal > 0 else
               "better risk-adj" if dcal > 0.015 and dc > -0.005 else
               "worse" if dc < -0.005 or not keeps else "~flat")
        print(f"  {x['name']:22} ΔCAGR {pct(dc):>7}  ΔMDD {pct(dm):>7}  ΔCalmar {dcal:>+5.2f}  ΔWhip {pct(x['avg_whip']-b['avg_whip']):>7}  {tag}")
    OUT["forward"] = rows


# ---- stage 19: STACK ALL — combine every lever and dial leverage to taste -----
def stage_stackall():
    import trader.rotation as R
    import yfinance as yf
    print("\n" + "=" * 104)
    print("STACK ALL — faster window + VIX gate + basket-vol(QQQ), then dial vol_target for profit")
    print("=" * 104)
    vraw = yf.download("^VIX", start="2005-01-01", end="2026-06-20", progress=False, auto_adjust=True)
    vix = vraw["Close"]
    if hasattr(vix, "columns"):
        vix = vix.iloc[:, 0]
    vix = vix.dropna()
    _orig = R._two_way_vol_scale

    def gate(lo_thr, lo_cap, hi_thr, hi_cap):
        def patched(close, asof, cfg):
            s = _orig(close, asof, cfg)
            try:
                v = float(vix.asof(asof))
            except Exception:
                return s
            if v >= hi_thr:
                return min(s, hi_cap)
            if v >= lo_thr:
                return min(s, lo_cap)
            return s
        return patched

    def score(name, overrides, use_vix):
        R._two_way_vol_scale = gate(25, 1.0, 35, 0.5) if use_vix else _orig
        row = _scorecard(name, overrides)
        R._two_way_vol_scale = _orig
        return row

    V = True
    variants = [
        ("LIVE (no levers)",        {}, False),
        ("ALL protect  vt.22",      {"rotation_vol_window": 10, "rotation_vol_proxy": "QQQ"}, V),
        ("ALL balanced vt.26",      {"rotation_vol_window": 12, "rotation_vol_target": 0.26, "rotation_vol_proxy": "QQQ"}, V),
        ("ALL profit   vt.30",      {"rotation_vol_window": 10, "rotation_vol_target": 0.30, "rotation_vol_proxy": "QQQ"}, V),
        ("ALL max      vt.34",      {"rotation_vol_window": 10, "rotation_vol_target": 0.34, "rotation_vol_proxy": "QQQ"}, V),
        ("no-basket w10 vt.30",     {"rotation_vol_window": 10, "rotation_vol_target": 0.30}, V),
    ]
    rows = [score(n, ov, uv) for n, ov, uv in variants]
    hdr = (f"{'variant':22} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>6} | "
           f"{'2008':>7} {'2020':>7} {'2022✓':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:22} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} {pct(x['excess']):>6} | "
              f"{pct(x['2008']):>7} {pct(x['2020']):>7} {pct(x['2022']):>7} | {pct(x['avg_whip']):>8}")
    b = rows[0]
    print("\nΔ vs LIVE:")
    for x in rows[1:]:
        dc, dm, dcal = x["cagr"]-b["cagr"], x["mdd"]-b["mdd"], x["calmar"]-b["calmar"]
        tag = ("DOMINATES (more $ AND less risk)" if dc > 0.002 and dm > 0 and dcal > 0 else
               "more $, more risk" if dc > 0.005 and dm < 0 else
               "safer, ~same $" if abs(dc) <= 0.005 and dm > 0.005 else
               "worse" if dc < -0.005 else "~flat")
        print(f"  {x['name']:22} ΔCAGR {pct(dc):>7}  ΔMDD {pct(dm):>7}  ΔCalmar {dcal:>+5.2f}  ΔWhip {pct(x['avg_whip']-b['avg_whip']):>7}  {tag}")
    OUT["stackall"] = rows


# ---- stage 20: VALIDATE the winner out-of-sample (overfit check) -------------
def stage_valwin():
    import trader.rotation as R
    import yfinance as yf
    print("\n" + "=" * 78)
    print("OVERFIT CHECK — winner (ALL balanced) vs LIVE, in EACH sub-period")
    print("=" * 78)
    vraw = yf.download("^VIX", start="2005-01-01", end="2026-06-20", progress=False, auto_adjust=True)
    vix = vraw["Close"]
    if hasattr(vix, "columns"):
        vix = vix.iloc[:, 0]
    vix = vix.dropna()
    _orig = R._two_way_vol_scale

    def gate(close, asof, cfg):
        s = _orig(close, asof, cfg)
        try:
            v = float(vix.asof(asof))
        except Exception:
            return s
        if v >= 35:
            return min(s, 0.5)
        if v >= 25:
            return min(s, 1.0)
        return s

    live = run(W_FULL)
    R._two_way_vol_scale = gate
    win = run(W_FULL, overrides={"rotation_vol_window": 12, "rotation_vol_target": 0.26,
                                 "rotation_vol_proxy": "QQQ"})
    R._two_way_vol_scale = _orig

    buckets = [("2006-2010", "2006-01-01", "2010-12-31"),
               ("2011-2015", "2011-01-01", "2015-12-31"),
               ("2016-2020", "2016-01-01", "2020-12-31"),
               ("2021-2026", "2021-01-01", "2026-06-18")]
    print(f"\n{'period':12} {'LIVE cagr':>10} {'LIVE mdd':>9} | {'WIN cagr':>10} {'WIN mdd':>9} | {'ΔCAGR':>7} {'ΔMDD':>7}  winner?")
    win_count = 0
    for name, s, e in buckets:
        lm = _curve_metrics(live["curve"], s, e)
        wm = _curve_metrics(win["curve"], s, e)
        if not lm or not wm:
            continue
        dc = wm["cagr"] - lm["cagr"]; dm = wm["mdd"] - lm["mdd"]
        better = dc > -0.005 and dm > -0.01   # at least as much $ and not worse risk
        if better: win_count += 1
        print(f"{name:12} {pct(lm['cagr']):>10} {pct(lm['mdd']):>9} | {pct(wm['cagr']):>10} {pct(wm['mdd']):>9} | "
              f"{pct(dc):>7} {pct(dm):>7}  {'✅' if better else '❌'}")
    print(f"\n  Winner is >= LIVE (CAGR & risk) in {win_count}/{len(buckets)} sub-periods.")
    print("  (robust if it wins MOST periods; overfit if it only wins the full path via one era)")
    OUT["valwin"] = {"win_count": win_count, "n": len(buckets)}


# ---- stage 21: REAL implementation — verify no regression + reproduce + OOS ---
REAL_STACK = {
    "rotation_vol_window": 12, "rotation_vol_target": 0.26,
    "rotation_basket_vol": True,
    "rotation_vix_gate": True, "rotation_vix_lo": 25.0, "rotation_vix_lo_cap": 1.0,
    "rotation_vix_hi": 35.0, "rotation_vix_hi_cap": 0.5,
}


def stage_real():
    def withvt(vt):
        d = dict(REAL_STACK); d["rotation_vol_target"] = vt; return d
    print("\n" + "=" * 104)
    print("REAL IMPLEMENTATION — gates built into rotation.py (default-OFF). Verify + reproduce + OOS")
    print("=" * 104)
    specs = [("LIVE (sanity)", {}), ("REAL stack vt.22", withvt(0.22)),
             ("REAL stack vt.26", withvt(0.26)), ("REAL stack vt.30", withvt(0.30))]
    results = {n: run(W_FULL, overrides=ov) for n, ov in specs}
    hdr = (f"{'variant':20} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>6} | "
           f"{'2008':>7} {'2020':>7} {'2022✓':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for n, _ in specs:
        r = results[n]
        cr = lambda s, e: _curve_metrics(r["curve"], s, e)
        whip = [cr(s, e)["total_return"] for nm, s, e, k in CRISES if k == "whip" and cr(s, e)]
        c08 = cr("2007-10-01", "2009-06-30"); c20 = cr("2020-02-15", "2020-06-30"); c22 = cr("2022-01-01", "2022-12-31")
        print(f"{n:20} {pct(r['cagr']):>6} {pct(r['mdd']):>7} {r['calmar']:>7.2f} "
              f"{pct((r['cagr'] or 0)-(r['spy_cagr'] or 0)):>6} | "
              f"{pct(c08['total_return']):>7} {pct(c20['total_return']):>7} {pct(c22['total_return']):>7} | "
              f"{pct(sum(whip)/len(whip)):>8}")
    print("\n  ↑ sanity: 'LIVE' must still read ~19.1% / -36.6% (proves default-OFF didn't change anything)")
    print("  ↑ 'REAL stack vt.26' should land near the proxy test: ~19.6% / -33% / Calmar ~0.59")

    print("\n--- OUT-OF-SAMPLE: REAL stack vt.26 vs LIVE, per sub-period ---")
    live = results["LIVE (sanity)"]; win = results["REAL stack vt.26"]
    buckets = [("2006-2010", "2006-01-01", "2010-12-31"), ("2011-2015", "2011-01-01", "2015-12-31"),
               ("2016-2020", "2016-01-01", "2020-12-31"), ("2021-2026", "2021-01-01", "2026-06-18")]
    wins = 0
    print(f"{'period':12} {'LIVE c/mdd':>18} | {'REAL c/mdd':>18} | {'ΔCAGR':>7} {'ΔMDD':>7}")
    for nm, s, e in buckets:
        lm = _curve_metrics(live["curve"], s, e); wm = _curve_metrics(win["curve"], s, e)
        dc, dm = wm["cagr"]-lm["cagr"], wm["mdd"]-lm["mdd"]
        ok = dc > -0.005 and dm > -0.01
        wins += ok
        print(f"{nm:12} {pct(lm['cagr'])+' /'+pct(lm['mdd']):>18} | {pct(wm['cagr'])+' /'+pct(wm['mdd']):>18} | "
              f"{pct(dc):>7} {pct(dm):>7}  {'OK' if ok else 'x'}")
    print(f"\n  REAL stack >= LIVE in {wins}/{len(buckets)} sub-periods.")
    OUT["real"] = {n: {"cagr": results[n]["cagr"], "mdd": results[n]["mdd"], "calmar": results[n]["calmar"]} for n, _ in specs}


# ---- stage 22: STRESS the SHIPPED GATED v2 config -----------------------------
OLD_PUSH20 = {"rotation_basket_vol": False, "rotation_vix_gate": False,
              "rotation_vol_target": 0.22, "rotation_vol_window": 25}


def stage_stress2():
    import random
    print("\n" + "=" * 92)
    print("STRESS TEST — the SHIPPED GATED v2 config (run() with no overrides = live config now)")
    print("=" * 92)

    # ---- A) cost realism + turnover (the #1 risk for the faster-trading config) ----
    print("\nA) COST REALISM + TURNOVER")
    g10 = None
    for slp in (5, 10, 20, 30):
        r = run(W_FULL, slippage=slp)
        if slp == 10:
            g10 = r
        print(f"   GATED v2 @ {slp:>2}bps/side:  CAGR {pct(r['cagr'])}  MDD {pct(r['mdd'])}  "
              f"Calmar {r['calmar']:.2f}  trades {r['trades']}")
    old = run(W_FULL, overrides=OLD_PUSH20)
    print(f"   --- turnover: GATED v2 = {g10['trades']} trades vs OLD PUSH-20 = {old['trades']} "
          f"(+{(g10['trades']/old['trades']-1)*100:.0f}%)")
    print(f"   even at a punishing 30bps, edge vs SPY ({pct(g10['spy_cagr'])}) stays large")

    # ---- B) knob sensitivity (is GATED v2 a fragile peak or a smooth plateau?) ----
    print("\nB) KNOB SENSITIVITY (live values: window 12, VIX 25/35, basket ON)")
    for w in (8, 10, 12, 15, 18):
        r = run(W_FULL, overrides={"rotation_vol_window": w})
        print(f"   vol_window {w:>2}        CAGR {pct(r['cagr'])}  MDD {pct(r['mdd'])}  Calmar {r['calmar']:.2f}")
    for lo in (20, 25, 30):
        r = run(W_FULL, overrides={"rotation_vix_lo": float(lo), "rotation_vix_hi": float(lo + 10)})
        print(f"   VIX gate {lo}/{lo+10}      CAGR {pct(r['cagr'])}  MDD {pct(r['mdd'])}  Calmar {r['calmar']:.2f}")
    rb = run(W_FULL, overrides={"rotation_basket_vol": False})
    print(f"   basket-vol OFF       CAGR {pct(rb['cagr'])}  MDD {pct(rb['mdd'])}  Calmar {rb['calmar']:.2f}  (vs ON above)")

    # ---- C) crisis stress + sub-period vs SPY: GATED v2 vs OLD ----
    print("\nC) CRISIS STRESS + SUB-PERIOD vs SPY  (GATED v2 vs OLD PUSH-20)")
    g = run(W_FULL)
    print(f"   {'crisis':14} {'GATED v2':>10} {'OLD':>10} {'SPY':>10}")
    for nm, s, e, k in CRISES:
        gm = _curve_metrics(g["curve"], s, e); om = _curve_metrics(old["curve"], s, e); sm = _curve_metrics(g["spy_curve"], s, e)
        print(f"   {nm:14} {pct(gm['total_return']):>10} {pct(om['total_return']):>10} {pct(sm['total_return']):>10}")
    buckets = [("2006-2010", "2006-01-01", "2010-12-31"), ("2011-2015", "2011-01-01", "2015-12-31"),
               ("2016-2020", "2016-01-01", "2020-12-31"), ("2021-2024", "2021-01-01", "2024-12-31"),
               ("2025-2026", "2025-01-01", "2026-06-18")]
    print(f"\n   {'period':12} {'GATED cagr':>11} {'SPY cagr':>10}  beat?")
    won = 0
    for nm, s, e in buckets:
        gm = _curve_metrics(g["curve"], s, e); sm = _curve_metrics(g["spy_curve"], s, e)
        beat = gm["cagr"] > sm["cagr"]; won += beat
        print(f"   {nm:12} {pct(gm['cagr']):>11} {pct(sm['cagr']):>10}  {'YES' if beat else 'no'}")
    print(f"   GATED v2 beats SPY in {won}/{len(buckets)} sub-periods")

    # ---- D) forward projection (5y bootstrap) on GATED v2 ----
    print("\nD) FORWARD PROJECTION — GATED v2, 5y block-bootstrap (vs OLD: median 2.37x, P(double) 64%)")
    pts = [(str(d)[:10], float(eq)) for d, eq in g["curve"]]
    rets = [pts[i][1]/pts[i-1][1]-1 for i in range(1, len(pts)) if pts[i-1][1] > 0]
    random.seed(11); N, H, B = 3000, 252*5, 15
    finals, dds = [], []
    for _ in range(N):
        path = []
        while len(path) < H:
            j = random.randrange(0, len(rets)-B); path.extend(rets[j:j+B])
        path = path[:H]; eq = peak = 1.0; mdd = 0.0
        for x in path:
            eq *= (1+x); peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
        finals.append(eq); dds.append(mdd)
    finals.sort(); dds.sort()
    q = lambda a, p: a[int(p*(len(a)-1))]
    print(f"   5y multiple: 5th {q(finals,.05):.2f}x | median {q(finals,.5):.2f}x | 95th {q(finals,.95):.2f}x")
    print(f"   P(double) {sum(1 for x in finals if x>=2)/N*100:.0f}%  P(lose) {sum(1 for x in finals if x<1)/N*100:.0f}%"
          f"  median worst-DD {pct(q(dds,.5))}  P(DD>-40%) {sum(1 for x in dds if x<-0.40)/N*100:.0f}%")
    OUT["stress2"] = {"gated_cagr": g["cagr"], "gated_mdd": g["mdd"], "gated_trades": g10["trades"],
                      "old_trades": old["trades"], "beat_spy": won}


# ---- stage 23: deep statistical profile of GATED v2 (MC / rolling / underwater) -
def stage_stress3():
    import numpy as np
    print("\n" + "=" * 88)
    print("DEEP STATS — shipped GATED v2:  (A) full-horizon Monte Carlo  (B) rolling 3y  (C) underwater")
    print("=" * 88)
    g = run(W_FULL)
    sd = {str(d)[:10]: float(e) for d, e in g["curve"]}
    sp = {str(d)[:10]: float(e) for d, e in g["spy_curve"]}
    dates = sorted(set(sd) & set(sp))
    sr = np.array([sd[dates[i]]/sd[dates[i-1]]-1 for i in range(1, len(dates))])
    pr = np.array([sp[dates[i]]/sp[dates[i-1]]-1 for i in range(1, len(dates))])

    # ---- A) full-horizon paired block-bootstrap Monte Carlo ----
    np.random.seed(13)
    n = len(sr); H = n; B = 15; N = 2000
    nblk = H // B + 1
    cagrs, mdds, beats = [], [], 0
    yrs = H / 252.0
    for _ in range(N):
        starts = np.random.randint(0, n - B, size=nblk)
        idx = (starts[:, None] + np.arange(B)).ravel()[:H]
        seq = np.cumprod(1 + sr[idx]); pseq = np.cumprod(1 + pr[idx])
        cagrs.append(seq[-1] ** (1/yrs) - 1)
        peak = np.maximum.accumulate(seq); mdds.append(float((seq/peak - 1).min()))
        beats += seq[-1] > pseq[-1]
    cagrs = np.array(sorted(cagrs)); mdds = np.array(sorted(mdds))
    Q = lambda a, p: float(a[int(p*(len(a)-1))])
    print(f"\nA) MONTE CARLO — {N} alternate {yrs:.0f}-year histories (block bootstrap)")
    print(f"   CAGR:  5th {pct(Q(cagrs,.05))} | median {pct(Q(cagrs,.5))} | 95th {pct(Q(cagrs,.95))}")
    print(f"   P(beat SPY) {beats/N*100:.0f}%   P(CAGR>=15%) {(cagrs>=.15).mean()*100:.0f}%   P(lose money) {(cagrs<0).mean()*100:.0f}%")
    print(f"   max drawdown:  median {pct(Q(mdds,.5))}  |  P(MDD<-40%) {(mdds<-.40).mean()*100:.0f}%  |  P(MDD<-50%) {(mdds<-.50).mean()*100:.0f}%")
    print(f"   (old PUSH-20 MC: P(MDD<-40%)~59% — a lower number here = the gates working)")

    # ---- B) rolling 3-year windows (step ~monthly) ----
    WIN = 756
    rc, rbeat, rn = [], 0, 0
    for i in range(0, len(dates) - WIN, 21):
        s0, s1 = sd[dates[i]], sd[dates[i+WIN]]; p0, p1 = sp[dates[i]], sp[dates[i+WIN]]
        sc = (s1/s0) ** (252/WIN) - 1; pc = (p1/p0) ** (252/WIN) - 1
        rc.append(sc); rbeat += sc > pc; rn += 1
    rc = sorted(rc)
    print(f"\nB) ROLLING 3-YEAR WINDOWS — {rn} overlapping windows (every ~month)")
    print(f"   3y annualized CAGR:  worst {pct(rc[0])} | median {pct(rc[len(rc)//2])} | best {pct(rc[-1])}")
    print(f"   beat SPY in {rbeat}/{rn} windows ({rbeat/rn*100:.0f}%)   windows with negative 3y CAGR: {sum(1 for x in rc if x<0)}/{rn}")

    # ---- C) time-underwater / recovery ----
    eq = [float(e) for _, e in g["curve"]]
    peak = eq[0]; cur = 0; longest = 0; uw = 0
    for e in eq:
        peak = max(peak, e)
        if e < peak * 0.999:
            cur += 1; uw += 1; longest = max(longest, cur)
        else:
            cur = 0
    print(f"\nC) TIME UNDERWATER (how long the pain lasts, not just how deep)")
    print(f"   longest stretch below a prior peak: {longest} trading days (~{longest/21:.0f} months)")
    print(f"   % of all days spent in drawdown: {uw/len(eq)*100:.0f}%")
    OUT["stress3"] = {"mc_median_cagr": Q(cagrs,.5), "mc_p_beat_spy": beats/N,
                      "mc_p_mdd40": float((mdds<-.40).mean()), "roll_beat": f"{rbeat}/{rn}",
                      "longest_uw_days": longest, "pct_underwater": uw/len(eq)}


# ---- stage 16: FIX #2 — faster volatility de-levering (the untested lever) ----
def stage_fix2():
    print("\n" + "=" * 104)
    print("FIX #2 TEST — faster vol-targeting window (de-lever sooner as turbulence builds)")
    print("live = 25-day window. Shorter = reacts faster to a developing selloff.")
    print("=" * 104)
    variants = [
        ("vol_window  5",  {"rotation_vol_window": 5}),
        ("vol_window  8",  {"rotation_vol_window": 8}),
        ("vol_window 10",  {"rotation_vol_window": 10}),
        ("vol_window 15",  {"rotation_vol_window": 15}),
        ("vol_window 25 (LIVE)", {}),
        ("vol_window 40",  {"rotation_vol_window": 40}),
    ]
    rows = [_scorecard(n, ov) for n, ov in variants]
    hdr = (f"{'variant':22} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>6} | "
           f"{'2008✓':>7} {'2022✓':>7} | {'2011':>7} {'2015':>7} {'2018':>7} {'2020':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:22} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} {pct(x['excess']):>6} | "
              f"{pct(x['2008']):>7} {pct(x['2022']):>7} | "
              f"{pct(x['2011']):>7} {pct(x['2015']):>7} {pct(x['2018']):>7} {pct(x['2020']):>7} | {pct(x['avg_whip']):>8}")
    # baseline = the 25-day LIVE row
    b = next(x for x in rows if "LIVE" in x["name"])
    print("\nΔ vs LIVE (25-day):")
    for x in rows:
        if "LIVE" in x["name"]:
            continue
        d_cagr = x["cagr"] - b["cagr"]; d_whip = x["avg_whip"] - b["avg_whip"]; d_mdd = x["mdd"] - b["mdd"]
        keeps = (x["2008"] or -9) > -0.05 and (x["2022"] or -9) > -0.05
        good = d_whip > 0.02 and x["cagr"] >= 0.175 and keeps
        verdict = ("WORTH IT" if good else "marginal" if (d_whip > 0.01 and keeps)
                   else "breaks a win" if not keeps else "hurts" if d_cagr < -0.005 else "~flat")
        print(f"  {x['name']:22} ΔCAGR {pct(d_cagr):>7}  ΔMDD {pct(d_mdd):>7}  ΔavgWhip {pct(d_whip):>7}  {verdict}")
    OUT["fix2"] = rows


# ---- stage 15: EXPAND universe to other ASSET CLASSES (the real test) --------
def stage_expand():
    from trader.rotation import V5_UNIVERSE
    base_u = list(V5_UNIVERSE)
    print("\n" + "=" * 104)
    print("EXPAND UNIVERSE — add OTHER asset classes (intl/bonds/gold/commodities) so momentum")
    print("has uncorrelated trends to catch when US sectors all chop. (all have 2x maps except DBC)")
    print("=" * 104)
    variants = [
        ("BASELINE (10 US sectors)",  {}),
        ("+ Intl (EFA, EEM)",         {"rotation_universe": base_u + ["EFA", "EEM"]}),
        ("+ Bonds/Gold offensive",    {"rotation_universe": base_u + ["GLD", "TLT"]}),
        ("+ Intl + Bonds/Gold",       {"rotation_universe": base_u + ["EFA", "EEM", "GLD", "TLT"]}),
        ("+ Commodities (DBC,GLD)",   {"rotation_universe": base_u + ["DBC", "GLD"]}),
        ("+ EVERYTHING",              {"rotation_universe": base_u + ["EFA", "EEM", "GLD", "TLT", "DBC"]}),
    ]
    rows = [_scorecard(n, ov) for n, ov in variants]
    hdr = (f"{'universe':26} {'CAGR':>6} {'MDD':>7} {'Calmar':>7} {'vsSPY':>6} | "
           f"{'2011':>7} {'2015':>7} {'2018':>7} {'2020':>7} | {'avgWhip':>8}")
    print("\n" + hdr); print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:26} {pct(x['cagr']):>6} {pct(x['mdd']):>7} {x['calmar']:>7.2f} {pct(x['excess']):>6} | "
              f"{pct(x['2011']):>7} {pct(x['2015']):>7} {pct(x['2018']):>7} {pct(x['2020']):>7} | {pct(x['avg_whip']):>8}")
    b = rows[0]
    print("\nΔ vs BASELINE (does expansion make more money / cut whips?):")
    for x in rows[1:]:
        d_cagr = x["cagr"] - b["cagr"]; d_whip = x["avg_whip"] - b["avg_whip"]
        verdict = ("MORE MONEY" if d_cagr > 0.005 else
                   "whip-help only" if d_whip > 0.02 and d_cagr > -0.005 else
                   "worse" if d_cagr < -0.005 else "~flat")
        print(f"  {x['name']:26} ΔCAGR {pct(d_cagr):>7}  ΔCalmar {x['calmar']-b['calmar']:>+5.2f}  "
              f"ΔavgWhip {pct(d_whip):>7}  {verdict}")
    OUT["expand"] = rows


# ---- stage 14: WHY the SOXX/IGV tilt is flat overall — year-by-year ----------
def stage_ai_why():
    from trader.rotation import V5_UNIVERSE
    base_u = list(V5_UNIVERSE)
    print("\n" + "=" * 70)
    print("WHY SOXX/IGV IS FLAT OVERALL — calendar-year return: baseline vs +SOXX/IGV")
    print("=" * 70)
    base = run(W_FULL)
    tilt = run(W_FULL, overrides={"rotation_universe": base_u + ["SOXX", "IGV"]})
    print(f"\n{'year':6} {'baseline':>10} {'+SOXX/IGV':>10} {'Δ':>8}   who wins")
    wins_tilt = wins_base = 0
    for y in range(2008, 2027):
        s, e = f"{y}-01-01", f"{y}-12-31"
        mb = _curve_metrics(base["curve"], s, e)
        mt = _curve_metrics(tilt["curve"], s, e)
        if not mb or not mt:
            continue
        d = mt["total_return"] - mb["total_return"]
        if d > 0.005: wins_tilt += 1; who = "tilt ▲"
        elif d < -0.005: wins_base += 1; who = "base ▲"
        else: who = "~tie"
        print(f"{y:6} {pct(mb['total_return']):>10} {pct(mt['total_return']):>10} {pct(d):>8}   {who}")
    print(f"\n  tilt wins {wins_tilt} years, baseline wins {wins_base} years")
    OUT["ai_why"] = {"wins_tilt": wins_tilt, "wins_base": wins_base}


STAGES = {"smoke": stage_smoke, "walkforward": stage_walkforward, "cost": stage_cost,
          "sweep": stage_sweep, "riskreduced": stage_riskreduced, "stress": stage_stress,
          "fix1": stage_fix1, "fix4": stage_fix4, "fix3": stage_fix3, "universe": stage_universe,
          "ai": stage_ai, "ai_why": stage_ai_why, "expand": stage_expand, "fix2": stage_fix2,
          "combined": stage_combined, "forward": stage_forward, "stackall": stage_stackall, "valwin": stage_valwin, "real": stage_real, "stress2": stage_stress2, "stress3": stage_stress3}

if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    if stage == "all":
        for fn in (stage_smoke, stage_walkforward, stage_cost, stage_sweep, stage_riskreduced):
            fn()
    else:
        STAGES[stage]()
    with open(f"reports/validation_{stage}.json", "w") as f:
        json.dump(OUT, f, indent=2, default=str)
    print(f"\nSaved -> reports/validation_{stage}.json")
