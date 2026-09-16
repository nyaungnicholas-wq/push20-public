"""Paper-trading engine with trailing stop and regime filter.

Fixes applied:
  Fix #2: Trailing stop — once at 2:1 R:R move stop to breakeven;
          once at 3:1 start trailing at 2x ATR below current price.
  Fix #6: Slippage raised to 15bps (realistic for live fills).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config


@dataclass
class Position:
    symbol: str
    shares: float
    avg_price: float
    stop_loss: float
    take_profit: float
    initial_stop: float = 0.0       # original stop — used to calculate R multiples
    trailing_active: bool = False   # True once trailing stop has been engaged
    entry_date: str = ""            # for time-based stop tracking
    bars_held: int = 0              # incremented each bar

    def __post_init__(self):
        if self.initial_stop == 0.0:
            self.initial_stop = self.stop_loss

    def market_value(self, price: float) -> float:
        return self.shares * price

    def unrealized_pct(self, price: float) -> float:
        if self.avg_price == 0:
            return 0.0
        return (price - self.avg_price) / self.avg_price

    def r_multiple(self, price: float) -> float:
        """How many R (initial risk units) is the current profit worth."""
        risk = self.avg_price - self.initial_stop
        if risk <= 0:
            return 0.0
        return (price - self.avg_price) / risk

    def heat(self, price: float) -> float:
        """Dollar risk to stop-loss at current price."""
        return max(0.0, self.shares * (price - self.stop_loss))


@dataclass
class Trade:
    date: str
    symbol: str
    side: str
    shares: float
    price: float
    cost: float
    reason: str
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: float = 0.0
    confidence: str = ""


def validate_live_entry(signal_price: float, stop_loss: float,
                        live_price: Optional[float], cfg: Config):
    """Sanity-check a BUY against a live quote before committing capital.

    Returns (ok, entry_price, reason). Signals are computed on daily bars;
    on Jun 5 2026 the system re-bought at the prior close while the market
    traded 3-10% lower — some entries had stops ABOVE the live price.
    """
    if not cfg.live_price_validation or live_price is None or live_price <= 0:
        return True, signal_price, ""
    drift = abs(live_price - signal_price) / signal_price if signal_price > 0 else 1.0
    if drift > cfg.max_entry_price_drift_pct:
        return False, signal_price, (f"stale signal: live ${live_price:.2f} is "
                                     f"{drift:.1%} from signal ${signal_price:.2f}")
    if live_price <= stop_loss:
        return False, signal_price, (f"stop ${stop_loss:.2f} already breached "
                                     f"at live ${live_price:.2f}")
    return True, live_price, ""


@dataclass
class PaperBroker:
    cfg: Config
    cash: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[tuple] = field(default_factory=list)
    last_stop_date: Dict[str, str] = field(default_factory=dict)  # symbol -> date of last stop-out

    def __post_init__(self):
        if self.cash == 0.0:
            self.cash = self.cfg.starting_cash

    # -----------------------------------------------------------------------
    # Valuation & heat
    # -----------------------------------------------------------------------
    def equity(self, prices: Dict[str, float]) -> float:
        invested = sum(
            p.market_value(prices.get(s, p.avg_price))
            for s, p in self.positions.items()
        )
        return self.cash + invested

    def portfolio_heat(self, prices: Dict[str, float]) -> float:
        eq = self.equity(prices)
        if eq <= 0:
            return 0.0
        total = sum(
            p.heat(prices.get(s, p.avg_price))
            for s, p in self.positions.items()
        )
        return total / eq

    def heat_allows_buy(self, prices: Dict[str, float]) -> bool:
        return self.portfolio_heat(prices) < self.cfg.max_portfolio_heat_pct

    def cluster_heat(self, prices: Dict[str, float], cluster: str) -> float:
        """Aggregate stop-distance risk (as fraction of equity) of open
        positions in one correlation cluster. Correlated names fall together;
        their heat must be priced as ONE bet, not independent ones."""
        eq = self.equity(prices)
        if eq <= 0:
            return 0.0
        cmap = self.cfg.correlation_clusters
        total = sum(
            p.heat(prices.get(s, p.avg_price))
            for s, p in self.positions.items()
            if cmap.get(s, s) == cluster
        )
        return total / eq

    def cluster_allows_buy(self, symbol: str, add_risk_dollars: float,
                           prices: Dict[str, float]) -> bool:
        """True if adding `add_risk_dollars` of stop-risk in symbol's cluster
        stays under cluster_heat_cap_pct."""
        eq = self.equity(prices)
        if eq <= 0:
            return False
        cluster = self.cfg.correlation_clusters.get(symbol, symbol)
        return (self.cluster_heat(prices, cluster) + add_risk_dollars / eq
                ) < self.cfg.cluster_heat_cap_pct

    def stopped_out_today(self, symbol: str, date: str) -> bool:
        """True if this symbol hit its stop-loss earlier on `date`.
        Re-entering the same falling name the same day cost -$4,614 of the
        -$6,133 lost in the Jun 4-9 2026 live week."""
        return (self.cfg.reentry_cooldown_enabled
                and self.last_stop_date.get(symbol) == date)

    def daily_loss_halt(self, prices: Dict[str, float], date: str) -> bool:
        """True (halt new buys) once equity is down daily_loss_halt_pct vs
        today's FIRST equity mark. Sells/stops still run."""
        if not self.cfg.daily_loss_halt_enabled:
            return False
        day_start = None
        for d, e in self.equity_curve:          # first mark of `date`
            if d == date:
                day_start = e
                break
        if day_start is None or day_start <= 0:
            return False
        current = self.equity(prices)
        return (day_start - current) / day_start >= self.cfg.daily_loss_halt_pct

    def circuit_breaker_open(self, prices: Dict[str, float]) -> bool:
        """Return True (halt buys) if account is down >10% from its ROLLING peak.

        Uses a 52-bar (1-year) rolling window instead of all-time peak.
        This prevents the circuit breaker from locking permanently after
        a bad stretch — it resets within 52 bars of recovery.
        """
        if not self.cfg.circuit_breaker_enabled or not self.equity_curve:
            return False
        # Rolling 52-bar window (1 year of weekly bars, or ~52 trading days)
        lookback = min(52, len(self.equity_curve))
        recent   = self.equity_curve[-lookback:]
        peak     = max(e for _, e in recent)
        current  = self.equity(prices)
        if peak <= 0:
            return False
        return (peak - current) / peak >= self.cfg.circuit_breaker_drawdown

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------
    def _fill_price(self, price: float, side: str) -> float:
        slip = price * (self.cfg.slippage_bps / 10_000.0)
        return price + slip if side == "BUY" else price - slip

    # -----------------------------------------------------------------------
    # Trailing stop management — Fix #2
    # Called every bar for all open positions.
    # -----------------------------------------------------------------------
    def update_trailing_stops(self, prices: Dict[str, float]):
        if not self.cfg.trailing_stop_enabled:
            return
        for sym, pos in self.positions.items():
            price = prices.get(sym)
            if price is None:
                continue
            r = pos.r_multiple(price)
            atr_approx = pos.avg_price * 0.015  # ~1.5% ATR proxy when no bar data

            # Stage 1: 2:1 reached → move stop to breakeven (entry price)
            if r >= self.cfg.breakeven_trigger_rr and pos.stop_loss < pos.avg_price:
                pos.stop_loss = pos.avg_price
                pos.trailing_active = True

            # Stage 2: 3:1 reached → trail stop at 2x ATR below current price
            if r >= self.cfg.trail_trigger_rr:
                trail_sl = price - self.cfg.trail_atr_multiple * atr_approx
                if trail_sl > pos.stop_loss:
                    pos.stop_loss = round(trail_sl, 2)
                    pos.trailing_active = True

    # -----------------------------------------------------------------------
    # Position sizing
    # -----------------------------------------------------------------------
    def target_shares(self, symbol: str, price: float, stop_loss: float,
                      prices: Dict[str, float],
                      max_pct_override: float = None) -> float:
        eq = self.equity(prices)
        stop_dist = price - stop_loss
        if stop_dist <= 0:
            return 0.0
        risk_dollars   = eq * self.cfg.per_trade_risk_pct
        shares         = risk_dollars / stop_dist
        # HIGH conviction trades use max_pct_override (e.g. 20%) instead of default cap
        max_pct        = max_pct_override if max_pct_override is not None else self.cfg.max_position_pct
        max_pos_value  = eq * max_pct
        shares         = min(shares, max_pos_value / price)
        invested       = sum(p.market_value(prices.get(s, p.avg_price))
                             for s, p in self.positions.items())
        room = min(eq * self.cfg.max_invested_pct - invested, self.cash)
        if room <= 0:
            return 0.0
        shares = min(shares, room / price)
        shares = max(0.0, float(int(shares)))
        # Dust filter: a 1-share order risking <0.05% of equity wastes a slot
        # and a heat budget without moving the needle (NVDA 1sh, AAPL 1sh...).
        if shares > 0 and shares * stop_dist < eq * self.cfg.min_trade_risk_pct:
            return 0.0
        return shares

    # -----------------------------------------------------------------------
    # Order execution
    # -----------------------------------------------------------------------
    def buy(self, date: str, symbol: str, price: float, shares: float,
            reason: str, stop_loss: float, take_profit: float,
            confidence: str = ""):
        if shares <= 0:
            return
        fill = self._fill_price(price, "BUY")
        cost = fill * shares + self.cfg.commission_per_trade
        if cost > self.cash:
            return
        self.cash -= cost
        if symbol in self.positions:
            pos   = self.positions[symbol]
            total = pos.shares + shares
            pos.avg_price   = (pos.avg_price * pos.shares + fill * shares) / total
            pos.shares      = total
            pos.stop_loss   = max(pos.stop_loss, stop_loss)
            pos.take_profit = max(pos.take_profit, take_profit)
        else:
            self.positions[symbol] = Position(
                symbol, shares, fill, stop_loss, take_profit, initial_stop=stop_loss
            )
        self.trades.append(Trade(
            date, symbol, "BUY", shares, fill, -cost, reason,
            stop_loss, take_profit, 0.0, confidence
        ))

    def sell(self, date: str, symbol: str, price: float, reason: str,
             shares: Optional[float] = None):
        if symbol not in self.positions:
            return
        pos    = self.positions[symbol]
        shares = pos.shares if shares is None else min(shares, pos.shares)
        if shares <= 0:
            return
        fill     = self._fill_price(price, "SELL")
        proceeds = fill * shares - self.cfg.commission_per_trade
        pnl      = (fill - pos.avg_price) * shares
        self.cash += proceeds
        pos.shares -= shares
        if pos.shares <= 1e-9:
            del self.positions[symbol]
        if reason.startswith("STOP-LOSS"):
            self.last_stop_date[symbol] = date   # feeds the same-day re-entry cooldown
        self.trades.append(Trade(
            date, symbol, "SELL", shares, fill, proceeds, reason, 0.0, 0.0, pnl
        ))

    # -----------------------------------------------------------------------
    # Risk exits — checked every bar BEFORE signals
    # -----------------------------------------------------------------------
    def check_risk_exits(self, date: str, prices: Dict[str, float]):
        # Update trailing stops first
        self.update_trailing_stops(prices)

        for sym in list(self.positions.keys()):
            price = prices.get(sym)
            if price is None:
                continue
            pos = self.positions[sym]

            # Increment bar counter
            pos.bars_held += 1

            if price <= pos.stop_loss:
                pct = pos.unrealized_pct(price)
                self.sell(date, sym, price,
                          f"STOP-LOSS @ ${pos.stop_loss:.2f} ({pct:+.1%})")
            elif price >= pos.take_profit:
                pct = pos.unrealized_pct(price)
                self.sell(date, sym, price,
                          f"TAKE-PROFIT 5:1 @ ${pos.take_profit:.2f} ({pct:+.1%})")
            # Time-based stop: exit dead-money positions after N bars with no profit
            elif (self.cfg.time_stop_enabled
                  and pos.bars_held >= self.cfg.time_stop_weeks
                  and price <= pos.avg_price * 1.005):  # less than 0.5% profit
                pct = pos.unrealized_pct(price)
                self.sell(date, sym, price,
                          f"TIME-STOP ({pos.bars_held} bars, {pct:+.1%})")

    def mark(self, date: str, prices: Dict[str, float]):
        self.equity_curve.append((date, self.equity(prices)))
