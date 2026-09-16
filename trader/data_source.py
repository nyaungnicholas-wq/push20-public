"""Market-data access behind a single interface.

Today this wraps yfinance (free, no key). The `MarketData` interface is the
seam we'll implement again with a real broker/data feed (Alpaca, Polygon)
when moving to live trading — the strategy and engine never import yfinance
directly.
"""

from __future__ import annotations

import os
from typing import Dict, List

import pandas as pd

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "_cache")


class MarketData:
    """Interface every data backend must satisfy."""

    def history(self, symbols: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
        """Return {symbol: OHLCV DataFrame indexed by date}."""
        raise NotImplementedError

    def latest(self, symbols: List[str]) -> Dict[str, float]:
        """Return {symbol: most recent close price}."""
        raise NotImplementedError


class YFinanceData(MarketData):
    """yfinance-backed daily bars with a local parquet cache."""

    def __init__(self, use_cache: bool = True):
        self.use_cache = use_cache
        os.makedirs(_CACHE_DIR, exist_ok=True)

    def _cache_path(self, symbol: str, start: str, end: str) -> str:
        safe = f"{symbol}_{start}_{end}".replace(":", "-")
        return os.path.join(_CACHE_DIR, f"{safe}.parquet")

    def history(self, symbols: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
        import yfinance as yf

        out: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            path = self._cache_path(sym, start, end)
            if self.use_cache and os.path.exists(path):
                out[sym] = pd.read_parquet(path)
                continue
            df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
            if df.empty:
                continue
            # yfinance may return a MultiIndex column frame for single tickers
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            df.index.name = "date"
            if self.use_cache:
                df.to_parquet(path)
            out[sym] = df
        return out

    def latest(self, symbols: List[str]) -> Dict[str, float]:
        import yfinance as yf

        out: Dict[str, float] = {}
        data = yf.download(symbols, period="5d", progress=False, auto_adjust=True)
        closes = data["Close"] if "Close" in data else data
        for sym in symbols:
            try:
                series = closes[sym] if len(symbols) > 1 else closes
                out[sym] = float(series.dropna().iloc[-1])
            except (KeyError, IndexError):
                continue
        return out


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Convert daily OHLCV bars to weekly bars (week ending Friday).

    Weekly bars dramatically reduce false signals from day-to-day noise.
    A MA crossover on weekly bars means something; on daily it might be noise.
    """
    weekly = df.resample("W-FRI").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["close"])
    weekly.index.name = "date"
    return weekly


def get_data_source(name: str = "yfinance") -> MarketData:
    if name == "yfinance":
        return YFinanceData()
    raise ValueError(f"Unknown data source: {name}")
