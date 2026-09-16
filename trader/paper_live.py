"""Live paper-trading cycle against current market data.

Run once per trading day (after the close). Each cycle:
  1. Fetch macro regime from all news sources — freeze if market is hostile.
  2. Pull recent bars + compute all indicators.
  3. Score news per ticker via Claude haiku (or lexicon fallback).
  4. Evaluate confluence signals with 5:1 R:R parameters.
  5. Check portfolio heat — skip buys if heat >= 6%.
  6. Execute fills, persist state, print structured output.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, timedelta
from typing import Dict

import pandas as pd

from .config import Config
from .data_source import get_data_source
from .engine import PaperBroker, Position, Trade, validate_live_entry
from .macro_monitor import get_macro_regime
from .news import get_news_provider
from .alpaca_broker import AlpacaBroker
from .alpaca_feed import AlpacaFeed
from . import strategy
from .strategy import Action

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "paper_account.json")


def _load_state(cfg: Config) -> PaperBroker:
    if not os.path.exists(STATE_PATH):
        return PaperBroker(cfg)
    with open(STATE_PATH) as f:
        s = json.load(f)
    broker = PaperBroker(cfg, cash=s["cash"])
    for sym, p in s.get("positions", {}).items():
        # Backward compat: old saves may not have stop_loss/take_profit
        p.setdefault("stop_loss", p.get("avg_price", 0) * (1 - cfg.stop_loss_pct))
        p.setdefault("take_profit", p.get("avg_price", 0) * (1 + cfg.stop_loss_pct * cfg.rr_ratio))
        broker.positions[sym] = Position(**p)
    broker.trades = [Trade(**t) for t in s.get("trades", [])]
    broker.equity_curve = [tuple(x) for x in s.get("equity_curve", [])]
    broker.last_stop_date = dict(s.get("last_stop_date", {}))
    return broker


def _save_state(broker: PaperBroker):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    s = {
        "cash": broker.cash,
        "positions": {sym: asdict(p) for sym, p in broker.positions.items()},
        "trades": [asdict(t) for t in broker.trades],
        "equity_curve": broker.equity_curve,
        "last_stop_date": broker.last_stop_date,
    }
    with open(STATE_PATH, "w") as f:
        json.dump(s, f, indent=2)


def run_cycle(cfg: Config, verbose: bool = True) -> Dict:
    # ------------------------------------------------------------------
    # 1. Macro regime check — runs first, may freeze all buys
    # ------------------------------------------------------------------
    if verbose:
        print("\n[1/4] Checking macro market regime...")
    regime = get_macro_regime(
        anthropic_key=cfg.anthropic_api_key,
        newsapi_key=cfg.newsapi_key,
        alpaca_key=cfg.alpaca_key,
        alpaca_secret=cfg.alpaca_secret,
        fmp_key=cfg.fmp_key,
        verbose=verbose,
    )
    macro_frozen = cfg.news_enabled and regime.freeze_buys

    if verbose:
        status = "FROZEN" if macro_frozen else "OPEN"
        print(f"  Macro score: {regime.score:+.2f}  Buy gate: {status}")
        print(f"  Top risk: {regime.top_risk}")
        print(f"  Sources: {', '.join(regime.sources_used) or 'none'}")

    # ------------------------------------------------------------------
    # 2. Pull market data
    # ------------------------------------------------------------------
    if verbose:
        print("\n[2/4] Fetching market data...")
    data = get_data_source("yfinance")
    end   = date.today() + timedelta(days=1)
    start = date.today() - timedelta(days=max(cfg.ema_long * 3, 250))
    raw   = data.history(cfg.universe, start.isoformat(), end.isoformat())
    raw   = {s: df for s, df in raw.items() if not df.empty}
    if not raw:
        raise RuntimeError("No recent market data available.")

    prices = {s: float(df["close"].iloc[-1]) for s, df in raw.items()}
    today  = date.today().isoformat()

    # Live quote overlay — daily bars are STALE (the 9:25am scan sees the prior
    # close). On Jun 5 2026 stale entries cost 6.5x intended risk. Entries are
    # validated against (and filled at) live quotes when available.
    live_prices: Dict[str, float] = {}
    if cfg.live_price_validation:
        src = "UNAVAILABLE (stale daily bars!)"
        try:
            feed = AlpacaFeed(cfg.alpaca_key, cfg.alpaca_secret)
            live_prices = feed.get_latest_prices(list(raw.keys())) or {}
            if live_prices:
                src = "Alpaca feed"
        except Exception:
            live_prices = {}
        if not live_prices:
            try:
                live_prices = data.latest(list(raw.keys())) or {}
                if live_prices:
                    src = "yfinance latest (delayed)"
            except Exception:
                live_prices = {}
        if live_prices:
            # live quotes also drive risk exits and heat — not yesterday's close
            prices = {**prices, **live_prices}
        if verbose:
            print(f"  Live prices: {src} ({len(live_prices)} symbols)")

    # ------------------------------------------------------------------
    # 3. Per-ticker news sentiment
    # ------------------------------------------------------------------
    if verbose:
        print("\n[3/4] Scoring per-ticker news sentiment...")
    news_provider = get_news_provider("auto", api_key=cfg.anthropic_api_key)
    scores = news_provider.score(list(raw.keys()), cfg.news_lookback_days)

    # ------------------------------------------------------------------
    # 4. Signal generation + execution
    # ------------------------------------------------------------------
    if verbose:
        print("\n[4/4] Evaluating signals...")

    broker = _load_state(cfg)
    actions = []

    # Alpaca execution — active if keys are present
    alpaca = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret)
    alpaca_live = bool(cfg.alpaca_key and cfg.alpaca_secret and alpaca.connected())
    if verbose:
        print(f"  Alpaca execution: {'LIVE (trades will appear in TradingView)' if alpaca_live else 'offline (local simulation only)'}")

    broker.check_risk_exits(today, prices)

    heat       = broker.portfolio_heat(prices)
    heat_ok    = heat < cfg.max_portfolio_heat_pct
    cb_tripped = broker.circuit_breaker_open(prices)
    day_halted = broker.daily_loss_halt(prices, today)

    if verbose and cb_tripped:
        print(f"  ⚠ CIRCUIT BREAKER: account down >{cfg.circuit_breaker_drawdown:.0%} from peak — new buys halted")
    if verbose and day_halted:
        print(f"  ⚠ DAILY LOSS HALT: equity down >{cfg.daily_loss_halt_pct:.0%} today — new buys halted until tomorrow")

    # Compute relative strength vs SPY for the RS filter
    rs_scores: dict = {}
    if cfg.rs_filter_enabled and cfg.regime_symbol in raw:
        spy_df = raw[cfg.regime_symbol]
        lookback = cfg.rs_lookback_weeks * 5  # weeks → trading days
        if len(spy_df) > lookback:
            spy_now  = float(spy_df["close"].iloc[-1])
            spy_then = float(spy_df["close"].iloc[-lookback])
            spy_ret  = (spy_now - spy_then) / spy_then if spy_then > 0 else 0.0
            for sym, df in raw.items():
                if len(df) > lookback:
                    n = float(df["close"].iloc[-1])
                    t = float(df["close"].iloc[-lookback])
                    rs_scores[sym] = ((n - t) / t - spy_ret) if t > 0 else 0.0

    for sym, df in raw.items():
        feat = strategy.compute_features(df, cfg)
        i    = len(feat) - 1
        ns   = scores.get(sym)
        sentiment = ns.sentiment if ns else 0.0
        impact    = ns.impact    if ns else "LOW"
        holding   = sym in broker.positions
        rs_score  = rs_scores.get(sym, 0.0)

        sig = strategy.signal_for_row(sym, feat, i, cfg, sentiment, impact, holding,
                                      rs_vs_spy=rs_score)
        if sig is None or sig.action == Action.HOLD:
            continue

        if sig.action == Action.BUY:
            if cb_tripped:
                actions.append(f"BLOCKED (circuit breaker) {sym}")
                continue
            if day_halted:
                actions.append(f"BLOCKED (daily -{cfg.daily_loss_halt_pct:.0%} halt) {sym}")
                continue
            if macro_frozen:
                actions.append(f"BLOCKED (macro freeze) {sym} — {sig.reason[:50]}")
                continue
            if not heat_ok:
                actions.append(f"BLOCKED (heat {heat:.1%}) {sym}")
                continue
            if broker.stopped_out_today(sym, today):
                actions.append(f"BLOCKED (re-entry cooldown: stopped out today) {sym}")
                continue
            ok, entry_price, why = validate_live_entry(
                sig.price, sig.stop_loss, live_prices.get(sym), cfg)
            if not ok:
                actions.append(f"BLOCKED (live-price check) {sym} — {why}")
                continue
            shares = broker.target_shares(sym, entry_price, sig.stop_loss, prices)
            if shares <= 0:
                continue
            add_risk = shares * (entry_price - sig.stop_loss)
            if not broker.cluster_allows_buy(sym, add_risk, prices):
                cluster = cfg.correlation_clusters.get(sym, sym)
                actions.append(f"BLOCKED (cluster '{cluster}' heat "
                               f">{cfg.cluster_heat_cap_pct:.0%}) {sym}")
                continue
            broker.buy(today, sym, entry_price, shares, sig.reason,
                       sig.stop_loss, sig.take_profit, sig.confidence.value)
            actions.append(("BUY", sym, shares, sig))
            # Mirror to Alpaca — bracket order with SL + TP legs
            if alpaca_live:
                alpaca.place_bracket_buy(sym, int(shares),
                                         sig.stop_loss, sig.take_profit)
        elif sig.action == Action.SELL:
            broker.sell(today, sym, live_prices.get(sym, sig.price), sig.reason)
            actions.append(("SELL", sym, 0, sig))
            # Mirror sell to Alpaca
            if alpaca_live:
                alpaca.place_market_sell(sym)

    broker.mark(today, prices)
    _save_state(broker)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    if verbose:
        eq = broker.equity(prices)
        pnl_today = eq - cfg.starting_cash
        print(f"\n{'='*60}")
        print(f"PAPER CYCLE — {today}")
        print(f"{'='*60}")
        print(f"Equity: ${eq:,.2f}  ({pnl_today:+,.2f} vs start)")
        print(f"Cash:   ${broker.cash:,.2f}   Heat: {heat:.1%}   Positions: {len(broker.positions)}")

        if actions:
            print("\n--- Signals ---")
            for a in actions:
                if isinstance(a, tuple):
                    side, sym, shares, sig = a
                    if side == "BUY":
                        print(sig.format())
                    else:
                        print(f"\nSELL {sym} @ ${sig.price:.2f}  Reason: {sig.reason}")
                else:
                    print(f"  {a}")
        else:
            print("\nNo signals fired today.")

        if broker.positions:
            print("\n--- Open Positions ---")
            for sym, p in broker.positions.items():
                px  = prices.get(sym, p.avg_price)
                pct = p.unrealized_pct(px)
                to_sl = (px - p.stop_loss) / px
                to_tp = (p.take_profit - px) / px
                print(f"  {sym:5}  {p.shares:>5.0f} shares  "
                      f"avg ${p.avg_price:.2f}  now ${px:.2f}  ({pct:+.1%})  "
                      f"SL ${p.stop_loss:.2f} ({-to_sl:.1%})  "
                      f"TP ${p.take_profit:.2f} (+{to_tp:.1%})")

    return {"broker": broker, "actions": actions, "prices": prices, "regime": regime}
