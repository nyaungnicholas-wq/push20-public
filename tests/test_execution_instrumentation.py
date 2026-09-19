"""Guards for the A3 execution instrument.

Every test here fails against the code as it stood on 2026-09-17 morning. The
defects they pin were found by T6/T8 in round 1:

  - `filled_at` was sliced with `str(when)[:10]`, which reads the UTC calendar
    date. A fill at 20:30 ET stamps onto the next day and the benchmark moves a
    whole session. ~85 of the first 105 logged rows are benchmarked against the
    wrong session as a result.
  - The time was thrown away entirely, so no quote could ever be paired with a
    fill.
  - `slippage_bps` was written whenever an open existed, with no check that the
    fill even happened in that session.
  - The log carried no venue, so "measured execution cost" and "Alpaca paper
    simulator output" were indistinguishable in the data.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_fill_date_is_the_et_session_not_the_utc_calendar_day():
    """The defect, stated as a test: these two disagree, and ET is correct."""
    from trader.rotation_live import _et_stamp

    when = "2026-09-15T00:30:00Z"                 # 20:30 ET on the 14th
    assert str(when)[:10] == "2026-09-15", "the old slice"
    ts, day = _et_stamp(when)
    assert day == "2026-09-14", f"ET session date, got {day}"
    assert ts == "2026-09-14T20:30:00-04:00", ts


def test_the_time_survives_to_full_resolution():
    """Nanosecond stamps must parse, and the seconds must still be there."""
    from trader.rotation_live import _et_stamp

    ts, day = _et_stamp("2026-09-14T13:30:01.123456789Z")
    assert ts == "2026-09-14T09:30:01-04:00", ts
    assert day == "2026-09-14"
    assert _et_stamp("") == (None, None)
    assert _et_stamp(None) == (None, None)
    assert _et_stamp("not a timestamp") == (None, None)


def _run_reconcile(monkeypatch, tmp_path, order, bar, quote=None, pending_extra=None,
                   arrival_quote="same"):
    """Drive reconcile_fills against a stub broker and return the logged rows."""
    from trader import rotation_live as rl

    pend = tmp_path / "pending.json"
    slip = tmp_path / "slip.jsonl"
    rec = {"date": "2026-09-16", "symbol": "DIG", "side": "buy", "qty": 10,
           "expected": 70.0, "order_id": "oid-1"}
    rec.update(pending_extra or {})
    pend.write_text(json.dumps([rec]))

    class Stub:
        def connected(self):
            return True

        def get_order(self, oid):
            return order

        def provenance(self):
            return {"endpoint": "https://paper-api.alpaca.markets/v2",
                    "venue": "paper", "key_prefix": "PK"}

    monkeypatch.setattr(rl, "_PENDING_PATH", str(pend))
    monkeypatch.setattr(rl, "_SLIPPAGE_PATH", str(slip))
    monkeypatch.setattr(rl, "AlpacaBroker", lambda *a, **k: Stub())
    monkeypatch.setattr(rl, "_session_bar", lambda sym, day: bar)
    arr = quote if arrival_quote == "same" else arrival_quote

    def _q(cfg, sym, when=None, after=False):
        # The arrival lookup asks for 09:30 ET; the fill lookup asks for the
        # raw UTC fill stamp. Distinguish them by the offset in the string.
        return arr if (when and when.endswith(("-04:00", "-05:00"))) else quote

    monkeypatch.setattr(rl, "_quote_at", _q)
    monkeypatch.setattr(rl, "_log", lambda m: None)
    monkeypatch.setattr(rl, "_notify", lambda t, m: None)

    from trader.config import Config
    n = rl.reconcile_fills(Config())
    rows = [json.loads(l) for l in slip.read_text().splitlines() if l.strip()]
    return n, rows


ORDER = {"status": "filled", "filled_qty": 10, "filled_avg_price": "71.00",
         "filled_at": "2026-09-17T13:30:04.500000000Z"}


def test_a_fill_outside_the_session_range_gets_no_slippage_number(monkeypatch, tmp_path):
    """71.00 cannot have executed in a 68.0-69.0 session. The benchmark is the
    wrong one, and a wrong benchmark must yield no number, not a 290bps one."""
    bar = {"open": 68.5, "high": 69.0, "low": 68.0, "close": 68.7}
    n, rows = _run_reconcile(monkeypatch, tmp_path, ORDER, bar)
    assert n == 1 and len(rows) == 1
    r = rows[0]
    assert r["bench_ok"] is False
    assert "slippage_bps" not in r, "a mis-dated benchmark must not be reported"
    assert r["drift_bps"] == 142.86, r["drift_bps"]   # decision 70.0 -> fill 71.0


def test_an_aligned_fill_is_measured_against_that_session_open(monkeypatch, tmp_path):
    bar = {"open": 70.5, "high": 71.5, "low": 70.0, "close": 71.2}
    _, rows = _run_reconcile(monkeypatch, tmp_path, ORDER, bar)
    r = rows[0]
    assert r["bench_ok"] is True
    assert r["slippage_bps"] == 70.92, r["slippage_bps"]   # (71-70.5)/70.5
    assert r["rested_sessions"] == 1, r["rested_sessions"]


def test_the_row_carries_venue_and_both_timestamps(monkeypatch, tmp_path):
    """Provenance is a field, not a code read; and the fill instant survives."""
    bar = {"open": 70.5, "high": 71.5, "low": 70.0, "close": 71.2}
    _, rows = _run_reconcile(monkeypatch, tmp_path, ORDER, bar,
                             pending_extra={"submitted_at": "2026-09-16T16:41:02-04:00"})
    r = rows[0]
    assert r["venue"] == "paper" and r["key_prefix"] == "PK"
    assert "paper-api" in r["endpoint"]
    assert r["filled_at"] == ORDER["filled_at"], "raw stamp kept"
    assert r["filled_at_et"] == "2026-09-17T09:30:04-04:00", r["filled_at_et"]
    assert r["submitted_at"] == "2026-09-16T16:41:02-04:00"


def test_effective_spread_is_signed_against_us_on_both_sides(monkeypatch, tmp_path):
    """A buy above the mid and a sell below it must BOTH read positive."""
    bar = {"open": 70.5, "high": 71.5, "low": 70.0, "close": 71.2}
    quote = {"ts": "2026-09-17T13:30:04Z", "bid": 70.90, "ask": 70.98,
             "mid": 70.94, "spread_bps": 11.28}
    _, rows = _run_reconcile(monkeypatch, tmp_path, ORDER, bar, quote=quote)
    buy = rows[0]
    assert buy["eff_spread_bps"] == 8.46, buy["eff_spread_bps"]
    assert buy["quote_fill"]["spread_bps"] == 11.28

    sell_order = dict(ORDER, filled_avg_price="70.90")
    _, rows = _run_reconcile(monkeypatch, tmp_path, sell_order, bar, quote=quote,
                             pending_extra={"side": "sell"})
    # same tmp_path, so the log now holds both rows; the sell is the last one.
    assert len(rows) == 2, len(rows)
    sell = rows[-1]
    assert sell["side"] == "sell"
    assert sell["eff_spread_bps"] == 5.64, sell["eff_spread_bps"]


def test_a_missing_quote_omits_the_field_rather_than_inventing_a_mid(monkeypatch, tmp_path):
    bar = {"open": 70.5, "high": 71.5, "low": 70.0, "close": 71.2}
    _, rows = _run_reconcile(monkeypatch, tmp_path, ORDER, bar, quote=None)
    assert "eff_spread_bps" not in rows[0] and "quote_fill" not in rows[0]


def test_a_zero_bid_quote_is_no_quote_not_a_free_stock():
    """bid 0 / ask 10 would shape to a mid of 5 and a 10,000bps 'saving'."""
    from trader.alpaca_feed import _shape_quote

    assert _shape_quote({"bp": 0, "ap": 10.0}, None) is None
    assert _shape_quote({"bp": 10.02, "ap": 10.0}, None) is None, "crossed book"
    q = _shape_quote({"t": "2026-09-17T13:30:00Z", "bp": 10.0, "ap": 10.02},
                     "2026-09-17T13:30:02Z")
    assert q["mid"] == 10.01 and q["spread_bps"] == 19.98
    assert q["age_s"] == 2.0, "staleness must be visible on the row"


def test_every_legacy_row_is_unattributed_and_the_harness_says_so():
    """The 105 pre-instrumentation rows carry no venue. They are paper fills
    (alpaca_broker.py has only the paper base), but the LOG cannot prove it, and
    the comparison harness must classify them as such rather than as real."""
    path = os.path.join(ROOT, "data", "slippage_log.jsonl")
    if not os.path.exists(path):
        return
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    from reports.a3_execution import classify

    assert rows, "log is empty"
    # Filter to the LEGACY rows the docstring is about. Rows written after the
    # instrumentation landed carry venue="paper" and classify as "paper" -- that
    # is the instrument WORKING, and globbing the whole file made a passing
    # system look like a failing test.
    legacy = [r for r in rows if not r.get("venue")]
    assert legacy, "no legacy rows left to check"
    assert {classify(r) for r in legacy} == {"paper-unattributed"}

    # The claim that actually matters, over every row: nothing in this log is a
    # real fill. If this ever fails, the execution numbers have become real and
    # every "paper-simulator output" caveat in research/v3/ must be revisited.
    assert "real" not in {classify(r) for r in rows}, (
        "a row classifies as a REAL fill -- the project assumes zero of these")


def test_execution_decomposes_into_spread_and_impact(monkeypatch, tmp_path):
    """The session open is not a price the order could have had. The reference
    is the NBBO mid when the order became live, and what the fill costs beyond
    the mid AT the fill is the book walk."""
    bar = {"open": 70.5, "high": 71.5, "low": 70.0, "close": 71.2}
    at_arrival = {"ts": "2026-09-17T13:30:00Z", "bid": 70.58, "ask": 70.62,
                  "mid": 70.60, "spread_bps": 5.67}
    at_fill = {"ts": "2026-09-17T13:30:04Z", "bid": 70.90, "ask": 70.98,
               "mid": 70.94, "spread_bps": 11.28}
    _, rows = _run_reconcile(monkeypatch, tmp_path, ORDER, bar,
                             quote=at_fill, arrival_quote=at_arrival)
    r = rows[0]
    assert r["arrival_ts"] == "2026-09-17T09:30:00-04:00", r["arrival_ts"]
    assert r["arrival_bps"] == 56.66, r["arrival_bps"]     # 71.00 vs mid 70.60
    assert r["eff_spread_bps"] == 8.46, r["eff_spread_bps"]  # 71.00 vs mid 70.94
    assert r["impact_bps"] == 48.20, r["impact_bps"]       # the book walk
    # and the open-based number, which is the one that was wrong, is smaller
    assert r["slippage_bps"] == 70.92


def test_an_rth_submit_arrives_when_it_was_sent_not_at_the_open(monkeypatch, tmp_path):
    """A top-up placed at 11:05 did not arrive at 09:30."""
    bar = {"open": 70.5, "high": 71.5, "low": 70.0, "close": 71.2}
    _, rows = _run_reconcile(
        monkeypatch, tmp_path, ORDER, bar, quote=None, arrival_quote=None,
        pending_extra={"submitted_at": "2026-09-17T11:05:00-04:00"})
    from trader.rotation_live import _arrival_instant
    assert _arrival_instant({"submitted_at": "2026-09-17T11:05:00-04:00"},
                            "2026-09-17") == "2026-09-17T11:05:00-04:00"
    assert _arrival_instant({}, "2026-01-15") == "2026-01-15T09:30:00-05:00", "EST"
    assert "arrival_bps" not in rows[0], "no quote -> no number, never a zero"
