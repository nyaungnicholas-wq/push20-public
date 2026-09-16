"""Tests for the SignalDeck shadow gate.

The one property that matters above all others: this code can NEVER affect the
trading path. Everything else — flag rules, vehicle mapping, honest absence —
is secondary to that isolation.
"""

import json

import pytest

from trader import shadow_gate as sg


def _payload(rows_by_kind):
    return {"forecasts": rows_by_kind}


def _row(symbol, kind, regime, conviction, acc=0.9):
    return {"symbol": symbol, "market": "stocks", "kind": kind, "regime": regime,
            "conviction": conviction, "historicalAccuracy": acc}


def test_downtrend_pick_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_fetch_regimes", lambda timeout=10.0: _payload({
        "trend21": [_row("XLE", "trend21", "downtrend", 0.9)],
        "vol21": [],
    }))
    rec = sg.shadow_check(["DIG"], {"DIG": 0.2}, log_path=str(tmp_path / "log.jsonl"))
    p = rec["picks"][0]
    assert p["underlying"] == "XLE"          # 2x vehicle maps to its sector
    assert p["wouldFlag"] and p["flags"] == ["trend21-downtrend"]


def test_low_conviction_downtrend_is_not_flagged(tmp_path, monkeypatch):
    """The rules are conviction-banded because the measured accuracy is — a
    0.3-conviction downtrend call is a 73% claim, not a 97% one."""
    monkeypatch.setattr(sg, "_fetch_regimes", lambda timeout=10.0: _payload({
        "trend21": [_row("XLK", "trend21", "downtrend", 0.3)],
        "vol21": [],
    }))
    rec = sg.shadow_check(["ROM"], {"ROM": 0.3}, log_path=str(tmp_path / "log.jsonl"))
    assert not rec["picks"][0]["wouldFlag"]


def test_elevated_vol_flags_at_high_conviction_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_fetch_regimes", lambda timeout=10.0: _payload({
        "trend21": [],
        "vol21": [_row("SMH", "vol21", "elevated", 0.85),
                  _row("QQQ", "vol21", "elevated", 0.5)],
    }))
    rec = sg.shadow_check(["USD", "QLD"], {"USD": 0.4, "QLD": 0.2},
                          log_path=str(tmp_path / "log.jsonl"))
    by = {p["symbol"]: p for p in rec["picks"]}
    assert by["USD"]["flags"] == ["vol21-elevated"]
    assert not by["QLD"]["wouldFlag"]


def test_uptrend_calm_pick_passes_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_fetch_regimes", lambda timeout=10.0: _payload({
        "trend21": [_row("XLK", "trend21", "uptrend", 0.95)],
        "vol21": [_row("XLK", "vol21", "calm", 0.9)],
    }))
    rec = sg.shadow_check(["ROM"], {"ROM": 0.35}, log_path=str(tmp_path / "log.jsonl"))
    assert rec["flaggedCount"] == 0 and rec["coverage"] == 1


def test_missing_symbol_is_honest_absence(tmp_path, monkeypatch):
    """A symbol SignalDeck doesn't track yields null forecasts and no flag —
    absence of evidence must never be treated as a verdict."""
    monkeypatch.setattr(sg, "_fetch_regimes", lambda timeout=10.0: _payload({
        "trend21": [], "vol21": [],
    }))
    rec = sg.shadow_check(["GLD"], {"GLD": 0.125}, log_path=str(tmp_path / "log.jsonl"))
    p = rec["picks"][0]
    assert p["trend21"] is None and p["vol21"] is None and not p["wouldFlag"]
    assert rec["coverage"] == 0


def test_signaldeck_down_still_logs_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_fetch_regimes", lambda timeout=10.0: None)
    log = tmp_path / "log.jsonl"
    rec = sg.shadow_check(["ROM"], {"ROM": 0.3}, log_path=str(log))
    assert rec["signaldeck_up"] is False
    assert json.loads(log.read_text().strip())["signaldeck_up"] is False


def test_trading_path_isolation(monkeypatch):
    """run_shadow_check_safe must swallow ANY failure — including its own bugs."""
    def bomb(*a, **k):
        raise RuntimeError("shadow gate exploded")
    monkeypatch.setattr(sg, "shadow_check", bomb)
    sg.run_shadow_check_safe({"weights": {"ROM": 0.3}})   # must not raise


def test_log_row_is_append_only_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_fetch_regimes", lambda timeout=10.0: _payload({
        "trend21": [], "vol21": [],
    }))
    log = tmp_path / "log.jsonl"
    sg.shadow_check(["ROM"], {"ROM": 0.3}, log_path=str(log))
    sg.shadow_check(["DIG"], {"DIG": 0.2}, log_path=str(log))
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["picks"][0]["symbol"] == "ROM"
    assert lines[1]["picks"][0]["symbol"] == "DIG"
    # forward-return field exists for the later evaluator join, and is null now
    assert all(l["fwd_return_21d"] is None for l in lines)
