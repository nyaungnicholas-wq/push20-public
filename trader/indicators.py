"""Pure technical-indicator functions operating on pandas Series.

Kept dependency-free (just pandas/numpy) and side-effect free so they're
trivial to unit test.
"""

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Trend / Moving Averages
# ---------------------------------------------------------------------------

def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (standard span/com formula)."""
    return series.ewm(span=span, min_periods=span, adjust=False).mean()


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi_vals = 100.0 - (100.0 / (1.0 + rs))
    rsi_vals = rsi_vals.where(avg_gain.notna(), other=pd.NA)
    rsi_vals[(avg_loss == 0.0) & avg_gain.notna()] = 100.0
    return rsi_vals


def macd(series: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line, and histogram.

    Returns DataFrame with columns: macd, signal, hist.
    """
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist},
                        index=series.index)


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------

def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Average True Range — volatility measure used for position sizing."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def bollinger_bands(series: pd.Series, period: int = 20,
                    n_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: middle (SMA), upper, lower, and %B.

    Returns DataFrame with columns: bb_mid, bb_upper, bb_lower, bb_pct.
    %B = (price - lower) / (upper - lower); >1 = above upper band, <0 = below lower.
    """
    mid = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + n_std * std
    lower = mid - n_std * std
    bb_pct = (series - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_pct": bb_pct},
        index=series.index,
    )


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume divided by N-day average. >1.2 = above-average volume."""
    avg = volume.rolling(window=period, min_periods=period).mean()
    return volume / avg.replace(0, np.nan)


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend STRENGTH, not direction.

    ADX > 25 = trending market (trade it).
    ADX < 20 = choppy/ranging market (avoid — false signals dominate).
    ADX is always 0-100 and direction-agnostic.
    """
    tr = atr(high, low, close, period)  # reuse ATR calc

    up_move   = high.diff()
    down_move = -low.diff()

    pos_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    neg_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr_s   = tr
    pos_di  = 100 * pos_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s
    neg_di  = 100 * neg_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_s

    dx = (100 * (pos_di - neg_di).abs() /
          (pos_di + neg_di).replace(0, np.nan))
    return dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def vwap_rolling(close: pd.Series, volume: pd.Series,
                 period: int = 20) -> pd.Series:
    """Rolling VWAP proxy over `period` daily bars.

    True intraday VWAP resets each session; this rolling version captures the
    price/volume center of gravity over recent history — useful as a daily
    support/resistance reference without intraday data.
    """
    pv = close * volume
    return (pv.rolling(window=period, min_periods=period).sum() /
            volume.rolling(window=period, min_periods=period).sum())
