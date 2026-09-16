"""Tests for the pre-trade risk gate.

The property that matters most is FAIL CLOSED. A gate that lets orders through
when it cannot evaluate risk is worse than no gate, because it creates the
belief that something is watching.
"""

import json
import os

import pytest

from trader import risk_gate as rg


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "_DATA", str(tmp_path))
    monkeypatch.setattr(rg, "KILL_FILE", str(tmp_path / "KILL_SWITCH"))
    monkeypatch.setattr(rg, "STATE_PATH", str(tmp_path / "risk_state.json"))
    yield


def acct(equity):
    return {"equity": str(equity)}


# ── fail closed ────────────────────────────────────────────────────────────

def test_no_account_data_blocks():
    """An unknown risk state is not a safe one."""
    assert rg.evaluate(None, {}).allowed is False
    assert rg.evaluate({}, {}).allowed is False


def test_unparseable_equity_blocks():
    assert rg.evaluate({"equity": "not-a-number"}, {}).allowed is False


def test_equity_below_floor_blocks():
    assert rg.evaluate(acct(500), {}).allowed is False


# ── kill switch ────────────────────────────────────────────────────────────

def test_kill_switch_file_blocks_everything():
    rg.engage_kill_switch("manual stop during investigation")
    d = rg.evaluate(acct(100_000), {})
    assert d.allowed is False
    assert "KILL SWITCH" in d.blocked_summary()
    assert "investigation" in d.blocked_summary()


def test_kill_switch_short_circuits_before_other_checks():
    # Engaged kill switch must be the ONLY reason reported — an operator
    # stopping trading should not have to read past unrelated diagnostics.
    rg.engage_kill_switch("stop")
    d = rg.evaluate(acct(100_000), {"X": {"market_value": "900000"}})
    assert len(d.reasons) == 1


def test_kill_switch_release_restores_trading():
    rg.engage_kill_switch("stop")
    assert rg.evaluate(acct(100_000), {}).allowed is False
    assert rg.release_kill_switch() is True
    assert rg.evaluate(acct(100_000), {}).allowed is True


# ── loss and drawdown halts ────────────────────────────────────────────────

def test_session_loss_halt():
    rg.evaluate(acct(100_000), {}, today="2026-07-25")          # sets day start
    d = rg.evaluate(acct(90_000), {}, today="2026-07-25")       # -10%
    assert d.allowed is False
    assert "session loss" in d.blocked_summary()


def test_session_loss_resets_on_a_new_day():
    rg.evaluate(acct(100_000), {}, today="2026-07-25")
    assert rg.evaluate(acct(90_000), {}, today="2026-07-25").allowed is False
    # A new session re-anchors: yesterday's loss must not halt today forever.
    assert rg.evaluate(acct(90_000), {}, today="2026-07-26").allowed is True


def test_drawdown_halt_uses_a_rolling_peak():
    rg.evaluate(acct(200_000), {}, today="2026-07-25")   # peak recorded
    d = rg.evaluate(acct(100_000), {}, today="2026-08-01")  # -50% from peak
    assert d.allowed is False
    assert "drawdown" in d.blocked_summary()


def test_peak_persists_across_calls():
    rg.evaluate(acct(200_000), {}, today="2026-07-25")
    rg.evaluate(acct(150_000), {}, today="2026-07-26")
    st = json.load(open(rg.STATE_PATH))
    assert st["peak_equity"] == 200_000


# ── concentration and leverage ─────────────────────────────────────────────

def test_gross_leverage_ceiling():
    d = rg.evaluate(acct(100_000), {"A": {"market_value": "300000"}})
    assert d.allowed is False
    assert "gross leverage" in d.blocked_summary()


def test_concentration_cap():
    # 80% in one name, but gross only 0.8x — concentration must trip on its own.
    d = rg.evaluate(acct(100_000), {"A": {"market_value": "80000"}})
    assert d.allowed is False
    assert "concentration" in d.blocked_summary()


def test_normal_book_passes():
    # The live book's actual shape: ~1.0x gross, largest position ~38%.
    d = rg.evaluate(acct(94_000), {
        "USD": {"market_value": "35400"}, "DIG": {"market_value": "19475"},
        "ROM": {"market_value": "15448"}, "TLT": {"market_value": "12240"},
        "GLD": {"market_value": "11900"},
    })
    assert d.allowed is True, d.blocked_summary()
    assert d.metrics["gross_leverage"] < 1.1


def test_limits_are_wider_than_the_strategy_operates():
    """A gate that trips in normal operation gets switched off, and a switched-off
    gate protects nothing. These must sit well outside the strategy's own range."""
    assert rg.MAX_GROSS_LEVERAGE > 1.5      # strategy caps effective leverage at 1.5x
    assert rg.DRAWDOWN_HALT > 0.40          # MC median drawdown is ~-43%
    assert rg.DAILY_LOSS_HALT > 0.05


def test_bad_position_values_do_not_crash_the_gate():
    d = rg.evaluate(acct(100_000), {"A": {"market_value": None},
                                    "B": {"market_value": "oops"},
                                    "C": {"market_value": "5000"}})
    assert d.allowed is True
    assert d.metrics["gross_leverage"] == pytest.approx(0.05)


def test_every_block_reports_a_reason():
    for account, pos in [(None, {}), ({}, {}), (acct(100), {}),
                         (acct(100_000), {"A": {"market_value": "400000"}})]:
        d = rg.evaluate(account, pos)
        assert d.allowed is False and d.reasons, f"silent block for {account}"
