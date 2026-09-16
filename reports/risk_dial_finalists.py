"""Head-to-head on the risk-dial finalists, including the sub-period robustness check
the sweep only ran on its top-12 by Calmar.

A config that wins the full sample by winning one era is curve-fit, not better — so
every candidate is re-run over three disjoint sub-periods and against the live baseline.
"""
from __future__ import annotations
import os, sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opt_harness import load_data, simulate, metrics, spy_bh, FETCH_END
from live_checkup import live_sim_config

PERIODS = [("2006-2012", "2006-01-01", "2012-12-31"),
           ("2013-2019", "2013-01-01", "2019-12-31"),
           ("2020-2026", "2020-01-01", FETCH_END)]

CANDIDATES = [
    ("LIVE (today)",       {}),
    ("A  best-Calmar",     dict(vol_target=0.30, vol_cap_bull=1.25, vol_window=8,  max_weight=0.60)),
    ("B  Calmar+CAGR",     dict(vol_target=0.35, vol_cap_bull=1.25, vol_window=8,  max_weight=0.60)),
    ("C  more CAGR",       dict(vol_target=0.40, vol_cap_bull=1.50, vol_window=8,  max_weight=0.40)),
    ("D  high CAGR",       dict(vol_target=0.40, vol_cap_bull=1.75, vol_window=8,  max_weight=0.60)),
    ("E  max CAGR",        dict(vol_target=0.35, vol_cap_bull=2.00, vol_window=8,  max_weight=0.60)),
]


def run(df, base, over, s="2006-01-01", e=FETCH_END):
    cfg = dict(base); cfg.update(over)
    m = metrics(simulate(df, cfg, s, e))
    return m or None


def main():
    df = load_data()
    base, _ = live_sim_config()
    spy = metrics(spy_bh(df, "2006-01-01", FETCH_END))

    print(f"SPY buy&hold: CAGR {spy['cagr']:+.2%}  MDD {spy['mdd']:+.2%}\n")
    hdr = "%-16s %8s %8s %7s %7s   %s"
    print(hdr % ("CONFIG", "CAGR", "MDD", "Calmar", "Sharpe", "  ".join(p[0] for p in PERIODS)))
    print("-" * 96)

    rows = []
    for name, over in CANDIDATES:
        m = run(df, base, over)
        subs = [run(df, base, over, s, e) for _, s, e in PERIODS]
        rows.append((name, over, m, subs))
        print(hdr % (name, f"{m['cagr']:+.2%}", f"{m['mdd']:+.2%}",
                     f"{m['cagr']/abs(m['mdd']):.3f}", f"{m['sharpe']:.2f}",
                     "  ".join(f"{s['cagr']:+7.2%}" if s else "    n/a" for s in subs)))

    # Beating the live baseline in every era is the bar for "actually better".
    base_subs = [run(df, base, {}, s, e) for _, s, e in PERIODS]
    print("\nsub-period verdict vs LIVE (a config must not lose any era by >2pp):")
    for name, over, m, subs in rows[1:]:
        worst = min((s["cagr"] - b["cagr"]) for s, b in zip(subs, base_subs) if s and b)
        ok = worst >= -0.02
        print(f"  {name:<16} worst era gap {worst:+.2%}   -> {'ROBUST' if ok else 'FRAGILE'}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "risk_dial_finalists.json")
    with open(out, "w") as f:
        json.dump([{"name": n, "overrides": o,
                    "cagr": m["cagr"], "mdd": m["mdd"], "sharpe": m["sharpe"],
                    "calmar": m["cagr"] / abs(m["mdd"]),
                    "subperiods": {p[0]: (s["cagr"] if s else None)
                                   for p, s in zip(PERIODS, subs)}}
                   for n, o, m, subs in rows], f, indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
