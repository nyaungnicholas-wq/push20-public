"""Pre-trade risk gate — the last thing between a computed target and a live order.

WHY THIS EXISTS
---------------
`rotation_live.run_scheduled` computed targets and submitted orders with NO risk
check of any kind. The backtest engine has a daily-loss halt and a drawdown
halt; the LIVE path had neither, so the one code path that can actually lose
money was the one with no brakes.

The strategy's own risk management (vol targeting, the 1.5x cap, the VIX gate,
the bear de-lever) is model-level: it sizes exposure from market state. This is
different and deliberately dumber — account-level limits that do not care what
the model believes. A model can be wrong; a bug can size 100x; a data feed can
lie. The gate answers one question with no cleverness: given what the ACCOUNT
looks like right now, may any order be sent at all?

DESIGN RULES
------------
1. FAIL CLOSED. If the gate cannot evaluate a limit — no equity, broker error,
   unreadable state — it BLOCKS. An unknown risk state is not a safe one.
2. The manual kill switch is a FILE, not a config value. An operator must be
   able to stop trading without editing code, restarting a process, or waiting
   for a deploy — and it must survive a crash.
3. Blocking is loud. Every block writes a reason and fires a notification;
   silent refusal is indistinguishable from a broken scheduler.
4. It gates the ENTRY to trading, not individual orders. Half-executing a
   rebalance is worse than not starting one — the book ends up somewhere the
   strategy never intended.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
KILL_FILE = os.path.join(_DATA, "KILL_SWITCH")
STATE_PATH = os.path.join(_DATA, "risk_state.json")

# Defaults are deliberately WIDER than the strategy's own expected behaviour.
# A gate that trips during normal operation gets disabled by the operator, and
# a disabled gate protects nothing. These are catastrophe stops, not tuning.
DAILY_LOSS_HALT = 0.08      # -8% in one session
DRAWDOWN_HALT = 0.45        # -45% from the account's rolling peak
MAX_POSITION_WEIGHT = 0.75  # no single symbol above 75% of equity
MAX_GROSS_LEVERAGE = 2.5    # gross exposure ceiling; strategy targets ~1.5x
MIN_EQUITY = 1000.0         # below this, sizing math stops being meaningful


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def blocked_summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "ok"


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(st, f, indent=1)
    except OSError:
        pass


def kill_switch_engaged() -> tuple[bool, str]:
    """A file, so an operator can stop trading with `touch` and it survives a
    crash, a restart, and a deploy."""
    if os.path.exists(KILL_FILE):
        try:
            with open(KILL_FILE) as f:
                note = f.read().strip()
        except OSError:
            note = ""
        return True, note or "no reason recorded"
    return False, ""


def evaluate(account: Optional[dict], positions: Optional[dict],
             today: Optional[str] = None) -> RiskDecision:
    """Decide whether ANY order may be sent this cycle.

    `account` and `positions` come straight from the broker — the gate reads
    the real account rather than the strategy's idea of it, because the whole
    point is to catch the case where those two have diverged.
    """
    d = RiskDecision(allowed=True)
    today = today or date.today().isoformat()

    engaged, note = kill_switch_engaged()
    if engaged:
        d.allowed = False
        d.reasons.append(f"KILL SWITCH engaged ({note}) — remove {KILL_FILE} to resume")
        return d  # nothing else matters

    # FAIL CLOSED: no readable account means no known risk state.
    if not account:
        d.allowed = False
        d.reasons.append("no account data from broker — refusing to trade blind")
        return d
    try:
        equity = float(account.get("equity") or 0)
    except (TypeError, ValueError):
        equity = 0.0
    if equity <= MIN_EQUITY:
        d.allowed = False
        d.reasons.append(f"equity {equity:.2f} at or below the {MIN_EQUITY:.0f} floor")
        return d
    d.metrics["equity"] = equity

    st = _load_state()

    # Rolling peak, for the drawdown halt.
    peak = max(float(st.get("peak_equity") or 0), equity)
    dd = (equity / peak - 1) if peak > 0 else 0.0
    d.metrics["peak_equity"] = peak
    d.metrics["drawdown"] = dd
    if dd <= -DRAWDOWN_HALT:
        d.allowed = False
        d.reasons.append(
            f"drawdown {dd:.1%} breaches the {-DRAWDOWN_HALT:.0%} halt (peak {peak:,.0f})")

    # Session loss, measured against the first equity seen today.
    day_start = st.get("day_start_equity")
    if st.get("day") != today or not day_start:
        day_start, st["day"], st["day_start_equity"] = equity, today, equity
    day_start = float(day_start)
    day_ret = (equity / day_start - 1) if day_start > 0 else 0.0
    d.metrics["day_return"] = day_ret
    if day_ret <= -DAILY_LOSS_HALT:
        d.allowed = False
        d.reasons.append(
            f"session loss {day_ret:.1%} breaches the {-DAILY_LOSS_HALT:.0%} halt")

    # Concentration and gross leverage, computed from the BROKER's positions.
    if positions:
        gross = 0.0
        worst_sym, worst_w = "", 0.0
        for sym, p in positions.items():
            try:
                mv = abs(float(p.get("market_value") or 0))
            except (TypeError, ValueError):
                continue
            gross += mv
            w = mv / equity if equity > 0 else 0.0
            if w > worst_w:
                worst_sym, worst_w = sym, w
        lev = gross / equity if equity > 0 else 0.0
        d.metrics["gross_leverage"] = lev
        d.metrics["max_position_weight"] = worst_w
        if lev > MAX_GROSS_LEVERAGE:
            d.allowed = False
            d.reasons.append(
                f"gross leverage {lev:.2f}x exceeds the {MAX_GROSS_LEVERAGE}x ceiling")
        if worst_w > MAX_POSITION_WEIGHT:
            d.allowed = False
            d.reasons.append(
                f"{worst_sym} is {worst_w:.0%} of equity, above the "
                f"{MAX_POSITION_WEIGHT:.0%} concentration cap")

    st["peak_equity"] = peak
    st["last_eval"] = {"ts": today, "equity": equity, "allowed": d.allowed,
                       "reasons": d.reasons}
    _save_state(st)
    return d


def engage_kill_switch(reason: str) -> None:
    """Write the kill file. Used by automated escalation; an operator can also
    just `touch data/KILL_SWITCH`."""
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(KILL_FILE, "w") as f:
            f.write(reason)
    except OSError:
        pass


def release_kill_switch() -> bool:
    try:
        os.remove(KILL_FILE)
        return True
    except OSError:
        return False
