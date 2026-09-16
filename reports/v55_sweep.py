#!/usr/bin/env python
"""
v55_sweep.py — V5.5 Balanced-Max parameter sweep.

Fixed (from user decisions):
  use_3x       = False      (kill the −$315k 3x tier)
  vol_cap      = 2.0        (bull ceiling, both proposals agree)
  weight       = squared    (keep p=2.0, no per-sector cap)
  breaker_sma  = 50         (50-day circuit breaker ON)

Swept:
  vol_cap_bear  ∈ {0.0, 0.25, 0.5}    bear-regime ceiling (0.0 = full GLD+TLT vault)
  vol_target    ∈ {0.10, 0.12, 0.15}  lower = more conservative leverage
  breaker_level ∈ {1.0, 0.5}          exposure cap when SPY < 50-SMA

Objective: maximize Calmar (CAGR/|MDD|) SUBJECT TO MDD ≤ 25%.
  Configs breaching −25% MDD are ranked below all compliant configs.

Run: cd ~/claude\ code/stock-trader && .venv/bin/python reports/v55_sweep.py
"""
import copy, sys, itertools
sys.path.insert(0, ".")
from trader.config import CONFIG
from trader.rotation import run_rotation_backtest, apply_v5
from trader import metrics

START, END = "2005-01-01", "2024-12-31"
MDD_BUDGET = 0.25   # hard ceiling for the "compliant" set

V4_CAGR, V4_MDD = 0.147, -0.315
V5_CAGR, V5_MDD, V5_SHARPE = 0.165, -0.368, 0.74


def build(bear, vt, brk):
    cfg = copy.deepcopy(CONFIG)
    apply_v5(cfg)
    cfg.backtest_start, cfg.backtest_end = START, END
    # ---- V5.5 fixed ----
    cfg.rotation_use_3x        = False
    cfg.rotation_vol_cap       = 2.0
    cfg.rotation_weight_squared = True
    cfg.rotation_breaker_sma   = 50
    # ---- swept ----
    cfg.rotation_vol_cap_bear  = bear
    cfg.rotation_vol_target    = vt
    cfg.rotation_breaker_level = brk
    return cfg


def run(cfg):
    r = run_rotation_backtest(cfg)
    m = metrics.summarize(r["broker"].equity_curve, r["broker"].trades,
                          cfg.starting_cash)
    return {
        "cagr"  : m["cagr"],
        "mdd"   : m["max_drawdown"],
        "sharpe": m.get("sharpe", 0),
        "calmar": m.get("calmar", 0),
        "winrate": m.get("win_rate", 0),
        "pf"    : m.get("profit_factor", 0),
        "final" : m["final_equity"],
    }


def score(m):
    """Calmar, but compliant configs (MDD ≤ budget) always beat non-compliant."""
    calmar = m["cagr"] / abs(m["mdd"]) if m["mdd"] < 0 else 0
    compliant = abs(m["mdd"]) <= MDD_BUDGET
    return (1 if compliant else 0, calmar)


def main():
    print("=" * 92)
    print("  V5.5 BALANCED-MAX SWEEP — objective: max Calmar s.t. MDD ≤ 25%")
    print(f"  Fixed: use_3x=OFF, bull_cap=2.0x, weight=squared, 50-day breaker=ON")
    print(f"  {START} → {END}  |  10-sector clean universe")
    print("=" * 92)

    bears   = [0.0, 0.25, 0.5]
    targets = [0.10, 0.12, 0.15]
    breaks  = [1.0, 0.5]

    print(f"\n  {'bear':>5} {'vTgt':>5} {'brk':>4} | {'CAGR':>7} {'MDD':>7} "
          f"{'Calmar':>7} {'Sharpe':>6} {'Win%':>5} {'PF':>5} | {'compliant':>9}")
    print("  " + "-" * 84)

    results = []
    for bear, vt, brk in itertools.product(bears, targets, breaks):
        cfg = build(bear, vt, brk)
        m = run(cfg)
        m["params"] = {"bear": bear, "vt": vt, "brk": brk}
        m["compliant"] = abs(m["mdd"]) <= MDD_BUDGET
        results.append(m)
        calmar = m["cagr"] / abs(m["mdd"]) if m["mdd"] < 0 else 0
        flag = "✓" if m["compliant"] else " "
        print(f"  {bear:>5.2f} {vt:>5.2f} {brk:>4.1f} | "
              f"{m['cagr']:>+6.1%} {m['mdd']:>+6.1%} {calmar:>7.2f} "
              f"{m['sharpe']:>6.2f} {m['winrate']*100:>4.0f}% {m['pf']:>5.2f} | "
              f"{flag:>9}")

    results.sort(key=score, reverse=True)

    print("\n" + "=" * 92)
    print("  TOP 5 BY OBJECTIVE (Calmar, compliant configs first)")
    print("=" * 92)
    print(f"  {'#':>2} {'bear':>5} {'vTgt':>5} {'brk':>4} | {'CAGR':>7} {'MDD':>7} "
          f"{'Calmar':>7} {'Sharpe':>6} {'Win%':>5} {'PF':>5} {'final$':>12}")
    print("  " + "-" * 86)
    for i, m in enumerate(results[:5], 1):
        p = m["params"]
        calmar = m["cagr"] / abs(m["mdd"]) if m["mdd"] < 0 else 0
        print(f"  {i:>2} {p['bear']:>5.2f} {p['vt']:>5.2f} {p['brk']:>4.1f} | "
              f"{m['cagr']:>+6.1%} {m['mdd']:>+6.1%} {calmar:>7.2f} "
              f"{m['sharpe']:>6.2f} {m['winrate']*100:>4.0f}% {m['pf']:>5.2f} "
              f"{m['final']:>12,.0f}")

    champ = results[0]
    p = champ["params"]
    calmar = champ["cagr"] / abs(champ["mdd"])
    print("\n" + "=" * 92)
    print("  ★ V5.5 BALANCED-MAX CHAMPION")
    print("=" * 92)
    print(f"  Params : vol_cap_bear={p['bear']}, vol_target={p['vt']}, "
          f"breaker_level={p['brk']}  (use_3x=False, bull_cap=2.0, breaker_sma=50)")
    print(f"\n  {'':14} {'V5.5':>10} {'V5':>10} {'V4 Turbo':>10}")
    print(f"  {'─'*14} {'─'*10} {'─'*10} {'─'*10}")
    print(f"  {'CAGR':14} {champ['cagr']:>+9.1%} {V5_CAGR:>+9.1%} {V4_CAGR:>+9.1%}")
    print(f"  {'Max Drawdown':14} {champ['mdd']:>+9.1%} {V5_MDD:>+9.1%} {V4_MDD:>+9.1%}")
    print(f"  {'Sharpe':14} {champ['sharpe']:>10.2f} {V5_SHARPE:>10.2f} {0.73:>10.2f}")
    print(f"  {'Calmar':14} {calmar:>10.2f} {V5_CAGR/abs(V5_MDD):>10.2f} "
          f"{V4_CAGR/abs(V4_MDD):>10.2f}")
    print(f"  {'Final $100k':14} {champ['final']:>10,.0f} {'2,100,141':>10} {'1,542,587':>10}")

    if champ["compliant"]:
        print(f"\n  ✓ Meets MDD ≤ 25% budget with Calmar {calmar:.2f} "
              f"(V5 was {V5_CAGR/abs(V5_MDD):.2f})")
    else:
        print(f"\n  ⚠ NO config met MDD ≤ 25%. Best available MDD: {champ['mdd']:.1%}")
        print(f"    The drawdown floor is structural — even at full risk-off, the "
              f"defensive\n    sleeve (GLD+TLT) itself drew down in 2013/2022.")

    print("=" * 92)


if __name__ == "__main__":
    main()
