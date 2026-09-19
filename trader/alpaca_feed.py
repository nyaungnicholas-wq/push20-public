"""Alpaca real-time price feed via REST polling.

Uses Alpaca's data API to get latest prices for all symbols.
Free with a paper trading account — no subscription needed.

Replaces yfinance for live price checks (yfinance can't stream live).
"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError
import os
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError
from datetime import datetime, timedelta, timezone

_DATA_BASE  = "https://data.alpaca.markets/v2"
_PAPER_BASE = "https://paper-api.alpaca.markets/v2"

# Which quote feed the execution instrument reads. "iex" is the free single-venue
# book; "sip" is the consolidated NBBO and needs Alpaca's paid data plan. Measured
# 2026-09-17: on IEX, GLD -- a ~1bp-spread ETF -- quotes a 105.7 bps median spread,
# so IEX is NOT the inside market and an IEX mid is not an execution benchmark.
# When the subscription exists, set ALPACA_DATA_FEED=sip. No code change.
# Measurement must never become the slow path. reconcile_fills asks for two
# quotes per fill, each of which may try two feeds x three windows, and it runs
# immediately before the rebalance decision.
_QUOTE_TIMEOUT_S = 4


def _feed() -> str:
    # Read at CALL time: .env is loaded by trader.config, which runs after import.
    return os.getenv("ALPACA_DATA_FEED", "iex")


class AlpacaFeed:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        # Why a request produced nothing, so a run can report what it did not see.
        self.errors: dict = {}
        self.last_error = None
        self.key    = api_key    or os.getenv("ALPACA_KEY", "")
        self.secret = api_secret or os.getenv("ALPACA_SECRET", "")

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID":     self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "Accept": "application/json",
        }

    def _get(self, url: str, timeout: int = 10) -> Optional[dict]:
        """GET, returning None when there is genuinely nothing to return.

        A bare `except Exception: return None` made a RATE LIMIT (HTTP 429)
        indistinguishable from "no quote exists". That is not a nuisance here --
        this call feeds the execution measurement, so a throttled request became
        a missing data point in the cost number the strategy is graded on, with
        nothing in the log to say so.

        429 is retried with backoff, because the data does exist and the server
        is only asking us to wait. Anything still failing after that is counted
        and logged, so a run can say how much it did not see.
        """
        for attempt in range(3):
            try:
                req = Request(url, headers=self._headers())
                with urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read())
            except HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                self.errors[e.code] = self.errors.get(e.code, 0) + 1
                if e.code == 429:
                    self.errors["rate_limited"] = self.errors.get("rate_limited", 0) + 1
                    print(f"  quote feed: RATE LIMITED after {attempt + 1} attempts "
                          f"-- this is a MISSING measurement, not an absent quote")
                elif e.code not in (404,):      # 404 really is "no such thing"
                    print(f"  quote feed: HTTP {e.code} on {url.split('?')[0]}")
                return None
            except Exception as e:
                self.errors["other"] = self.errors.get("other", 0) + 1
                self.last_error = repr(e)[:120]
                return None
        return None

    def get_latest_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Return {symbol: latest_price} for a list of symbols.

        Uses the /snapshots endpoint which returns all symbols in one call.
        Falls back to individual calls if the batch fails.
        """
        if not symbols:
            return {}

        # Alpaca handles up to 100 symbols per batch call
        prices: Dict[str, float] = {}
        chunk_size = 100
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i + chunk_size]
            syms  = ",".join(chunk)
            url   = f"{_DATA_BASE}/stocks/snapshots?symbols={syms}&feed=iex"
            data  = self._get(url)
            if data:
                for sym, snap in data.items():
                    try:
                        # Prefer latest trade price; fall back to latest quote mid
                        lp = (snap.get("latestTrade") or {}).get("p")
                        if lp is None:
                            q  = snap.get("latestQuote") or {}
                            ap = q.get("ap", 0)
                            bp = q.get("bp", 0)
                            lp = (ap + bp) / 2 if ap and bp else None
                        if lp:
                            prices[sym] = float(lp)
                    except (TypeError, KeyError):
                        continue

        return prices

    # -----------------------------------------------------------------------
    # NBBO capture — the execution instrument
    # -----------------------------------------------------------------------

    def get_quote(self, symbol: str, when: Optional[str] = None,
                  feed: Optional[str] = None, after: bool = False) -> Optional[dict]:
        """NBBO for `symbol`: the latest one, or the one in force at `when`.

        `when` is any RFC-3339 instant — Alpaca's own `filled_at` string works
        unchanged, which is the point: with the full-resolution fill timestamp
        the quote at the fill can be recovered, and a fill decomposes into
        spread and drift instead of arriving as one unexplained number.

        Returns None on anything unexpected. Never raises: this feeds
        measurement, and a measurement that can break the trading loop is
        strictly worse than a missing data point.
        """
        if not symbol:
            return None
        # SIP is the consolidated NBBO and is what a benchmark needs. The free
        # plan serves it at a 15-minute delay, which is not a limit here:
        # reconciliation runs the NEXT session, so every fill is hours old by
        # the time its quote is fetched. A live lookup falls back to IEX, and
        # the row records which feed answered -- they are not the same number.
        feeds = [feed] if feed else (["sip", "iex"] if when else [_feed()])
        for f in feeds:
            if when:
                for win_s in (2, 120, 1800):
                    q = self._quote_near(symbol, when, win_s, f, after)
                    if q:
                        return _shape_quote(q, when, f)
            else:
                d = self._get(f"{_DATA_BASE}/stocks/{symbol}/quotes/latest?feed={f}",
                              timeout=_QUOTE_TIMEOUT_S)
                q = _shape_quote((d or {}).get("quote"), when, f)
                if q:
                    return q
        return None

    def _quote_near(self, symbol: str, when: str, win_s: int,
                    feed: Optional[str] = None, after: bool = False) -> Optional[dict]:
        """One raw quote within `win_s` seconds of `when`.

        `after` takes the FIRST quote at or after the instant instead of the last
        one before it. That is what an arrival benchmark at the 09:30 release
        needs: the last quote BEFORE the open is a pre-market quote, which on a
        thin 2x sleeve is stale and far wider than the regular-hours book -- it
        would put a fabricated 100 bps into exactly the fills that matter most.
        """
        t = _as_utc(when)
        if t is None:
            return None
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        if after:
            lo, hi, sort = t, t + timedelta(seconds=win_s), "asc"
        else:
            lo, hi, sort = t - timedelta(seconds=win_s), t, "desc"
        start = lo.strftime(fmt)
        url = (f"{_DATA_BASE}/stocks/{symbol}/quotes?start={start}"
               f"&end={hi.strftime(fmt)}&limit=1&sort={sort}&feed={feed or _feed()}")
        qs = (self._get(url, timeout=_QUOTE_TIMEOUT_S) or {}).get("quotes") or []
        return qs[0] if qs else None

    def get_account(self) -> Optional[dict]:
        """Return Alpaca paper account info (equity, cash, buying power)."""
        return self._get(f"{_PAPER_BASE}/account")

    def is_market_open(self) -> bool:
        """Check if the US equity market is currently open."""
        data = self._get(f"{_PAPER_BASE}/clock")
        if data:
            return bool(data.get("is_open", False))
        return False

    def connected(self) -> bool:
        """Return True if API keys are valid and reachable."""
        return self.get_account() is not None


def _as_utc(when) -> Optional[datetime]:
    """RFC-3339 string (Alpaca style, 'Z' or offset) -> aware UTC datetime."""
    if not when:
        return None
    try:
        s = str(when).replace("Z", "+00:00")
        # Alpaca stamps nanoseconds; fromisoformat takes at most microseconds.
        if "." in s:
            head, _, tail = s.partition(".")
            frac = "".join(c for c in tail if c.isdigit())[:6]
            rest = tail[len(frac):].lstrip("0123456789")
            s = f"{head}.{frac or '0'}{rest}"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _shape_quote(q: Optional[dict], when: Optional[str],
                 feed: Optional[str] = None) -> Optional[dict]:
    """Alpaca's raw quote -> the fields a fill row records.

    A bid or ask of 0 means "no quote", not "free". Those come back None rather
    than a mid of half the ask, which would look like a 10,000 bps saving.
    """
    if not q:
        return None
    try:
        bid, ask = float(q.get("bp", 0) or 0), float(q.get("ap", 0) or 0)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    out = {"ts": q.get("t"), "bid": bid, "ask": ask, "mid": round(mid, 6),
           "bid_sz": q.get("bs"), "ask_sz": q.get("as"),
           "spread_bps": round((ask - bid) / mid * 10000.0, 2)}
    if feed:
        # WHICH feed answered. An IEX mid and a SIP mid are different numbers
        # on the same instant, and a row that does not say which is unreadable.
        out["feed"] = feed
    # Staleness has to be visible in the row. A 16:40 ET submit captures the
    # last regular-hours quote, so this is routinely ~2400s; read as live, that
    # mid would be mistaken for the price the order could have had.
    a, b = _as_utc(q.get("t")), _as_utc(when)
    if a is not None and b is not None:
        out["age_s"] = round((b - a).total_seconds(), 1)
    return out
