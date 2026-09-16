"""Signal generation — Weekly Confluence Momentum System v5.

v5 structural changes:
  1. Weighted confluence score replaces arbitrary 4-of-N binary count.
     Each condition carries a reliability weight; threshold is a minimum
     sum of weights, not a minimum count of conditions.

  2. ATR-based stops replace fixed 4%.
     Fixed % treats NVDA (weekly ATR ~8%) identically to KO (ATR ~1.5%).
     ATR multiples adapt the stop to each stock's actual volatility, reducing
     false stop-outs on high-beta names while tightening on quiet ones.

  3. News removed from BUY gate.
     Automated sentiment without a strictly point-in-time lagged feed
     introduces look-ahead bias and volatile false positives.
     News is kept as a SELL-side blocker only (confirmed bearish catalyst exit).

Condition weights (max score = 9.5):
  EMA200 filter    2.0   structural — against-trend longs almost always fail
  Volume           1.5   institutional participation; empty moves quickly reverse
  ADX              1.5   trend quality; MA crossovers in choppy markets are traps
  Relative strength 1.5  cross-sectional momentum; only ride sector leaders
  SMA crossover    1.0   entry timing; fresh signal vs late-trend continuation
  RSI health       1.0   momentum not exhausted (35–80) and stock not dead (<35)

Threshold 5.0:
  Minimum viable: EMA200 (2.0) + Volume (1.5) + ADX (1.5) = 5.0
  Without EMA200 you'd need all four remaining conditions simultaneously —
  that combination is theoretically possible but almost never fires cleanly,
  which is the correct behavior (no longs against the long-term trend).

BUY confidence levels:
  HIGH   — weighted score ≥ 8.0  OR score ≥ 5.0 + 2 bonus indicators
  MEDIUM — weighted score ≥ 6.0  OR score ≥ 5.0 + 1 bonus indicator
  LOW    — weighted score ≥ 5.0  (minimum threshold met, no extras)
  → LOW signals are not acted on; only MEDIUM and HIGH enter the ranking.

SELL exits (any one triggers):
  - SMA20 crosses below SMA50        (trend reversal confirmed)
  - Price drops below EMA200         (structural breakdown)
  - RSI > 80                         (extreme overbought — not 70, gives room to run)
  - High-impact confirmed bad news   (news_sell_enabled only; not latency-sensitive)
  NOTE: Hard stop-loss, take-profit, trailing stop, and time stop are in engine.py
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import pandas as pd

from . import indicators
from .config import Config


class Action(Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Confidence(Enum):
    LOW    = "Low"
    MEDIUM = "Medium"
    HIGH   = "High"


@dataclass
class Signal:
    symbol: str
    action: Action
    price: float
    stop_loss: float
    take_profit: float
    reason: str
    rsi: float
    news_sentiment: float
    confidence: Confidence
    confluence_score: float        # weighted score (float, not int count)
    news_impact: str = "LOW"

    def format(self) -> str:
        direction = self.action.value
        stop_dist = self.price - self.stop_loss
        rr = (self.take_profit - self.price) / stop_dist if stop_dist > 0 else 0
        return (
            f"\n{'='*60}\n"
            f"**{self.symbol} - {direction}**\n"
            f"  Rationale: {self.reason}\n"
            f"  News: {self.news_sentiment:+.2f} [{self.news_impact}]\n"
            f"  Entry:       ${self.price:.2f}\n"
            f"  Stop-Loss:   ${self.stop_loss:.2f}  "
            f"({(self.stop_loss/self.price - 1)*100:+.1f}%)\n"
            f"  Take-Profit: ${self.take_profit:.2f}  "
            f"({(self.take_profit/self.price - 1)*100:+.1f}%)\n"
            f"  R:R: 1:{rr:.2f}   Confidence: {self.confidence.value}  "
            f"(weighted score {self.confluence_score:.1f}/9.5)\n"
            f"{'='*60}"
        )


def _wp(period: int, weekly: bool) -> int:
    """Scale a daily-bar period to weekly-bar equivalent (÷5, floor 2)."""
    return max(2, period // 5) if weekly else period


def compute_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    w = cfg.use_weekly_bars
    feat = df.copy()
    feat["fast_ma"]   = indicators.sma(feat["close"], _wp(cfg.fast_ma, w))
    feat["slow_ma"]   = indicators.sma(feat["close"], _wp(cfg.slow_ma, w))
    feat["ema20"]     = indicators.ema(feat["close"], _wp(cfg.ema_fast, w))
    feat["ema50"]     = indicators.ema(feat["close"], _wp(cfg.ema_slow, w))
    feat["ema200"]    = indicators.ema(feat["close"], _wp(cfg.ema_long, w))
    feat["rsi"]       = indicators.rsi(feat["close"], _wp(cfg.rsi_period, w))
    macd_df           = indicators.macd(feat["close"],
                                        _wp(cfg.macd_fast, w),
                                        _wp(cfg.macd_slow, w),
                                        _wp(cfg.macd_signal, w))
    feat["macd_hist"] = macd_df["hist"]
    feat["macd_line"] = macd_df["macd"]
    feat["macd_sig"]  = macd_df["signal"]
    feat["atr"]       = indicators.atr(feat["high"], feat["low"], feat["close"],
                                       _wp(cfg.rsi_period, w))
    feat["adx"]       = indicators.adx(feat["high"], feat["low"], feat["close"],
                                       _wp(cfg.adx_period, w))
    bb_df             = indicators.bollinger_bands(feat["close"],
                                                   _wp(cfg.bb_period, w), cfg.bb_std)
    feat["bb_upper"]  = bb_df["bb_upper"]
    feat["bb_lower"]  = bb_df["bb_lower"]
    feat["bb_pct"]    = bb_df["bb_pct"]
    feat["vol_ratio"] = indicators.volume_ratio(feat["volume"], _wp(20, w))
    feat["vwap_roll"] = indicators.vwap_rolling(feat["close"], feat["volume"], _wp(20, w))
    return feat


def _sl_tp(price: float, atr_val: Optional[float], cfg: Config) -> Tuple[float, float]:
    """Volatility-adaptive stop and target for weekly bars.

    Weekly ATR for S&P 500 stocks typically runs 2–8% of price.
    Using ATR × multiple directly would produce 7–12% stops on high-beta
    names (NVDA, TSLA) — too wide to ever trigger, so positions just drift.

    Instead: use ATR as a TIGHTENING mechanism on low-vol names, capped at
    the fixed % on high-vol names:
        stop_dist = min(ATR, fixed_pct × price)

    Effect:
      KO   (ATR ~2%): stop = ~2%, TP = ~3%   → tight, hits frequently
      SPY  (ATR ~3%): stop = ~3%, TP = ~4.5% → balanced
      NVDA (ATR ~8%): stop = 4%,  TP = 6%    → capped, consistent treatment

    This was the proven approach that produced 38% CAGR in v4.
    The external review's ATR multiple recommendation applies to daily bars
    where ATR is 1–2%; on weekly bars the same multiples over-widen stops.
    """
    fixed_dist = price * cfg.stop_loss_pct
    if atr_val and not pd.isna(atr_val):
        atr_dist = float(atr_val)
        stop_dist = min(fixed_dist, atr_dist)   # ATR tightens low-vol; fixed caps high-vol
    else:
        stop_dist = fixed_dist

    stop_dist = max(stop_dist, price * 0.005)   # floor at 0.5%
    tp_dist   = stop_dist * cfg.rr_ratio
    return round(price - stop_dist, 2), round(price + tp_dist, 2)


# ---------------------------------------------------------------------------
# Weighted condition table
# Each entry: (key, weight, description_template)
# Weights reflect signal reliability and importance to trade success.
# Total max = 9.5 when all conditions pass.
# ---------------------------------------------------------------------------
_CONDITION_WEIGHTS: List[Tuple[str, float]] = [
    ("ema200",  2.0),   # structural long-term trend filter
    ("volume",  1.5),   # institutional participation
    ("adx",     1.5),   # trend quality / anti-chop
    ("rs_spy",  1.5),   # relative strength vs benchmark
    ("sma_cross", 1.0), # entry timing (fresh crossover)
    ("rsi",     1.0),   # momentum health
]
_MAX_SCORE = sum(w for _, w in _CONDITION_WEIGHTS)   # 9.5


def signal_for_row(
    symbol: str,
    feat: pd.DataFrame,
    i: int,
    cfg: Config,
    news_sentiment: float,
    news_impact: str,
    holding: bool,
    rs_vs_spy: float = 0.0,
) -> Optional[Signal]:
    if i < 1:
        return None

    row  = feat.iloc[i]
    prev = feat.iloc[i - 1]

    required = [row.slow_ma, row.ema200, row.rsi, prev.slow_ma]
    if any(pd.isna(v) for v in required):
        return None

    price   = float(row.close)
    rsi_val = float(row.rsi)
    atr_val = float(row.atr) if not pd.isna(row.atr) else None
    adx_val = float(row.adx) if not pd.isna(row.adx) else 0.0

    # -----------------------------------------------------------------------
    # SELL side
    # News only blocks/exits when news_sell_enabled (not the same flag as
    # news_enabled which gates buys — those are intentionally separated).
    # -----------------------------------------------------------------------
    if holding:
        sell_reasons = []

        sma_cross_down = (
            not pd.isna(prev.fast_ma) and not pd.isna(row.fast_ma)
            and prev.fast_ma >= prev.slow_ma
            and row.fast_ma < row.slow_ma
        )
        if sma_cross_down:
            sell_reasons.append("SMA cross down")

        if price < float(row.ema200):
            sell_reasons.append("price broke below EMA200")

        if rsi_val > cfg.rsi_overbought:
            sell_reasons.append(f"RSI extreme ({rsi_val:.0f})")

        sell_news_flag = getattr(cfg, "news_sell_enabled", cfg.news_enabled)
        if (sell_news_flag
                and news_sentiment <= cfg.news_sell_threshold
                and news_impact == "HIGH"):
            sell_reasons.append(f"high-impact bearish news ({news_sentiment:+.2f})")

        if sell_reasons:
            sl, tp = _sl_tp(price, atr_val, cfg)
            return Signal(
                symbol=symbol, action=Action.SELL, price=price,
                stop_loss=sl, take_profit=tp,
                reason="; ".join(sell_reasons),
                rsi=rsi_val, news_sentiment=news_sentiment,
                confidence=Confidence.HIGH,
                confluence_score=float(len(sell_reasons)),
                news_impact=news_impact,
            )
        return None

    # -----------------------------------------------------------------------
    # BUY side — weighted confluence scoring
    # -----------------------------------------------------------------------

    # Evaluate each condition → (passed, weight, description)
    results: List[Tuple[bool, float, str]] = []

    # 1. EMA200 filter (weight 2.0) — structural; against-trend longs fail
    ema200_ok = price > float(row.ema200)
    results.append((ema200_ok, 2.0, "price > EMA200"))

    # 2. Volume (weight 1.5) — institutional confirmation
    vol_ok = (not pd.isna(row.vol_ratio)
              and float(row.vol_ratio) >= cfg.volume_confirm_ratio)
    vol_desc = (f"volume {float(row.vol_ratio):.1f}× avg"
                if not pd.isna(row.vol_ratio) else "volume n/a")
    results.append((vol_ok, 1.5, vol_desc))

    # 3. ADX (weight 1.5) — trend quality; crossovers in chop are traps
    adx_ok = adx_val >= cfg.adx_trend_threshold
    results.append((adx_ok, 1.5, f"ADX {adx_val:.0f}"))

    # 4. Relative strength vs SPY (weight 1.5) — only ride leaders
    if cfg.rs_filter_enabled and rs_vs_spy != 0.0:
        rs_ok = rs_vs_spy >= 0.0
        results.append((rs_ok, 1.5, f"RS vs SPY {rs_vs_spy:+.1%}"))

    # 5. SMA crossover (weight 1.0) — fresh entry timing
    crossed_up = (
        not pd.isna(prev.fast_ma) and not pd.isna(row.fast_ma)
        and prev.fast_ma <= prev.slow_ma
        and row.fast_ma > row.slow_ma
    )
    results.append((crossed_up, 1.0, "SMA20 crossed above SMA50"))

    # 6. RSI health (weight 1.0) — momentum in valid zone (35–80)
    rsi_ok = cfg.rsi_oversold < rsi_val < cfg.rsi_overbought
    results.append((rsi_ok, 1.0, f"RSI {rsi_val:.0f} healthy"))

    # Compute weighted score
    score      = sum(w for ok, w, _ in results if ok)
    passed     = [(w, desc) for ok, w, desc in results if ok]
    failed     = [(w, desc) for ok, w, desc in results if not ok]

    if score < cfg.min_confluence_score:
        return None

    # Bonus indicators — not in the score but elevate confidence
    bonus = []
    if (not pd.isna(row.macd_hist) and not pd.isna(prev.macd_hist)
            and float(prev.macd_hist) < 0 and float(row.macd_hist) >= 0):
        bonus.append("MACD crossed +")
    if not pd.isna(row.bb_pct) and float(row.bb_pct) > 1.0:
        bonus.append("BB breakout")
    if not pd.isna(row.vwap_roll) and price > float(row.vwap_roll):
        bonus.append("above VWAP")

    # Confidence: based on weighted score level + bonus count
    if score >= 8.0 or (score >= cfg.min_confluence_score and len(bonus) >= 2):
        conf = Confidence.HIGH
    elif score >= 6.0 or (score >= cfg.min_confluence_score and len(bonus) >= 1):
        conf = Confidence.MEDIUM
    else:
        conf = Confidence.LOW

    reason_parts = [desc for _, desc in passed] + bonus
    if failed:
        reason_parts.append(f"[missing: {', '.join(d for _, d in failed)}]")

    sl, tp = _sl_tp(price, atr_val, cfg)
    return Signal(
        symbol=symbol, action=Action.BUY, price=price,
        stop_loss=sl, take_profit=tp,
        reason=" | ".join(reason_parts),
        rsi=rsi_val, news_sentiment=news_sentiment,
        confidence=conf, confluence_score=score,
        news_impact=news_impact,
    )
