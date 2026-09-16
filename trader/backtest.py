"""Event-driven backtest — v5.

New in this version:
  - ATR-based stops (via strategy.py _sl_tp change)
  - Weighted confluence score gate (via strategy.py)
  - News removed from BUY gate (via config + strategy.py)
  - SPY floor reduced 30% → 15%
  - Sector correlation cap: max 3 positions per sector simultaneously
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from .config import Config
from .data_source import get_data_source, resample_weekly
from .engine import PaperBroker
from .news import get_news_provider
from . import strategy
from .strategy import Action, Signal


# Sector map — used to enforce max_sector_positions cap.
# ETF wrappers (XLK, SMH, SOXX) count as their sector — prevents loading up
# on "tech" through both individual names and the sector ETF simultaneously.
SECTOR_MAP: Dict[str, str] = {
    # Tech
    "XLK": "tech", "SMH": "tech", "SOXX": "tech",
    "AAPL": "tech", "MSFT": "tech", "AMZN": "tech", "GOOGL": "tech",
    "GOOG": "tech", "META": "tech", "NVDA": "tech", "TSLA": "tech",
    "INTC": "tech", "QCOM": "tech", "ORCL": "tech", "IBM": "tech",
    "CSCO": "tech", "ADBE": "tech", "CRM": "tech", "NOW": "tech",
    "AMD": "tech", "TXN": "tech", "AMAT": "tech", "LRCX": "tech",
    "KLAC": "tech", "MU": "tech", "AVGO": "tech",
    "SHOP": "tech", "PYPL": "tech", "ZM": "tech",
    # Finance
    "XLF": "finance", "KRE": "finance",
    "JPM": "finance", "BAC": "finance", "GS": "finance", "WFC": "finance",
    "MS": "finance", "C": "finance", "BLK": "finance", "AXP": "finance",
    "V": "finance", "MA": "finance", "BRK-B": "finance", "USB": "finance",
    "PNC": "finance",
    # Health
    "XLV": "health", "IBB": "health", "XBI": "health",
    "JNJ": "health", "UNH": "health", "PFE": "health", "MRK": "health",
    "ABBV": "health", "LLY": "health", "TMO": "health", "ABT": "health",
    "AMGN": "health", "GILD": "health", "BIIB": "health", "ISRG": "health",
    "MDT": "health",
    # Energy
    "XLE": "energy", "XOM": "energy", "CVX": "energy", "COP": "energy",
    "SLB": "energy", "EOG": "energy",
    # Materials / Commodities
    "XLB": "materials", "GDX": "materials", "GDXJ": "materials",
    # Industrials
    "XLI": "industrial", "CAT": "industrial", "HON": "industrial",
    "BA": "industrial", "GE": "industrial", "MMM": "industrial",
    "RTX": "industrial", "LMT": "industrial", "DE": "industrial",
    # Consumer
    "XLY": "consumer", "XLP": "consumer", "XRT": "consumer",
    "XHB": "consumer", "ITB": "consumer",
    "HD": "consumer", "MCD": "consumer", "KO": "consumer", "WMT": "consumer",
    "PG": "consumer", "TGT": "consumer", "COST": "consumer", "NKE": "consumer",
    "SBUX": "consumer", "LOW": "consumer", "TJX": "consumer", "DIS": "consumer",
    # Communication
    "T": "comm", "VZ": "comm", "NFLX": "comm", "CMCSA": "comm",
    # Utilities / Real estate
    "XLU": "utility", "NEE": "utility", "DUK": "utility",
    "XLRE": "realty", "AMT": "realty", "PLD": "realty",
    # International / Broad
    "EFA": "intl", "EEM": "intl", "FXI": "intl", "EWJ": "intl", "EWZ": "intl",
    "SPY": "broad", "QQQ": "broad", "IWM": "broad", "DIA": "broad",
    "MDY": "broad", "VTI": "broad",
}


def _is_bear_regime(spy_feat: Optional[pd.DataFrame], date) -> bool:
    """Bear regime: SPY is below its EMA40 (≈ 200-day MA on weekly bars)."""
    if spy_feat is None:
        return False
    try:
        row = spy_feat.loc[date]
        return not pd.isna(row.ema200) and float(row.close) < float(row.ema200)
    except (KeyError, AttributeError):
        return False


def _catastrophic_drop(spy_feat: Optional[pd.DataFrame], date) -> bool:
    """True if SPY dropped more than 5% this week — exit all positions immediately."""
    if spy_feat is None:
        return False
    try:
        idx = spy_feat.index.get_loc(date)
        if idx < 1:
            return False
        curr = float(spy_feat.iloc[idx].close)
        prev = float(spy_feat.iloc[idx - 1].close)
        return prev > 0 and (curr - prev) / prev <= -0.05
    except (KeyError, IndexError, AttributeError):
        return False


def _drawdown_size_scale(broker: PaperBroker, prices: Dict[str, float],
                         cfg: Config) -> float:
    """Fix #3: reduce position size proportional to current drawdown depth."""
    if not cfg.benchmark_sizing_enabled or not broker.equity_curve:
        return 1.0
    peak = max(e for _, e in broker.equity_curve)
    current = broker.equity(prices)
    dd = (current - peak) / peak  # negative number
    if dd >= 0:
        return 1.0
    # Each 5% drawdown reduces size by 25%, floored at 25% of normal
    steps = abs(dd) / cfg.sizing_drawdown_scale
    scale = max(0.25, 1.0 - steps * 0.25)
    return scale


def _defensive_signals(defensive_feats: Dict[str, pd.DataFrame],
                       date, cfg: Config,
                       holding: Dict[str, bool]) -> List[Signal]:
    """Generate buy signals for defensive assets (TLT, GLD, SHY) in bear markets."""
    sigs = []
    for sym, f in defensive_feats.items():
        i = None
        for idx, d in enumerate(f.index):
            if d == date:
                i = idx
                break
        if i is None or i < 1:
            continue
        sig = strategy.signal_for_row(
            sym, f, i, cfg,
            news_sentiment=0.0,   # neutral — defensive buys don't need news
            news_impact="LOW",
            holding=holding.get(sym, False),
        )
        if sig and sig.action == Action.BUY:
            sigs.append(sig)
    return sigs


def _compute_rs_scores(feats: Dict[str, pd.DataFrame], spy_feat: Optional[pd.DataFrame],
                       date, lookback: int) -> Dict[str, float]:
    """For each symbol, compute return relative to SPY over lookback weeks."""
    if spy_feat is None:
        return {}
    try:
        spy_idx = spy_feat.index.get_loc(date)
    except KeyError:
        return {}
    if spy_idx < lookback:
        return {}
    spy_now  = float(spy_feat.iloc[spy_idx].close)
    spy_then = float(spy_feat.iloc[spy_idx - lookback].close)
    if spy_then <= 0:
        return {}
    spy_return = (spy_now - spy_then) / spy_then

    scores = {}
    for sym, f in feats.items():
        try:
            i = f.index.get_loc(date)
        except KeyError:
            continue
        if i < lookback:
            continue
        now  = float(f.iloc[i].close)
        then = float(f.iloc[i - lookback].close)
        if then <= 0:
            continue
        scores[sym] = ((now - then) / then) - spy_return
    return scores


def run_backtest(cfg: Config, news: str = "neutral", verbose: bool = False) -> Dict:
    all_symbols = list(dict.fromkeys(
        cfg.universe + (cfg.defensive_assets if cfg.defensive_rotation_enabled else [])
    ))

    data = get_data_source("yfinance")
    raw  = data.history(all_symbols, cfg.backtest_start, cfg.backtest_end)
    if not raw:
        raise RuntimeError("No historical data returned.")

    # Fix #1: resample to weekly bars if enabled
    if cfg.use_weekly_bars:
        raw = {sym: resample_weekly(df) for sym, df in raw.items() if not df.empty}

    feats: Dict[str, pd.DataFrame] = {
        sym: strategy.compute_features(df, cfg)
        for sym, df in raw.items() if not df.empty
    }

    defensive_syms = set(cfg.defensive_assets) if cfg.defensive_rotation_enabled else set()
    offensive_syms = {s for s in feats if s not in defensive_syms}
    defensive_feats = {s: feats[s] for s in defensive_syms if s in feats}

    all_dates = sorted(set().union(*[set(f.index) for f in feats.values()]))
    pos_index = {sym: {d: i for i, d in enumerate(f.index)} for sym, f in feats.items()}

    spy_feat = feats.get(cfg.regime_symbol)
    broker = PaperBroker(cfg)
    news_provider = get_news_provider(news)
    static_news = {}
    if news != "neutral":
        static_news = news_provider.score(list(offensive_syms), cfg.news_lookback_days)

    # Half-Kelly state: track last equity snapshot to detect losing weeks
    prev_equity: float = cfg.starting_cash
    half_kelly_active: bool = False
    half_kelly_recovery_level: float = 0.0

    for d in all_dates:
        prices: Dict[str, float] = {}
        for sym, f in feats.items():
            i = pos_index[sym].get(d)
            if i is not None and not pd.isna(f.iloc[i].close):
                prices[sym] = float(f.iloc[i].close)

        ds = d.strftime("%Y-%m-%d")

        broker.check_risk_exits(ds, prices)

        bear_regime = (cfg.regime_filter_enabled
                       and spy_feat is not None
                       and _is_bear_regime(spy_feat, d))
        heat_ok    = broker.portfolio_heat(prices) < cfg.max_portfolio_heat_pct
        cb_tripped = broker.circuit_breaker_open(prices)

        # Catastrophic exit: SPY dropped >5% this week → exit EVERYTHING now
        if _catastrophic_drop(spy_feat, d):
            for sym in list(broker.positions.keys()):
                px = prices.get(sym)
                if px:
                    broker.sell(ds, sym, px, "CATASTROPHIC EXIT: SPY weekly drop >5%")
            if verbose:
                print(f"{ds} *** CATASTROPHIC EXIT — SPY dropped >5% this week ***")

        # Fix #3: drawdown-based size scalar
        size_scale = _drawdown_size_scale(broker, prices, cfg)

        # Fix #6: half-Kelly — detect losing week; halve sizing until recovered
        current_equity = broker.equity(prices)
        if cfg.half_kelly_after_loss:
            if not half_kelly_active and current_equity < prev_equity:
                half_kelly_active = True
                half_kelly_recovery_level = prev_equity
                if verbose:
                    print(f"{ds} HALF-KELLY engaged (equity ${current_equity:,.0f} < "
                          f"${prev_equity:,.0f})")
            elif half_kelly_active and current_equity >= half_kelly_recovery_level:
                half_kelly_active = False
                if verbose:
                    print(f"{ds} HALF-KELLY lifted — recovered to ${current_equity:,.0f}")
        if half_kelly_active:
            size_scale *= 0.5
        prev_equity = current_equity

        # Fix #7: SPY floor — ensure we always hold spy_floor_pct of equity in SPY
        if cfg.spy_floor_enabled and cfg.regime_symbol in prices:
            spy_price      = prices[cfg.regime_symbol]
            target_spy_val = current_equity * cfg.spy_floor_pct
            spy_pos        = broker.positions.get(cfg.regime_symbol)
            current_spy_val = spy_pos.market_value(spy_price) if spy_pos else 0.0
            gap = target_spy_val - current_spy_val
            if gap > spy_price * 1.0:   # need at least 1 share gap
                buy_shares = int(gap / spy_price)
                if buy_shares > 0 and broker.cash >= buy_shares * spy_price:
                    # Use a wide stop (10%) so the floor position is rarely stopped out
                    spy_sl = spy_price * 0.90
                    spy_tp = spy_price * 1.20
                    broker.buy(ds, cfg.regime_symbol, spy_price, buy_shares,
                               "SPY FLOOR — permanent allocation",
                               spy_sl, spy_tp, "High")
                    if verbose:
                        print(f"{ds} SPY-FLOOR +{buy_shares} shares @ {spy_price:.2f} "
                              f"(target {target_spy_val:,.0f})")

        # Fix #5: compute relative strength — bars = weeks×5 on daily, weeks×1 on weekly
        rs_bar_lookback = cfg.rs_lookback_weeks * (1 if cfg.use_weekly_bars else 5)
        rs_scores = (_compute_rs_scores(
            {s: feats[s] for s in offensive_syms if s in feats},
            spy_feat, d, rs_bar_lookback
        ) if cfg.rs_filter_enabled else {})

        # -------------------------------------------------------------------
        # Fix #2: Defensive rotation — in bear market, buy TLT/GLD/SHY
        # Only hold ONE defensive position at a time (the strongest signal)
        # -------------------------------------------------------------------
        if bear_regime and cfg.defensive_rotation_enabled and not cb_tripped:
            holding_defensive = {s: s in broker.positions for s in defensive_syms}
            def_sigs = _defensive_signals(defensive_feats, d, cfg, holding_defensive)
            if def_sigs and heat_ok:
                # Pick best defensive signal (highest confluence)
                best = max(def_sigs, key=lambda s: s.confluence_score)
                if best.action == Action.BUY:
                    shares = broker.target_shares(
                        best.symbol, best.price, best.stop_loss, prices
                    )
                    shares = int(shares * size_scale)
                    if shares > 0:
                        broker.buy(ds, best.symbol, best.price, shares,
                                   f"DEFENSIVE {best.reason}",
                                   best.stop_loss, best.take_profit,
                                   best.confidence.value)
                        if verbose:
                            print(f"{ds} DEF  {best.symbol:5} {shares:>5} "
                                  f"@ {best.price:8.2f} (bear rotation)")

        # Sell defensive positions when bull regime returns
        if not bear_regime and cfg.defensive_rotation_enabled:
            for sym in list(defensive_syms):
                if sym in broker.positions:
                    broker.sell(ds, sym, prices.get(sym, 0),
                                "Exiting defensive — bull regime returned")
                    if verbose:
                        print(f"{ds} EXIT {sym:5} — bull regime, back to offense")

        # -------------------------------------------------------------------
        # Offensive signals (skip in bear regime)
        # Fix #4: rank all signals, only take top-N by confluence score
        # Fix #5: pass RS score into signal for relative strength filter
        # -------------------------------------------------------------------
        if not bear_regime and not cb_tripped:
            pending: List[Tuple[Signal, int]] = []   # (signal, shares)

            for sym in offensive_syms:
                # Skip SPY here — it's managed by the floor logic above
                if cfg.spy_floor_enabled and sym == cfg.regime_symbol:
                    continue
                f = feats.get(sym)
                if f is None:
                    continue
                i = pos_index[sym].get(d)
                if i is None:
                    continue
                ns        = static_news.get(sym)
                sentiment = ns.sentiment if ns else 0.0
                impact    = ns.impact    if ns else "LOW"
                holding   = sym in broker.positions
                rs_score  = rs_scores.get(sym, 0.0)

                sig = strategy.signal_for_row(
                    sym, f, i, cfg, sentiment, impact, holding, rs_vs_spy=rs_score
                )
                if sig is None or sig.action == Action.HOLD:
                    continue

                if sig.action == Action.SELL:
                    broker.sell(ds, sym, sig.price, sig.reason)
                    if verbose:
                        print(f"{ds} SELL {sym:5}       @ {sig.price:8.2f}  {sig.reason[:50]}")

                elif sig.action == Action.BUY and heat_ok:
                    # HIGH confidence → 20% position cap; MEDIUM → 15%
                    from .strategy import Confidence as _Conf
                    conv_pct = (cfg.high_conviction_pct
                                if sig.confidence == _Conf.HIGH
                                else cfg.max_position_pct)
                    shares = broker.target_shares(sym, sig.price, sig.stop_loss,
                                                  prices, max_pct_override=conv_pct)
                    shares = int(shares * size_scale)
                    if shares > 0:
                        pending.append((sig, shares))

            # Sort by weighted confluence score, take top N
            if pending and cfg.momentum_rank_enabled:
                pending.sort(key=lambda x: x[0].confluence_score, reverse=True)
                pending = pending[:cfg.momentum_top_n]

            # Sector correlation cap — track how many positions we open per sector
            # this cycle; stop adding once a sector hits max_sector_positions.
            sector_counts: Dict[str, int] = {}
            for sym in broker.positions:
                sec = SECTOR_MAP.get(sym, "other")
                sector_counts[sec] = sector_counts.get(sec, 0) + 1

            for sig, shares in pending:
                sec = SECTOR_MAP.get(sig.symbol, "other")
                if sector_counts.get(sec, 0) >= cfg.max_sector_positions:
                    if verbose:
                        print(f"{ds} SKIP {sig.symbol:5} — sector '{sec}' at cap "
                              f"({cfg.max_sector_positions})")
                    continue
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
                broker.buy(ds, sig.symbol, sig.price, shares, sig.reason,
                           sig.stop_loss, sig.take_profit, sig.confidence.value)
                if verbose:
                    print(f"{ds} BUY  {sig.symbol:5} {shares:>5} "
                          f"@ {sig.price:8.2f}  SL:{sig.stop_loss:.2f} "
                          f"TP:{sig.take_profit:.2f}  [{sig.confidence.value}]  "
                          f"scale={size_scale:.0%}")

        broker.mark(ds, prices)

    return {"broker": broker, "cfg": cfg}


def run_walk_forward(cfg: Config, verbose: bool = False) -> List[Dict]:
    from datetime import date, timedelta
    from .metrics import summarize, format_summary

    start = date.fromisoformat(cfg.backtest_start)
    end   = date.fromisoformat(cfg.backtest_end)
    total_days  = (end - start).days
    window_days = total_days // 4
    warmup_days = 300

    print(f"\nWalk-forward: {cfg.backtest_start} → {cfg.backtest_end}  "
          f"(4 windows, {warmup_days}d warmup)\n")

    results = []
    for w in range(4):
        test_start = start + timedelta(days=w * window_days)
        test_end   = min(test_start + timedelta(days=window_days), end)
        data_start = test_start - timedelta(days=warmup_days)

        wcfg = Config(
            universe=cfg.universe,
            defensive_assets=cfg.defensive_assets,
            starting_cash=cfg.starting_cash,
            rr_ratio=cfg.rr_ratio,
            stop_loss_pct=cfg.stop_loss_pct,
            trailing_stop_enabled=cfg.trailing_stop_enabled,
            regime_filter_enabled=cfg.regime_filter_enabled,
            defensive_rotation_enabled=cfg.defensive_rotation_enabled,
            use_weekly_bars=cfg.use_weekly_bars,
            benchmark_sizing_enabled=cfg.benchmark_sizing_enabled,
            momentum_rank_enabled=cfg.momentum_rank_enabled,
            slippage_bps=cfg.slippage_bps,
            backtest_start=data_start.isoformat(),
            backtest_end=test_end.isoformat(),
        )
        res    = run_backtest(wcfg, verbose=False)
        broker = res["broker"]
        m      = summarize(broker.equity_curve, broker.trades, cfg.starting_cash)
        m["window"] = f"W{w+1}: {test_start.isoformat()} → {test_end.isoformat()}"
        results.append(m)
        print(f"Window {w+1} ({test_start.isoformat()} → {test_end.isoformat()}):")
        print(format_summary(m))
        print()

    profitable = sum(1 for r in results if r.get("total_return", 0) > 0)
    print(f"Consistency: {profitable}/4 windows profitable")
    verdict = (
        "VERDICT: Consistent — low overfitting risk." if profitable == 4
        else "VERDICT: Mostly consistent." if profitable >= 3
        else "VERDICT: Inconsistent — review before going live."
    )
    print(verdict)
    return results
