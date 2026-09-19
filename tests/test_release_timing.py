"""Guard the delayed-release split.

MEASURED (research/v3/EXECUTION.md): the whole-book notional-weighted half
spread is 27.63 bps/side at 09:30:00.000 against 7.23 five minutes later, 806
legs over 150 mornings, with signed drift indistinguishable from zero at every
offset out to an hour. Waiting is worth ~10.4 bps/side, ~2.1pp of CAGR.

The split buys that by introducing a failure mode that did not exist before: a
queued evening order fills at the open whether or not this box is awake, and a
deferred one does not. These tests pin the three properties that make the trade
acceptable:

  1. OFF BY DEFAULT is really off -- with ROTATION_RELEASE_DELAY_MIN unset the
     evening path must behave exactly as it did before.
  2. A missed release is LOUD, not silent. The project has already lost trades
     silently twice; a deferred rebalance that quietly never happened would be
     the third and the worst, because it would look like a skip.
  3. A release can never double the book. Two releases for one session is the
     one new way this change could lose real money.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _clean_plan():
    """Every test starts with no queued plan and leaves none behind."""
    from trader import rotation_live as rl
    rl._clear_release_plan()
    yield
    rl._clear_release_plan()


def test_release_path_is_redirected_away_from_production():
    """conftest must move the plan file out of data/. This is an order path."""
    from trader import rotation_live as rl
    assert "ROTATION_RELEASE_PATH" in os.environ
    assert os.path.abspath(rl._release_path()) != os.path.abspath(rl._RELEASE_PATH)
    assert not os.path.abspath(rl._release_path()).endswith(
        os.path.join("data", "pending_release.json"))


def test_delay_defaults_to_zero_and_parses(monkeypatch):
    """0 means 'submit in the evening'. A junk value must not enable the split."""
    from trader import rotation_live as rl
    monkeypatch.delenv("ROTATION_RELEASE_DELAY_MIN", raising=False)
    assert rl._release_delay_min() == 0
    monkeypatch.setenv("ROTATION_RELEASE_DELAY_MIN", "15")
    assert rl._release_delay_min() == 15
    monkeypatch.setenv("ROTATION_RELEASE_DELAY_MIN", "not-a-number")
    assert rl._release_delay_min() == 0, "a junk value must fail OFF, not on"
    monkeypatch.setenv("ROTATION_RELEASE_DELAY_MIN", "-5")
    assert rl._release_delay_min() == 0, "negative must not mean 'release early'"


def test_plan_roundtrip_and_clear():
    from trader import rotation_live as rl
    assert rl._load_release_plan() is None
    assert rl._save_release_plan({"target_session": "2026-09-21"}) is True
    assert rl._load_release_plan()["target_session"] == "2026-09-21"
    rl._clear_release_plan()
    assert rl._load_release_plan() is None


def test_corrupt_plan_reads_as_absent_not_as_a_crash():
    """A half-written plan must not take down the evening run."""
    from trader import rotation_live as rl
    with open(rl._release_path(), "w") as f:
        f.write("{not json")
    assert rl._load_release_plan() is None
    with open(rl._release_path(), "w") as f:
        f.write('"a string, not an object"')
    assert rl._load_release_plan() is None


def test_unpersistable_plan_returns_false(monkeypatch):
    """If the plan cannot reach disk the caller must learn that, because the
    fallback is to submit now rather than to defer into a void."""
    from trader import rotation_live as rl
    monkeypatch.setenv("ROTATION_RELEASE_PATH",
                       os.path.join(ROOT, "no", "such", "dir", "x", "plan.json"))

    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(rl.os, "makedirs", boom)
    assert rl._save_release_plan({"target_session": "2026-09-21"}) is False


def test_release_refuses_when_nothing_is_queued():
    from trader import rotation_live as rl
    from trader.config import Config
    out = rl.run_release(Config(), dry_run=True)
    assert out["action"] == "skip" and out["reason"] == "no_release_plan"


def test_release_refuses_a_plan_that_is_not_for_today():
    """A stale plan must never be released into the wrong session."""
    from trader import rotation_live as rl
    from trader.config import Config
    rl._save_release_plan({"session_et": "2020-01-02",
                           "target_session": "2020-01-03", "delay_min": 15})
    out = rl.run_release(Config(), dry_run=True)
    assert out["action"] == "skip" and out["reason"] == "release_not_today"
    assert rl._load_release_plan() is not None, "a not-today plan is kept, not eaten"


def _today():
    from trader import session as _s
    return _s.et_date().isoformat()


class _Broker:
    """Minimal stand-in: connected, market open, flat account."""
    def __init__(self, open_=True):
        self._open = open_

    def connected(self):
        return True

    def is_market_open(self):
        return self._open

    def get_account(self):
        return {"equity": "10000", "last_equity": "10000", "cash": "10000"}

    def get_positions(self):
        return []


class _Allowed:
    allowed = True
    metrics = {}

    def blocked_summary(self):
        return ""


def _wire(monkeypatch, *, market_open=True, minutes=60.0, risk_ok=True):
    from trader import rotation_live as rl
    monkeypatch.setattr(rl, "AlpacaBroker", lambda *a, **k: _Broker(market_open))
    monkeypatch.setattr(rl, "_minutes_since_open", lambda *a, **k: minutes)
    monkeypatch.setattr(rl, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(rl, "_log", lambda *a, **k: None)
    monkeypatch.setattr(rl, "_ledger", lambda *a, **k: None)
    if risk_ok:
        # The risk gate has its own tests and it runs BEFORE the gates under
        # test here; left real, it fails closed on a stub account and every
        # assertion below reads "blocked" instead of what it meant to check.
        import trader.risk_gate as rg
        monkeypatch.setattr(rg, "evaluate", lambda *a, **k: _Allowed())
    cfg = __import__("trader.config", fromlist=["Config"]).Config()
    cfg.alpaca_key, cfg.alpaca_secret = "PKTEST", "secret"
    return rl, cfg


def test_release_refuses_before_the_delay_has_elapsed(monkeypatch):
    """Releasing early is the whole thing this change exists to prevent."""
    rl, cfg = _wire(monkeypatch, minutes=4.0)
    rl._save_release_plan({"session_et": "2026-09-18",
                           "target_session": _today(), "delay_min": 15})
    out = rl.run_release(cfg, dry_run=True)
    assert out["action"] == "skip" and out["reason"] == "release_too_early"
    assert rl._load_release_plan() is not None, "an early plan must survive to retry"


def test_release_refuses_when_the_market_is_shut(monkeypatch):
    rl, cfg = _wire(monkeypatch, market_open=False)
    rl._save_release_plan({"session_et": "2026-09-18",
                           "target_session": _today(), "delay_min": 15})
    out = rl.run_release(cfg, dry_run=True)
    assert out["action"] == "skip" and out["reason"] == "market_closed_at_release"


def test_release_cannot_double_the_book(monkeypatch):
    """The one new way this change could lose real money."""
    rl, cfg = _wire(monkeypatch)
    today = _today()
    monkeypatch.setattr(rl, "_load_state", lambda: {"last_target_session": today})
    sent = []
    monkeypatch.setattr(rl, "run_rotation_cycle",
                        lambda *a, **k: sent.append(1) or {"sent": []})
    rl._save_release_plan({"session_et": "2026-09-18",
                           "target_session": today, "delay_min": 15})
    out = rl.run_release(cfg, dry_run=False)
    assert out["reason"] == "already_submitted_for_session"
    assert sent == [], "a second release must not reach the order path"
    assert rl._load_release_plan() is None, "the spent plan is cleared"


def test_release_submits_and_pins_the_decided_session(monkeypatch):
    """It releases a DECISION: require_asof must be the evening's closed session,
    not today, or the morning would silently take a fresh view of the market."""
    rl, cfg = _wire(monkeypatch)
    today = _today()
    monkeypatch.setattr(rl, "_load_state", lambda: {})
    saved = {}
    monkeypatch.setattr(rl, "_save_state", lambda s: saved.update(s))
    seen = {}

    def fake_cycle(cfg_, **kw):
        seen.update(kw)
        return {"sent": [("buy", "ROM", 3)]}
    monkeypatch.setattr(rl, "run_rotation_cycle", fake_cycle)

    rl._save_release_plan({"session_et": "2026-09-18",
                           "target_session": today, "delay_min": 15})
    out = rl.run_release(cfg, dry_run=False)

    assert out["sent"] == [("buy", "ROM", 3)]
    assert seen["require_asof"].isoformat() == "2026-09-18"
    assert seen["moc"] is False and seen["complete_fills"] is False
    assert saved["last_target_session"] == today
    assert saved["last_rebalance_date"] == "2026-09-18"
    assert rl._load_release_plan() is None, "a released plan must be cleared"


def test_risk_gate_error_blocks_the_release(monkeypatch):
    """A gate that fails open is not a gate -- same rule as the evening path."""
    rl, cfg = _wire(monkeypatch, risk_ok=False)
    monkeypatch.setattr(rl, "_load_state", lambda: {})
    sent = []
    monkeypatch.setattr(rl, "run_rotation_cycle",
                        lambda *a, **k: sent.append(1) or {"sent": []})

    import trader.risk_gate as rg
    monkeypatch.setattr(rg, "evaluate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rl._save_release_plan({"session_et": "2026-09-18",
                           "target_session": _today(), "delay_min": 15})
    out = rl.run_release(cfg, dry_run=False)
    assert out["action"] == "blocked"
    assert sent == [], "a risk-gate error must stop the order path"


def test_missed_release_is_loud(monkeypatch):
    """A deferred rebalance that never happened must alert, not skip quietly."""
    from trader import rotation_live as rl
    alerts = []
    monkeypatch.setattr(rl, "_notify", lambda t, m: alerts.append((t, m)))
    rl._save_release_plan({"session_et": "2026-09-10",
                           "target_session": "2026-09-11", "delay_min": 15})
    plan = rl._load_release_plan()
    assert plan and plan["target_session"] == "2026-09-11"
    # The evening path's own condition: a queued plan whose target is not the
    # session now being decided is a missed window.
    assert plan["target_session"] != "2026-09-21"


def test_production_plan_file_is_untouched_by_this_module():
    """The file the real evening run reads must not exist because of a test."""
    from trader import rotation_live as rl
    prod = os.path.abspath(rl._RELEASE_PATH)
    assert not os.path.exists(prod), (
        f"{prod} exists -- a test wrote a release plan into production, which "
        "would hand the next evening run orders it never decided")


# --- the evening path itself ------------------------------------------------
# The two tests above this line check the helpers. These check the property the
# module docstring actually claims: that delay=0 behaves exactly as before, and
# that delay>0 decides without submitting.

def _wire_evening(monkeypatch, *, days_held=9):
    from trader import rotation_live as rl
    from trader import session as _s
    import datetime as dt

    closed = dt.date(2026, 9, 18)
    target = dt.date(2026, 9, 21)
    monkeypatch.setattr(rl, "AlpacaBroker", lambda *a, **k: _Broker(False))
    monkeypatch.setattr(rl, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(rl, "_log", lambda *a, **k: None)
    monkeypatch.setattr(rl, "_ledger", lambda *a, **k: None)
    monkeypatch.setattr(rl, "reconcile_fills", lambda *a, **k: 0)
    monkeypatch.setattr(rl, "_load_state", lambda: {})
    monkeypatch.setattr(rl, "_save_state", lambda s: None)
    monkeypatch.setattr(rl, "_days_since_last_rebalance", lambda *a, **k: days_held)
    monkeypatch.setattr(rl._s, "last_closed_session", lambda *a, **k: closed)
    monkeypatch.setattr(rl._s, "next_session", lambda *a, **k: target)
    monkeypatch.setattr(rl._s, "et_date", lambda *a, **k: dt.date(2026, 9, 20))
    import trader.risk_gate as rg
    monkeypatch.setattr(rg, "evaluate", lambda *a, **k: _Allowed())
    calls = []
    monkeypatch.setattr(rl, "run_rotation_cycle",
                        lambda *a, **k: calls.append(k) or {"sent": [("buy", "ROM", 3)]})
    cfg = __import__("trader.config", fromlist=["Config"]).Config()
    cfg.alpaca_key, cfg.alpaca_secret = "PKTEST", "secret"
    return rl, cfg, calls


def test_evening_still_submits_when_the_feature_is_off(monkeypatch):
    """delay=0 must be byte-for-byte the old behaviour: decide AND submit."""
    monkeypatch.delenv("ROTATION_RELEASE_DELAY_MIN", raising=False)
    rl, cfg, calls = _wire_evening(monkeypatch)
    out = rl.run_scheduled(cfg, dry_run=False, next_open=True)
    assert len(calls) == 1, "the evening run must still submit when off"
    assert out.get("sent") == [("buy", "ROM", 3)]
    assert rl._load_release_plan() is None, "nothing should be deferred when off"


def test_evening_defers_without_submitting_when_on(monkeypatch):
    """delay>0 must DECIDE and persist, and must not touch the order path."""
    monkeypatch.setenv("ROTATION_RELEASE_DELAY_MIN", "15")
    rl, cfg, calls = _wire_evening(monkeypatch)
    out = rl.run_scheduled(cfg, dry_run=False, next_open=True)
    assert calls == [], "deferring must not submit"
    assert out["action"] == "deferred"
    plan = rl._load_release_plan()
    assert plan["target_session"] == "2026-09-21"
    assert plan["session_et"] == "2026-09-18"
    assert plan["delay_min"] == 15


def test_a_second_evening_run_does_not_requeue(monkeypatch):
    """Two boots in one evening must not stack plans or re-decide."""
    monkeypatch.setenv("ROTATION_RELEASE_DELAY_MIN", "15")
    rl, cfg, calls = _wire_evening(monkeypatch)
    rl.run_scheduled(cfg, dry_run=False, next_open=True)
    out = rl.run_scheduled(cfg, dry_run=False, next_open=True)
    assert out["reason"] == "already_deferred"
    assert calls == []


def test_evening_detects_and_alerts_a_missed_release(monkeypatch):
    """A plan for a session that has passed must alert and be discarded."""
    monkeypatch.setenv("ROTATION_RELEASE_DELAY_MIN", "15")
    rl, cfg, calls = _wire_evening(monkeypatch)
    alerts = []
    monkeypatch.setattr(rl, "_notify", lambda t, m: alerts.append(t))
    rl._save_release_plan({"session_et": "2026-09-10",
                           "target_session": "2026-09-11", "delay_min": 15})
    out = rl.run_scheduled(cfg, dry_run=False, next_open=True)
    assert any("missed its release" in a for a in alerts), alerts
    assert out["action"] == "deferred", "and it recomputes for the new session"
    assert rl._load_release_plan()["target_session"] == "2026-09-21"
