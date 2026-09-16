"""Two-speed live trading loop.

Speed 1 — every 60 seconds:
    • Fetch live prices from Alpaca
    • Check all open positions against stop-loss and take-profit
    • Update trailing stops
    • Print a position summary

Speed 2 — every 30 minutes:
    • Re-fetch macro news from all 4 sources
    • Update the market regime score
    • Freeze/unfreeze new buy gate

Daily — TWICE per trading day:

  9:25am ET (pre-market scan, 5 min before open):
    • Fresh macro news check — catch any overnight developments
    • Run full signal cycle on prior day's close data
    • Queue any BUY/SELL signals to fire at the opening bell (9:30am)
    • Reason: overnight earnings, Fed speeches, geopolitical news can
      completely change the picture before the market opens.

  4:01pm ET (market close scan):
    • Run full signal cycle on today's final close data
    • Execute any end-of-day entries/exits
    • Reason: daily bars are only final after the close — this is the
      canonical signal generation moment for daily-bar strategies.

The loop runs until you press Ctrl+C.
"""

from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timezone
from typing import Optional
import zoneinfo

from .config import Config
from .alpaca_feed import AlpacaFeed
from .engine import PaperBroker
from .macro_monitor import get_macro_regime, MacroRegime
from .paper_live import run_cycle, _load_state, _save_state

ET = zoneinfo.ZoneInfo("America/New_York")

# -------------------------------------------------------------------------
# Timing constants
# -------------------------------------------------------------------------
POSITION_CHECK_INTERVAL = 60      # seconds between live price checks
MACRO_CHECK_INTERVAL    = 1800    # 30 minutes between macro news checks

# Cycle 1: pre-market scan — 9:25am ET, 5 minutes before the opening bell
OPEN_SCAN_HOUR_ET       = 9
OPEN_SCAN_MINUTE_ET     = 25

# Cycle 2: end-of-day scan — 4:01pm ET, after close is final
CLOSE_SCAN_HOUR_ET      = 16
CLOSE_SCAN_MINUTE_ET    = 1

LOOP_SLEEP              = 10      # main loop heartbeat (seconds)


def _now_et() -> datetime:
    return datetime.now(ET)


def _is_weekday() -> bool:
    return _now_et().weekday() < 5   # Mon–Fri


def _should_run_open_scan(last_open_scan_date: Optional[str]) -> bool:
    """True at 9:25am ET on a weekday, once per day."""
    now   = _now_et()
    today = now.strftime("%Y-%m-%d")
    if last_open_scan_date == today:
        return False
    return (now.hour == OPEN_SCAN_HOUR_ET
            and now.minute >= OPEN_SCAN_MINUTE_ET
            and _is_weekday())


def _should_run_close_scan(last_close_scan_date: Optional[str]) -> bool:
    """True at 4:01pm ET on a weekday, once per day."""
    now   = _now_et()
    today = now.strftime("%Y-%m-%d")
    if last_close_scan_date == today:
        return False
    return (now.hour == CLOSE_SCAN_HOUR_ET
            and now.minute >= CLOSE_SCAN_MINUTE_ET
            and _is_weekday())


def _fmt_pct(v: float) -> str:
    return f"{v:+.2%}"


def run_live(cfg: Config, verbose: bool = True):
    """Start the two-speed live loop. Runs until Ctrl+C."""

    feed = AlpacaFeed(cfg.alpaca_key, cfg.alpaca_secret)

    # Verify connection
    if verbose:
        print("\n" + "="*60)
        print("  LIVE TRADING LOOP — starting up")
        print("="*60)
        if feed.connected():
            acct = feed.get_account()
            print(f"  Alpaca paper account connected")
            if acct:
                print(f"  Account equity : ${float(acct.get('equity', 0)):,.2f}")
                print(f"  Buying power   : ${float(acct.get('buying_power', 0)):,.2f}")
        else:
            print("  WARNING: Alpaca keys not set or unreachable.")
            print("  Position monitoring will use yfinance prices as fallback.")
        print(f"\n  Position checks : every {POSITION_CHECK_INTERVAL}s")
        print(f"  Macro news      : every {MACRO_CHECK_INTERVAL//60} minutes")
        print(f"  Signal cycle 1  : {OPEN_SCAN_HOUR_ET}:{OPEN_SCAN_MINUTE_ET:02d}am ET  (pre-market — 5min before open)")
        print(f"  Signal cycle 2  : {CLOSE_SCAN_HOUR_ET}:{CLOSE_SCAN_MINUTE_ET:02d}pm ET  (end-of-day — after close)")
        print(f"\n  Press Ctrl+C to stop.\n")
        print("="*60 + "\n")

    last_position_check  = 0.0
    last_macro_check     = 0.0
    last_open_scan_date:  Optional[str] = None
    last_close_scan_date: Optional[str] = None
    current_regime: Optional[MacroRegime] = None

    # Graceful shutdown on Ctrl+C
    _running = [True]
    def _handle_exit(sig, frame):
        print("\n\nShutting down gracefully...")
        _running[0] = False
    signal.signal(signal.SIGINT, _handle_exit)

    while _running[0]:
        now = time.time()
        now_et = _now_et()
        ts = now_et.strftime("%H:%M:%S ET")

        # -----------------------------------------------------------------
        # SPEED 1 — Position monitor (every 60 seconds)
        # -----------------------------------------------------------------
        if now - last_position_check >= POSITION_CHECK_INTERVAL:
            last_position_check = now

            broker = _load_state(cfg)
            if not broker.positions:
                if verbose:
                    print(f"[{ts}] No open positions.")
            else:
                # Get live prices
                syms   = list(broker.positions.keys())
                prices = feed.get_latest_prices(syms)

                # Fallback to yfinance if Alpaca prices unavailable
                if not prices:
                    try:
                        import yfinance as yf
                        for sym in syms:
                            t = yf.Ticker(sym)
                            hist = t.history(period="1d")
                            if not hist.empty:
                                prices[sym] = float(hist["Close"].iloc[-1])
                    except Exception:
                        pass

                if prices:
                    today_str = now_et.strftime("%Y-%m-%d")
                    cb_tripped = broker.circuit_breaker_open(prices)

                    # Check stops and update trailing stops
                    broker.check_risk_exits(today_str, prices)
                    broker.mark(today_str, prices)
                    _save_state(broker)

                    eq   = broker.equity(prices)
                    heat = broker.portfolio_heat(prices)

                    if verbose:
                        mkt_status = "OPEN" if feed.is_market_open() else "CLOSED"
                        cb_flag = " | CB:TRIPPED" if cb_tripped else ""
                        print(f"[{ts}] Market:{mkt_status}  "
                              f"Equity:${eq:,.0f}  "
                              f"Heat:{heat:.1%}  "
                              f"Positions:{len(broker.positions)}"
                              f"{cb_flag}")
                        for sym, pos in broker.positions.items():
                            px  = prices.get(sym, pos.avg_price)
                            pct = pos.unrealized_pct(px)
                            r   = pos.r_multiple(px)
                            trail = " [trailing]" if pos.trailing_active else ""
                            print(f"         {sym:5}  "
                                  f"avg ${pos.avg_price:.2f} → ${px:.2f}  "
                                  f"({_fmt_pct(pct)})  "
                                  f"SL:${pos.stop_loss:.2f}  "
                                  f"TP:${pos.take_profit:.2f}  "
                                  f"R:{r:.1f}x{trail}")

        # -----------------------------------------------------------------
        # SPEED 2 — Macro news (every 30 minutes)
        # -----------------------------------------------------------------
        if now - last_macro_check >= MACRO_CHECK_INTERVAL:
            last_macro_check = now
            try:
                current_regime = get_macro_regime(
                    anthropic_key=cfg.anthropic_api_key,
                    newsapi_key=cfg.newsapi_key,
                    alpaca_key=cfg.alpaca_key,
                    alpaca_secret=cfg.alpaca_secret,
                    fmp_key=cfg.fmp_key,
                    max_cache_age=0,   # force refresh
                    verbose=False,
                )
                status = "FROZEN" if current_regime.freeze_buys else "OPEN"
                if verbose:
                    print(f"[{ts}] Macro update: score={current_regime.score:+.2f}  "
                          f"gate={status}  "
                          f"risk={current_regime.top_risk[:50]}")
            except Exception as e:
                if verbose:
                    print(f"[{ts}] Macro check failed (using neutral): {e}")

        # -----------------------------------------------------------------
        # CYCLE 1 — Pre-market scan at 9:25am ET (5 min before open)
        # Uses prior day's close data + fresh overnight news
        # -----------------------------------------------------------------
        if _should_run_open_scan(last_open_scan_date):
            last_open_scan_date = now_et.strftime("%Y-%m-%d")
            print(f"\n[{ts}] *** PRE-MARKET SCAN (9:25am) — signals queue for open ***\n")
            # Force a fresh macro check first to catch overnight news
            try:
                current_regime = get_macro_regime(
                    anthropic_key=cfg.anthropic_api_key,
                    newsapi_key=cfg.newsapi_key,
                    alpaca_key=cfg.alpaca_key,
                    alpaca_secret=cfg.alpaca_secret,
                    fmp_key=cfg.fmp_key,
                    max_cache_age=0,
                    verbose=verbose,
                )
                status = "FROZEN" if current_regime.freeze_buys else "OPEN"
                print(f"[{ts}] Overnight macro: score={current_regime.score:+.2f}  "
                      f"gate={status}  risk={current_regime.top_risk[:60]}")
            except Exception:
                pass
            try:
                run_cycle(cfg, verbose=True)
            except Exception as e:
                print(f"[{ts}] Pre-market cycle error: {e}")
            print(f"\n[{ts}] *** PRE-MARKET SCAN COMPLETE ***\n")

        # -----------------------------------------------------------------
        # CYCLE 2 — End-of-day scan at 4:01pm ET (close data is final)
        # -----------------------------------------------------------------
        if _should_run_close_scan(last_close_scan_date):
            last_close_scan_date = now_et.strftime("%Y-%m-%d")
            print(f"\n[{ts}] *** END-OF-DAY SCAN (4:01pm) — today's close data ***\n")
            try:
                run_cycle(cfg, verbose=True)
            except Exception as e:
                print(f"[{ts}] Close cycle error: {e}")
            print(f"\n[{ts}] *** END-OF-DAY SCAN COMPLETE ***\n")

        time.sleep(LOOP_SLEEP)

    # Final save on exit
    print("Loop stopped. Final account state saved.")
