"""Trader HUD — local dashboard server (stdlib only, no new dependencies).

Aggregates live + local data into one JSON endpoint and serves the UI:
  GET /             -> dashboard/index.html
  GET /api/summary  -> account, positions, equity history vs SPY, trades,
                       strategy status, checkup report, slippage stats

Run:  .venv/bin/python dashboard/server.py        (http://localhost:8787)
"""
from __future__ import annotations

import glob
import json
import os
import sys
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from trader.config import load_config     # noqa: E402  (loads .env keys)
from trader.rotation import DAILY_CHAMPION_FLAGS  # noqa: E402
from trader.macro_monitor import get_macro_regime  # noqa: E402

PORT = 8787
PAPER = "https://paper-api.alpaca.markets/v2"
DATA = "https://data.alpaca.markets/v2"

CFG = load_config()

# PUSH-20 backtest expectations (for the strategy panel)
EXPECT = {"name": "V7 PUSH-20", "cagr": 0.207, "mdd": -0.304,
          "monthly_win": 0.62, "deployed": "2026-06-09",
          "config": {"top_n": 3, "vol_cap": 1.5, "vol_target": 0.22,
                     "position_cap": 0.50, "defensive": "GLD+TLT"}}

_cache = {"t": 0.0, "data": None}
_lock = threading.Lock()


def _alpaca(path: str, base: str = PAPER):
    try:
        req = Request(base + path, headers={
            "APCA-API-KEY-ID": CFG.alpaca_key or "",
            "APCA-API-SECRET-KEY": CFG.alpaca_secret or "",
            "Accept": "application/json"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _read_json(path):
    try:
        with open(os.path.join(ROOT, path)) as f:
            return json.load(f)
    except Exception:
        return None


def _equity_history():
    """Account equity (1M daily) and SPY closes over the same span, both
    normalized to 100 at the first point."""
    hist = _alpaca("/account/portfolio/history?period=1M&timeframe=1D")
    if not hist or not hist.get("equity"):
        return None
    pairs = [(t, e) for t, e in zip(hist.get("timestamp") or [],
                                    hist.get("equity") or []) if t and e]
    if not pairs:
        return None
    days = [date.fromtimestamp(t).isoformat() for t, _ in pairs]
    equity = [e for _, e in pairs]
    start = (date.fromtimestamp(pairs[0][0]) - timedelta(days=3)).isoformat()
    spy_map = {}
    bars = _alpaca(f"/stocks/SPY/bars?timeframe=1Day&start={start}&limit=60&feed=iex", DATA)
    for b in (bars or {}).get("bars", []):
        spy_map[str(b.get("t", ""))[:10]] = b.get("c")
    e0 = equity[0]
    acct_norm = [round(e / e0 * 100, 2) for e in equity]
    spy_series, last = [], None
    for d in days:
        last = spy_map.get(d, last)
        spy_series.append(last)
    s0 = next((s for s in spy_series if s), None)
    spy_norm = [round(s / s0 * 100, 2) if (s and s0) else None for s in spy_series]
    return {"dates": days, "account": acct_norm, "spy": spy_norm,
            "equity_raw": equity}


def _slippage():
    rows = []
    try:
        with open(os.path.join(ROOT, "data", "slippage_log.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except Exception:
        pass
    # Same computation as live_checkup section 4, and for the same reason:
    #   slippage_bps = fill vs the session OPEN -> real execution cost
    #   drift_bps    = fill vs the decision close -> overnight/weekend market
    #                  movement, symmetric, NOT a cost
    # Both are already signed at write time (positive = worse for us). Flipping
    # sells a second time here was a bug on 2026-09-13; it read 8.49bps on a book
    # whose drift was 5.41. Weight by notional: cost lands on dollars traded.
    def _wavg(key):
        g = [(r[key], abs(float(r.get("qty", 0)) * float(r.get("fill", 0))))
             for r in rows if key in r]
        w = sum(n for _, n in g)
        return (sum(v * n for v, n in g) / w) if w > 0 else None

    exec_bps, drift_bps = _wavg("slippage_bps"), _wavg("drift_bps")
    n = sum(1 for r in rows if "slippage_bps" in r)
    try:
        from reports.live_checkup import SLIPPAGE_ASSUMED_BPS as assumed
    except Exception:
        assumed = 9.5   # keep in step with reports/live_checkup.py
    return {"n": n,
            "avg_bps": round(exec_bps, 2) if exec_bps is not None else None,
            "drift_bps": round(drift_bps, 2) if drift_bps is not None else None,
            "worst_bps": round(max((r["slippage_bps"] for r in rows
                                    if "slippage_bps" in r), default=0.0), 2),
            "assumed_bps": assumed}


def _latest_checkup():
    files = sorted(glob.glob(os.path.join(ROOT, "reports", "checkup_*.md")))
    if not files:
        return None
    with open(files[-1]) as f:
        text = f.read()
    return {"file": os.path.basename(files[-1]), "text": text,
            "alerts": [l.strip("- ⚠ ").strip() for l in text.splitlines()
                       if l.strip().startswith("- ⚠")]}


def _macro():
    """News-based macro risk read — MONITORING ONLY, never feeds the strategy.
    (News gates were tested via the VIX proxy: -2pp CAGR over 20y. The vol
    target already de-levers when news turns into volatility.)"""
    try:
        r = get_macro_regime(
            anthropic_key=CFG.anthropic_api_key, newsapi_key=CFG.newsapi_key,
            alpaca_key=CFG.alpaca_key, alpaca_secret=CFG.alpaca_secret,
            fmp_key=CFG.fmp_key, max_cache_age=1800, verbose=False)
        return {"score": round(r.score, 2),
                "gate": "FROZEN" if r.freeze_buys else "OPEN",
                "top_risk": r.top_risk,
                "sources": getattr(r, "sources_used", []) or []}
    except Exception:
        return None


def _rotation_health(rot):
    """Is the rotation actually running? The panel that was missing.

    Five trading days were missed in August and nothing on this dashboard said
    so: it showed `last_rebalance` as a date, and a date on its own cannot tell
    "correctly holding" from "has not run in a week". This reports the elapsed
    TRADING days (the unit the strategy holds in) against min_hold, plus the
    last few run-ledger rows, so a skip and a never-ran stop looking alike.
    """
    from datetime import date as _date
    from trader import session as _s
    from trader import notify as _n

    last = rot.get("last_rebalance_date") or ""
    held = None
    if last:
        try:
            held = _s.sessions_between(_date.fromisoformat(last), _s.et_date())
        except ValueError:
            held = None

    mhd = int(DAILY_CHAMPION_FLAGS.get("rotation_min_hold_days", 3))
    try:
        rows = _n.read_ledger(limit=5)
    except Exception:
        rows = []

    # STALE is deliberately generous: holding past min_hold is normal (the
    # strategy only rebalances when its signal says so), but ~2x the hold with
    # no run at all is the shape the August gap had.
    if held is None:
        state = "unknown"
    elif not rows:
        state = "stale"          # nothing has ever recorded a run
    elif held > mhd * 2:
        state = "stale"
    elif held > mhd:
        state = "watch"
    else:
        state = "ok"

    return {
        "last_rebalance": last or None,
        "trading_days_held": held,
        "min_hold_days": mhd,
        "state": state,
        "last_target_session": rot.get("last_target_session"),
        "recent_runs": [
            {"utc": r.get("utc", "")[:16].replace("T", " "),
             "action": r.get("action"), "reason": r.get("reason"),
             "orders": r.get("orders_sent"), "ok": r.get("ok")}
            for r in rows
        ],
    }


def build_summary():
    acct = _alpaca("/account") or {}
    positions = _alpaca("/positions") or []
    orders = _alpaca("/orders?status=closed&limit=25&direction=desc") or []
    rot = _read_json("data/rotation_state.json") or {}
    equity = float(acct.get("equity") or 0)
    last_eq = float(acct.get("last_equity") or 0)
    pos = [{
        "symbol": p.get("symbol"),
        "qty": float(p.get("qty", 0)),
        "avg": float(p.get("avg_entry_price", 0)),
        "price": float(p.get("current_price", 0) or 0),
        "value": float(p.get("market_value", 0) or 0),
        "upl": float(p.get("unrealized_pl", 0) or 0),
        "upl_pct": float(p.get("unrealized_plpc", 0) or 0),
    } for p in positions if isinstance(p, dict)]
    trades = [{
        "filled_at": (o.get("filled_at") or "")[:16].replace("T", " "),
        "symbol": o.get("symbol"),
        "side": o.get("side"),
        "qty": o.get("filled_qty"),
        "price": o.get("filled_avg_price"),
    } for o in orders if isinstance(o, dict) and o.get("filled_at")]
    return {
        "asof": time.strftime("%Y-%m-%d %H:%M:%S"),
        "connected": bool(acct.get("equity")),
        "account": {
            "equity": equity,
            "day_pnl": equity - last_eq if equity and last_eq else 0,
            "day_pnl_pct": (equity / last_eq - 1) if last_eq else 0,
            "since_start": (equity / 100000 - 1) if equity else 0,
            "cash": float(acct.get("cash") or 0),
            "buying_power": float(acct.get("buying_power") or 0),
        },
        "positions": sorted(pos, key=lambda x: -abs(x["value"])),
        "trades": trades[:15],
        "history": _equity_history(),
        "strategy": {**EXPECT, "flags": {k: str(v) for k, v in DAILY_CHAMPION_FLAGS.items()},
                     "last_rebalance": rot.get("last_rebalance_date")},
        "rotation_health": _rotation_health(rot),
        "slippage": _slippage(),
        "checkup": _latest_checkup(),
        "macro": _macro(),
    }


def get_summary():
    with _lock:
        if _cache["data"] is None or time.time() - _cache["t"] > 30:
            _cache["data"] = build_summary()
            _cache["t"] = time.time()
        return _cache["data"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/summary"):
            try:
                body = json.dumps(get_summary()).encode()
                self._send(200, body, "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}).encode(),
                           "application/json")
        elif self.path in ("/", "/index.html"):
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")


if __name__ == "__main__":
    print(f"Trader HUD -> http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
