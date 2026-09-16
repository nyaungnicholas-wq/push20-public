import sys, json, os, warnings
from pathlib import Path
warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from trader.config import Config
from trader.rotation import apply_daily_champion, run_rotation_backtest
from trader.metrics import summarize

W_BASELINE = ("2006-01-01", "2026-06-18")
W_TODAY = ("2006-01-01", "2026-08-03")
BASELINE = {
    "source": "reports/validation_stress2.json (2026-06-23) + reports/live_checkup.py EXPECT_CAGR",
    "window": "2006-01-01..2026-06-18",
    "cagr": 0.19719064283938925,
    "mdd": -0.35487582817285124,
    "trades": 3971
}

def run_backtest(window):
    cfg = Config()
    apply_daily_champion(cfg)
    cfg.backtest_start, cfg.backtest_end = window
    res = run_rotation_backtest(cfg)
    book = res["broker"]
    m = summarize(book.equity_curve, book.trades, cfg.starting_cash)
    bench = res.get("benchmark", {})
    return {"metrics": m, "benchmark": bench}

def compute_diff(run, base):
    d = {}
    d["cagr_delta_pp"] = (run["cagr"] - base["cagr"]) * 100
    d["mdd_delta_pp"] = (run["max_drawdown"] - base["mdd"]) * 100
    d["trades_delta"] = run["num_trades"] - base["trades"]
    d["trades_delta_pct"] = (run["num_trades"] - base["trades"]) / base["trades"] * 100
    regression_reasons = []
    if abs(d["cagr_delta_pp"]) > 1.0:
        regression_reasons.append(f"CAGR {d['cagr_delta_pp']:+.1f}pp")
    if abs(d["mdd_delta_pp"]) > 2.0:
        regression_reasons.append(f"MDD {d['mdd_delta_pp']:+.1f}pp")
    d["regression_reasons"] = regression_reasons
    d["verdict"] = "REGRESSION" if regression_reasons else "PASS"
    return d

def format_table(results):
    rows = []
    headers = "Config | CAGR | MaxDD | Sharpe | Calmar | Win% | Trades | vs SPY"
    rows.append(headers)
    rows.append("-" * len(headers))
    for label, res in results.items():
        m = res["metrics"]
        bench = res.get("benchmark", {})
        spy_cagr = bench.get("cagr", 0)
        vs_spy = m["cagr"] - spy_cagr
        row = (
            f"{label:15} | "
            f"{m['cagr']*100:5.1f}% | "
            f"{m['max_drawdown']*100:5.1f}% | "
            f"{m['sharpe']:5.2f} | "
            f"{m['calmar']:5.2f} | "
            f"{m['win_rate']*100:5.1f}% | "
            f"{m['num_trades']:5d} | "
            f"{vs_spy*100:+5.1f}%"
        )
        rows.append(row)
    # SPY buy-and-hold row for baseline window
    spy_baseline = results["Baseline"]["benchmark"]
    # benchmark only carries cagr/max_drawdown/equity_curve — print "n/a" rather than fake zeros
    rows.append(
        f"{'SPY buy-hold':15} | "
        f"{spy_baseline.get('cagr', float('nan'))*100:5.1f}% | "
        f"{spy_baseline.get('max_drawdown', float('nan'))*100:5.1f}% | "
        f"{'n/a':>5} | {'n/a':>5} | {'n/a':>6} | {'n/a':>5} | {'n/a':>6}"
    )
    return "\n".join(rows)

def main():
    print("Running baseline window backtest...", file=sys.stderr)
    baseline_res = run_backtest(W_BASELINE)
    print("Running today window backtest...", file=sys.stderr)
    today_res = run_backtest(W_TODAY)

    results = {
        "Baseline": baseline_res,
        "Today": today_res
    }

    print("\nMetrics Table:")
    print(format_table(results))

    diff = compute_diff(baseline_res["metrics"], BASELINE)
    print("\nDIFF vs recorded baseline:")
    print(f"  CAGR delta: {diff['cagr_delta_pp']:+.2f} pp")
    print(f"  MDD delta:  {diff['mdd_delta_pp']:+.2f} pp")
    print(f"  Trades delta: {diff['trades_delta']} ({diff['trades_delta_pct']:+.1f}%)")
    print("  (Trade count from baseline recorded at 10bps slippage; current run uses default slippage.)")
    print(f"VERDICT: {diff['verdict']}")
    if diff["regression_reasons"]:
        print("  Regression due to: " + ", ".join(diff["regression_reasons"]))

    # Save JSON
    json_out = {
        "windows": {
            "baseline": {
                "config": "V7 PUSH-20 GATED v2 (live cfg, no overrides)",
                "window": W_BASELINE,
                "metrics": baseline_res["metrics"],
                "benchmark": baseline_res["benchmark"]
            },
            "today": {
                "config": "V7 PUSH-20 GATED v2 (live cfg, no overrides)",
                "window": W_TODAY,
                "metrics": today_res["metrics"],
                "benchmark": today_res["benchmark"]
            }
        },
        "recorded_baseline": BASELINE,
        "diff": diff
    }
    json_path = REPO / "reports" / "verify_gated_2026-08-03.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(json_out, f, indent=2, default=str)
    print(f"\nWrote {json_path}", file=sys.stderr)

    # Append to RESULTS.md
    results_md = REPO / "experiments" / "RESULTS.md"
    results_md.parent.mkdir(parents=True, exist_ok=True)
    if not results_md.exists():
        with open(results_md, "w") as f:
            f.write("# Simulation results\n")
    m = baseline_res["metrics"]
    line = (
        f"- 2026-08-03 | V7 PUSH-20 GATED v2 (live cfg, no overrides) | "
        f"{W_BASELINE[0]}..{W_BASELINE[1]} | CAGR {m['cagr']*100:.1f}% "
        f"MDD {m['max_drawdown']*100:.1f}% Sharpe {m['sharpe']:.2f} trades {m['num_trades']} | "
        f"{diff['verdict']} | experiments/2026-08-03_v7push20_gated_verify.py\n"
    )
    with open(results_md, "a") as f:
        f.write(line)
    print(f"Appended to {results_md}", file=sys.stderr)

    if diff["verdict"] == "PASS":
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()