"""Central configuration — Weekly Confluence Momentum System v5.

v5 changes based on external review:
  - ATR-based stops replace fixed 4% (stops now adapt to each stock's volatility)
  - Weighted confluence score replaces arbitrary count gate
  - News removed from BUY side (latency/reliability concern; kept as SELL blocker)
  - SPY floor reduced 30% → 15% (was dragging performance in bull markets)
  - Fractional Kelly sizing: 1.5% risk per trade (was 2%, which was over-Kelly)
  - Max open positions reduced 15 → 10 (concentrate in highest-ranked setups)
  - Sector exposure cap: max 3 positions per sector simultaneously
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Correlation clusters — symbols that fall together fall as ONE bet.
# Jun 5 2026: 8 simultaneous "independent" longs (SPY/QQQ/XLK/SMH/SOXX/NVDA/
# GOOGL/DIA) all stopped out in a single session. Heat math must price that.
_CLUSTERS: Dict[str, List[str]] = {
    "tech_beta":   ["QQQ", "XLK", "SMH", "SOXX", "AAPL", "MSFT", "AMZN", "GOOGL",
                    "GOOG", "META", "NVDA", "TSLA", "INTC", "QCOM", "ORCL", "IBM",
                    "CSCO", "ADBE", "CRM", "NOW", "AMD", "TXN", "AMAT", "LRCX",
                    "KLAC", "MU", "AVGO", "NFLX", "PYPL", "SHOP", "ZM"],
    "broad_index": ["SPY", "IWM", "DIA", "MDY", "VTI"],
    "financials":  ["XLF", "KRE", "JPM", "BAC", "GS", "WFC", "MS", "C", "BLK",
                    "AXP", "V", "MA", "BRK-B", "USB", "PNC"],
    "healthcare":  ["XLV", "IBB", "XBI", "JNJ", "UNH", "PFE", "MRK", "ABBV",
                    "LLY", "TMO", "ABT", "AMGN", "GILD", "BIIB", "ISRG", "MDT"],
    "energy":      ["XLE", "XOM", "CVX", "COP", "SLB", "EOG"],
    "industrials": ["XLI", "CAT", "HON", "BA", "GE", "MMM", "RTX", "LMT", "DE"],
    "consumer":    ["XLY", "XLP", "XRT", "XHB", "ITB", "HD", "MCD", "KO", "WMT",
                    "PG", "TGT", "COST", "NKE", "SBUX", "LOW", "TJX", "DIS"],
    "communication": ["T", "VZ", "CMCSA"],
    "intl":        ["EFA", "EEM", "FXI", "EWJ", "EWZ"],
    "gold":        ["GDX", "GDXJ", "GLD"],
    "reit_util":   ["XLRE", "XLU", "AMT", "PLD", "NEE", "DUK"],
    "defensive":   ["TLT", "SHY"],
}


@dataclass
class Config:
    # -------------------------------------------------------------------------
    # Universe — 117 liquid symbols with 15+ year price history
    # No post-2010 micro-caps or recent IPOs (survivorship bias prevention)
    # -------------------------------------------------------------------------
    universe: List[str] = field(default_factory=lambda: [
        # Broad market ETFs
        "SPY", "QQQ", "IWM", "DIA", "MDY", "VTI",
        # Sector ETFs
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLB", "XLP", "XLU", "XLRE",
        # Sub-sector / thematic ETFs
        "SMH", "SOXX", "IBB", "XBI", "KRE", "XHB", "ITB", "XRT",
        # International
        "EFA", "EEM", "FXI", "EWJ", "EWZ", "GDX", "GDXJ",
        # Mega-cap tech
        "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "NVDA", "TSLA",
        "INTC", "QCOM", "ORCL", "IBM", "CSCO", "ADBE", "CRM", "NOW",
        # Semiconductors
        "AMD", "TXN", "AMAT", "LRCX", "KLAC", "MU", "AVGO",
        # Financials
        "JPM", "BAC", "GS", "WFC", "MS", "C", "BLK", "AXP", "V", "MA",
        "BRK-B", "USB", "PNC",
        # Healthcare
        "JNJ", "UNH", "PFE", "MRK", "ABBV", "LLY", "TMO", "ABT",
        "AMGN", "GILD", "BIIB", "ISRG", "MDT",
        # Energy & Industrials
        "XOM", "CVX", "COP", "SLB", "EOG",
        "CAT", "HON", "BA", "GE", "MMM", "RTX", "LMT", "DE",
        # Consumer
        "HD", "MCD", "KO", "WMT", "PG", "TGT", "COST", "NKE",
        "SBUX", "LOW", "TJX", "DIS",
        # Communication
        "T", "VZ", "NFLX", "CMCSA",
        # Real estate / utilities
        "AMT", "PLD", "NEE", "DUK",
        # Growth
        "PYPL", "SHOP", "ZM",
    ])

    defensive_assets: List[str] = field(default_factory=lambda: ["TLT", "GLD", "SHY"])

    # -------------------------------------------------------------------------
    # Account
    # -------------------------------------------------------------------------
    starting_cash: float = 100_000.0

    # -------------------------------------------------------------------------
    # Position sizing — concentrated, conviction-weighted
    #
    # v7: fewer positions, bigger size per trade.
    # HIGH confidence signals → 20% per position (high_conviction_pct)
    # MEDIUM confidence signals → 15% per position (max_position_pct)
    # With top-5 slots: up to 5 × 20% = 100%, capped by max_invested_pct.
    # -------------------------------------------------------------------------
    max_position_pct: float = 0.15       # MEDIUM signals: 15% per position
    high_conviction_pct: float = 0.20    # HIGH signals: 20% per position
    max_invested_pct: float = 0.90       # deploy up to 90% — cash is not alpha
    per_trade_risk_pct: float = 0.02     # risk anchor (sizing by risk dollars)

    # -------------------------------------------------------------------------
    # ATR-based stops and targets
    #
    # Fixed % stops (old: 4%) treat NVDA the same as KO. NVDA has a weekly ATR
    # of ~8%; a 4% stop fires on noise. KO's ATR is ~1.5%; 4% is too loose.
    # ATR multiples scale to each stock's actual volatility automatically.
    #
    # Stop  = entry − (atr_stop_multiple × ATR)   →  typically 1.5–2.5×
    # Target = entry + (atr_tp_multiple × ATR)    →  implied R:R ≈ 1.67:1
    # -------------------------------------------------------------------------
    use_atr_stops: bool = True
    atr_stop_multiple: float = 1.5       # stop = 1.5 × weekly ATR
    atr_tp_multiple: float = 2.5         # TP   = 2.5 × weekly ATR  (1.67:1 R:R)

    # v7: TP is set very wide — the trailing stop is the real exit for winners.
    # Fixed TP at 6% was cutting winners that would have run 20-40%.
    # Set to 25% (6.25:1) so it only triggers on exceptional one-week rips;
    # everything else exits via trailing stop, time stop, or signal reversal.
    rr_ratio: float = 6.25              # TP = 25% (4% stop × 6.25) — trailing stop exits
    stop_loss_pct: float = 0.04

    # Portfolio heat: no new buys if aggregate stop-distance risk > 6%
    max_portfolio_heat_pct: float = 0.06

    # -------------------------------------------------------------------------
    # Trailing stop — locks in profit as the trade moves
    # Stage 1: at 2× initial risk → move stop to breakeven (no loss possible)
    # Stage 2: at 3× initial risk → trail stop at 2× ATR below current price
    # -------------------------------------------------------------------------
    trailing_stop_enabled: bool = True
    breakeven_trigger_rr: float = 2.0
    trail_trigger_rr: float = 3.0
    trail_atr_multiple: float = 2.0

    # -------------------------------------------------------------------------
    # Circuit breaker
    # Halts all new buys if account is down >10% from its rolling 52-week peak.
    # Resets automatically when equity recovers.
    # -------------------------------------------------------------------------
    circuit_breaker_enabled: bool = True
    circuit_breaker_drawdown: float = 0.10

    # -------------------------------------------------------------------------
    # Risk fixes from the Jun 4-9 2026 live trade-log autopsy.
    # 75% of that week's -6.1% came from stop-out -> same-day re-entry at stale
    # signal prices; most of the rest from one correlated tech-beta bet whose
    # stops gap-filled 1.1-6.5x past intended risk.
    # -------------------------------------------------------------------------
    reentry_cooldown_enabled: bool = True   # never re-buy a symbol stopped out today
    live_price_validation: bool = True      # entries must be sanity-checked vs a live quote
    max_entry_price_drift_pct: float = 0.015  # reject entry if live is >1.5% from signal price
    min_trade_risk_pct: float = 0.0005      # skip dust orders risking <0.05% of equity
    daily_loss_halt_enabled: bool = True
    daily_loss_halt_pct: float = 0.02       # halt new buys once equity is -2% vs today's first mark
    cluster_heat_cap_pct: float = 0.02      # max combined stop-distance risk per correlation cluster
    correlation_clusters: Dict[str, str] = field(default_factory=lambda: {
        sym: name for name, syms in _CLUSTERS.items() for sym in syms
    })

    # -------------------------------------------------------------------------
    # Technical indicator periods (daily-bar scale; auto-adjusted for weekly)
    # -------------------------------------------------------------------------
    fast_ma: int = 20
    slow_ma: int = 50
    ema_fast: int = 20
    ema_slow: int = 50
    ema_long: int = 200
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 80.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    adx_period: int = 14
    volume_confirm_ratio: float = 1.2

    # -------------------------------------------------------------------------
    # Weighted confluence score (replaces arbitrary binary count gate)
    #
    # Each condition carries a weight reflecting its reliability and importance.
    # A signal fires only when the sum of passing weights >= min_confluence_score.
    #
    # Weights (see strategy.py for full table):
    #   EMA200 filter:    2.0  (structural — most important; against-trend trades fail)
    #   Volume:           1.5  (institutional participation; empty moves reverse)
    #   ADX:              1.5  (trend quality; MA crossovers in chop are traps)
    #   Relative strength:1.5  (cross-sectional momentum; only ride sector leaders)
    #   SMA crossover:    1.0  (entry timing; confirms fresh trend, not continuation)
    #   RSI health:       1.0  (not exhausted or dead; 35–80 zone)
    #
    # Max possible score: 9.5
    # Threshold 5.0 → need EMA200 (2.0) + at least 2 supporting factors.
    # Without EMA200, need every other condition simultaneously (rare and noisy).
    # -------------------------------------------------------------------------
    min_confluence_score: float = 6.5    # v6 calibrated: 117 trades/yr at 64% win rate
    adx_trend_threshold: float = 15.0

    # -------------------------------------------------------------------------
    # Relative strength filter
    # Only buy stocks outperforming SPY over the last 13 weeks.
    # Cross-sectional momentum is one of the most robust documented factors.
    # -------------------------------------------------------------------------
    rs_filter_enabled: bool = True
    rs_lookback_weeks: int = 13

    # -------------------------------------------------------------------------
    # Sector correlation cap
    # Never hold more than max_sector_positions in the same sector simultaneously.
    # Prevents accidentally owning the same factor (e.g. "tech") in 10 wrappers.
    # -------------------------------------------------------------------------
    max_sector_positions: int = 99       # v6: uncapped — sector cap hurt profit in sweeps

    # -------------------------------------------------------------------------
    # Half-Kelly after loss
    # After any week where portfolio closes lower, halve new position sizes.
    # Restores when equity recovers to the pre-loss level.
    # -------------------------------------------------------------------------
    half_kelly_after_loss: bool = True

    # -------------------------------------------------------------------------
    # SPY floor — reduced from 30% to 15%
    #
    # Rationale: 30% was diluting active returns too heavily in bull markets.
    # 15% still guarantees a baseline CAGR floor (~1.5%/yr unconditional)
    # while freeing 15% more capital for high-alpha active setups.
    # The active and passive sleeves are intentionally separated so each can
    # be evaluated independently (active alpha vs passive beta).
    # -------------------------------------------------------------------------
    # v7: SPY floor removed — it was a drag against active positions.
    # SPY returned 14% CAGR; if the active strategy can't beat that, the floor
    # just averages down the returns. Invest passively elsewhere if desired.
    spy_floor_enabled: bool = False
    spy_floor_pct: float = 0.0

    # -------------------------------------------------------------------------
    # Time-based stop — exit dead-money positions
    # 20 weeks with < 0.5% profit → recycle capital into fresher setups
    # -------------------------------------------------------------------------
    time_stop_enabled: bool = True
    time_stop_weeks: int = 20

    # -------------------------------------------------------------------------
    # Weekly bars — validated as optimal for this signal set
    # Daily bars tested and rejected: win rate drops 58% → 48% due to MA noise
    # -------------------------------------------------------------------------
    use_weekly_bars: bool = True

    # -------------------------------------------------------------------------
    # v7: Defensive rotation DISABLED — go to cash in bear markets.
    # TLT/GLD/SHY all fell in 2022 alongside stocks (rising rate environment).
    # The "bonds rise when stocks fall" assumption broke.
    # Cash is the only guaranteed safe asset in a rising rate bear market.
    # In bear regime: no new buys, existing positions exit via stops.
    # -------------------------------------------------------------------------
    defensive_rotation_enabled: bool = False

    # -------------------------------------------------------------------------
    # Drawdown-proportional sizing
    # Each 5% drawdown reduces new position sizes by 25%, floor at 25% of normal
    # -------------------------------------------------------------------------
    benchmark_sizing_enabled: bool = True
    sizing_drawdown_scale: float = 0.05

    # -------------------------------------------------------------------------
    # Signal ranking — take only top N by weighted confluence score per cycle
    # Reduced from 15 → 10 to concentrate capital in the highest-quality setups
    # -------------------------------------------------------------------------
    momentum_rank_enabled: bool = True
    momentum_top_n: int = 5              # v7: top 5 only — concentrate in the best

    # -------------------------------------------------------------------------
    # Regime filter
    # -------------------------------------------------------------------------
    regime_filter_enabled: bool = True
    regime_symbol: str = "SPY"

    # -------------------------------------------------------------------------
    # Sector rotation — the strategy that actually beats SPY (see trader/rotation.py).
    #
    # Holds the top-N sector ETFs by blended 6mo/12mo relative strength, equal
    # weight, rebalanced monthly. An absolute-momentum filter parks slack capital
    # in T-bills (BIL) when fewer than N sectors are trending up. ETF-only universe
    # → no single-name survivorship bias, unlike the signal-based system.
    #
    # Backtest 2018-2024 (10bps/side): ~20% CAGR / -31% MDD vs SPY 13.7% / -34%.
    # Beats SPY in both 2018-2020 and 2021-2024 sub-periods independently.
    # -------------------------------------------------------------------------
    rotation_universe: List[str] = field(default_factory=lambda: [
        # ---- 13 core GICS sector ETFs (non-overlapping, no survivorship bias) ----
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLB", "XLP", "XLU", "XLRE", "XLC",
        "SMH", "QQQ",
        # ---- sub-sector & thematic ETFs ----
        "KRE",   # Regional Banks
        "XHB",   # Homebuilders
        "IBB",   # Biotech
        "XRT",   # Retail
        "ITB",   # Home Construction
        # ---- factor / style ETFs ----
        "IWD",   # Russell 1000 Value
        "IWF",   # Russell 1000 Growth
        "MTUM",  # MSCI USA Momentum
        # ---- international ETFs ----
        "EFA",   # MSCI EAFE (developed intl)
        "EEM",   # MSCI Emerging Markets
        "VEA",   # Vanguard Developed Markets
        "VWO",   # Vanguard Emerging Markets
        "FXI",   # China Large-Cap
        "EWJ",   # Japan
        # ---- commodity & macro ETFs ----
        "GLD",   # Gold
        "USO",   # Oil
        "DBA",   # Agriculture
        "DBC",   # Broad Commodities
        # ---- fixed income ETFs ----
        "TLT",   # 20yr Treasury
        "IEF",   # 7-10yr Treasury
        "HYG",   # High Yield
        # ---- broad market ----
        "IWM",   # Russell 2000 (small-cap)
        "MDY",   # Mid-Cap
        # ---- individual large-cap stocks (15+ yr history; survivorship bias in backtest) ----
        # NOTE: backtest numbers for stocks are inflated — live trading is what matters.
        "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "AMD", "QCOM", "AVGO",
        "JPM", "BAC", "GS", "V", "MA", "BLK",
        "JNJ", "UNH", "LLY", "ABBV", "TMO",
        "XOM", "CVX", "COP",
        "HD", "MCD", "KO", "WMT", "COST", "NKE",
        "CAT", "HON", "BA", "GE", "RTX",
        "NFLX", "CMCSA",
    ])
    rotation_top_n: int = 3                 # hold the 3 strongest sectors
    rotation_lookbacks: tuple = (126, 252)  # blended 6-month & 12-month momentum (trading days)
    rotation_abs_momentum: bool = True      # require positive momentum to hold a sector
    rotation_cash_symbol: str = "BIL"       # park unfilled slots in 1-3mo T-bills
    rotation_warmup_days: int = 420         # calendar days of pre-start history for the 12mo lookback
    # Tranched rebalancing: split the book into N equal sub-portfolios, each
    # rebalanced monthly on a different trading-day-of-month (0=1st, 10≈mid, 20≈late).
    # This averages out the timing luck of single-day rebalancing (which swings
    # ~11-19% CAGR by day). Set to (0,) for classic first-of-month single rebalance.
    rotation_tranches: tuple = (0, 10, 20)

    # -------------------------------------------------------------------------
    # v2 research-loop upgrades — all OFF by default so the baseline `rotation`
    # command is byte-for-byte unchanged. The `rotation-v2` command and the
    # research harness flip these on to search for a configuration that beats
    # SPY over the full 2005-2024 cycle (the baseline LOSES over 20 years).
    # -------------------------------------------------------------------------
    rotation_skip_days: int = 0            # 12-1 momentum: skip most-recent N days (≈21) to dodge short-term reversal
    rotation_trend_filter: bool = False    # a sector must be above its own SMA to be eligible
    rotation_trend_sma: int = 200
    rotation_cluster_filter: bool = False  # hold at most one ETF per correlation cluster (kills triple-tech)
    rotation_momentum_weight: bool = False # size positions ∝ momentum score (floored) instead of equal weight
    rotation_weight_floor: float = 0.15    # min weight per position when momentum-weighting
    rotation_regime_filter: bool = False   # when SPY is below its SMA, go fully defensive
    rotation_regime_sma: int = 200
    rotation_dual_momentum: bool = False   # a sector must beat SPY's own momentum (not merely be >0)
    rotation_defensive_symbol: str = ""    # asset held when defensive / slots empty ("" → use rotation_cash_symbol)
    rotation_position_cap: float = 1.0     # cap on any single position weight (1.0 = uncapped); de-concentrates
    rotation_vol_target: float = 0.0       # >0 → scale gross equity exposure to this annualized vol (de-lever only)
    rotation_vol_window: int = 20          # trailing trading days used to estimate realized vol
    rotation_vol_proxy: str = "SPY"        # symbol whose realized vol drives the vol-target overlay
    # --- v3: a genuinely NEW factor (risk structure), to break the single-factor plateau ---
    rotation_weight_scheme: str = "momentum"  # momentum | invvol | sharpe | rp_blend  (how to size the held sleeve)
    rotation_rank_metric: str = "mom"         # mom | sharpe  (rank/select sectors by raw momentum or momentum/vol)
    rotation_factor_window: int = 63          # trailing days for per-sector vol used by invvol/sharpe schemes

    # -------------------------------------------------------------------------
    # V4 Turbo upgrades (A+E)
    # -------------------------------------------------------------------------
    rotation_return_prop: bool = False         # weight ∝ R_i / sum(R) — overweights the #1 sector
    rotation_two_way_vol: bool = False         # two-way vol target: lever up when vol < target
    rotation_vol_cap: float = 2.0             # max scale factor (requires per-sector 2x ETFs)
    rotation_vol_floor: float = 0.50          # min scale factor (50% invested at max stress)

    # -------------------------------------------------------------------------
    # V5 Hyper-Drive upgrades
    # -------------------------------------------------------------------------
    rotation_weight_squared: bool = False      # weight ∝ R_i² / sum(R²) — stronger concentration vs return_prop
    rotation_vol_cap_bear: float = 1.5        # asymmetric cap: when SPY < 200 SMA, limit scale to this (0.0 = full vault)
    rotation_vol_cap_sma: int = 200           # SMA window used to determine bull/bear for asymmetric cap
    rotation_use_3x: bool = False             # deploy 3x ETFs when scale > 2.0 (TECL/TQQQ/SOXL/FAS)
    # V5.5: 50-day circuit breaker — early de-lever before 20-day vol spikes
    rotation_breaker_sma: int = 0             # if >0: when SPY < this SMA, cap scale at breaker_level (0 = off)
    rotation_breaker_level: float = 1.0       # exposure cap when the breaker fires (1.0 = no leverage, 0.5 = half)

    # -------------------------------------------------------------------------
    # News — BUY side disabled (latency and reliability concerns)
    #
    # The external review correctly identified that automated news sentiment
    # without a strictly point-in-time, lagged feed introduces look-ahead bias
    # and false positives during volatile weeks.
    #
    # news_enabled controls the BUY gate only.
    # High-impact bearish news still triggers SELL exits (news_sell_enabled).
    # -------------------------------------------------------------------------
    news_enabled: bool = False           # disabled on BUY side — use technical only
    news_sell_enabled: bool = True       # still blocks / exits on confirmed bad news
    news_lookback_days: int = 3
    news_buy_threshold: float = 0.4
    news_sell_threshold: float = -0.4
    news_block_threshold: float = -0.4
    macro_freeze_threshold: float = -0.3
    news_fail_safe: str = "neutral"

    # -------------------------------------------------------------------------
    # API keys (injected from environment)
    # -------------------------------------------------------------------------
    anthropic_api_key: Optional[str] = None
    newsapi_key: Optional[str] = None
    alpaca_key: Optional[str] = None
    alpaca_secret: Optional[str] = None
    fmp_key: Optional[str] = None

    # -------------------------------------------------------------------------
    # Backtest window
    # -------------------------------------------------------------------------
    backtest_start: str = "2018-01-01"
    backtest_end: str = "2024-12-31"

    # -------------------------------------------------------------------------
    # Transaction costs — conservative estimate for weekly large-cap execution
    # -------------------------------------------------------------------------
    commission_per_trade: float = 0.0
    slippage_bps: float = 10.0          # raised slightly for high-beta names in universe

    # -------------------------------------------------------------------------
    # Broker
    # -------------------------------------------------------------------------
    broker: str = "paper"


def _load_dotenv() -> None:
    """Populate os.environ from the project .env (no external dependency).

    Existing real environment variables win (setdefault), so exported keys
    override the file. Without this, ALPACA_KEY/SECRET in .env never reach the
    config and every live/paper command silently runs offline.
    """
    import os
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def load_config() -> Config:
    import os
    _load_dotenv()
    cfg = Config()
    seen, uniq = set(), []
    for s in cfg.universe:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    cfg.universe = uniq
    cfg.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    cfg.newsapi_key       = os.getenv("NEWSAPI_KEY")
    cfg.alpaca_key        = os.getenv("ALPACA_KEY")
    cfg.alpaca_secret     = os.getenv("ALPACA_SECRET")
    cfg.fmp_key           = os.getenv("FMP_KEY")
    return cfg


CONFIG = load_config()
