#!/usr/bin/env python3
"""Stock trading simulation — CLI entry point.

Usage:
    python main.py run                  # START the live two-speed loop (recommended)
    python main.py paper                # run one manual paper-trading cycle
    python main.py status               # show current paper account + performance
    python main.py reset                # wipe paper account state
    python main.py backtest [--verbose] # run historical backtest
    python main.py walkforward          # run 4-window overfitting check
"""

import argparse
import sys
import json
import os

from trader.config import CONFIG
from trader import metrics


def cmd_run(args):
    """Start the continuous two-speed live loop."""
    from trader.live_loop import run_live
    run_live(CONFIG, verbose=True)


def cmd_paper(args):
    from trader.paper_live import run_cycle
    run_cycle(CONFIG, verbose=True)


def cmd_status(args):
    from trader.paper_live import _load_state, STATE_PATH
    from trader.alpaca_feed import AlpacaFeed
    if not os.path.exists(STATE_PATH):
        print("No paper account yet. Run: python main.py paper")
        return
    broker = _load_state(CONFIG)

    # Try to get live prices for accurate equity
    feed   = AlpacaFeed(CONFIG.alpaca_key, CONFIG.alpaca_secret)
    syms   = list(broker.positions.keys())
    prices = feed.get_latest_prices(syms) if syms else {}

    eq = broker.equity(prices) if prices else (
        broker.equity_curve[-1][1] if broker.equity_curve else CONFIG.starting_cash
    )
    pnl = eq - CONFIG.starting_cash

    print(f"\n{'='*50}")
    print(f"  PAPER ACCOUNT STATUS")
    print(f"{'='*50}")
    print(f"  Cash             : ${broker.cash:,.2f}")
    print(f"  Total equity     : ${eq:,.2f}  ({pnl:+,.2f})")
    print(f"  Open positions   : {len(broker.positions)}")
    print(f"  Total trades     : {len(broker.trades)}")

    if broker.positions:
        print(f"\n  Holdings:")
        heat = broker.portfolio_heat(prices) if prices else 0
        for sym, p in broker.positions.items():
            px  = prices.get(sym, p.avg_price)
            pct = p.unrealized_pct(px)
            r   = p.r_multiple(px)
            trail = " [trailing]" if p.trailing_active else ""
            print(f"    {sym:5}  {p.shares:>5.0f} shares  "
                  f"avg ${p.avg_price:.2f} → ${px:.2f}  "
                  f"({pct:+.1%})  R:{r:.1f}x{trail}")
        print(f"\n  Portfolio heat   : {heat:.1%}")
        cb = broker.circuit_breaker_open(prices)
        if cb:
            print(f"  ⚠ Circuit breaker TRIPPED — new buys halted")

    m = metrics.summarize(broker.equity_curve, broker.trades, CONFIG.starting_cash)
    if m and m.get("num_closed", 0) > 0:
        print(f"\n  Performance:")
        print(metrics.format_summary(m))
    else:
        print(f"\n  (Performance metrics available once trades are closed)")
    print()


def cmd_reset(args):
    from trader.paper_live import STATE_PATH
    confirm = input("Reset paper account? This deletes all trade history. (yes/no): ")
    if confirm.lower() == "yes":
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
            print("Paper account reset to $100,000.")
        else:
            print("Nothing to reset.")
    else:
        print("Cancelled.")


def cmd_backtest(args):
    from trader.backtest import run_backtest
    print(f"Running backtest {CONFIG.backtest_start} → {CONFIG.backtest_end} "
          f"on {len(CONFIG.universe)} symbols...\n")
    result = run_backtest(CONFIG, news=args.news, verbose=args.verbose)
    broker = result["broker"]
    m = metrics.summarize(broker.equity_curve, broker.trades, CONFIG.starting_cash)
    print("\n=== Backtest results ===")
    print(metrics.format_summary(m))
    os.makedirs("reports", exist_ok=True)
    with open("reports/backtest_summary.json", "w") as f:
        json.dump(m, f, indent=2, default=str)
    print("\nSaved → reports/backtest_summary.json")


def cmd_rotation(args):
    from trader.rotation import run_rotation_backtest
    print(f"Running sector-rotation backtest {CONFIG.backtest_start} → "
          f"{CONFIG.backtest_end}\n"
          f"  Universe : {CONFIG.rotation_universe}\n"
          f"  Hold     : top {CONFIG.rotation_top_n} by blended "
          f"{tuple(CONFIG.rotation_lookbacks)}-day momentum\n"
          f"  Rebalance: monthly, tranched across days {tuple(CONFIG.rotation_tranches)}"
          f" (positions averaged over rebalance day)\n")
    result = run_rotation_backtest(CONFIG, verbose=args.verbose)
    broker = result["broker"]
    m = metrics.summarize(broker.equity_curve, broker.trades, CONFIG.starting_cash)
    bench = result.get("benchmark", {})

    print("\n=== Sector rotation results ===")
    print(metrics.format_summary(m))

    if bench:
        print("\n=== vs SPY buy & hold (same window, same data) ===")
        print(f"  {'':18}{'ROTATION':>14}{'SPY B&H':>14}{'edge':>12}")
        rows = [
            ("Final equity",  m["final_equity"], bench["final_equity"], "money"),
            ("CAGR",          m["cagr"],         bench["cagr"],         "pct"),
            ("Total return",  m["total_return"], bench["total_return"], "pct"),
            ("Max drawdown",  m["max_drawdown"], bench["max_drawdown"], "pct"),
        ]
        for label, a, b, kind in rows:
            if kind == "money":
                print(f"  {label:18}{a:>14,.0f}{b:>14,.0f}{a-b:>+12,.0f}")
            else:
                print(f"  {label:18}{a:>13.1%}{b:>13.1%}{(a-b)*100:>+11.1f}pp")
        verdict = ("BEATS SPY" if m["final_equity"] > bench["final_equity"]
                   else "underperforms SPY")
        print(f"\n  {verdict}: ${m['final_equity']:,.0f} vs ${bench['final_equity']:,.0f} "
              f"({(m['final_equity']/bench['final_equity']-1)*100:+.1f}% relative)")

    os.makedirs("reports", exist_ok=True)
    with open("reports/rotation_summary.json", "w") as f:
        json.dump({"strategy": m, "spy_benchmark":
                   {k: v for k, v in bench.items() if k != "equity_curve"}},
                  f, indent=2, default=str)
    print("\nSaved → reports/rotation_summary.json")


def cmd_rotation_v2(args):
    """Champion config from the 20-year research loop (reports/champion_config.json).

    Found by hill-climbing with an anti-overfit robustness gate over 2005-2024:
      momentum-weighted sizing + 12-month momentum + GLD as the risk-off asset.
    Beats SPY buy-and-hold on every test window (2005-24, 2005-14, 2015-24, 2018-24).
    The original `rotation` command is left exactly as-is.
    """
    import copy
    from trader.rotation import run_rotation_backtest, apply_champion, CHAMPION_FLAGS

    cfg = copy.deepcopy(CONFIG)
    cfg.backtest_start = args.start
    cfg.backtest_end = args.end
    apply_champion(cfg)   # single source of truth: rotation.CHAMPION_FLAGS

    print(f"Running ROTATION-V2 (V4 Turbo champion) backtest "
          f"{cfg.backtest_start} → {cfg.backtest_end}\n"
          f"  Universe  : {cfg.rotation_universe}\n"
          f"  Hold      : top {cfg.rotation_top_n} by 12-month momentum  "
          f"[return-proportional weighting]\n"
          f"  Risk-off  : {cfg.rotation_defensive_symbol}\n"
          f"  Vol target: {cfg.rotation_vol_target:.0%} two-way  "
          f"(cap {cfg.rotation_vol_cap:.1f}x via 2x ETFs)\n"
          f"  Rebalance : monthly, tranched across days {tuple(cfg.rotation_tranches)}\n")

    result = run_rotation_backtest(cfg, verbose=args.verbose)
    broker = result["broker"]
    m = metrics.summarize(broker.equity_curve, broker.trades, cfg.starting_cash)
    bench = result.get("benchmark", {})

    print("\n=== Rotation-v2 (champion) results ===")
    print(metrics.format_summary(m))

    if bench:
        print("\n=== vs SPY buy & hold (same window, same data) ===")
        print(f"  {'':18}{'ROT-V2':>14}{'SPY B&H':>14}{'edge':>12}")
        rows = [
            ("Final equity",  m["final_equity"], bench["final_equity"], "money"),
            ("CAGR",          m["cagr"],         bench["cagr"],         "pct"),
            ("Max drawdown",  m["max_drawdown"], bench["max_drawdown"], "pct"),
        ]
        for label, a, b, kind in rows:
            if kind == "money":
                print(f"  {label:18}{a:>14,.0f}{b:>14,.0f}{a-b:>+12,.0f}")
            else:
                print(f"  {label:18}{a:>13.1%}{b:>13.1%}{(a-b)*100:>+11.1f}pp")
        verdict = ("BEATS SPY" if m["final_equity"] > bench["final_equity"]
                   else "underperforms SPY")
        print(f"\n  {verdict}: ${m['final_equity']:,.0f} vs ${bench['final_equity']:,.0f} "
              f"({(m['final_equity']/bench['final_equity']-1)*100:+.1f}% relative)")

    os.makedirs("reports", exist_ok=True)
    with open("reports/rotation_v2_summary.json", "w") as f:
        json.dump({"strategy": m, "spy_benchmark":
                   {k: v for k, v in bench.items() if k != "equity_curve"}},
                  f, indent=2, default=str)
    print("\nSaved → reports/rotation_v2_summary.json")


def cmd_rotation_v5(args):
    """V5 Hyper-Drive: 10-sector high-beta universe + momentum-squared + 3-tier leverage.

    Changes vs V4 Turbo champion:
      Universe  : 10 high-beta sectors (removed XLP/XLU/XLRE — structural laggards)
      Weighting : momentum-squared R²/ΣR² (stronger concentration in the leader)
      Leverage  : 3-tier (1x→2x→3x) with asymmetric vol cap:
                    SPY > 200 SMA → cap 3.0x via TECL/TQQQ/SOXL/FAS
                    SPY < 200 SMA → hard cap 1.5x (conservative in bear markets)
      Defensive : GLD+TLT (unchanged — BIL proved −1.8pp worse in backtests)
      MDD limit : accepted −50% (aggressive tier)
    """
    import copy
    from trader.rotation import run_rotation_backtest, apply_v5, V5_FLAGS, V5_UNIVERSE

    cfg = copy.deepcopy(CONFIG)
    cfg.backtest_start = args.start
    cfg.backtest_end = args.end
    apply_v5(cfg)

    print(f"Running ROTATION-V5 (Hyper-Drive) backtest "
          f"{cfg.backtest_start} → {cfg.backtest_end}\n"
          f"  Universe  : {cfg.rotation_universe}\n"
          f"  Hold      : top {cfg.rotation_top_n} by 12-month momentum  "
          f"[momentum-SQUARED weighting]\n"
          f"  Risk-off  : {cfg.rotation_defensive_symbol}\n"
          f"  Leverage  : 3-tier (1x→2x→3x) | vol_target={cfg.rotation_vol_target:.0%}\n"
          f"              Bull cap {cfg.rotation_vol_cap:.1f}x (SPY>200SMA)  "
          f"Bear cap {cfg.rotation_vol_cap_bear:.1f}x (SPY<200SMA)\n"
          f"  3x ETFs   : TECL/TQQQ/SOXL/FAS (available 2008/2010+; 2x fallback prior)\n"
          f"  Rebalance : monthly, tranched across days {tuple(cfg.rotation_tranches)}\n")

    result = run_rotation_backtest(cfg, verbose=args.verbose)
    broker = result["broker"]
    m = metrics.summarize(broker.equity_curve, broker.trades, cfg.starting_cash)
    bench = result.get("benchmark", {})

    print("\n=== Rotation-v5 (Hyper-Drive) results ===")
    print(metrics.format_summary(m))

    if bench:
        print("\n=== vs SPY buy & hold  &  V4 Turbo champion ===")
        print(f"  {'':18}{'ROT-V5':>14}{'V4 Turbo':>14}{'SPY B&H':>14}")
        v4_cagr, v4_mdd = 0.147, -0.315
        rows = [
            ("Final equity",  m["final_equity"], None,    bench["final_equity"], "money"),
            ("CAGR",          m["cagr"],         v4_cagr, bench["cagr"],         "pct"),
            ("Max drawdown",  m["max_drawdown"], v4_mdd,  bench["max_drawdown"], "pct"),
        ]
        for label, v5v, v4v, bv, kind in rows:
            if kind == "money":
                print(f"  {label:18}{v5v:>14,.0f}{'—':>14}{bv:>14,.0f}")
            else:
                v4s = f"{v4v:.1%}" if v4v is not None else "—"
                print(f"  {label:18}{v5v:>13.1%}{v4s:>14}{bv:>13.1%}")
        verdict = ("BEATS V4 TURBO" if m["cagr"] > v4_cagr else "V4 Turbo still leads on CAGR")
        print(f"\n  {verdict}  |  V5: {m['cagr']:.1%} vs V4: {v4_cagr:.1%}  "
              f"({(m['cagr']-v4_cagr)*100:+.1f}pp)")
        if m["max_drawdown"] < -0.50:
            print(f"  ⚠ MDD {m['max_drawdown']:.1%} exceeded −50% target — "
                  f"consider tightening vol_cap_bear or vol_floor")

    os.makedirs("reports", exist_ok=True)
    with open("reports/rotation_v5_summary.json", "w") as f:
        json.dump({
            "strategy": m,
            "v5_flags": {k: str(v) for k, v in V5_FLAGS.items()},
            "universe": V5_UNIVERSE,
            "spy_benchmark": {k: v for k, v in bench.items() if k != "equity_curve"},
        }, f, indent=2, default=str)
    print("\nSaved → reports/rotation_v5_summary.json")


def cmd_rotation_live(args):
    """Run the champion (rotation-v2) against the live Alpaca paper account.

    Default is a DRY RUN (prints the rebalance plan, sends nothing). Pass --live
    to actually submit orders. Run monthly — the strategy rebalances monthly.
    """
    if getattr(args, "selftest", False):
        from trader.rotation_live import selftest
        return selftest()
    if getattr(args, 'release', False):
        from trader.rotation_live import run_release
        return run_release(CONFIG, dry_run=not args.live)
    if args.scheduled:
        from trader.rotation_live import run_scheduled
        run_scheduled(CONFIG, dry_run=not args.live,
                      next_open=(args.session == "next-open"))
    else:
        # --live WITHOUT --scheduled used to reach the bare primitive, skipping
        # ten guards and defaulting complete_fills True (which re-sends the whole
        # book if run twice in an evening). Refused. The dry run still works and
        # is the useful manual case: it prints the plan and sends nothing.
        if args.live:
            print("refusing: `--live` without `--scheduled` bypasses the "
                  "pre-trade risk gate, min_hold_days, the market-open check, "
                  "the data-freshness assert and the session idempotency key, "
                  "and would re-send the entire book if run twice.\n"
                  "  to rebalance now, gated:  main.py rotation-live --scheduled --live\n"
                  "  to release a queued plan: main.py rotation-live --release --live\n"
                  "  to see the plan only:     main.py rotation-live")
            return 2
        from trader.rotation_live import run_rotation_cycle
        run_rotation_cycle(CONFIG, dry_run=True, verbose=True)


def cmd_montecarlo(args):
    from trader.montecarlo import run_monte_carlo, format_report
    from trader.config import Config
    # Override backtest window to 20 years
    cfg = Config()
    cfg.backtest_start = args.start
    cfg.backtest_end   = args.end
    import os
    cfg.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    cfg.alpaca_key        = os.getenv("ALPACA_KEY")
    cfg.alpaca_secret     = os.getenv("ALPACA_SECRET")
    result = run_monte_carlo(cfg, n_sims=args.sims, block_size=15)
    print(format_report(result))
    os.makedirs("reports", exist_ok=True)
    import json
    clean = {k: v for k, v in result.items() if k != "sims"}
    clean["sims_count"] = len(result["sims"])
    with open("reports/montecarlo.json", "w") as f:
        json.dump(clean, f, indent=2, default=str)
    print("Full results saved → reports/montecarlo.json")


def cmd_alpaca(args):
    """Show what Alpaca paper account currently holds."""
    from trader.alpaca_broker import AlpacaBroker
    alpaca = AlpacaBroker(CONFIG.alpaca_key, CONFIG.alpaca_secret)
    if not alpaca.connected():
        print("Not connected. Check ALPACA_KEY and ALPACA_SECRET in .env")
        return
    print("\n" + alpaca.sync_report())


def cmd_walkforward(args):
    from trader.backtest import run_walk_forward
    run_walk_forward(CONFIG)


def main():
    parser = argparse.ArgumentParser(
        description="Stock trading simulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  run          Start the live two-speed loop (position check every 60s,
               macro news every 30min, signals daily at 4pm ET)
  paper        Run one manual signal cycle right now
  status       Show account balance, open positions, performance
  reset        Wipe the paper account and start fresh
  backtest     Run historical backtest (2022-2024)
  walkforward  Run 4-window overfitting check
        """
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run",
        help="start continuous live loop").set_defaults(func=cmd_run)

    sub.add_parser("paper",
        help="run one manual cycle now").set_defaults(func=cmd_paper)

    sub.add_parser("status",
        help="show account and positions").set_defaults(func=cmd_status)

    sub.add_parser("reset",
        help="wipe paper account").set_defaults(func=cmd_reset)

    p_bt = sub.add_parser("backtest", help="run historical backtest")
    p_bt.add_argument("--news", choices=["lexicon", "neutral"], default="neutral")
    p_bt.add_argument("--verbose", action="store_true")
    p_bt.set_defaults(func=cmd_backtest)

    p_rot = sub.add_parser("rotation",
        help="run sector-rotation backtest (the strategy that beats SPY)")
    p_rot.add_argument("--verbose", action="store_true",
        help="print each monthly rebalance")
    p_rot.set_defaults(func=cmd_rotation)

    p_rot5 = sub.add_parser("rotation-v5",
        help="V5 Hyper-Drive: 10-sector, momentum-squared, 3-tier leverage (−50%% MDD accepted)")
    p_rot5.add_argument("--start", default="2005-01-01")
    p_rot5.add_argument("--end",   default="2024-12-31")
    p_rot5.add_argument("--verbose", action="store_true",
        help="print each monthly rebalance")
    p_rot5.set_defaults(func=cmd_rotation_v5)

    p_rot2 = sub.add_parser("rotation-v2",
        help="run the 20-year champion rotation (momentum-weighted + GLD + 12mo)")
    p_rot2.add_argument("--start", default="2005-01-01")
    p_rot2.add_argument("--end",   default="2024-12-31")
    p_rot2.add_argument("--defensive", default="GLD+TLT",
        help="risk-off sleeve: 'GLD+TLT' (recommended), 'GLD' (max CAGR), 'BIL' (min risk)")
    p_rot2.add_argument("--scheme", default="rp_blend",
        help="position sizing: 'rp_blend' (multi-factor, recommended), 'momentum', 'invvol', 'sharpe'")
    p_rot2.add_argument("--vol-target", dest="vol_target", type=float, default=0.12,
        help="annualized vol target for the de-risk overlay (0.10 = lower DD, 0.12 = recommended)")
    p_rot2.add_argument("--verbose", action="store_true",
        help="print each monthly rebalance")
    p_rot2.set_defaults(func=cmd_rotation_v2)

    p_rotl = sub.add_parser("rotation-live",
        help="run the champion live on the Alpaca paper account (dry-run by default)")
    p_rotl.add_argument("--live", action="store_true",
        help="actually submit orders to Alpaca (omit for a dry-run plan)")
    p_rotl.add_argument("--release", action="store_true",
                        help="release a plan the evening run deferred "
                             "(ROTATION_RELEASE_DELAY_MIN > 0)")
    p_rotl.add_argument("--scheduled", action="store_true",
        help="background mode: gate on market-open + once-per-month, exit fast if not due")
    p_rotl.add_argument("--session", choices=["intraday", "next-open"],
        default="intraday",
        help="'next-open' runs AFTER the close and queues orders for the next "
             "session -- the mode used on this machine, which is powered off "
             "during market hours")
    p_rotl.add_argument("--selftest", action="store_true",
        help="run the rotation gate checks with no network and exit")
    p_rotl.set_defaults(func=cmd_rotation_live)

    p_mc = sub.add_parser("montecarlo", help="run 50 Monte Carlo simulations")
    p_mc.add_argument("--sims",  type=int, default=50)
    p_mc.add_argument("--start", default="2005-01-01")
    p_mc.add_argument("--end",   default="2024-12-31")
    p_mc.set_defaults(func=cmd_montecarlo)

    sub.add_parser("alpaca",
        help="show Alpaca paper account").set_defaults(func=cmd_alpaca)

    sub.add_parser("walkforward",
        help="4-window overfitting check").set_defaults(func=cmd_walkforward)

    args = parser.parse_args()
    rc = args.func(args)
    # Honour a non-zero return. cmd_rotation_live refuses an ungated --live and
    # returns 2; discarding that made the refusal exit 0, so any wrapper script
    # checking the status code would read a blocked trade as a successful one.
    if isinstance(rc, int) and rc:
        sys.exit(rc)


if __name__ == "__main__":
    main()
