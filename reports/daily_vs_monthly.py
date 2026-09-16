"""Daily- vs monthly-cadence comparison for the V4 Turbo champion.

Clean 13-sector universe, 2005-2024. The ONLY difference between the two runs
is the rebalance schedule: monthly (real tranched _rebalance_dates) vs daily
(every trading day). Everything else — signal, weighting, vol overlay, costs —
is identical champion config.
"""
import copy
import pandas as pd
from trader.config import CONFIG
from trader import rotation, metrics

CLEAN_13 = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLB",
            "XLP", "XLU", "XLRE", "XLC", "SMH", "QQQ"]

START, END = "2005-01-01", "2024-12-31"

_ORIG_REBAL = rotation._rebalance_dates


def _make_cfg():
    cfg = copy.deepcopy(CONFIG)
    cfg.backtest_start = START
    cfg.backtest_end = END
    rotation.apply_champion(cfg)
    cfg.rotation_universe = list(CLEAN_13)
    return cfg


def run(cadence):
    cfg = _make_cfg()
    if cadence == "daily":
        # one tranche, rebalance EVERY trading day in the window
        cfg.rotation_tranches = (0,)

        def _all_days(index, start, end, offset=0):
            return list(index[(index >= start) & (index <= end)])

        rotation._rebalance_dates = _all_days
    else:
        rotation._rebalance_dates = _ORIG_REBAL

    result = rotation.run_rotation_backtest(cfg, verbose=False)
    rotation._rebalance_dates = _ORIG_REBAL  # restore

    broker = result["broker"]
    m = metrics.summarize(broker.equity_curve, broker.trades, cfg.starting_cash)
    m["_trades"] = len(broker.trades)
    m["_bench"] = result.get("benchmark", {})
    return m


print(f"Clean 13-sector universe | {START} → {END} | champion config\n")
res = {}
for cad in ("monthly", "daily"):
    print(f"  running {cad} ...", flush=True)
    res[cad] = run(cad)

bench = res["monthly"]["_bench"]
print("\n=== RESULTS ===")
hdr = f"{'metric':16}{'MONTHLY':>14}{'DAILY':>14}{'SPY B&H':>14}"
print(hdr)
print("-" * len(hdr))


def pct(x):
    return f"{x*100:>13.1f}%"


def money(x):
    return f"{x:>14,.0f}"


rows = [
    ("Final equity", "final_equity", money),
    ("CAGR", "cagr", pct),
    ("Max drawdown", "max_drawdown", pct),
    ("Sharpe", "sharpe", lambda x: f"{x:>14.2f}"),
]
for label, key, fmt in rows:
    mv = res["monthly"].get(key, float("nan"))
    dv = res["daily"].get(key, float("nan"))
    bv = bench.get(key, float("nan"))
    print(f"{label:16}{fmt(mv)}{fmt(dv)}{fmt(bv)}")

print(f"{'# trades':16}{res['monthly']['_trades']:>14,}{res['daily']['_trades']:>14,}{'':>14}")

mc, dc = res["monthly"]["cagr"], res["daily"]["cagr"]
print(f"\nDaily vs Monthly: {(dc-mc)*100:+.1f}pp CAGR  "
      f"({res['daily']['_trades']/max(1,res['monthly']['_trades']):.1f}x the trades)")
