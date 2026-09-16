"""Guard the execution-cost measurement and the top-up failure path.

Both were silently wrong before 2026-09-14:

  - `slippage_bps` compared the fill against the DECISION close, which for this
    system is one or more sessions earlier. It reported 1002bps on a USD sell
    that executed 98bps from the open — the rest was 2x semis falling 10% over a
    weekend. Cost and market drift are now separate fields.

  - `_complete_fills` logged "top-up — sell 13 USD" BEFORE attempting the order,
    and dropped a None result (what a disconnected broker returns) without a
    word. Four such lines were written on 2026-09-13; no order existed for any
    of them, and the book sat off target for three days.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_fill_open_lookup_is_causal_and_fails_closed():
    """A missing bar must yield None, never a fabricated price."""
    from trader.rotation_live import _fill_session_open

    # No timestamp at all -> nothing to measure against.
    assert _fill_session_open("SPY", "") == (None, None)
    assert _fill_session_open("SPY", None) == (None, None)

    # A symbol with no data must not raise and must not invent a price.
    day, px = _fill_session_open("NOSUCHTICKER_XYZ", "2026-09-14T08:01:17Z")
    assert day == "2026-09-14"
    assert px is None


def test_slippage_rows_never_carry_a_zero_stand_in():
    """A row without a known open must OMIT slippage_bps, not record 0.0.

    0.0 reads as "executed exactly at the open" to every consumer, which is the
    most flattering possible value for a measurement that failed.
    """
    path = os.path.join(ROOT, "data", "slippage_log.jsonl")
    if not os.path.exists(path):
        pytest.skip("no slippage log yet")
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    for r in rows:
        if "slippage_bps" in r:
            assert r.get("open_px"), (
                f"{r.get('symbol')} on {r.get('fill_date')} has slippage_bps but "
                "no open_px — it cannot have been measured against anything"
            )


def test_topup_failure_is_logged_and_alerted(monkeypatch, tmp_path):
    """A broker that returns nothing must produce a log line and an alert."""
    from trader import rotation_live as rl

    logged, alerted = [], []
    monkeypatch.setattr(rl, "_log", lambda m: logged.append(m))
    monkeypatch.setattr(rl, "_notify", lambda t, m: alerted.append((t, m)))

    class DeadBroker:
        """Connected enough to report a terminal underfill, dead for placement —
        exactly the shape seen on 2026-09-13."""

        def get_order(self, oid):
            return {"status": "expired", "filled_qty": 0}

        def place_market_sell_qty(self, sym, qty, tif="day"):
            return None

        def place_market_buy(self, sym, qty, tif="day"):
            return None

    import datetime as _dt
    pending = [{"date": "2026-09-13", "symbol": "USD", "side": "sell",
                "qty": 13, "expected": 86.06, "order_id": "abc"}]
    out = rl._complete_fills(DeadBroker(), pending, {"USD": 86.06},
                             _dt.datetime(2026, 9, 13), rounds=1, wait_s=0)

    assert any("TOP-UP FAILED" in m for m in logged), (
        f"a failed placement must say so; log was {logged}"
    )
    # "was NOT submitted" legitimately contains the word, so match the success
    # shape instead: the old bug emitted a bare "top-up — sell 13 USD" line.
    assert not any(m.lstrip().startswith("top-up —") for m in logged), (
        f"nothing was submitted, so no line may read as a success: {logged}"
    )
    assert alerted, "a book left off target with no order to fix it must alert"
    assert not any(r.get("order_id") == "abc" and r.get("_topped") is None
                   for r in out if "_topped" in r)
    # The dead order must not be tracked as if it were live.
    assert all(r.get("order_id") != "None" for r in out)


def test_successful_topup_logs_the_order_id(monkeypatch):
    """The success path must log the outcome, with the id, not the intention."""
    from trader import rotation_live as rl

    logged = []
    monkeypatch.setattr(rl, "_log", lambda m: logged.append(m))
    monkeypatch.setattr(rl, "_notify", lambda t, m: pytest.fail("no alert expected"))

    class LiveBroker:
        def get_order(self, oid):
            return {"status": "expired", "filled_qty": 3}

        def place_market_sell_qty(self, sym, qty, tif="day"):
            return {"id": "deadbeef-1111-2222"}

        def place_market_buy(self, sym, qty, tif="day"):
            return {"id": "deadbeef-1111-2222"}

    import datetime as _dt
    pending = [{"date": "2026-09-14", "symbol": "DIG", "side": "sell",
                "qty": 10, "expected": 71.0, "order_id": "abc"}]
    out = rl._complete_fills(LiveBroker(), pending, {"DIG": 71.0},
                             _dt.datetime(2026, 9, 14), rounds=1, wait_s=0)

    assert any("submitted" in m and "deadbeef" in m for m in logged), logged
    assert any(r.get("order_id") == "deadbeef-1111-2222" for r in out), (
        "a successful top-up must be tracked for the next reconciliation"
    )


def test_order_summary_is_readable_and_never_raises():
    """This string is the body of a phone alert, so it must survive bad input."""
    from trader.rotation_live import _order_summary

    real = {"sent": [("SELL", "DIG", 81), ("BUY", "TLT", 67)]}
    assert _order_summary(real) == "SELL 81 DIG\nBUY 67 TLT"
    assert _order_summary({"sent": []}) == "No orders were sent."
    assert _order_summary({}) == "No orders were sent."
    # Malformed input must degrade, not explode: the alert is the last line of
    # defence and must not die formatting itself.
    assert isinstance(_order_summary({"sent": [("only-two", "fields")]}), str)


def test_success_path_alerts_not_only_failures():
    """A rebalance that WORKED must notify.

    Every _notify call in rotation_live used to hang off an error path, so a
    successful run and a box that never woke up looked identical from outside.
    That is how five August sessions went missing unnoticed.
    """
    import re

    src = open(os.path.join(ROOT, "trader", "rotation_live.py"), encoding="utf-8").read()
    # Both success paths: evening mode queues for the next open, RTH submits now.
    assert len(re.findall(r'_notify\(\s*f?"PUSH-20 traded', src)) == 2, (
        "both the evening-mode and RTH success paths must alert"
    )


def test_ntfy_titles_survive_the_latin1_header_encoding():
    """ntfy sends headers as latin-1, so emoji in a title would raise inside
    urllib and be swallowed as a delivery failure. They must be stripped, and
    the whitespace they leave behind collapsed."""
    from trader import notify as n

    monkey = os.environ.get(n.WEBHOOK_ENV)
    os.environ[n.WEBHOOK_ENV] = "https://ntfy.sh/unit-test-topic"
    try:
        captured = {}

        class FakeResp:
            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        real_urlopen = n.urllib.request.urlopen
        n.urllib.request.urlopen = lambda req, timeout=None: (
            captured.update(headers=dict(req.headers), body=req.data), FakeResp())[1]
        try:
            assert n.webhook("⚠️ PUSH-20 traded — 5 orders", "SELL 81 DIG", "alert") is True
        finally:
            n.urllib.request.urlopen = real_urlopen

        title = captured["headers"].get("Title")
        assert title == "PUSH-20 traded 5 orders", repr(title)
        assert title.encode("latin-1"), "title must be latin-1 encodable"
        assert captured["body"] == b"SELL 81 DIG", "body must be the plain message"
    finally:
        if monkey is None:
            os.environ.pop(n.WEBHOOK_ENV, None)
        else:
            os.environ[n.WEBHOOK_ENV] = monkey


def _capture_webhook(url, title, message, level="alert"):
    """Run webhook() against `url` with urlopen stubbed; return the request."""
    from trader import notify as n

    captured = {}

    class FakeResp:
        def getcode(self):
            return 204            # Discord's success code, not 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    saved_env = os.environ.get(n.WEBHOOK_ENV)
    saved_open = n.urllib.request.urlopen
    os.environ[n.WEBHOOK_ENV] = url
    n.urllib.request.urlopen = lambda req, timeout=None: (
        captured.update(url=req.full_url, headers=dict(req.headers), body=req.data),
        FakeResp())[1]
    try:
        captured["returned"] = n.webhook(title, message, level)
    finally:
        n.urllib.request.urlopen = saved_open
        if saved_env is None:
            os.environ.pop(n.WEBHOOK_ENV, None)
        else:
            os.environ[n.WEBHOOK_ENV] = saved_env
    return captured


def test_discord_gets_an_embed_not_the_generic_envelope():
    """Discord rejects a payload with neither content nor embeds.

    The shape asserted here was checked against Discord's live API on
    2026-09-15: it answered 403/1010 (bad credentials) rather than 400 with
    form-body errors, which means the body itself was accepted.
    """
    import json

    for host in ("https://discord.com/api/webhooks/123/tok",
                 "https://ptb.discord.com/api/webhooks/123/tok",
                 "https://discordapp.com/api/webhooks/123/tok"):
        got = _capture_webhook(host, "PUSH-20 traded: 5 orders", "SELL 81 DIG")
        assert got["returned"] is True, "204 must count as success"
        body = json.loads(got["body"].decode())
        assert "embeds" in body and len(body["embeds"]) == 1, body
        e = body["embeds"][0]
        assert e["title"] == "PUSH-20 traded: 5 orders"
        assert e["description"] == "SELL 81 DIG"
        assert isinstance(e["color"], int)
        assert "content" not in body, "an embed alone is the valid payload"


def test_discord_respects_the_hard_field_limits():
    """Over-long title or description makes Discord reject the WHOLE payload."""
    import json

    got = _capture_webhook("https://discord.com/api/webhooks/1/t",
                           "T" * 400, "D" * 5000)
    e = json.loads(got["body"].decode())["embeds"][0]
    assert len(e["title"]) <= 256, len(e["title"])
    assert len(e["description"]) <= 4096, len(e["description"])
    assert e["title"].endswith("..."), "a cut field should show it was cut"


def test_discord_never_sends_an_empty_description():
    """Discord 400s on an embed whose description is an empty string."""
    import json

    for msg in ("", "   ", "\n\n"):
        got = _capture_webhook("https://discord.com/api/webhooks/1/t", "title", msg)
        e = json.loads(got["body"].decode())["embeds"][0]
        assert e["description"], f"empty description sent for {msg!r}"


def test_discord_keeps_unicode_that_ntfy_has_to_strip():
    """The two branches must stay independent.

    ntfy carries the title in a latin-1 HTTP header so emoji must go; Discord
    takes UTF-8 in the JSON body, so stripping there would be a pointless loss.
    """
    import json

    title = "⚠️ PUSH-20 — halted"
    d = _capture_webhook("https://discord.com/api/webhooks/1/t", title, "body")
    assert json.loads(d["body"].decode())["embeds"][0]["title"] == title

    nt = _capture_webhook("https://ntfy.sh/topic", title, "body")
    assert nt["headers"]["Title"] == "PUSH-20 halted"


def test_non_discord_non_ntfy_url_keeps_the_generic_envelope():
    """A Slack or custom endpoint must not be handed a Discord embed."""
    import json

    got = _capture_webhook("https://hooks.slack.com/services/T/B/X", "t", "m", "info")
    body = json.loads(got["body"].decode())
    assert set(body) == {"title", "message", "level", "utc"}, body
