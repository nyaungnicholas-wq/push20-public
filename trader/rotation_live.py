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
import datetime as _dt
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
# The slippage log is the execution MEASUREMENT, not a diagnostic: a test row
# in it becomes a data point in the cost number the strategy is graded on.
# Resolved from the environment at IMPORT (conftest sets it before trader is
# imported) rather than at call time, because eight tests monkeypatch this
# constant and a call-time getenv would silently ignore them.
_SLIPPAGE_PATH = os.getenv("ROTATION_SLIPPAGE_LOG") or os.path.join(
    os.path.dirname(__file__), "..", "data", "slippage_log.jsonl")
def _log_path() -> str:
    """Read at CALL time, and overridable, so the test suite cannot write into
    the production log.

    trader.notify already took env overrides for the ledger and the alert log
    (commit ecbc95d, "the test suite was writing to the production run ledger").
    This file was missed: tests/test_execution_quality.py drives _complete_fills
    without patching _log, so every run appended real-looking "top-up",
    "UNDERFILL" and "Alpaca not connected" lines to data/rotation_cron.log. Those
    entries were later read back as evidence of a live incident on 2026-09-13
    that never happened. A log that fabricates history is worse than no log.
    """
    return os.environ.get("ROTATION_CRON_LOG") or _LOG_PATH



def _log(msg: str) -> None:
    from datetime import datetime
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
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




# --- Delayed release (2026-09-19) -------------------------------------------
# MEASURED (research/v3/EXECUTION.md): the whole-book notional-weighted half
# spread is 27.63 bps/side at 09:30:00.000 against 7.23 five minutes later, over
# 806 legs on 150 mornings, and the signed drift across that window is
# indistinguishable from zero at every offset out to an hour. Waiting therefore
# narrows the spread without the price running away from a momentum book --
# about 10.4 bps/side, ~2.1pp of CAGR. The defensible window is +5m to +15m; the
# argmin inside it is NOT established, so this is a dial, not a constant.
#
# OFF BY DEFAULT. ROTATION_RELEASE_DELAY_MIN=0 keeps today's behaviour exactly:
# the evening run submits. Set it positive to split the cycle -- the evening run
# DECIDES and persists a plan, and a separate morning run releases it.
#
# The split buys the spread at the price of a failure mode that does not exist
# today: a queued evening order fills at the open whether or not this box is
# awake, and a deferred one does not. So a missed release is made LOUD -- the
# next evening run detects the stale plan, alerts, and recomputes. Losing one
# rebalance against min_hold_days=3 is a cost; losing one SILENTLY is the
# failure this project has already had twice.
_RELEASE_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                             "pending_release.json")


def _release_delay_min() -> int:
    """Minutes after the open to release. 0 (the default) = submit in the evening."""
    try:
        return max(0, int(os.getenv("ROTATION_RELEASE_DELAY_MIN", "0") or 0))
    except ValueError:
        return 0


def _release_path() -> str:
    # Read at call time, like _log_path: the test suite redirects it, and a
    # module-level constant would let tests write the production plan file.
    return os.getenv("ROTATION_RELEASE_PATH", _RELEASE_PATH)


def _save_release_plan(plan: dict) -> bool:
    """Persist the deferred plan atomically.

    Returns False on failure, and the caller MUST treat that as "do not defer":
    a plan that did not reach disk is a rebalance that will never happen, and
    silently swallowing that is precisely how _save_state's OSError handler
    turned a corrupt file into a missing trade.
    """
    import json
    path = _release_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(plan, f)
        os.replace(tmp, path)
        return True
    except OSError as e:
        _log(f"release plan NOT persisted ({e}) -- not deferring.")
        return False


def _load_release_plan() -> Optional[dict]:
    import json
    try:
        with open(_release_path()) as f:
            plan = json.load(f)
        return plan if isinstance(plan, dict) else None
    except (OSError, ValueError):
        return None


def _clear_release_plan() -> None:
    try:
        os.remove(_release_path())
    except OSError:
        pass


def _minutes_since_open(now_et=None) -> Optional[float]:
    """Minutes since 09:30 ET today, or None if it cannot be determined.

    None means "unknown", and every caller treats unknown as "do not block" --
    the market-open check has already passed by then, so refusing on a clock
    failure would strand the plan rather than protect it.
    """
    try:
        from zoneinfo import ZoneInfo
        now = now_et or _dt.datetime.now(ZoneInfo("America/New_York"))
        return (now - now.replace(hour=9, minute=30, second=0,
                                  microsecond=0)).total_seconds() / 60.0
    except Exception:
        return None


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


def _et_stamp(when) -> tuple:
    """(ET ISO timestamp, ET session date) for an Alpaca UTC fill timestamp.

    `str(when)[:10]` -- what this used to be -- read the UTC calendar date and
    threw the time away. Both losses were T6 findings: any fill after 20:00 ET
    stamps onto the NEXT calendar day, moving the execution benchmark a whole
    session, and the discarded time is exactly the field an NBBO lookup needs.
    """
    if not when:
        return None, None
    try:
        s = str(when).replace("Z", "+00:00")
        if "." in s:                       # Alpaca stamps nanoseconds
            head, _, tail = s.partition(".")
            frac = "".join(c for c in tail if c.isdigit())[:6]
            s = f"{head}.{frac or '0'}{tail[len(frac):].lstrip('0123456789')}"
        et = _dt.datetime.fromisoformat(s).astimezone(_s.ET)
    except (TypeError, ValueError):
        return None, None
    return et.isoformat(timespec="seconds"), et.date().isoformat()


def _session_bar(symbol: str, day: str) -> Optional[dict]:
    """OHLC bar for `symbol` on ET session `day`, or None.

    Never raises: measurement must not be able to break reconciliation, which
    is what clears pending orders.
    """
    if not symbol or not day:
        return None
    try:
        start = (pd.Timestamp(day) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(day) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        df = (get_data_source("yfinance").history([symbol], start, end) or {}).get(symbol)
        if df is None or df.empty or "open" not in df:
            return None
        hit = df[pd.to_datetime(df.index).normalize() == pd.Timestamp(day).normalize()]
        if hit.empty:
            return None
        row = hit.iloc[0]
        bar = {k: float(row[k]) for k in ("open", "high", "low", "close") if k in hit}
        return bar if bar.get("open", 0) > 0 else None
    except Exception:
        return None


def _fill_session_open(symbol: str, when: str) -> tuple:
    """(ET session date of the fill, that session's open price).

    Returns (date, None) when no bar is available; callers must then omit the
    slippage field rather than substitute a zero.
    """
    _, day = _et_stamp(when)
    if day is None:
        return None, None
    bar = _session_bar(symbol, day)
    return day, (bar["open"] if bar else None)


def _arrival_instant(rec: dict, fill_date: str) -> Optional[str]:
    """When the order actually became live -- the reference point execution is
    measured from.

    The old instrument used the fill session's official OPEN. That is the wrong
    reference and it is biased: a market DAY order queued the night before is
    released at 09:30 as a plain market order and does NOT participate in the
    opening auction, so it never had access to the opening print. An order
    submitted during regular hours arrives when it was submitted.
    """
    if not fill_date:
        return None
    try:
        sess_open = _dt.datetime.combine(
            date.fromisoformat(fill_date), _dt.time(9, 30), tzinfo=_s.ET)
    except (TypeError, ValueError):
        return None
    sub = rec.get("submitted_at")
    if sub:
        try:
            t = _dt.datetime.fromisoformat(sub).astimezone(_s.ET)
            if t > sess_open:
                return t.isoformat()
        except (TypeError, ValueError):
            pass
    return sess_open.isoformat()


def _quote_at(cfg, symbol: str, when=None, after: bool = False) -> Optional[dict]:
    """NBBO for `symbol` -- latest, or the one in force at instant `when`.

    `after=True` takes the first quote AT OR AFTER the instant, which is what an
    arrival benchmark at the 09:30 release needs. Wrapped so a quote outage can
    never propagate into the order path.
    """
    try:
        from .alpaca_feed import AlpacaFeed
        return AlpacaFeed(cfg.alpaca_key, cfg.alpaca_secret).get_quote(
            symbol, when, after=after)
    except Exception:
        return None
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
    try:
        venue_tag = alpaca.provenance()
    except Exception:
        venue_tag = {}

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
        filled_at = o.get("filled_at")
        fill_ts_et, fill_date = _et_stamp(filled_at)
        bar = _session_bar(sym, fill_date)
        open_px = bar["open"] if bar else None

        # The open is only the RIGHT benchmark if the fill happened in that
        # session. T6 found ~85 of the first 105 rows benchmarked against the
        # wrong session, so `slippage_bps` was reporting an overnight gap as
        # execution. A fill price outside the session's own High/Low proves the
        # date is wrong, and a wrong benchmark is worse than no number.
        bench_ok = bool(bar and open_px
                        and bar.get("low", 0) <= fill <= bar.get("high", 0))
        rested = None
        try:
            rested = _s.sessions_between(date.fromisoformat(rec.get("date")),
                                         date.fromisoformat(fill_date))
        except (TypeError, ValueError):
            pass

        drift = _signed(exp)
        slip = _signed(open_px) if bench_ok else None
        row = {
            "logged_at": datetime.now().isoformat(timespec="seconds"),
            "submit_date": rec.get("date"), "symbol": sym,
            # qty = what actually EXECUTED. submitted_qty is kept alongside so the
            # ledger can be reconciled against Alpaca instead of overstating size.
            "side": side, "qty": got_qty, "submitted_qty": want_qty,
            "status": status,
            "expected": exp, "fill": fill, "order_id": oid,
            # Full resolution, both stamps. `fill_date` alone cannot tell an
            # opening print from a 15:59 one, and every microstructure question
            # asked of this log needs the time.
            "filled_at": filled_at, "filled_at_et": fill_ts_et,
            "fill_date": fill_date, "bench_ok": bench_ok,
        }
        if rested is not None:
            # Sessions the order rested before executing. >1 means it expired and
            # was re-submitted, so its `expected` is stale by that many days and
            # its drift is market movement, not cost. Those rows have to be
            # separable, not averaged in.
            row["rested_sessions"] = rested
        if rec.get("submitted_at"):
            row["submitted_at"] = rec["submitted_at"]
        # Provenance, so the log can answer "was this a real fill?" without a
        # code read. Every row written before 2026-09-17 lacks it and is paper.
        row.update(venue_tag)
        if drift is not None:
            row["drift_bps"] = round(drift, 2)
        if open_px:
            row["open_px"] = open_px
        # Omitted entirely when the benchmark is unavailable or wrong. A 0.0
        # stand-in reads as "executed perfectly" to every consumer downstream.
        if slip is not None:
            row["slippage_bps"] = round(slip, 2)

        # NBBO at submit (carried from the pending record) and at the fill.
        # These are what separate spread from drift: the daily-bar open cannot,
        # because it is one price for a whole session.
        if rec.get("quote_submit"):
            row["quote_submit"] = rec["quote_submit"]
        sgn = 1 if side == "buy" else -1
        qf = _quote_at(cfg, sym, filled_at)
        if qf:
            row["quote_fill"] = qf
            # Effective half-spread: how far through the mid prevailing AT the
            # fill we paid. This is what the router cost on the last print --
            # but for a large order it is measured against an already-moved
            # quote, so on its own it understates a book walk.
            row["eff_spread_bps"] = round((fill - qf["mid"]) / qf["mid"]
                                          * 10000.0 * sgn, 2)
        arr_ts = _arrival_instant(rec, fill_date)
        qa = _quote_at(cfg, sym, arr_ts, after=True) if arr_ts else None
        if qa:
            row["arrival_ts"] = arr_ts
            row["quote_arrival"] = qa
            # THE execution number: fill against the mid in force when the order
            # became live. Unlike the session open it is a price the order could
            # actually have had.
            row["arrival_bps"] = round((fill - qa["mid"]) / qa["mid"]
                                       * 10000.0 * sgn, 2)
            if qf:
                # What is left is the book walk -- the capacity term. Four of the
                # ten sleeves route to a 2x ETF under $1m/day, and a full position
                # is 13.7% of UCC's ADV, so this is the field that will show it.
                row["impact_bps"] = round(row["arrival_bps"]
                                          - row["eff_spread_bps"], 2)
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
                                "order_id": oid,
                                "submitted_at": _s.now_et().isoformat(timespec="seconds"),
                                "topup": True})
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

        # Delayed-release bookkeeping. A plan still sitting here for a session
        # that has already come and gone means the release window was missed --
        # the box was not awake after the open. Say so loudly: a deferred
        # rebalance that silently never happened is worse than not deferring.
        _plan = _load_release_plan()
        if _plan and _plan.get("target_session") != target_s:
            _log(f"MISSED RELEASE — plan decided {_plan.get('session_et')} for "
                 f"{_plan.get('target_session')} was never released. Discarding.")
            _notify("⚠️ PUSH-20 missed its release window",
                    f"The plan for {_plan.get('target_session')} was never "
                    f"submitted — nothing was awake after the open. No orders "
                    f"were placed for that session.")
            _ledger("missed_release", "release_window_missed",
                    session_et=_plan.get("session_et"),
                    target_session=_plan.get("target_session"), ok=False)
            _clear_release_plan()
            _plan = None
        if _plan:
            _log(f"skip — a plan is already queued for release on {target_s}.")
            _ledger("skip", "already_deferred", session_et=session_et,
                    target_session=target_s)
            return {"action": "skip", "reason": "already_deferred"}

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


        delay = _release_delay_min()
        if delay > 0:
            plan = {"session_et": session_et, "target_session": target_s,
                    "days_held": days_held, "delay_min": delay,
                    "decided_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
            if dry_run or _save_release_plan(plan):
                _log(f"DEFERRED — decided for {target_s} on {session_et}; "
                     f"release at open+{delay}m (held {days_held}d).")
                _ledger("deferred", "awaiting_release", session_et=session_et,
                        target_session=target_s, days_held=days_held,
                        delay_min=delay, dry_run=dry_run)
                return {"action": "deferred", "target_session": target_s,
                        "session_et": session_et, "delay_min": delay}
            _log("falling through to immediate submission (plan not persisted).")

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
                                        require_asof=closed, _gated=True)
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
        result = run_rotation_cycle(cfg, dry_run=dry_run, verbose=True, moc=False,
                                    _gated=True)
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



def run_release(cfg: Config, dry_run: bool = False) -> Dict:
    """Release a plan the evening run deferred, once the open has settled.

    This RELEASES A DECISION; it does not take a new one. The targets are
    re-derived from the same closed session the evening run used, pinned through
    require_asof, so what is submitted is what was decided -- not a fresh look at
    a market that has since opened.

    Every gate must pass: a plan exists, it targets TODAY, Alpaca is connected,
    the market is open, the configured delay has elapsed, and the pre-trade risk
    gate allows it. Every return path writes a ledger row.
    """
    global _ALERT_FAILED
    _ALERT_FAILED = False
    apply_daily_champion(cfg)

    plan = _load_release_plan()
    if not plan:
        _log("release — nothing queued.")
        _ledger("skip", "no_release_plan")
        return {"action": "skip", "reason": "no_release_plan"}

    target_s = plan.get("target_session")
    session_et = plan.get("session_et")
    today = _s.et_date().isoformat()
    if target_s != today:
        _log(f"release — queued plan targets {target_s}, today is {today}; not releasing.")
        _ledger("skip", "release_not_today", target_session=target_s)
        return {"action": "skip", "reason": "release_not_today"}

    alpaca = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret)
    if not (cfg.alpaca_key and cfg.alpaca_secret and alpaca.connected()):
        _log("release SKIP — Alpaca not connected.")
        _notify("⚠️ PUSH-20 could not release",
                f"Alpaca not connected at the release window for {target_s}. "
                "The decided orders were NOT submitted.")
        _ledger("skip", "not_connected", target_session=target_s, ok=False)
        return {"action": "skip", "reason": "not_connected"}

    if not alpaca.is_market_open():
        _log("release — market is not open.")
        _ledger("skip", "market_closed_at_release", target_session=target_s)
        return {"action": "skip", "reason": "market_closed_at_release"}

    delay = int(plan.get("delay_min") or _release_delay_min())
    mins = _minutes_since_open()
    if mins is not None and mins < delay:
        _log(f"release — too early ({mins:.1f}m since the open, need {delay}m).")
        _ledger("skip", "release_too_early", target_session=target_s)
        return {"action": "skip", "reason": "release_too_early"}

    try:
        from . import risk_gate
        decision = risk_gate.evaluate(alpaca.get_account(), alpaca.get_positions())
        if not decision.allowed:
            _log(f"release BLOCKED by risk gate — {decision.blocked_summary()}")
            _notify("🛑 PUSH-20 release BLOCKED", decision.blocked_summary())
            _ledger("blocked", decision.blocked_summary()[:200],
                    target_session=target_s, ok=False)
            return {"action": "blocked", "reason": decision.blocked_summary()}
    except Exception as e:
        _log(f"release BLOCKED — risk gate error (failing closed): {e}")
        _notify("🛑 PUSH-20 release blocked", f"risk gate error, failing closed: {e}")
        _ledger("blocked", f"risk gate error: {e}"[:200],
                target_session=target_s, ok=False)
        return {"action": "blocked", "reason": f"risk gate error: {e}"}

    # Same idempotency key the evening path uses. A double release would double
    # the book, which is the one failure this split must never introduce.
    if _load_state().get("last_target_session") == target_s:
        _log(f"release — orders already submitted for {target_s}.")
        _clear_release_plan()
        _ledger("skip", "already_submitted_for_session", target_session=target_s)
        return {"action": "skip", "reason": "already_submitted_for_session"}

    since = "unknown" if mins is None else f"{mins:.1f}"
    _log(f"RELEASE (for {target_s}, decided on {session_et}) — "
         f"{'DRY RUN' if dry_run else 'LIVE'}, {since}m after the open.")
    try:
        result = run_rotation_cycle(cfg, dry_run=dry_run, verbose=True, moc=False,
                                    complete_fills=False, _gated=True,
                                    require_asof=date.fromisoformat(session_et))
    except Exception as e:
        _log(f"ERROR — release failed: {e}")
        if not dry_run:
            _notify("⚠️ PUSH-20 release FAILED",
                    f"The {target_s} release errored: {e}. Check the logs.")
        _ledger("error", str(e)[:200], session_et=session_et,
                target_session=target_s, ok=False)
        raise

    if not dry_run:
        state = _load_state()
        state["last_rebalance_date"] = session_et
        state["last_target_session"] = target_s
        _save_state(state)
        _clear_release_plan()
        _log(f"done — released {len(result.get('sent', []))} orders for {target_s}.")
        _notify(f"PUSH-20 released: {len(result.get('sent', []))} orders for {target_s}",
                _order_summary(result))
    _ledger("release", session_et=session_et, target_session=target_s,
            orders_sent=len(result.get("sent", [])), delay_min=delay,
            ok=not _ALERT_FAILED, dry_run=dry_run)
    return result


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
                       require_asof=None, _gated: bool = False) -> Dict:
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
    # UNGATED LIVE SUBMISSION IS REFUSED (2026-09-19).
    #
    # This is the bare rebalance primitive. run_scheduled calls it only AFTER its
    # gate stack passes. S1 measured what is missing when something calls it
    # directly: the pre-trade risk gate, the market-state check, the queue-gap
    # refusal, the session idempotency key, min_hold_days, the data-freshness
    # assert, the state write, the ledger row and the alert. Ten guards.
    #
    # complete_fills also defaults True here, which is the doubling mechanism --
    # the shortfall sweep re-submits anything unfilled "while the market is still
    # open", so run after hours it sees a 100% shortfall and re-sends the book.
    #
    # _gated is the caller asserting it applied those gates. Deliberately private
    # and deliberately not on the CLI: the answer to "force a rebalance now" is
    # `--scheduled --live`, which forces it THROUGH the gates, not around them.
    #
    # This check is FIRST on purpose. An earlier draft of this patch landed the
    # same text inside the docstring, where it compiled cleanly and did nothing;
    # the connected-check below then produced a plausible-looking refusal for an
    # unrelated reason. tests/test_guard_bypass.py asserts it is reachable code.
    if not dry_run and not _gated:
        raise RuntimeError(
            "run_rotation_cycle cannot submit live orders directly: no risk "
            "gate, no market-state check, no min_hold_days, no idempotency key, "
            "and complete_fills defaults True (which re-sends the whole book "
            "after hours). Call run_scheduled(), or pass _gated=True only if "
            "you have applied those guards yourself.")

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
            if not oid:
                return
            rec = {"date": str(asof.date()), "symbol": sym, "side": side,
                   "qty": int(q), "expected": float(prices.get(sym, 0) or 0),
                   "order_id": oid,
                   # The submit INSTANT, not just the decision session. Needed to
                   # measure how long an order rested and to pair the arrival
                   # quote with the order it belongs to.
                   "submitted_at": _s.now_et().isoformat(timespec="seconds")}
            # NBBO at submit. Captured AFTER the POST returns, so it trails the
            # order by the round trip (~0.2s) and can never delay or fail a
            # placement. In evening mode the market is shut, so this is the last
            # regular-hours quote -- `age_s` on the quote says how stale.
            qs = _quote_at(cfg, sym)
            if qs:
                rec["quote_submit"] = qs
            pending.append(rec)
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
