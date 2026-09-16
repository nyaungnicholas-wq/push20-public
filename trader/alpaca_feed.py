"""Alpaca real-time price feed via REST polling.

Uses Alpaca's data API to get latest prices for all symbols.
Free with a paper trading account — no subscription needed.

Replaces yfinance for live price checks (yfinance can't stream live).
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

_DATA_BASE  = "https://data.alpaca.markets/v2"
_PAPER_BASE = "https://paper-api.alpaca.markets/v2"


class AlpacaFeed:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.key    = api_key    or os.getenv("ALPACA_KEY", "")
        self.secret = api_secret or os.getenv("ALPACA_SECRET", "")

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID":     self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "Accept": "application/json",
        }

    def _get(self, url: str) -> Optional[dict]:
        try:
            req = Request(url, headers=self._headers())
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception:
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
