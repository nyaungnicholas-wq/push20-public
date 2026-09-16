"""Shadow gate — record what SignalDeck's regime forecasts WOULD say about each
rebalance, without ever touching the trade.

Why shadow-only, and why this must not become a live filter yet
---------------------------------------------------------------
The plan is: SignalDeck confirms -> PUSH-20 executes. Before that wiring can be
honest, two facts have to be respected:

1. SignalDeck's structural forecasts (trend21/vol21, the 62-97% claims) have a
   live record that starts grading 2026-08-07. Until claims survive live
   grading, they are backtest numbers.
2. PUSH-20's own research already tested SEVEN overlay gates (trend filters,
   VIX gates beyond the shipped one, drawdown breakers, regime->defensive...)
   and every one either cost CAGR or broke 2022. Filters are guilty until
   proven innocent ON THIS STRATEGY.

So the bridge starts as a ledger, not a switch: every rebalance, log what the
gate would have flagged, then later join those rows against realized returns.
If flagged picks measurably underperform unflagged ones over enough rebalances,
the gate has EARNED a live wire; if not, this file is the evidence that saved
the strategy from another well-intentioned filter. Either outcome is a win.

Failure isolation: everything here is best-effort. SignalDeck being down, slow,
or missing a symbol produces an honest log row, never an exception in the
trading path.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from urllib.request import Request, urlopen

SIGNALDECK_URL = os.environ.get("SIGNALDECK_URL", "http://127.0.0.1:8322")
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "shadow_gate_log.jsonl")

# The rotation holds leveraged VEHICLES; the signal is about the underlying
# exposure, so forecasts are read on the 1x symbol the pick was chosen on.
VEHICLE_TO_UNDERLYING = {
    "ROM": "XLK", "QLD": "QQQ", "USD": "SMH", "UYG": "XLF", "DIG": "XLE",
    "UYM": "XLB", "UXI": "XLI", "UCC": "XLY", "SSO": "SPY", "UPW": "XLU",
}

# Pre-registered shadow rules — fixed BEFORE any outcome data exists, so the
# later evaluation cannot be accused of picking rules that happened to work.
# A pick is FLAGGED when SignalDeck's structural view disagrees with holding it:
#   - trend21 says downtrend at conviction >= 0.5 (claimed band accuracy >= 90%)
#   - vol21 says elevated at conviction >= 0.8 (claimed band accuracy >= 66.8%)
TREND_FLAG_CONVICTION = 0.5
VOL_FLAG_CONVICTION = 0.8


def _fetch_regimes(timeout: float = 10.0) -> Optional[dict]:
    """One read of SignalDeck's regimes payload (SWR-cached server-side, ~80ms).
    None on any failure — the caller logs the absence honestly."""
    try:
        req = Request(SIGNALDECK_URL + "/api/regimes",
                      headers={"X-Signaldeck": "1",
                               "Origin": "http://localhost:8323"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _index_forecasts(payload: dict) -> Dict[str, Dict[str, dict]]:
    """{symbol: {kind: forecast-row}} for the kinds the shadow rules read."""
    out: Dict[str, Dict[str, dict]] = {}
    for kind in ("trend21", "vol21"):
        for row in (payload.get("forecasts") or {}).get(kind, []) or []:
            out.setdefault(row.get("symbol", ""), {})[kind] = row
    return out


def shadow_check(picks: List[str], weights: Dict[str, float],
                 log_path: str = LOG_PATH) -> dict:
    """Evaluate the shadow rules for one rebalance and append the row.

    `picks` are the symbols actually ordered (vehicles included); `weights` the
    target weights. Returns the logged record (tests read it; the trading path
    ignores it entirely).
    """
    payload = _fetch_regimes()
    rec: dict = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "picks": [],
        "signaldeck_up": payload is not None,
        # Joined later by the evaluator once forward returns exist.
        "fwd_return_21d": None,
        "note": ("shadow only — never affects trading; rules pre-registered "
                 "2026-07-24, structural claims first live-gradable 2026-08-07"),
    }
    forecasts = _index_forecasts(payload) if payload else {}

    for sym in picks:
        underlying = VEHICLE_TO_UNDERLYING.get(sym, sym)
        f = forecasts.get(underlying, {})
        trend = f.get("trend21")
        vol = f.get("vol21")
        flags = []
        if trend and trend.get("regime") == "downtrend" \
                and float(trend.get("conviction") or 0) >= TREND_FLAG_CONVICTION:
            flags.append("trend21-downtrend")
        if vol and vol.get("regime") == "elevated" \
                and float(vol.get("conviction") or 0) >= VOL_FLAG_CONVICTION:
            flags.append("vol21-elevated")
        rec["picks"].append({
            "symbol": sym,
            "underlying": underlying,
            "weight": round(float(weights.get(sym, 0.0)), 4),
            "trend21": {k: trend[k] for k in ("regime", "conviction", "historicalAccuracy")}
                       if trend else None,
            "vol21": {k: vol[k] for k in ("regime", "conviction", "historicalAccuracy")}
                     if vol else None,
            "flags": flags,
            "wouldFlag": bool(flags),
        })

    rec["flaggedCount"] = sum(1 for p in rec["picks"] if p["wouldFlag"])
    rec["coverage"] = sum(1 for p in rec["picks"] if p["trend21"] or p["vol21"])

    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return rec


def run_shadow_check_safe(result: dict) -> None:
    """Trading-path entry point: derive picks/weights from a rotation-cycle
    result and log the shadow verdict. Swallows everything — a gate that can
    break the rebalance it observes is worse than no gate."""
    try:
        weights = result.get("weights") or {}
        picks = list(weights.keys()) or list((result.get("desired_shares") or {}).keys())
        if picks:
            shadow_check(picks, weights)
    except Exception:
        pass
