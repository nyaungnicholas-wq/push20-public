"""Two defects that fail SILENTLY — the worst kind in a system that trades.

Neither raised, neither logged, and both corrupted a number rather than stopping.

1. rotation._arrays cached on id(close) and revalidated only on row count.
   CPython reuses an id once the object is collected, so a freed price frame and
   a new frame of the same length landing on the same address served the FIRST
   frame's prices for the second — on the momentum hot path, with no error.

2. alpaca_feed._get caught bare Exception and returned None, making a rate limit
   (HTTP 429) indistinguishable from "no quote exists". That call feeds the
   execution measurement, so a throttled request silently became a missing data
   point in the cost number the strategy is graded on.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# --- 1. the array cache -----------------------------------------------------

def _frame(vals):
    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="D")
    return pd.DataFrame({"AAA": vals}, index=idx)


def test_cache_entry_is_identity_checked_not_just_length_checked():
    """The fix: a recycled id must not serve the previous frame's prices."""
    from trader import rotation as R

    a = _frame([1.0, 2.0, 3.0])
    got_a = R._arrays(a)
    assert got_a["cols"]["AAA"][0][0] == 1.0

    key = id(a)
    entry = R._ARR_CACHE.get(key)
    assert entry is not None, "first call should have cached"
    assert entry.get("ref") is not None, "entry must hold a weakref for identity"
    assert entry["ref"]() is a

    # Simulate the collision directly: a DIFFERENT frame, same length, whose
    # cache slot is already occupied by the first frame's arrays.
    b = _frame([10.0, 20.0, 30.0])
    R._ARR_CACHE[id(b)] = entry          # as an id reuse would leave it
    got_b = R._arrays(b)
    assert got_b["cols"]["AAA"][0][0] == 10.0, (
        "served the WRONG frame's prices — the id-reuse collision is back")


def test_dead_entries_are_swept_on_a_miss():
    """Without a sweep the cache keeps an entry per frame ever seen.

    An earlier version of this test built 400 frames in a loop and asserted the
    cache stayed small -- it passed even with the sweep disabled, because
    CPython reused each deleted frame's address and they all collided onto a few
    keys. It tested nothing. This drives the mechanism directly: seed dead
    entries, force a miss, and require them gone.
    """
    from trader import rotation as R

    R._ARR_CACHE.clear()
    for k in range(R._ARR_CACHE_SWEEP_AT + 5):
        tmp = _frame([1.0, 2.0])
        ref = __import__("weakref").ref(tmp)
        del tmp                                  # ref is now dead
        R._ARR_CACHE[900000 + k] = {"pos_map": {}, "cols": {}, "nrows": 2, "ref": ref}
    seeded = len(R._ARR_CACHE)
    assert seeded >= R._ARR_CACHE_SWEEP_AT

    live = _frame([3.0, 4.0, 5.0])
    R._arrays(live)                              # a miss must sweep

    dead = [k for k, v in R._ARR_CACHE.items()
            if v.get("ref") is not None and v["ref"]() is None]
    assert not dead, f"{len(dead)} of {seeded} dead entries survived the sweep"
    assert id(live) in R._ARR_CACHE, "the live frame must still be cached"


def test_cache_still_returns_correct_values_for_a_live_frame():
    """The guard must not break the thing it guards."""
    from trader import rotation as R

    f = _frame([5.0, 6.0, 7.0, 8.0])
    first = R._arrays(f)
    second = R._arrays(f)
    assert second is first, "a live frame should still hit the cache"
    assert list(second["cols"]["AAA"][0]) == [5.0, 6.0, 7.0, 8.0]


# --- 2. the quote feed ------------------------------------------------------

class _HTTPErr(Exception):
    def __init__(self, code):
        self.code = code


def _feed(monkeypatch, codes):
    """A feed whose urlopen raises the given HTTP codes in sequence."""
    from trader import alpaca_feed as AF
    from urllib.error import HTTPError

    seq = list(codes)

    def fake_urlopen(req, timeout=None):
        code = seq.pop(0) if seq else 500
        raise HTTPError(req.full_url, code, "boom", {}, None)

    monkeypatch.setattr(AF, "urlopen", fake_urlopen)
    monkeypatch.setattr(AF.time, "sleep", lambda *_: None)   # no real backoff
    return AF.AlpacaFeed("k", "s")


def test_rate_limit_is_retried_then_counted_not_silently_swallowed(monkeypatch, capsys):
    f = _feed(monkeypatch, [429, 429, 429])
    assert f._get("https://example.test/q") is None
    assert f.errors.get("rate_limited"), "a 429 must be counted, not swallowed"
    out = capsys.readouterr().out
    assert "RATE LIMITED" in out, "a rate limit must be visible in the output"
    assert "MISSING measurement" in out, (
        "the log must distinguish a throttle from an absent quote")


def test_rate_limit_that_clears_on_retry_returns_the_data(monkeypatch):
    """429 then success must yield the quote — the data existed all along."""
    from trader import alpaca_feed as AF
    import json as _json
    from urllib.error import HTTPError

    calls = {"n": 0}

    class _Resp:
        def read(self): return _json.dumps({"quote": {"bp": 1.0, "ap": 1.02}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPError(req.full_url, 429, "slow down", {}, None)
        return _Resp()

    monkeypatch.setattr(AF, "urlopen", fake_urlopen)
    monkeypatch.setattr(AF.time, "sleep", lambda *_: None)
    f = AF.AlpacaFeed("k", "s")
    got = f._get("https://example.test/q")
    assert got and got["quote"]["bp"] == 1.0
    assert calls["n"] == 2, "it must actually retry rather than give up"


def test_404_stays_quiet_because_it_really_is_no_such_thing(monkeypatch, capsys):
    f = _feed(monkeypatch, [404])
    assert f._get("https://example.test/q") is None
    assert "HTTP 404" not in capsys.readouterr().out, "404 should not be noise"
    assert f.errors.get(404) == 1, "but it should still be counted"


def test_other_http_errors_are_reported(monkeypatch, capsys):
    f = _feed(monkeypatch, [503])
    assert f._get("https://example.test/q") is None
    assert "HTTP 503" in capsys.readouterr().out
