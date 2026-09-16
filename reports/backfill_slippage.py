"""Backfill slippage log: separate market drift from true execution cost.

The existing slippage_bps compares fill price against the DECISION-time price
(close of a session days before the fill). This conflates two effects:
  1. Market drift across the queue gap (decision close -> fill open)
  2. True execution cost (fill open -> fill price)

This script splits them in place, idempotently:
  - drift_bps = the old slippage_bps (renamed, not recomputed)
  - slippage_bps = signed execution cost vs session open on fill_date
    buy  -> (fill - open) / open * 10000  (positive = paid more = worse)
    sell -> (open - fill) / open * 10000  (positive = received less = worse)
  - fill_date = trading date of fill (logged_at date, walked back to prior trading day)
  - open_px = that symbol's open on fill_date (null if unavailable)

If open_px is unavailable, slippage_bps is omitted entirely (never 0.0).
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from trader.data_source import get_data_source  # type: ignore[import-not-found]


LOG_PATH = Path(ROOT) / "data" / "slippage_log.jsonl"
BACKUP_PATH = LOG_PATH.with_suffix(".jsonl.bak")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill slippage log: split drift from execution cost")
    p.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    p.add_argument("__selfcheck", nargs="?", const=True, default=False, help=argparse.SUPPRESS)
    return p.parse_args()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def get_fill_date(logged_at: str, symbol: str, price_data: dict[str, pd.DataFrame]) -> str | None:
    """Return fill_date as YYYY-MM-DD string, or None if no trading day found within 5 days back."""
    dt = datetime.fromisoformat(logged_at)
    candidate = dt.date()
    df = price_data.get(symbol)
    if df is None or df.empty:
        return None
    trading_dates = set(df.index.date)
    for _ in range(6):  # 0..5 days back inclusive
        if candidate in trading_dates:
            return candidate.isoformat()
        candidate -= timedelta(days=1)
    return None


def get_open_px(symbol: str, fill_date: str, price_data: dict[str, pd.DataFrame]) -> float | None:
    df = price_data.get(symbol)
    if df is None or df.empty:
        return None
    try:
        ts = pd.Timestamp(fill_date)
        if ts in df.index:
            val = df.loc[ts, "open"]
            return float(val) if pd.notna(val) else None
    except Exception:
        pass
    return None


def compute_slippage_bps(side: str, fill: float, open_px: float) -> float:
    raw = (fill - open_px) / open_px * 10000.0
    return raw if side == "buy" else -raw


def transform_rows(
    rows: list[dict[str, Any]],
    price_data: dict[str, pd.DataFrame],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Transform rows in place. Returns (rows, stats)."""
    stats = {
        "total": len(rows),
        "already_migrated": 0,
        "migrated_now": 0,
        "no_open_price": 0,
    }
    for row in rows:
        # drift_bps: rename existing slippage_bps if not already done
        if "drift_bps" not in row and "slippage_bps" in row:
            row["drift_bps"] = row.pop("slippage_bps")
            stats["migrated_now"] += 1
        elif "drift_bps" in row:
            stats["already_migrated"] += 1

        # fill_date and open_px
        logged_at = row.get("logged_at")
        symbol = row.get("symbol")
        if not logged_at or not symbol:
            continue

        fill_date = row.get("fill_date")
        if not fill_date:
            fill_date = get_fill_date(logged_at, symbol, price_data)
            if fill_date:
                row["fill_date"] = fill_date

        open_px = row.get("open_px")
        if open_px is None and fill_date:
            open_px = get_open_px(symbol, fill_date, price_data)
            if open_px is not None:
                row["open_px"] = open_px
            else:
                row["open_px"] = None

        # slippage_bps (true execution cost vs open)
        has_slippage = "slippage_bps" in row
        should_compute = not has_slippage and row.get("open_px") is not None
        if should_compute:
            side = row.get("side")
            fill = row.get("fill")
            open_px_val = row["open_px"]
            if side in ("buy", "sell") and fill is not None and open_px_val is not None:
                row["slippage_bps"] = compute_slippage_bps(side, float(fill), float(open_px_val))
            else:
                row.pop("slippage_bps", None)
        elif not should_compute and row.get("open_px") is None:
            # Ensure slippage_bps is not present if no open price
            row.pop("slippage_bps", None)
            stats["no_open_price"] += 1

    return rows, stats


def fetch_price_data(
    rows: list[dict[str, Any]],
) -> dict[str, pd.DataFrame]:
    symbols = sorted({r.get("symbol") for r in rows if r.get("symbol")})
    if not symbols:
        return {}

    dates = []
    for r in rows:
        la = r.get("logged_at")
        if la:
            try:
                dates.append(datetime.fromisoformat(la).date())
            except Exception:
                pass
    if not dates:
        return {}

    min_date = min(dates) - timedelta(days=10)
    max_date = max(dates) + timedelta(days=2)

    ds = get_data_source("yfinance")
    raw = ds.history(symbols, min_date.isoformat(), max_date.isoformat())
    # Ensure lowercase columns and date index
    out = {}
    for sym, df in raw.items():
        if df is not None and not df.empty:
            df = df.copy()
            df.columns = [c.lower() for c in df.columns]
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            out[sym] = df
    return out


def compute_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_notional_drift = 0.0
    total_notional_slip = 0.0
    weight_drift = 0.0
    weight_slip = 0.0

    for r in rows:
        qty = r.get("qty")
        fill = r.get("fill")
        if qty is None or fill is None:
            continue
        notional = abs(float(qty) * float(fill))

        if "drift_bps" in r and r["drift_bps"] is not None:
            total_notional_drift += notional * float(r["drift_bps"])
            weight_drift += notional

        if "slippage_bps" in r and r["slippage_bps"] is not None:
            total_notional_slip += notional * float(r["slippage_bps"])
            weight_slip += notional

    return {
        "weighted_drift_bps": total_notional_drift / weight_drift if weight_drift else 0.0,
        "weighted_slippage_bps": total_notional_slip / weight_slip if weight_slip else 0.0,
    }


def print_report(stats: dict[str, int], report: dict[str, Any]) -> None:
    print(f"total rows: {stats['total']}")
    print(f"rows already migrated: {stats['already_migrated']}")
    print(f"rows migrated now: {stats['migrated_now']}")
    print(f"rows with no open price: {stats['no_open_price']}")
    print(f"notional-weighted drift_bps: {report['weighted_drift_bps']:.2f}")
    print(f"notional-weighted slippage_bps: {report['weighted_slippage_bps']:.2f}")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    dir_path = path.parent
    # Backup once
    if not BACKUP_PATH.exists():
        import shutil
        shutil.copy2(path, BACKUP_PATH)
    # Atomic write via temp file
    with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, suffix=".tmp", encoding="utf-8") as tf:
        for row in rows:
            tf.write(json.dumps(row, separators=(",", ":")) + "\n")
        tmp_name = tf.name
    os.replace(tmp_name, path)


def run_selfcheck() -> None:
    # Fabricated rows covering all cases
    test_rows = [
        # Buy filled above open -> positive slippage_bps
        {
            "logged_at": "2026-09-14T17:46:13",
            "symbol": "TESTBUY",
            "side": "buy",
            "qty": 10.0,
            "fill": 105.0,
            "slippage_bps": 500.0,  # old drift
        },
        # Sell filled below open -> positive slippage_bps
        {
            "logged_at": "2026-09-14T17:46:13",
            "symbol": "TESTSELL",
            "side": "sell",
            "qty": 10.0,
            "fill": 95.0,
            "slippage_bps": 300.0,
        },
        # Already migrated (has drift_bps)
        {
            "logged_at": "2026-09-14T17:46:13",
            "symbol": "TESTALREADY",
            "side": "buy",
            "qty": 10.0,
            "fill": 100.0,
            "drift_bps": 200.0,
            "fill_date": "2026-09-14",
            "open_px": 100.0,
            "slippage_bps": 10.0,
        },
        # No price data for symbol
        {
            "logged_at": "2026-09-14T17:46:13",
            "symbol": "NODATA",
            "side": "buy",
            "qty": 10.0,
            "fill": 100.0,
            "slippage_bps": 100.0,
        },
    ]

    # Mock price data
    dates = pd.date_range("2026-09-14", periods=1, freq="D")
    price_data = {
        "TESTBUY": pd.DataFrame({"open": [100.0]}, index=dates),
        "TESTSELL": pd.DataFrame({"open": [100.0]}, index=dates),
        "TESTALREADY": pd.DataFrame({"open": [100.0]}, index=dates),
        # NODATA intentionally omitted
    }

    # First pass
    rows1, _ = transform_rows([dict(r) for r in test_rows], price_data)
    # Second pass (idempotence)
    rows2, _ = transform_rows([dict(r) for r in rows1], price_data)

    # Assertions
    # Buy: fill=105, open=100 -> raw=500 -> buy -> +500
    assert rows1[0]["slippage_bps"] == 500.0, f"buy slippage: {rows1[0]['slippage_bps']}"
    assert rows1[0]["drift_bps"] == 500.0
    # Sell: fill=95, open=100 -> raw=-500 -> sell -> +500
    assert rows1[1]["slippage_bps"] == 500.0, f"sell slippage: {rows1[1]['slippage_bps']}"
    assert rows1[1]["drift_bps"] == 300.0
    # Already migrated unchanged
    assert rows1[2]["drift_bps"] == 200.0
    assert rows1[2]["slippage_bps"] == 10.0
    assert rows1[2]["open_px"] == 100.0
    # No data row: no slippage_bps key
    assert "slippage_bps" not in rows1[3], f"NODATA has slippage_bps: {rows1[3].get('slippage_bps')}"
    assert rows1[3]["drift_bps"] == 100.0
    # Idempotence
    assert rows1 == rows2, "Not idempotent"

    print(f"SELFCHECK OK backfill_slippage rows={len(test_rows)} checks=5")


def main() -> None:
    args = parse_args()

    if args.__selfcheck:
        run_selfcheck()
        return

    if not LOG_PATH.exists():
        print(f"Log not found: {LOG_PATH}", file=sys.stderr)
        sys.exit(1)

    rows = load_rows(LOG_PATH)
    price_data = fetch_price_data(rows)
    rows, stats = transform_rows(rows, price_data)
    report = compute_report(rows)
    print_report(stats, report)

    if args.apply:
        write_rows(LOG_PATH, rows)
        print("Applied.")
    else:
        print("Dry run complete. Use --apply to write.")


if __name__ == "__main__":
    main()