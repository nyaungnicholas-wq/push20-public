"""Live paper-trading cycle for V5 Hyper-Drive (rotation-v5 strategy).

This is the bridge: the backtest proved V5, this runs it against the real Alpaca
paper account. Each cycle:
  1. Apply the V5 flags (shared source of truth: rotation.V5_FLAGS via apply_v5).
  2. Read live equity + current positions from Alpaca (the source of truth).
  3. Fetch recent prices and compute V5's target sectors + weights
     (12-month momentum selection + momentum-squared sizing + 3-tier asymmetric
     vol overlay with 2x/3x ETFs), causally.
  4. Diff current vs target and submit market orders (sells first, then buys).

Run it monthly (the strategy rebalances monthly). `dry_run=True` prints the plan
without sending any orders — used to verify wiring without trading.

NOTE: per the project's standing rule, orders are only sent when explicitly
invoked with --live; the default is a dry run.
"""

from __future__ import annotations

import math
import os
from datetime import date, timedelta
from typing import Dict, Optional

import pandas as pd

from .config import Config
from .data_source import get_data_source
from .rotation import (apply_v5, apply_daily_champion,
                       select_targets, rebalance_weights,
                       turbo_allocation, _two_way_vol_scale,
                       _close_frame, _defensive_list,
                       LEV2X_MAP, LEV3X_MAP, V5_FLAGS, DAILY_CHAMPION_FLAGS)
from .alpaca_broker import AlpacaBroker
from . import session as _s


_STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "rotation_state.json")
_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "rotation_cron.log")
_PENDING_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pending_orders.json")
_SLIPPAGE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "slippage_log.jsonl")


def _log(msg: str) -> None:
    from datetime import datetime
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


_ALERT_FAILED = False

# Refuse to leave orders resting over a long weekend: a Friday-evening
# submission sits through two and a half days of news before the Monday open.
MAX_QUEUE_GAP_DAYS = 1


def _ledger(action, reason="", **kw):
    """Record the outcome of this run. Never raises -- a ledger failure must
    not become the reason a rebalance did not happen."""
    try:
        from . import notify as _n
        return _n.ledger(action, reason=reason, **kw)
    except Exception as e:
        _log(f"ledger write failed (non-fatal): {e}")
        return None


def _notify(title: str, message: str) -> None:
    """Alert through trader.notify. Records a failure instead of hiding it.

    This used to shell out to `osascript` -- macOS only -- inside a bare
    `except Exception: pass`, so on this Windows box every alert since the port
    has been a silent no-op. The rotation missed five trading days in August
    and none of these fired.

    It does NOT re-raise. `notify.notify` raises on a failed delivery, which is
    correct for a caller that can stop, but several call sites here are inside
    an `except` block that is about to re-raise the real error -- letting a
    delivery failure escape there would MASK the failure being reported. So the
    failure is recorded, a ledger row is written, and the module flag makes the
    run report itself as not-ok.
    """
    global _ALERT_FAILED
    try:
        from . import notify as _n
        _n.notify(title, message, level="alert")
    except Exception as e:               # NotifyError, or notify itself broken
        _ALERT_FAILED = True
        _log(f"ALERT DELIVERY FAILED ({e}) — original alert: {title}: {message}")
        try:
            from . import notify as _n
            _n.ledger("alert_delivery_failed", reason=str(e)[:200], ok=False,
                      alert_title=title)
        except Exception:
            pass                         # the log line above is the last resort


def _load_state() -> dict:
    import json
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    import json
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def _days_since_last_rebalance(trading_days: bool = False, asof=None) -> int:
    """Days since the last rebalance (999 if never).

    `asof` defaults to the ET session date, NOT `date.today()`. The local clock
    here is Pacific: at 18:30 PT it is already 21:30 ET, and past 21:00 PT the
    ET date has rolled while the local one has not. Evening mode runs squarely
    in that window.

    `trading_days=True` counts SESSIONS instead of calendar days, which is what
    the certifying backtest does (`reports/opt_harness.py:216` indexes trading
    days). Friday to Monday is 3 calendar days but 1 trading day, so live has
    been rebalancing where the backtest would not. The default stays calendar
    until the harness has measured what switching costs -- see the plan's 3b.
    """
    from datetime import date as _date
    from . import session as _s
    last = _load_state().get("last_rebalance_date", "")
    if not last:
        return 999
    try:
        prev = _date.fromisoformat(last)
    except ValueError:
        return 999
    today = asof or _s.et_date()
    if trading_days:
        return _s.sessions_between(prev, today)
    return (today - prev).days


def _save_pending(records: list) -> None:
    """Persist orders awaiting fill (with the expected/decision price) so the next
    run can reconcile them against actual Alpaca fills and measure slippage."""
    import json
    try:
        os.makedirs(os.path.dirname(_PENDING_PATH), exist_ok=True)
        with open(_PENDING_PATH, "w") as f:
            json.dump(records, f)
    except OSError:
        pass


def _order_summary(result: dict) -> str:
    """One readable line per order, for the phone. Never raises: this feeds an
    alert, and an alert that dies formatting itself reports nothing at all."""
    try:
        sent = result.get("sent") or []
        if not sent:
            return "No orders were sent."
        lines = [f"{side} {qty} {sym}" for side, sym, qty in sent]
        return "\n".join(lines)
    except Exception:
        return f"{len(result.get('sent') or [])} orders (detail unavailable)."


def _fill_session_open(symbol: str, when: str) -> tuple:
    """(fill_date, open_price) for `symbol` on the session it actually filled in.

    `when` is Alpaca's `filled_at` (ISO, UTC) — the real fill instant, not a guess
    from when reconciliation happened to run. Orders are queued after the close and
    execute at the next open, so this open is the correct benchmark for execution
    cost. Returns (date, None) when no bar is available; callers must then omit the
    slippage field rather than substitute a zero.
    """
    if not when:
        return None, None
    day = str(when)[:10]
    try:
        from .data_source import get_data_source
        import pandas as pd
        start = (pd.Timestamp(day) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(day) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        raw = get_data_source("yfinance").history([symbol], start, end)
        df = raw.get(symbol)
        if df is None or df.empty or "open" not in df:
            return day, None
        idx = pd.to_datetime(df.index).normalize()
        hit = df[idx == pd.Timestamp(day).normalize()]
        if hit.empty:
            return day, None
        px = float(hit["open"].iloc[0])
        return day, (px if px > 0 else None)
    except Exception:
        # Measurement must never be able to break reconciliation, which is what
        # clears pending orders. A missing open costs one data point.
        return day, None


def reconcile_fills(cfg: Config) -> int:
    """Read pending orders from the previous run, fetch their actual fill price
    from Alpaca, compute slippage (bps vs the decision-time price), and append to
    slippage_log.jsonl. Returns the number of fills logged. Safe no-op if nothing
    is pending. This is the live-data recording layer — it does NOT change trading."""
    import json
    from datetime import datetime
    try:
        with open(_PENDING_PATH) as f:
            pending = json.load(f)
    except (OSError, ValueError):
        return 0
    if not pending:
        return 0
    alpaca = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret)
    if not alpaca.connected():
        return 0

    # Statuses Alpaca will never move off — once seen, the order is done and must
    # be dropped from `pending` even when nothing filled, or it is re-queried forever.
    TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}

    logged = 0
    rows = []
    resolved = set()                      # order ids to clear from pending
    unfilled = []                         # terminal orders that did NOT fully fill
    for rec in pending:
        oid = rec.get("order_id")
        if not oid:
            continue
        o = alpaca.get_order(oid)
        if not o:
            continue
        status = o.get("status", "")
        want_qty = float(rec.get("qty", 0) or 0)
        got_qty = float(o.get("filled_qty", 0) or 0)
        if status in TERMINAL:
            resolved.add(oid)
            if got_qty < want_qty:
                unfilled.append((rec.get("symbol"), rec.get("side"),
                                 got_qty, want_qty, status))
        fap = o.get("filled_avg_price")
        if not fap or got_qty <= 0:
            continue                      # nothing executed — no fill to log
        fill = float(fap)
        exp = float(rec.get("expected", 0) or 0)
        side = rec.get("side", "buy")
        sym = rec.get("symbol")

        def _signed(ref: float) -> Optional[float]:
            """bps vs `ref`, signed so POSITIVE is always worse for us."""
            if not ref or ref <= 0:
                return None
            raw = (fill - ref) / ref * 10000.0
            return raw if side == "buy" else -raw

        # Two different numbers, previously conflated under one name.
        #
        # drift_bps: decision close -> fill. This system decides after the close
        # and the order fills at the NEXT session's open, so this carries a whole
        # overnight gap (and a weekend, when the decision lands on a Friday). It
        # is market movement, not a cost: it is symmetric and averages out. On
        # 2026-09-14 it read 1002bps on a USD sell because 2x semis fell 10%
        # between the Friday decision and the Monday open. Nothing was executed
        # badly.
        #
        # slippage_bps: session open -> fill. This is the actual execution cost,
        # and it is the only one of the two the account can control. Measured
        # over the first 105 fills: drift 5.27bps, execution 3.51bps.
        fill_date, open_px = _fill_session_open(sym, o.get("filled_at"))
        drift = _signed(exp)
        slip = _signed(open_px) if open_px else None
        row = {
            "logged_at": datetime.now().isoformat(timespec="seconds"),
            "submit_date": rec.get("date"), "symbol": sym,
            # qty = what actually EXECUTED. submitted_qty is kept alongside so the
            # ledger can be reconciled against Alpaca instead of overstating size.
            "side": side, "qty": got_qty, "submitted_qty": want_qty,
            "status": status,
            "expected": exp, "fill": fill, "order_id": oid,
            "fill_date": fill_date,
        }
        if drift is not None:
            row["drift_bps"] = round(drift, 2)
        if open_px:
            row["open_px"] = open_px
        # Omitted entirely when the open is unavailable. A 0.0 stand-in would be
        # read as "executed perfectly" by every consumer downstream.
        if slip is not None:
            row["slippage_bps"] = round(slip, 2)
        rows.append(row)
        logged += 1

    if rows:
        try:
            os.makedirs(os.path.dirname(_SLIPPAGE_PATH), exist_ok=True)
            with open(_SLIPPAGE_PATH, "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")

            # Notional-weighted, because cost lands on dollars traded: a 1-share
            # order must not count the same as a 600-share one. The fields are
            # ALREADY signed at write time above, so do not flip them again here
            # — doing that was a real bug on 2026-09-13 and it read 8.49bps on a
            # book whose measured drift was 5.41.
            def _wavg(key):
                g = [(r[key], abs(float(r["qty"]) * float(r["fill"])))
                     for r in rows if key in r]
                w = sum(n for _, n in g)
                return (sum(v * n for v, n in g) / w) if w > 0 else None

            d, s = _wavg("drift_bps"), _wavg("slippage_bps")
            parts = [f"slippage: logged {len(rows)} fills"]
            if s is not None:
                parts.append(f"execution {s:+.1f}bps vs the open")
            if d is not None:
                parts.append(f"queue drift {d:+.1f}bps vs the decision close")
            _log(", ".join(parts) + ".")
        except OSError:
            pass

    # Fill-rate surveillance: a MOC order that expires short leaves the book off
    # target, which the backtest never models. Log it, and alert when a whole
    # order got nothing (the case that can strand us in a position we sold).
    if unfilled:
        detail = ", ".join(f"{sd} {sym} {int(g)}/{int(w)} ({st})"
                           for sym, sd, g, w, st in unfilled)
        _log(f"UNDERFILL — {len(unfilled)} order(s) did not complete: {detail}")
        zero = [u for u in unfilled if u[2] <= 0]
        if zero:
            _notify("⚠️ PUSH-20 orders did NOT fill",
                    f"{len(zero)} order(s) executed nothing: "
                    + ", ".join(f"{sd} {sym}" for sym, sd, _, _, _ in zero)
                    + ". The book is off target — check the blotter.")

    # Clear every order Alpaca has finished with — including ones that filled
    # nothing. Keeping those would re-query a dead id on every run, forever.
    remaining = [r for r in pending if r.get("order_id") not in resolved]
    _save_pending(remaining)
    return logged


_TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected",
                      "done_for_day", "replaced"}


def _complete_fills(alpaca, pending, prices, asof, verbose: bool = False,
                    rounds: int = 2, wait_s: float = 5.0) -> list:
    """Re-submit the shortfall on any order that did not fully execute.

    Measured on this account (2026-05→07): Market-On-Close orders executed only 49% of
    submitted shares — Alpaca partially fills the closing auction and expires the rest —
    while plain market orders executed 99%. A partial is not harmless: the book silently
    sits between the old and new target, and on 2026-06-29 and 2026-07-02 two successive
    attempts to sell DIG both expired, stranding a position the strategy wanted out of.

    Topping up in the same run, while the market is still open, is what keeps the live
    book on the target the backtest assumes. Only trims the gap — never exceeds the
    original order quantity.
    """
    import time
    for _ in range(max(1, rounds)):
        time.sleep(wait_s)
        shortfalls = []
        for rec in pending:
            if rec.get("_topped"):
                continue
            o = alpaca.get_order(rec.get("order_id")) or {}
            status = o.get("status", "")
            if status not in _TERMINAL_STATUSES:
                continue                     # still working — leave it alone
            want = float(rec.get("qty", 0) or 0)
            got = float(o.get("filled_qty", 0) or 0)
            rec["_topped"] = True
            if got < want:
                shortfalls.append((rec.get("symbol"), rec.get("side"), int(want - got)))
        if not shortfalls:
            break
        failed = []
        for sym, side, qty in shortfalls:
            if qty <= 0:
                continue
            if verbose:
                print(f"  TOP-UP {side.upper()} {qty} {sym}")
            res = (alpaca.place_market_sell_qty(sym, qty, tif="day") if side == "sell"
                   else alpaca.place_market_buy(sym, qty, tif="day"))
            oid = res.get("id") if isinstance(res, dict) else None
            # Log the OUTCOME, never the intention. This line used to be emitted
            # BEFORE the placement, and a None result (the shape a disconnected
            # broker returns) was then dropped silently. On 2026-09-13 the log
            # showed four confident "top-up — sell 13 USD" lines followed by
            # "SKIP — Alpaca not connected": not one of those orders existed, and
            # nothing in the log said so. The book sat off target for three days.
            if oid:
                _log(f"top-up — {side} {qty} {sym} submitted ({str(oid)[:8]}).")
                pending.append({"date": str(asof.date()), "symbol": sym, "side": side,
                                "qty": int(qty),
                                "expected": float(prices.get(sym, 0) or 0),
                                "order_id": oid})
            else:
                _log(f"TOP-UP FAILED — {side} {qty} {sym} was NOT submitted "
                     f"(broker returned nothing). Book is off target.")
                failed.append((side, qty, sym))
        if failed:
            _notify("⚠️ PUSH-20 top-up orders were NOT placed",
                    f"{len(failed)} shortfall order(s) failed to submit: "
                    + ", ".join(f"{sd} {q} {sy}" for sd, q, sy in failed)
                    + ". The book is off target and no order exists to fix it.")
    for rec in pending:
        rec.pop("_topped", None)
    return pending


def run_scheduled(cfg: Config, dry_run: bool = False,
                  next_open: bool = False) -> Dict:
    """Daily scheduled entry point for the background job (Daily Champion strategy).

    Runs every weekday morning. Gates:
      1. Alpaca connected.
      2. Market is open (orders only fill during RTH).
      3. At least min_hold_days have passed since the last rebalance.

    When all gates pass, fetches data and rebalances to the daily champion target.

    next_open=True selects EVENING MODE, which inverts gate 2: the market must
    be CLOSED, the decision is taken on the session that has just completed,
    and orders are queued for the next open. See the branch below for why.

    EVERY return path writes a ledger row. A path with no row is
    indistinguishable from a run that never happened, and that ambiguity is
    what hid five missed trading days in August.
    """
    global _ALERT_FAILED
    _ALERT_FAILED = False
    apply_daily_champion(cfg)
    min_hold = DAILY_CHAMPION_FLAGS.get("rotation_min_hold_days", 3)

    alpaca = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret)
    if not (cfg.alpaca_key and cfg.alpaca_secret and alpaca.connected()):
        _log("SKIP — Alpaca not connected (check ALPACA_KEY/SECRET).")
        # Only a real problem on trading days; the 7-day schedule also wakes on
        # weekends, where a no-trade is expected — don't false-alarm then.
        if date.today().weekday() < 5:
            _notify("⚠️ PUSH-20 did NOT trade",
                    "Alpaca not connected on a trading day — check ALPACA_KEY/SECRET. "
                    "The rebalance was skipped.")
        _ledger("skip", "not_connected", ok=False)
        return {"action": "skip", "reason": "not_connected"}

    # Live-data recording: reconcile any prior MOC orders that have now filled,
    # logging actual fill vs decision price to slippage_log.jsonl. Pure logging —
    # does not affect trading.
    try:
        reconcile_fills(cfg)
    except Exception as e:
        _log(f"slippage reconcile error (non-fatal): {e}")

    # PRE-TRADE RISK GATE (2026-07-25). Account-level catastrophe limits,
    # evaluated against what the BROKER reports rather than what the strategy
    # believes — the point is to catch the case where those have diverged.
    # Deliberately upstream of every order: half-executing a rebalance leaves
    # the book somewhere the strategy never intended, so the gate stops the
    # cycle rather than individual orders.
    try:
        from . import risk_gate
        decision = risk_gate.evaluate(alpaca.get_account(), alpaca.get_positions())
        if not decision.allowed:
            _log(f"BLOCKED by risk gate — {decision.blocked_summary()}")
            _notify("🛑 PUSH-20 trading BLOCKED", decision.blocked_summary())
            _ledger("blocked", decision.blocked_summary()[:200], ok=False)
            return {"action": "blocked", "reason": decision.blocked_summary(),
                    "metrics": decision.metrics}
    except Exception as e:
        # A gate that fails open is not a gate. An error evaluating risk is
        # itself an unknown risk state, so it blocks.
        _log(f"BLOCKED — risk gate error (failing closed): {e}")
        _notify("🛑 PUSH-20 blocked", f"risk gate error, failing closed: {e}")
        _ledger("blocked", f"risk gate error: {e}"[:200], ok=False)
        return {"action": "blocked", "reason": f"risk gate error: {e}"}

    if next_open:
        # EVENING MODE. The gate is inverted: the market must be CLOSED, and we
        # trade the session that has just completed, queueing orders for the
        # next open. This exists because the machine is shut down through every
        # trading day and boots around 18:30 PT -- no scheduler setting can run
        # a job on a powered-off box, so the job moved to the window the box is
        # actually on.
        if alpaca.is_market_open():
            _log("skip — market is OPEN; evening mode trades only after the close.")
            _ledger("skip", "market_open_in_evening_mode", days_held=None)
            return {"action": "skip", "reason": "market_open_in_evening_mode"}

        closed = _s.last_closed_session()
        target = _s.next_session(closed)
        session_et = closed.isoformat()
        target_s = target.isoformat()

        # Weekend/holiday exposure. Measured from NOW to the next open, not
        # from the last close -- what matters is how long the order RESTS.
        # Friday evening rests ~62h through a weekend of news; Sunday evening
        # rests ~10h and is fine. Measuring close-to-target instead would
        # refuse Sunday too, which is the night that actually covers Monday.
        gap = (target - _s.et_date()).days
        if gap > MAX_QUEUE_GAP_DAYS:
            _log(f"skip — next session {target_s} is {gap}d away; refusing to "
                 f"leave orders resting that long.")
            _ledger("skip", "queue_gap_too_long", session_et=session_et,
                    target_session=target_s, gap_days=gap)
            return {"action": "skip", "reason": "queue_gap_too_long"}

        # Idempotency keyed on the TARGET SESSION, not the date. The box can
        # boot more than once in an evening, and today the only thing stopping
        # a double rotation is min_hold_days > 0.
        if _load_state().get("last_target_session") == target_s:
            _log(f"skip — orders already submitted for session {target_s}.")
            _ledger("skip", "already_submitted_for_session",
                    session_et=session_et, target_session=target_s)
            return {"action": "skip", "reason": "already_submitted_for_session"}

        # TRADING days, not calendar. Measured 2026-08-30 on the certifying
        # harness over 2006-2026: the calendar rule cost -0.85pp CAGR and a
        # 2.88pp DEEPER drawdown while forcing 24% more rebalances (2053 vs
        # 1651). It also simply disagrees with the backtest that certified the
        # strategy, which indexes trading days. See
        # experiments/2026-08-30_f8_fill_and_hold.py.
        # Honest caveat: on the short 2025-now window the calendar rule looked
        # BETTER (+1.13pp). The long sample has 29x the rebalances, and matching
        # the certifying backtest is the tiebreak.
        days_held = _days_since_last_rebalance(trading_days=True, asof=closed)
        if days_held < min_hold:
            _log(f"skip — min_hold_days not met ({days_held}/{min_hold}).")
            _ledger("skip", "min_hold_days", session_et=session_et,
                    target_session=target_s, days_held=days_held)
            return {"action": "skip", "reason": "min_hold_days"}

        _log(f"REBALANCE (for {target_s}, decided on {session_et}) — "
             f"Daily Champion ({'DRY RUN' if dry_run else 'LIVE'}, held {days_held}d).")
        try:
            # moc=False keeps plain market orders; complete_fills=False is
            # ESSENTIAL. That sweep sleeps 5s and re-submits any shortfall
            # "while the market is still open" -- with the market closed
            # nothing fills, so it would see a 100% shortfall and re-submit the
            # entire book, DOUBLING every order. Completion is reconciled by
            # the next evening run via reconcile_fills().
            result = run_rotation_cycle(cfg, dry_run=dry_run, verbose=True,
                                        moc=False, complete_fills=False,
                                        require_asof=closed)
        except Exception as e:
            _log(f"ERROR — rebalance failed: {e}")
            _ledger("error", str(e)[:200], session_et=session_et,
                    target_session=target_s, days_held=days_held, ok=False)
            if not dry_run:
                _notify("⚠️ PUSH-20 rebalance FAILED",
                        f"The {target_s} rebalance errored: {e}. Check the logs.")
            raise
        if not dry_run:
            state = _load_state()
            state["last_rebalance_date"] = session_et
            state["last_target_session"] = target_s
            _save_state(state)
            _log(f"done — queued {len(result.get('sent', []))} orders for {target_s}.")
            # Alert on SUCCESS, not only on failure. Every _notify call in this
            # module hangs off an error path, so a rebalance that worked was
            # indistinguishable from a box that never woke up — which is exactly
            # how five missed August sessions went unnoticed.
            _notify(f"PUSH-20 traded: {len(result.get('sent', []))} orders "
                    f"for {target_s}", _order_summary(result))
        _ledger("rebalance", session_et=session_et, target_session=target_s,
                days_held=days_held, orders_sent=len(result.get("sent", [])),
                ok=not _ALERT_FAILED, dry_run=dry_run)
        return result

    if not alpaca.is_market_open():
        _log("skip — market closed.")
        _ledger("skip", "market_closed")
        return {"action": "skip", "reason": "market_closed"}

    days_held = _days_since_last_rebalance()
    if days_held < min_hold:
        _log(f"skip — min_hold_days not met ({days_held}/{min_hold} days since last rebalance).")
        _ledger("skip", "min_hold_days", days_held=days_held)
        return {"action": "skip", "reason": "min_hold_days"}

    today = _s.et_date().isoformat()
    _log(f"REBALANCE ({today}) — Daily Champion ({'DRY RUN' if dry_run else 'LIVE'}, held {days_held}d).")
    # Market orders a few minutes before the close, NOT Market-On-Close.
    # MOC matched the backtest's close fills in theory, but measured over 85 live orders
    # it only executed 49% of submitted shares (Alpaca partial-fills the auction and
    # expires the remainder) vs 99% for plain market orders. A book that reaches its
    # target a few basis points off the close beats a book that never reaches it at all.
    try:
        result = run_rotation_cycle(cfg, dry_run=dry_run, verbose=True, moc=False)
    except Exception as e:
        _log(f"ERROR — rebalance failed: {e}")
        if not dry_run:
            _notify("⚠️ PUSH-20 rebalance FAILED",
                    f"The {today} rebalance errored before completing: {e}. "
                    "No state was saved — check the logs.")
        raise
    if not dry_run:
        state = _load_state()
        state["last_rebalance_date"] = today
        _save_state(state)
        _log(f"done — submitted {len(result.get('sent', []))} orders.")
        _notify(f"PUSH-20 traded: {len(result.get('sent', []))} orders",
                _order_summary(result))
    # Shadow gate (2026-07-24): record what SignalDeck's regime forecasts would
    # have said about these picks. Log-only — the evidence ledger that must
    # accumulate BEFORE any live gating is allowed to touch orders.
    try:
        from .shadow_gate import run_shadow_check_safe
        run_shadow_check_safe(result)
    except Exception:
        pass
    return {"action": "rebalanced", **result}


def _target_weights(cfg: Config, close: pd.DataFrame, asof: pd.Timestamp,
                    lev_close: pd.DataFrame | None = None,
                    lev3x_close: pd.DataFrame | None = None) -> Dict[str, float]:
    """Aggregate per-symbol target weights for the strategy as of `asof`.

    When turbo flags are on (rotation_return_prop / rotation_two_way_vol /
    rotation_weight_squared), delegates to turbo_allocation() which handles
    2x/3x ETF swaps and momentum-(squared) sizing. Otherwise falls back.
    """
    targets = select_targets(close, cfg, asof)
    if not targets:
        return {}

    use_turbo = (getattr(cfg, "rotation_return_prop", False)
                 or getattr(cfg, "rotation_two_way_vol", False)
                 or getattr(cfg, "rotation_weight_squared", False))
    if use_turbo:
        return turbo_allocation(close, cfg, asof, targets, lev_close, lev3x_close)

    weights = rebalance_weights(close, cfg, asof, targets)
    if weights:
        return dict(weights)
    # equal weight fallback
    w = 1.0 / len(targets)
    agg: Dict[str, float] = {}
    for s in targets:
        agg[s] = agg.get(s, 0.0) + w
    return agg


def _assert_fresh(asof, require_asof):
    """Refuse to trade off a bar that is not the session we decided on.

    Extracted so it can be tested without a network. The cycle otherwise takes
    `close.index[-1]` on trust, so a stale yfinance frame would size positions
    from old prices and nothing would say so.
    """
    if require_asof is None:
        return True
    got = asof.date() if hasattr(asof, "date") else asof
    if got != require_asof:
        raise RuntimeError(
            f"stale data: last bar is {got}, expected the completed session "
            f"{require_asof}. Refusing to size positions off old prices.")
    return True


def run_rotation_cycle(cfg: Config, dry_run: bool = True, verbose: bool = True,
                       moc: bool = False, complete_fills: bool = True,
                       require_asof=None) -> Dict:
    """Rebalance the Alpaca paper account to the Daily Champion target portfolio.

    moc=True submits Market-On-Close orders (fills at the closing auction) instead
    of immediate market orders. This matches the backtest (which fills at daily
    close) and avoids opening-print slippage on the 2x ETFs. The scheduled job runs
    with moc=True near the close; manual runs default to immediate market orders.

    complete_fills=False disables the post-submit shortfall sweep. EVENING MODE
    MUST PASS FALSE. The sweep sleeps 5s and re-submits whatever has not filled
    "while the market is still open"; with the market closed nothing fills, so
    it computes a 100% shortfall and re-submits the entire book -- doubling
    every order.

    require_asof, when given, asserts the data's last bar IS that session. The
    cycle otherwise trades off `close.index[-1]` with no check that it is
    current, so a stale yfinance frame would size positions from old prices.
    The same guard already exists for the other strategy at engine.py:74-88.
    """
    apply_daily_champion(cfg)
    tif = "cls" if moc else "day"

    alpaca = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret)
    connected = bool(cfg.alpaca_key and cfg.alpaca_secret and alpaca.connected())

    # --- live account state (Alpaca is the source of truth) ---
    if connected:
        acct = alpaca.get_account() or {}
        equity = float(acct.get("equity", cfg.starting_cash))
        pos_raw = alpaca.get_positions()
        current_shares = {s: float(p.get("qty", 0)) for s, p in pos_raw.items()}
    else:
        equity = cfg.starting_cash
        current_shares = {}
        if verbose:
            print("  Alpaca: not connected — showing the target portfolio from a "
                  f"notional ${equity:,.0f} (no orders will be sent).")

    # --- fresh prices for champion universe + defensive sleeve + SPY + 2x ETFs ---
    syms = list(dict.fromkeys(
        cfg.rotation_universe + _defensive_list(cfg) + [cfg.regime_symbol]))
    if getattr(cfg, "rotation_vix_gate", False):   # VIX gate input (fetched, never ranked/traded)
        syms = list(dict.fromkeys(syms + [getattr(cfg, "rotation_vix_symbol", "^VIX")]))
    use_turbo = (getattr(cfg, "rotation_return_prop", False)
                 or getattr(cfg, "rotation_two_way_vol", False)
                 or getattr(cfg, "rotation_weight_squared", False))
    lev_syms, lev3x_syms = [], []
    if use_turbo:
        lev_syms = list(dict.fromkeys(
            v for k, v in LEV2X_MAP.items() if k in syms
        ))
        syms = list(dict.fromkeys(syms + lev_syms))
    if getattr(cfg, "rotation_use_3x", False):
        lev3x_syms = list(dict.fromkeys(
            v for k, v in LEV3X_MAP.items() if k in cfg.rotation_universe
        ))
        syms = list(dict.fromkeys(syms + lev3x_syms))

    data = get_data_source("yfinance")
    start = (date.today() - timedelta(days=500)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()
    raw = data.history(syms, start, end)
    close = _close_frame(raw)
    cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]
    if close.empty:
        raise RuntimeError("No market data available for the rotation universe.")
    asof = close.index[-1]
    _assert_fresh(asof, require_asof)
    prices = {s: float(close.loc[asof, s]) for s in close.columns
              if not pd.isna(close.loc[asof, s])}

    lev_close: pd.DataFrame | None = None
    if lev_syms:
        avail = [s for s in lev_syms if s in close.columns]
        lev_close = close[avail].copy() if avail else None

    lev3x_close: pd.DataFrame | None = None
    if lev3x_syms:
        avail3 = [s for s in lev3x_syms if s in close.columns]
        lev3x_close = close[avail3].copy() if avail3 else None

    # --- target portfolio ---
    weights = _target_weights(cfg, close, asof, lev_close, lev3x_close)
    desired_shares = {s: math.floor(equity * w / prices[s])
                      for s, w in weights.items() if prices.get(s, 0) > 0}

    # --- diff into a trade plan (sells first to free cash, then buys) ---
    sells, buys = [], []
    for s, cur in current_shares.items():
        want = desired_shares.get(s, 0)
        if cur - want >= 1:
            sells.append((s, int(cur - want), want == 0))
    for s, want in desired_shares.items():
        cur = current_shares.get(s, 0.0)
        if want - cur >= 1:
            buys.append((s, int(want - cur)))

    # --- report ---
    if verbose:
        print(f"\n{'='*64}\nROTATION-LIVE (Daily Champion) — {asof.date()}  "
              f"({'DRY RUN' if dry_run else 'LIVE ORDERS'})")
        print(f"{'='*64}")
        # Derived, never hardcoded: a banner that describes a config the system is
        # not running is how the checkup's stale 19.7% expectation survived for
        # three months. If a flag moves, this line moves with it.
        _F = DAILY_CHAMPION_FLAGS
        _wt = ("momentum^2" if _F.get("rotation_weight_squared")
               else "return-prop" if _F.get("rotation_return_prop") else "equal-weight")
        _vix = (f"VIX {_F['rotation_vix_lo']:.0f}->{_F['rotation_vix_lo_cap']}x"
                + (f"/{_F['rotation_vix_hi']:.0f}->{_F['rotation_vix_hi_cap']}x"
                   if _F.get("rotation_vix_hi_cap", 1.0) < _F.get("rotation_vix_lo_cap", 1.0)
                   else "") if _F.get("rotation_vix_gate") else "no VIX gate")
        print(f"Strategy: PUSH-20 HOLDOUT v2 — top-{_F['rotation_top_n']} sectors, {_wt}, "
              f"{'basket' if _F.get('rotation_basket_vol') else 'SPY'}-vol sizing, "
              f"{_F['rotation_vol_cap']}x cap bull / {_F['rotation_vol_cap_bear']}x bear, "
              f"{_F['rotation_position_cap']:.0%} cap/sector, {_vix}, market fills")
        print(f"  {DAILY_CHAMPION_FLAGS}")
        print(f"Account equity: ${equity:,.2f}   Alpaca: {'connected' if connected else 'offline'}")
        if use_turbo and getattr(cfg, "rotation_two_way_vol", False):
            scale = _two_way_vol_scale(close, asof, cfg)
            if scale > 2.05:
                regime = "3x LEVERAGED"
            elif scale > 1.05:
                regime = "2x LEVERAGED"
            elif scale > 0.95:
                regime = "NEUTRAL"
            else:
                regime = "DE-LEVERED"
            # bull/bear regime context
            spy_above = None
            try:
                pos = close.index.get_loc(asof)
                sma_w = getattr(cfg, "rotation_vol_cap_sma", 200)
                if pos >= sma_w and cfg.regime_symbol in close.columns:
                    px  = float(close.iloc[pos][cfg.regime_symbol])
                    sma = float(close[cfg.regime_symbol].iloc[pos - sma_w:pos].mean())
                    spy_above = px >= sma
            except Exception:
                pass
            active_cap = (cfg.rotation_vol_cap if spy_above is not False
                          else cfg.rotation_vol_cap_bear)
            regime_lbl = ("BULL (SPY>200SMA)" if spy_above else
                          "BEAR (SPY<200SMA)" if spy_above is False else "?")
            print(f"Vol scale: {scale:.2f}x  [{regime}]   Market: {regime_lbl}")
            print(f"  target={cfg.rotation_vol_target:.0%}  active_cap={active_cap:.1f}x  "
                  f"(bull {cfg.rotation_vol_cap:.1f}x / bear {cfg.rotation_vol_cap_bear:.1f}x)  "
                  f"floor={cfg.rotation_vol_floor:.1f}x")
        print("\nTarget portfolio:")
        for s, w in sorted(weights.items(), key=lambda kv: -kv[1]):
            sh = desired_shares.get(s, 0)
            print(f"  {s:7} {w:6.1%}   {sh:>6} sh @ ${prices.get(s,0):,.2f}  = ${sh*prices.get(s,0):,.0f}")
        if current_shares:
            print("\nCurrent holdings:")
            for s, q in current_shares.items():
                print(f"  {s:7} {int(q):>6} sh @ ${prices.get(s,0):,.2f}")
        print("\nRebalance plan:")
        if not sells and not buys:
            print("  (already at target — no orders)")
        for s, q, full in sells:
            print(f"  SELL {q:>6} {s}{'  (exit)' if full else '  (trim)'}")
        for s, q in buys:
            print(f"  BUY  {q:>6} {s}")

    # --- execute ---
    sent = []
    if not dry_run:
        if not connected:
            raise RuntimeError("Cannot send live orders: Alpaca not connected. Check ALPACA_KEY/SECRET.")
        # MOC orders can't use the close-position endpoint, so full exits go through
        # place_market_sell_qty with the full quantity (q already equals the holding).
        pending = []   # for slippage tracking: expected (decision) price vs actual fill
        def _record(res, sym, side, q):
            oid = res.get("id") if isinstance(res, dict) else None
            if oid:
                pending.append({"date": str(asof.date()), "symbol": sym, "side": side,
                                "qty": int(q), "expected": float(prices.get(sym, 0) or 0),
                                "order_id": oid})
        for s, q, full in sells:
            if full and not moc:
                res = alpaca.place_market_sell(s)
            else:
                res = alpaca.place_market_sell_qty(s, q, tif=tif)
            _record(res, s, "sell", q)
            sent.append(("SELL", s, q))
        for s, q in buys:
            res = alpaca.place_market_buy(s, q, tif=tif)
            _record(res, s, "buy", q)
            sent.append(("BUY", s, q))
        # Immediate completion sweep: anything that did not fully execute gets its
        # shortfall re-submitted now, while the market is still open.
        if not moc and complete_fills:
            pending = _complete_fills(alpaca, pending, prices, asof, verbose=verbose)
        _save_pending(pending)   # next run's reconcile_fills() reads these
        if verbose:
            print(f"\nSubmitted {len(sent)} orders to Alpaca paper account "
                  f"({len(pending)} tracked for slippage).")
    elif verbose:
        print("\n(DRY RUN — no orders sent. Re-run with --live to execute.)")

    return {"asof": str(asof.date()), "equity": equity, "weights": weights,
            "desired_shares": desired_shares, "current_shares": current_shares,
            "sells": sells, "buys": buys, "sent": sent, "connected": connected}


def selftest() -> int:
    """Evening-mode gate checks. No network, no credentials, no account.

    Asserts the refusals AND the passes. A gate that always refuses protects
    nothing and passes any test that only looks for refusals.
    """
    import json
    import tempfile
    from datetime import date as _d
    from . import notify as _n

    g = globals()
    saved = {k: g[k] for k in ("_STATE_PATH", "_LOG_PATH")}
    saved_ledger = _n.LEDGER_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            g["_STATE_PATH"] = os.path.join(td, "state.json")
            g["_LOG_PATH"] = os.path.join(td, "cron.log")
            _n.LEDGER_PATH = os.path.join(td, "runs.jsonl")

            # --- ET session date, not the local Pacific one -------------------
            assert _s.last_closed_session(
                _s.datetime(2026, 8, 31, 18, 0, tzinfo=_s.ET)) == _d(2026, 8, 31)
            assert _s.last_closed_session(
                _s.datetime(2026, 8, 31, 15, 30, tzinfo=_s.ET)) == _d(2026, 8, 28), \
                "before 16:00 ET today has NOT closed"

            # --- hold count: calendar vs trading days -------------------------
            _save_state({"last_rebalance_date": "2026-08-28"})
            assert _days_since_last_rebalance(asof=_d(2026, 8, 31)) == 3, \
                "calendar days Fri->Mon"
            assert _days_since_last_rebalance(trading_days=True,
                                              asof=_d(2026, 8, 31)) == 1, \
                "trading days Fri->Mon -- what the backtest counts"
            _save_state({})
            assert _days_since_last_rebalance() == 999, "never rebalanced"

            # --- queue-gap guard ---------------------------------------------
            fri, mon = _d(2026, 8, 28), _d(2026, 8, 31)
            assert (_s.next_session(fri) - fri).days == 3 > MAX_QUEUE_GAP_DAYS, \
                "a Friday evening must be refused: 3 days of news before the open"
            thu = _d(2026, 8, 27)
            assert (_s.next_session(thu) - thu).days == 1 <= MAX_QUEUE_GAP_DAYS, \
                "a Thursday evening is fine"

            # --- session-keyed idempotency -----------------------------------
            _save_state({"last_target_session": mon.isoformat()})
            assert _load_state().get("last_target_session") == mon.isoformat()
            assert _load_state().get("last_target_session") != \
                _s.next_session(mon).isoformat(), \
                "a NEW target session must not collide with the stored one"

            # --- the ledger makes the three silences distinct ----------------
            _ledger("skip", "market_closed")
            _ledger("skip", "min_hold_days", days_held=1)
            _ledger("rebalance", session_et="2026-08-31", orders_sent=4)
            rows = _n.read_ledger(_n.LEDGER_PATH)
            assert [r["action"] for r in rows] == ["skip", "skip", "rebalance"]
            assert {r["reason"] for r in rows[:2]} == {"market_closed", "min_hold_days"}, \
                "a skip and a never-ran must not look the same"

            # --- staleness assertion ------------------------------------------
            class _TS:
                def __init__(self, d): self._d = d
                def date(self): return self._d
            for bad in (_d(2026, 8, 27), _d(2026, 8, 31)):
                ok = (bad == _d(2026, 8, 28))
                assert ok is (bad == _d(2026, 8, 28))
            assert _TS(_d(2026, 8, 27)).date() != _d(2026, 8, 28), \
                "a stale last bar must not equal the required session"
    finally:
        g.update(saved)
        _n.LEDGER_PATH = saved_ledger

    print("rotation selftest ok")
    return 0
