"""Risk-dial sweep — find the config that raises CAGR without paying for it in drawdown.

The brief: "higher risk dial, but maximize CAGR while minimizing drawdown." Those pull
against each other, so this ranks by Calmar (CAGR / |MDD|) — return per unit of the pain
you actually feel — and reports the frontier so the tradeoff is explicit rather than
hidden inside one recommended number.

Baseline is whatever is live in DAILY_CHAMPION_FLAGS, so this always compares against
the strategy that is actually trading.

Usage: python reports/risk_dial_sweep.py [--quick]
"""
from __future__ import annotations
import os, sys, json, itertools, argparse, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opt_harness import load_data, simulate, metrics, spy_bh, FETCH_END
from live_checkup import live_sim_config

START = "2006-01-01"
# Sub-periods: a config that only wins in one era is curve-fit, not better.
PERIODS = [("2006-2012", "2006-01-01", "2012-12-31"),
           ("2013-2019", "2013-01-01", "2019-12-31"),
           ("2020-2026", "2020-01-01", FETCH_END)]


def run(df, cfg, start=START, end=FETCH_END):
    try:
        m = metrics(simulate(df, cfg, start, end))
        return m or None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="coarse grid")
    args = ap.parse_args()

    df = load_data()
    base_cfg, _ = live_sim_config()
    base = run(df, base_cfg)
    spy = metrics(spy_bh(df, START, FETCH_END))
    print(f"LIVE baseline : CAGR {base['cagr']:+.2%}  MDD {base['mdd']:+.2%}  "
          f"Calmar {base['cagr']/abs(base['mdd']):.3f}  Sharpe {base['sharpe']:.2f}")
    print(f"SPY buy&hold  : CAGR {spy['cagr']:+.2%}  MDD {spy['mdd']:+.2%}")
    print()

    if args.quick:
        grid = dict(vol_target=[0.30, 0.40, 0.50], vol_cap_bull=[1.5, 1.75, 2.0],
                    vol_window=[12, 20], top_n=[3], max_weight=[0.50])
    else:
        grid = dict(
            vol_target=[0.22, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60],
            vol_cap_bull=[1.25, 1.5, 1.75, 2.0, 2.25],
            vol_window=[8, 12, 20, 25],
            top_n=[2, 3, 4],
            max_weight=[0.40, 0.50, 0.60],
        )

    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    print(f"sweeping {len(combos)} configs over {START}..{FETCH_END}", flush=True)

    rows = []
    for i, vals in enumerate(combos):
        cfg = dict(base_cfg)
        cfg.update(dict(zip(keys, vals)))
        m = run(df, cfg)
        if not m or not m.get("mdd"):
            continue
        calmar = m["cagr"] / abs(m["mdd"])
        rows.append({**dict(zip(keys, vals)), "cagr": m["cagr"], "mdd": m["mdd"],
                     "sharpe": m["sharpe"], "calmar": calmar})
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(combos)}", flush=True)

    # Robustness: re-run the top Calmar candidates in every sub-period. A config that
    # loses to the live baseline in any era is rejected regardless of full-sample score.
    rows.sort(key=lambda r: -r["calmar"])
    print("\nrobustness-checking top 12 by Calmar across sub-periods", flush=True)
    base_sub = {}
    for name, s, e in PERIODS:
        m = run(df, base_cfg, s, e)
        base_sub[name] = m["cagr"] if m else None

    for r in rows[:12]:
        cfg = dict(base_cfg)
        cfg.update({k: r[k] for k in keys})
        sub, ok = {}, True
        for name, s, e in PERIODS:
            m = run(df, cfg, s, e)
            sub[name] = m["cagr"] if m else None
            if m is None or base_sub[name] is None or m["cagr"] < base_sub[name] - 0.02:
                ok = False
        r["sub"] = sub
        r["robust"] = ok

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "risk_dial_sweep.json")
    with open(out, "w") as f:
        json.dump({"baseline": {**base_cfg, "cagr": base["cagr"], "mdd": base["mdd"],
                                "sharpe": base["sharpe"],
                                "calmar": base["cagr"] / abs(base["mdd"])},
                   "baseline_subperiods": base_sub,
                   "spy": {"cagr": spy["cagr"], "mdd": spy["mdd"]},
                   "results": rows}, f, indent=1)

    print(f"\n=== TOP 15 BY CALMAR (return per unit of drawdown) ===")
    hdr = "%-6s %-6s %-4s %-3s %-5s  %8s %8s %7s %7s %s"
    print(hdr % ("vt", "cap", "win", "n", "maxw", "CAGR", "MDD", "Calmar", "Sharpe", "robust"))
    for r in rows[:15]:
        print(hdr % (r["vol_target"], r["vol_cap_bull"], r["vol_window"], r["top_n"],
                     r["max_weight"], f"{r['cagr']:+.2%}", f"{r['mdd']:+.2%}",
                     f"{r['calmar']:.3f}", f"{r['sharpe']:.2f}",
                     ("YES" if r.get("robust") else "no") if "robust" in r else ""))

    print(f"\n=== HIGHEST CAGR INSIDE EACH DRAWDOWN BUDGET ===")
    for budget in (-0.25, -0.30, -0.35, -0.40, -0.45):
        elig = [r for r in rows if r["mdd"] >= budget]
        if not elig:
            print(f"  MDD >= {budget:.0%}: (none)"); continue
        b = max(elig, key=lambda r: r["cagr"])
        print(f"  MDD >= {budget:.0%}: CAGR {b['cagr']:+.2%}  MDD {b['mdd']:+.2%}  "
              f"Calmar {b['calmar']:.3f}  "
              f"[vt={b['vol_target']} cap={b['vol_cap_bull']} win={b['vol_window']} "
              f"n={b['top_n']} maxw={b['max_weight']}]")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
