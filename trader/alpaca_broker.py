"""Alpaca order execution adapter.

Sends BUY and SELL orders to the Alpaca paper trading account so every
trade appears in TradingView (and later, in a live account with no code
changes — just swap paper API keys for live keys).

Each BUY is submitted as a bracket order:
  - Market entry (fills at best available price)
  - Stop-loss leg  (fires automatically if price drops to SL)
  - Take-profit leg (fires automatically if price rises to TP)

This means even if our loop goes offline, Alpaca itself will protect the
position — the stop and target are sitting on their servers, not ours.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

_PAPER_BASE = "https://paper-api.alpaca.markets/v2"


class AlpacaBroker:
    def __init__(self, api_key: Optional[str] = None,
                 api_secret: Optional[str] = None):
        self.key    = api_key    or os.getenv("ALPACA_KEY", "")
        self.secret = api_secret or os.getenv("ALPACA_SECRET", "")

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID":     self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "Content-Type":        "application/json",
            "Accept":              "application/json",
        }

    def _request(self, method: str, path: str,
                 body: Optional[dict] = None) -> Optional[dict]:
        url  = f"{_PAPER_BASE}{path}"
        data = json.dumps(body).encode() if body else None
        req  = Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urlopen(req, timeout=10) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}   # 204 No Content -> {}
        except HTTPError as e:
            err = e.read().decode()
            print(f"  [Alpaca] {method} {path} -> HTTP {e.code}: {err[:120]}")
            return None
        except Exception as e:
            print(f"  [Alpaca] {method} {path} -> Error: {e}")
            return None

    # -----------------------------------------------------------------------
    # Orders
    # -----------------------------------------------------------------------

    def place_bracket_buy(self, symbol: str, shares: int,
                          stop_loss: float, take_profit: float) -> Optional[dict]:
        """Place a bracket order: market entry + stop-loss + take-profit.

        Alpaca holds the SL and TP legs server-side — they execute even if
        our loop is offline.
        """
        if shares <= 0:
            return None
        order = {
            "symbol":        symbol,
            "qty":           str(shares),
            "side":          "buy",
            "type":          "market",
            "time_in_force": "day",
            "order_class":   "bracket",
            "stop_loss":     {"stop_price": str(round(stop_loss, 2))},
            "take_profit":   {"limit_price": str(round(take_profit, 2))},
        }
        result = self._request("POST", "/orders", order)
        if result:
            print(f"  [Alpaca] BUY {shares} {symbol}  "
                  f"SL:${stop_loss:.2f}  TP:${take_profit:.2f}  "
                  f"-> order {result.get('id','?')[:8]}")
        return result

    def place_market_buy(self, symbol: str, shares: int, tif: str = "day") -> Optional[dict]:
        """Plain market BUY of `shares` (no bracket legs). Used by the rotation
        strategy, which exits on the monthly rebalance rather than on stops.

        tif="cls" submits a Market-On-Close order — fills at the official closing
        auction (peak liquidity, tightest spreads). Must be sent before 3:50pm ET.
        This is what the backtest assumes (it fills at daily close)."""
        if shares <= 0:
            return None
        order = {
            "symbol":        symbol,
            "qty":           str(int(shares)),
            "side":          "buy",
            "type":          "market",
            "time_in_force": tif,
        }
        result = self._request("POST", "/orders", order)
        if result:
            kind = "MOC" if tif == "cls" else "market"
            print(f"  [Alpaca] BUY {int(shares)} {symbol} ({kind}) -> order {result.get('id','?')[:8]}")
        return result

    def place_market_sell_qty(self, symbol: str, shares: int, tif: str = "day") -> Optional[dict]:
        """Market SELL of a specific quantity (used to trim a position toward
        its new target weight on rebalance, rather than closing it entirely).
        tif="cls" -> Market-On-Close (fills at the closing auction)."""
        if shares <= 0:
            return None
        order = {
            "symbol":        symbol,
            "qty":           str(int(shares)),
            "side":          "sell",
            "type":          "market",
            "time_in_force": tif,
        }
        result = self._request("POST", "/orders", order)
        if result:
            kind = "MOC" if tif == "cls" else "market"
            print(f"  [Alpaca] SELL {int(shares)} {symbol} ({kind}) -> order {result.get('id','?')[:8]}")
        return result

    def place_market_sell(self, symbol: str) -> Optional[dict]:
        """Sell entire position in a symbol at market price.

        Also cancels any open SL/TP legs tied to this symbol.
        """
        # Cancel open orders for this symbol first (clears bracket legs)
        self.cancel_orders_for(symbol)
        order = {
            "symbol":        symbol,
            "qty":           None,        # qty=None + side=sell = close full position
            "side":          "sell",
            "type":          "market",
            "time_in_force": "day",
        }
        # Alpaca requires either qty or notional — use positions endpoint instead
        result = self._request("DELETE", f"/positions/{symbol}")
        if result:
            print(f"  [Alpaca] SELL all {symbol}  -> closed")
        return result

    def cancel_orders_for(self, symbol: str):
        """Cancel all open orders for a symbol (clears orphaned bracket legs)."""
        orders = self._request("GET", f"/orders?status=open&symbols={symbol}") or []
        for o in orders:
            self._request("DELETE", f"/orders/{o['id']}")

    def cancel_all_orders(self):
        """Cancel every open order (used on shutdown or circuit breaker)."""
        self._request("DELETE", "/orders")

    # -----------------------------------------------------------------------
    # Account & positions
    # -----------------------------------------------------------------------

    def get_positions(self) -> Dict[str, dict]:
        """Return {symbol: position_dict} for all open Alpaca positions."""
        positions = self._request("GET", "/positions") or []
        return {p["symbol"]: p for p in positions}

    def get_account(self) -> Optional[dict]:
        return self._request("GET", "/account")

    def get_order(self, order_id: str) -> Optional[dict]:
        """Fetch a single order by id — used to read the actual filled price
        (filled_avg_price) for slippage tracking after a MOC order settles."""
        if not order_id:
            return None
        return self._request("GET", f"/orders/{order_id}")

    def provenance(self) -> dict:
        """Which venue produced a fill. Recorded on every logged fill row.

        Round 1 could only establish that all 105 logged fills were Alpaca
        simulator fills by reading this file: the log itself did not say, so
        "measured execution cost" and "simulator output" were indistinguishable
        in the data. Writing the endpoint and the key prefix onto the row makes
        provenance a field instead of a code read.
        """
        return {"endpoint": _PAPER_BASE,
                "venue": "paper" if "paper-api." in _PAPER_BASE else "live",
                "key_prefix": (self.key or "")[:2].upper()}

    def get_clock(self) -> Optional[dict]:
        """Market clock: {is_open, next_open, next_close, timestamp}."""
        return self._request("GET", "/clock")

    def is_market_open(self) -> bool:
        c = self.get_clock()
        return bool(c and c.get("is_open"))

    def connected(self) -> bool:
        return self.get_account() is not None

    def sync_report(self) -> str:
        """Print a summary of what Alpaca currently holds."""
        acct = self.get_account()
        positions = self.get_positions()
        lines = []
        if acct:
            lines.append(f"Alpaca equity   : ${float(acct.get('equity',0)):,.2f}")
            lines.append(f"Alpaca cash     : ${float(acct.get('cash',0)):,.2f}")
        lines.append(f"Alpaca positions: {len(positions)}")
        for sym, p in positions.items():
            qty    = p.get("qty", "?")
            cost   = float(p.get("avg_entry_price", 0))
            market = float(p.get("market_value", 0))
            pnl    = float(p.get("unrealized_pl", 0))
            lines.append(f"  {sym:5}  {qty} shares  "
                         f"avg ${cost:.2f}  value ${market:,.0f}  "
                         f"P&L ${pnl:+,.0f}")
        return "\n".join(lines)
