"""Tests for the 2026-07-24 execution-quality fixes.

Context: an audit of 100 live Alpaca orders found Market-On-Close executed only 49% of
submitted shares (partial fill, remainder expired) against 99% for plain market orders,
while the ledger recorded submitted quantity as if it were filled quantity. These tests
pin the four behaviours that were repaired.
"""

import json

import pandas as pd
import pytest

from trader import rotation_live as rl


class StubBroker:
    """Minimal Alpaca stand-in. `plan` maps order id -> (status, filled_qty)."""

    def __init__(self, plan, fill_topups=True):
        self.plan = dict(plan)
        self.fill_topups = fill_topups
        self.submitted = []          # (side, symbol, qty, tif)
        self._n = 0

    def get_order(self, oid):
        if oid not in self.plan:
            return None
        status, filled = self.plan[oid]
        return {"status": status, "filled_qty": str(filled),
                "filled_avg_price": "100.0" if float(filled) > 0 else None}

    def _new(self, side, symbol, qty, tif):
        self._n += 1
        oid = f"topup-{self._n}"
        self.submitted.append((side, symbol, int(qty), tif))
        filled = qty if self.fill_topups else 0
        self.plan[oid] = ("filled" if self.fill_topups else "expired", filled)
        return {"id": oid}

    def place_market_sell_qty(self, symbol, qty, tif="day"):
        return self._new("sell", symbol, qty, tif)

    def place_market_buy(self, symbol, qty, tif="day"):
        return self._new("buy", symbol, qty, tif)

    def connected(self):
        return True


ASOF = pd.Timestamp("2026-07-24")


def _cfg():
    """reconcile_fills only reads the Alpaca credentials off the config."""
    from trader.config import Config
    c = Config()
    c.alpaca_key = "k"
    c.alpaca_secret = "s"
    return c


# ---------------------------------------------------------------------------
# _complete_fills — the top-up sweep
# ---------------------------------------------------------------------------

def test_underfilled_order_is_topped_up_for_the_exact_shortfall():
    # The real 2026-07-23 case: sell 18 DIG, only 11 executed before the order expired.
    pending = [{"date": "2026-07-24", "symbol": "DIG", "side": "sell", "qty": 18,
                "expected": 61.0, "order_id": "o1"}]
    broker = StubBroker({"o1": ("expired", 11)})
    out = rl._complete_fills(broker, pending, {"DIG": 61.0}, ASOF, wait_s=0)

    assert broker.submitted == [("sell", "DIG", 7, "day")]
    assert any(r["order_id"] == "topup-1" and r["qty"] == 7 for r in out)


def test_zero_fill_order_is_topped_up_in_full():
    # sell USD x13 executed nothing and sat pending forever before the fix.
    pending = [{"date": "2026-07-24", "symbol": "USD", "side": "sell", "qty": 13,
                "expected": 90.0, "order_id": "o1"}]
    broker = StubBroker({"o1": ("expired", 0)})
    rl._complete_fills(broker, pending, {"USD": 90.0}, ASOF, wait_s=0)
    assert broker.submitted == [("sell", "USD", 13, "day")]


def test_fully_filled_order_is_left_alone():
    pending = [{"date": "2026-07-24", "symbol": "GLD", "side": "sell", "qty": 1,
                "expected": 371.0, "order_id": "o1"}]
    broker = StubBroker({"o1": ("filled", 1)})
    rl._complete_fills(broker, pending, {"GLD": 371.0}, ASOF, wait_s=0)
    assert broker.submitted == []


def test_buy_shortfall_tops_up_on_the_buy_side():
    pending = [{"date": "2026-07-24", "symbol": "ROM", "side": "buy", "qty": 30,
                "expected": 130.0, "order_id": "o1"}]
    broker = StubBroker({"o1": ("expired", 12)})
    rl._complete_fills(broker, pending, {"ROM": 130.0}, ASOF, wait_s=0)
    assert broker.submitted == [("buy", "ROM", 18, "day")]


def test_still_working_order_is_not_topped_up():
    """A live order that has not reached a terminal status must never be duplicated."""
    pending = [{"date": "2026-07-24", "symbol": "DIG", "side": "sell", "qty": 18,
                "expected": 61.0, "order_id": "o1"}]
    broker = StubBroker({"o1": ("partially_filled", 11)})
    rl._complete_fills(broker, pending, {"DIG": 61.0}, ASOF, wait_s=0, rounds=1)
    assert broker.submitted == []


def test_top_up_never_exceeds_the_original_quantity():
    """Repeated rounds must not stack duplicate orders on the same shortfall."""
    pending = [{"date": "2026-07-24", "symbol": "DIG", "side": "sell", "qty": 10,
                "expected": 61.0, "order_id": "o1"}]
    broker = StubBroker({"o1": ("expired", 4)}, fill_topups=True)
    rl._complete_fills(broker, pending, {"DIG": 61.0}, ASOF, wait_s=0, rounds=3)
    assert sum(q for _, _, q, _ in broker.submitted) == 6


def test_internal_bookkeeping_key_does_not_leak_into_saved_state():
    pending = [{"date": "2026-07-24", "symbol": "DIG", "side": "sell", "qty": 4,
                "expected": 61.0, "order_id": "o1"}]
    broker = StubBroker({"o1": ("filled", 4)})
    out = rl._complete_fills(broker, pending, {"DIG": 61.0}, ASOF, wait_s=0)
    assert all("_topped" not in r for r in out)
    json.dumps(out)   # must stay serialisable for pending_orders.json


# ---------------------------------------------------------------------------
# reconcile_fills — ledger integrity + dead-order cleanup
# ---------------------------------------------------------------------------

def test_reconcile_logs_executed_quantity_not_submitted(tmp_path, monkeypatch):
    """The ledger is the real-money gate ('ledger matches Alpaca'), so a partial fill
    must be recorded at the quantity that actually executed."""
    pend = tmp_path / "pending.json"
    slip = tmp_path / "slippage.jsonl"
    pend.write_text(json.dumps([{"date": "2026-07-23", "symbol": "DIG", "side": "sell",
                                 "qty": 18, "expected": 61.0, "order_id": "o1"}]))
    monkeypatch.setattr(rl, "_PENDING_PATH", str(pend))
    monkeypatch.setattr(rl, "_SLIPPAGE_PATH", str(slip))
    monkeypatch.setattr(rl, "AlpacaBroker",
                        lambda *a, **k: StubBroker({"o1": ("expired", 11)}))

    assert rl.reconcile_fills(_cfg()) == 1
    row = json.loads(slip.read_text().strip())
    assert row["qty"] == 11           # executed
    assert row["submitted_qty"] == 18  # intent, kept for reconciliation
    assert row["status"] == "expired"


def test_reconcile_clears_dead_zero_fill_orders(tmp_path, monkeypatch):
    """An expired 0-fill order has no fill price; before the fix it was skipped and
    re-queried on every run forever."""
    pend = tmp_path / "pending.json"
    slip = tmp_path / "slippage.jsonl"
    pend.write_text(json.dumps([{"date": "2026-07-23", "symbol": "USD", "side": "sell",
                                 "qty": 13, "expected": 90.0, "order_id": "o1"}]))
    monkeypatch.setattr(rl, "_PENDING_PATH", str(pend))
    monkeypatch.setattr(rl, "_SLIPPAGE_PATH", str(slip))
    monkeypatch.setattr(rl, "AlpacaBroker",
                        lambda *a, **k: StubBroker({"o1": ("expired", 0)}))
    monkeypatch.setattr(rl, "_notify", lambda *a, **k: None)

    assert rl.reconcile_fills(_cfg()) == 0
    assert json.loads(pend.read_text()) == []      # dropped, not re-queried forever


def test_reconcile_keeps_orders_that_are_still_working(tmp_path, monkeypatch):
    pend = tmp_path / "pending.json"
    pend.write_text(json.dumps([{"date": "2026-07-24", "symbol": "DIG", "side": "sell",
                                 "qty": 5, "expected": 61.0, "order_id": "o1"}]))
    monkeypatch.setattr(rl, "_PENDING_PATH", str(pend))
    monkeypatch.setattr(rl, "_SLIPPAGE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setattr(rl, "AlpacaBroker",
                        lambda *a, **k: StubBroker({"o1": ("new", 0)}))
    rl.reconcile_fills(_cfg())
    assert len(json.loads(pend.read_text())) == 1


def test_zero_fill_raises_an_alert(tmp_path, monkeypatch):
    """Silence was the original failure: orders executed nothing and nobody was told."""
    pend = tmp_path / "pending.json"
    pend.write_text(json.dumps([{"date": "2026-07-23", "symbol": "USD", "side": "sell",
                                 "qty": 13, "expected": 90.0, "order_id": "o1"}]))
    monkeypatch.setattr(rl, "_PENDING_PATH", str(pend))
    monkeypatch.setattr(rl, "_SLIPPAGE_PATH", str(tmp_path / "s.jsonl"))
    monkeypatch.setattr(rl, "AlpacaBroker",
                        lambda *a, **k: StubBroker({"o1": ("expired", 0)}))
    fired = []
    monkeypatch.setattr(rl, "_notify", lambda t, m: fired.append((t, m)))
    rl.reconcile_fills(_cfg())
    assert fired and "did NOT fill" in fired[0][0]


# ---------------------------------------------------------------------------
# The disconnect alarm must alert, not crash
# ---------------------------------------------------------------------------

def test_disconnect_path_returns_cleanly(monkeypatch):
    """A redundant `from datetime import date` inside run_scheduled() made `date`
    function-local, so the disconnect branch raised UnboundLocalError — the one alarm
    that fires when the system stops trading died instead of firing."""
    from trader.config import Config
    monkeypatch.setattr(rl, "AlpacaBroker", lambda *a, **k: StubBroker({}))
    monkeypatch.setattr(rl, "_notify", lambda *a, **k: None)
    cfg = Config()
    cfg.alpaca_key = ""
    cfg.alpaca_secret = ""
    assert rl.run_scheduled(cfg, dry_run=True) == {"action": "skip",
                                                   "reason": "not_connected"}
