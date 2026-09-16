"""Tests for the five risk fixes from the Jun 4-9 2026 live trade-log autopsy:
  1. same-day re-entry cooldown after a stop-out
  2. live-price entry validation (stale-signal / breached-stop rejection)
  3. correlation-cluster heat cap
  4. daily loss halt (-2% intraday -> no new buys)
  5. dust-order sizing floor
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader.config import Config
from trader.engine import PaperBroker, validate_live_entry


def _cfg(**kw):
    cfg = Config()
    cfg.slippage_bps = 0
    cfg.commission_per_trade = 0.0
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


# ── 1. re-entry cooldown ─────────────────────────────────────────────────────

def test_reentry_cooldown_blocks_same_day():
    b = PaperBroker(_cfg())
    b.buy("2026-06-05", "SMH", 628.47, 10, "test", 618.12, 674.59)
    b.check_risk_exits("2026-06-05", {"SMH": 605.00})   # below stop -> stop-out
    assert "SMH" not in b.positions
    assert b.stopped_out_today("SMH", "2026-06-05") is True
    assert b.stopped_out_today("SMH", "2026-06-08") is False     # next day OK
    assert b.stopped_out_today("QQQ", "2026-06-05") is False     # other symbol OK


def test_reentry_cooldown_respects_disable_flag():
    b = PaperBroker(_cfg(reentry_cooldown_enabled=False))
    b.buy("2026-06-05", "SMH", 628.47, 10, "test", 618.12, 674.59)
    b.check_risk_exits("2026-06-05", {"SMH": 605.00})
    assert b.stopped_out_today("SMH", "2026-06-05") is False


def test_non_stop_sell_does_not_trigger_cooldown():
    b = PaperBroker(_cfg())
    b.buy("2026-06-05", "XLF", 52.27, 100, "test", 51.47, 55.78)
    b.sell("2026-06-05", "XLF", 53.00, "signal reversal")
    assert b.stopped_out_today("XLF", "2026-06-05") is False


# ── 2. live-price validation ─────────────────────────────────────────────────

def test_live_validation_rejects_stale_signal():
    # Jun 5 replay: signal said SMH @ 628.47 while live market was 569
    ok, _, why = validate_live_entry(628.47, 618.12, 569.00, _cfg())
    assert not ok and "stale" in why


def test_live_validation_rejects_breached_stop():
    cfg = _cfg(max_entry_price_drift_pct=0.20)   # wide drift so only stop check fires
    ok, _, why = validate_live_entry(628.47, 618.12, 605.00, cfg)
    assert not ok and "breached" in why


def test_live_validation_accepts_and_uses_live_price():
    ok, entry, _ = validate_live_entry(628.47, 618.12, 630.00, _cfg())
    assert ok and entry == 630.00


def test_live_validation_passes_through_without_quote():
    ok, entry, _ = validate_live_entry(628.47, 618.12, None, _cfg())
    assert ok and entry == 628.47


# ── 3. cluster heat cap ──────────────────────────────────────────────────────

def test_cluster_heat_counts_correlated_names_as_one_bet():
    cfg = _cfg(cluster_heat_cap_pct=0.02)
    b = PaperBroker(cfg)
    prices = {"QQQ": 741.72, "SMH": 628.47, "NVDA": 218.99, "XLF": 52.27}
    # two tech positions, each ~0.9% stop-risk on ~$100k equity
    b.buy("d", "QQQ", 741.72, 12, "t", 666.0, 800.0)    # risk 12*75.7 ≈ $908
    b.buy("d", "SMH", 628.47, 14, "t", 564.0, 700.0)    # risk 14*64.5 ≈ $903
    assert b.cluster_heat(prices, "tech_beta") > 0.017
    # third tech name pushing cluster past 2% -> blocked
    assert b.cluster_allows_buy("NVDA", 500.0, prices) is False
    # financials cluster is empty -> allowed
    assert b.cluster_allows_buy("XLF", 500.0, prices) is True


# ── 4. daily loss halt ───────────────────────────────────────────────────────

def test_daily_loss_halt_trips_after_2pct_day():
    cfg = _cfg(daily_loss_halt_pct=0.02)
    b = PaperBroker(cfg)
    b.mark("2026-06-05", {})                  # day start: 100k (all cash)
    b.cash = 97_500.0                          # -2.5% intraday
    assert b.daily_loss_halt({}, "2026-06-05") is True


def test_daily_loss_halt_clear_when_flat_or_new_day():
    b = PaperBroker(_cfg(daily_loss_halt_pct=0.02))
    b.mark("2026-06-05", {})
    b.cash = 99_000.0                          # only -1%
    assert b.daily_loss_halt({}, "2026-06-05") is False
    b.cash = 97_000.0
    assert b.daily_loss_halt({}, "2026-06-08") is False   # no mark for new day yet


# ── 5. dust-order sizing floor ───────────────────────────────────────────────

def test_sizing_floor_skips_dust_orders():
    cfg = _cfg(min_trade_risk_pct=0.0005)
    b = PaperBroker(cfg)
    b.cash = 400.0                             # nearly fully invested elsewhere
    b.positions = {}
    # room allows ~1 share of AAPL -> risk $5 on a tiny account... use big eq:
    # simulate: equity 400 cash only; 1 share AAPL risk 5.14 vs floor 0.0005*400=0.2
    # -> passes; so instead test the real shape: large equity, tiny room.
    b.cash = 500.0
    b.positions["XLF"] = __import__("trader.engine", fromlist=["Position"]).Position(
        "XLF", 1700, 52.27, 51.47, 55.78)
    prices = {"XLF": 52.27, "AAPL": 311.70}
    # equity ≈ 89,359; floor = $44.7 of risk; room $500 -> 1 share, risk $5.14 -> dust
    shares = b.target_shares("AAPL", 311.70, 306.56, prices)
    assert shares == 0.0


def test_sizing_floor_allows_normal_orders():
    b = PaperBroker(_cfg(min_trade_risk_pct=0.0005))
    prices = {"AAPL": 311.70}
    shares = b.target_shares("AAPL", 311.70, 306.56, prices)
    assert shares > 0
    assert shares * (311.70 - 306.56) >= 100_000 * 0.0005


# ── state round-trip ─────────────────────────────────────────────────────────

def test_last_stop_date_survives_save_load(tmp_path):
    import json
    from dataclasses import asdict
    b = PaperBroker(_cfg())
    b.buy("2026-06-05", "SMH", 628.47, 10, "t", 618.12, 674.59)
    b.check_risk_exits("2026-06-05", {"SMH": 605.00})
    s = {"cash": b.cash, "positions": {}, "trades": [asdict(t) for t in b.trades],
         "equity_curve": b.equity_curve, "last_stop_date": b.last_stop_date}
    p = tmp_path / "state.json"
    p.write_text(json.dumps(s))
    loaded = json.loads(p.read_text())
    assert loaded["last_stop_date"] == {"SMH": "2026-06-05"}
