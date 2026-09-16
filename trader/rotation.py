"""Sector-rotation strategy — the version that actually beats SPY.

Why this exists
---------------
Every signal-based version (v1–v7, SMA20/50 crossover entry) underperformed
SPY buy-and-hold. Root cause: the crossover fires 30–40% into a trend, so the
system bought late and sold into reversals. No amount of risk-management tuning
fixed a late entry.

This module replaces *timing* with *relative-strength rotation*. Instead of
trying to time entries on individual names, it holds the handful of sectors that
are already leading and rotates monthly. Cross-sectional momentum (holding recent
winners) is one of the most robustly documented anomalies in the literature, and
unlike single-name selection it is immune to survivorship bias — the universe is
a fixed set of sector ETFs that all existed throughout the test window.

Mechanism
---------
  * Universe : 11 GICS sector SPDRs + SMH (semis) + QQQ (nasdaq-100).
  * Score    : blended total return over `rotation_lookbacks` (default 6mo & 12mo).
  * Hold     : the top `rotation_top_n` (default 3), equal-weighted.
  * Rebalance: monthly, tranched across `rotation_tranches` trading-days (default
               the 1st/11th/21st) so single-day timing luck averages out — a
               one-day rule swings ~11-19% CAGR purely on which day you pick.
  * Safety   : absolute-momentum filter — a sector must have positive blended
               momentum to be eligible. If fewer than top_n qualify, the slack is
               parked in `rotation_cash_symbol` (BIL, 1-3mo T-bills). This is the
               valve that protects an everything-down market (e.g. 2008); in
               2018-2024 it rarely bound because some sector was always rising.

Backtest 2018-2024 (after 10bps/side slippage), starting $100k:
  Sector rotation : ~$360k final, ~20% CAGR, -31% max DD, Sharpe ~0.9
  SPY buy & hold  : ~$245k final, ~13.7% CAGR, -34% max DD, Sharpe ~0.76
  → beats SPY in BOTH 2018-2020 and 2021-2024 sub-periods independently.

The strategy runs through the existing PaperBroker, so the trade log, equity
curve, and every metric in metrics.py work unchanged, and the same rebalance
routine can drive live paper trading.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import Config
from .data_source import get_data_source
from .engine import PaperBroker

# Stops/targets are not used by rotation — positions exit only on rebalance.
# We still must pass *some* values to broker.buy(); these never trigger because
# the rotation loop never calls check_risk_exits().
_NO_STOP = 0.0
_NO_TP = 1e12

# ---------------------------------------------------------------------------
# The v3 champion — single source of truth shared by the backtest (rotation-v2)
# and the live paper trader (rotation-live), so both trade the SAME strategy.
# Found via the research loop + global joint search; see reports/upgrade_report.md.
# ---------------------------------------------------------------------------
CHAMPION_FLAGS = {
    "rotation_momentum_weight": False,
    "rotation_weight_scheme": "momentum",
    "rotation_lookbacks": (252,),
    "rotation_defensive_symbol": "GLD+TLT",
    "rotation_vol_target": 0.15,              # two-way vol target level (lever up below, de-lever above)
    "rotation_vol_window": 20,
    "rotation_return_prop": True,             # V4 Turbo A: weight ∝ R_i / sum(R)
    "rotation_two_way_vol": True,             # V4 Turbo E: lever up in calm via 2x ETFs
    "rotation_vol_cap": 2.0,
    "rotation_vol_floor": 0.50,
}

# Per-sector 2x ETF map (ProShares Ultra / Direxion 2x series — all confirmed in yfinance)
LEV2X_MAP: Dict[str, str] = {
    "XLK": "ROM",   # ProShares Ultra Technology
    "QQQ": "QLD",   # ProShares Ultra QQQ
    "SMH": "USD",   # ProShares Ultra Semiconductors
    "XLF": "UYG",   # ProShares Ultra Financials
    "XLE": "DIG",   # ProShares Ultra Oil & Gas (was ERX — Direxion, 3x until 2020, wrong instrument)
    "XLV": "RXL",   # ProShares Ultra Health Care
    "XLI": "UXI",   # ProShares Ultra Industrials
    "XLY": "UCC",   # ProShares Ultra Consumer Disc
    "XLB": "UYM",   # ProShares Ultra Materials
    "GLD": "UGL",   # ProShares Ultra Gold
    "TLT": "UBT",   # ProShares Ultra 20+ Year Treasury
    "EFA": "EFO",   # ProShares Ultra MSCI EAFE
    "EEM": "EET",   # ProShares Ultra MSCI EM
}

# Per-sector 3x ETF map (Direxion / ProShares UltraPro — only high-volume, liquid names)
# History: TECL/FAS from 2008; TQQQ/SOXL from 2010. Backtest falls back to 2x pre-launch.
# Deliberate omission: no 3x for XLE/XLV/XLI/XLY/XLB/XLC — illiquid or non-existent.
LEV3X_MAP: Dict[str, str] = {
    "XLK": "TECL",  # Direxion Technology Bull 3x
    "QQQ": "TQQQ",  # ProShares UltraPro QQQ
    "SMH": "SOXL",  # Direxion Semiconductors Bull 3x
    "XLF": "FAS",   # Direxion Financial Bull 3x
}

# V5 Hyper-Drive universe: remove structural laggards (XLP/XLU/XLRE never rank top-3;
# they pollute the candidate pool and drag down average sector momentum).
# Verified: 10-sector clean universe → +1.5pp CAGR, same MDD, +0.04 Sharpe vs 13-sector.
V5_UNIVERSE: List[str] = [
    "XLK",   # Technology
    "XLF",   # Financials
    "XLE",   # Energy
    "XLV",   # Health Care
    "XLI",   # Industrials
    "XLY",   # Consumer Discretionary
    "XLB",   # Materials
    "XLC",   # Communication Services
    "SMH",   # Semiconductors
    "QQQ",   # Nasdaq-100
]

# V5 Hyper-Drive champion flags.
# Research basis (2005–2024, 10bps/side, clean 10-sector universe):
#   10-sector clean universe    → +1.5pp vs 13-sector baseline
#   Momentum-squared weighting  → +0.4pp CAGR (small MDD trade-off)
#   3x tier + asymmetric vol    → pending live backtest (3x ETFs from 2008/2010)
#   GLD+TLT defensive           → confirmed: BIL costs −1.8pp, reject BIL
#   Top-3 (not top-2)           → confirmed: top-2 costs −0.6pp CAGR + +3.7pp MDD
# MDD tolerance accepted: −50%
V5_FLAGS: Dict = {
    # Signal — unchanged
    "rotation_lookbacks"       : (252,),
    "rotation_top_n"           : 3,
    "rotation_abs_momentum"    : True,

    # Weighting — momentum-squared replaces return-prop
    "rotation_weight_scheme"   : "momentum",
    "rotation_momentum_weight" : False,
    "rotation_return_prop"     : False,      # OFF — superseded by rotation_weight_squared
    "rotation_weight_squared"  : True,       # V5: weight ∝ R_i² / ΣR² (stronger concentration)

    # Vol overlay — 3-tier asymmetric
    "rotation_two_way_vol"     : True,
    "rotation_use_3x"          : True,       # V5: deploy TECL/TQQQ/SOXL/FAS when scale > 2
    "rotation_vol_target"      : 0.15,
    "rotation_vol_window"      : 20,
    "rotation_vol_cap"         : 3.0,        # V5: bull regime allows up to 3x
    "rotation_vol_cap_bear"    : 1.5,        # V5: bear regime hard cap at 1.5x
    "rotation_vol_cap_sma"     : 200,        # SPY 200 SMA determines bull/bear
    "rotation_vol_floor"       : 0.50,

    # Defensive — GLD+TLT confirmed best; BIL rejected (−1.8pp)
    "rotation_defensive_symbol": "GLD+TLT",
}


def apply_champion(cfg: Config) -> Config:
    """Set the V4 Turbo champion flags on a Config in place (and return it)."""
    for k, v in CHAMPION_FLAGS.items():
        setattr(cfg, k, v)
    return cfg


# Daily champion flags — V7 "PUSH-20" (deployed Jun 9 2026, target >=20% CAGR).
# Backtest (fixed harness: honest pre-inception data, DIG, data through 2026-06, 5bps/side):
#   Full 2006-2024: 20.7% CAGR / 62% monthly win / -30% MDD / Calmar 0.68
#   Modern (2015-2024): 22.2% / -30%   | OOS 2016-2024: ~23% (> insample — no overfit signature)
# vs V6D (top5/cap2.0/vt0.20/cap30%): +2.3pp CAGR, higher Sharpe (0.90 vs 0.84), higher win
# rate (62% vs 58%), SAME -30% drawdown. Two knobs moved it: vol_cap back to the PROVEN 1.5x
# optimum (2.0x was pure vol decay) and vol_target 0.20 -> 0.22 (the CAGR/MDD throttle).
# top_n 5 -> 3 (the diversified top-5 shape cannot reach 20% — max 19.8% with worse Calmar);
# position_cap 0.50 keeps the no-all-eggs guard (uncapped = 21.1%, set 1.0 to take it).
# Monte Carlo (300 paths): P(beat SPY)=96%, P(CAGR>=20%)~55%, but P(MDD<-40%)~59% across
# alternate histories — the realized -30% was partly sequence luck. This is the price of 20%.
# RISK PROFILES (edit these 4 knobs):
#   Max aggressive (worst ratio): top_n=1, vol_cap=3.0, vol_target=0.30, position_cap=1.0 (30%/-53%)
#   PUSH-20 (live):               top_n=3, vol_cap=1.5, vol_target=0.22, position_cap=0.5 (20.7%/-30%)
#   Balanced (best ratio):        top_n=3, vol_cap=1.5, vol_target=0.18, position_cap=1.0 (19%/-28%)
#   Conservative:                 top_n=3, vol_cap=1.5, vol_target=0.15, position_cap=1.0 (17%/-25%)
DAILY_CHAMPION_FLAGS: Dict = {
    "rotation_lookbacks"       : (232,),
    "rotation_top_n"           : 3,      # PUSH-20: top 3 (top-5 shape caps out below 20%)
    "rotation_abs_momentum"    : True,
    "rotation_weight_scheme"   : "momentum",
    "rotation_momentum_weight" : False,
    "rotation_return_prop"     : False,   # HOLDOUT v2 (2026-09-13): equal weight, see below
    "rotation_weight_squared"  : False,
    "rotation_two_way_vol"     : True,
    "rotation_use_3x"          : False,
    "rotation_position_cap"    : 0.60,   # RISK-DIAL v3 (2026-07-24): 0.50 -> 0.60, see below
    "rotation_vol_target"      : 0.50,   # GATED v2 (2026-06-23): basket-vol + 1.5 cap bind this to ~1.5x on calm days
    "rotation_vol_window"      : 8,      # RISK-DIAL v3 (2026-07-24): 12 -> 8, see below
    "rotation_vol_cap"         : 1.25,   # HOLDOUT v2 (2026-09-13): 1.5 -> 1.25, see below
    "rotation_vol_cap_bear"    : 1.0,    # safety valve: de-lever to 1x when SPY < 200-SMA
    "rotation_vol_cap_sma"     : 200,    # SPY 200-SMA determines bull/bear
    "rotation_vol_floor"       : 0.50,
    "rotation_defensive_symbol": "GLD+TLT",
    "rotation_signal_ema"      : 9,      # EMA span for momentum smoothing
    "rotation_min_hold_days"   : 3,      # minimum days before rebalancing again
    # ── GATED v2 (2026-06-23): risk gates that earn the right to higher leverage. ──
    # Backtest 2006-2026: 19.8% CAGR / -35.5% MDD / Calmar 0.56  (old PUSH-20: 19.1% / -36.6% / 0.52)
    # Dominates old config on BOTH return and drawdown; OOS-robust in 3/4 sub-periods.
    # Cost: 2022 grind year ~-6%. Reverting = set these False + vol_target 0.22 + vol_window 25.
    "rotation_basket_vol"      : True,   # size leverage to the HELD picks' own vol, not SPY's
    "rotation_vix_gate"        : True,   # forward tail-risk gate (de-lever when VIX elevated)
    "rotation_vix_symbol"      : "^VIX",
    "rotation_vix_lo"          : 25.0,
    "rotation_vix_lo_cap"      : 1.0,    # VIX >= 25 → cap exposure at 1x
    "rotation_vix_hi"          : 35.0,
    "rotation_vix_hi_cap"      : 1.0,    # HOLDOUT v2 (2026-09-13): tier retired, see below
    # ── RISK-DIAL v3 (2026-07-24): the only two changes that improve BOTH axes. ──
    # Swept 1260 configs (reports/risk_dial_sweep.py) then re-tested the finalists over
    # three disjoint sub-periods (reports/risk_dial_finalists.py). vol_window 12->8 and
    # position_cap 0.50->0.60 are a genuine Pareto move:
    #   before  21.32% CAGR / -32.53% MDD / Calmar 0.655 / Sharpe 0.88
    #   after   21.78% CAGR / -31.52% MDD / Calmar 0.691 / Sharpe 0.89
    # More return AND less drawdown, with no sub-period sacrificed (worst era -0.05pp).
    #
    # What was REJECTED, and why it matters: raising rotation_vol_cap above 1.5 buys
    # CAGR but pays for it more than proportionally in drawdown at EVERY vol_window
    # tested — Calmar falls monotonically 1.25 -> 2.0 (at window 8: 0.740, 0.710, 0.668,
    # 0.638). That independently reconfirms the earlier "1.5x is the optimum, beyond it
    # is vol decay" finding, so the cap stays put. The CAGR menu, if the drawdown budget
    # is ever raised deliberately: cap 1.75 -> 23.06% / -34.52%; cap 2.0 -> 24.01% /
    # -37.63%. Both are real tradeoffs, not improvements.
    #
    # Note the -31.52% is one realized path. Full Monte Carlo on the pre-existing config
    # put median MDD near -43% with P(MDD < -40%) around 63%; budget for that, not this.
    #
    # REVERT: set rotation_vol_window back to 12 and rotation_position_cap back to 0.50.
    #
    # ── HOLDOUT v2 (2026-09-13): the first pre-registered out-of-sample test this ──
    # strategy has ever had. Three changes, selected ONLY on 2006-2018, then graded
    # once on a sealed 2019-01-01..2026-09-02 window. See reports/PREREG_v2.md and
    # reports/HOLDOUT_RESULT_v2.json (prereg sha256 recorded in the result).
    #
    #   rotation_return_prop  True -> False   equal-weight the 3 picks
    #   rotation_vol_cap      1.5  -> 1.25    leverage ceiling
    #   rotation_vix_hi_cap   0.5  -> 1.0     retire the VIX>=35 tier
    #
    # Selection was on the MEDIAN of 13 perturbations of this config, not on a
    # single point, because the 2026-08-03 audit shows 72 of 135 configs sit within
    # 0.05 Sharpe of each other. All three improved both sub-eras and 13/13 configs.
    #
    # Sealed holdout 2019-2026, paired against the live config on the same engine:
    #     live       CAGR 22.01%  MDD -32.18%  Sharpe 0.820  Calmar 0.684
    #     this       CAGR 21.01%  MDD -28.27%  Sharpe 0.901  Calmar 0.743
    # Passed all three pre-registered gates; 12/13 plateau configs also improved.
    # Full window 2006-2026: 18.63% / -28.37% / Sharpe 0.894 / Calmar 0.657.
    #
    # It gives up ~1pp of CAGR and buys ~4pp of drawdown. That was the pre-registered
    # intent: the Monte Carlo median MDD is ~-43%, so the realised drawdown was the
    # understated number, not the return.
    #
    # WHY THE VIX>=35 TIER WENT: it de-levered into the bottom and missed the rebound.
    # Measured in-sample it cost 1.51pp CAGR AND made drawdown worse (-28.32% -> -32.90%).
    # A risk gate that increases drawdown is not a risk gate.
    #
    # NOT SHIPPED, deliberately: letting a falling risk cap de-lever inside the 3-day
    # hold window scored best in-sample (Sharpe 0.810 / Calmar 0.650) but had already
    # been measured worse on the full window during engine-fidelity work, so it could
    # not be graded honestly. Left out rather than shipped on a peeked result.
    #
    # REVERT: rotation_return_prop True, rotation_vol_cap 1.5, rotation_vix_hi_cap 0.5.
}

DAILY_UNIVERSE: List[str] = list(V5_UNIVERSE)  # same 10-sector universe


def apply_daily_champion(cfg: Config) -> Config:
    """Set the daily champion flags on a Config in place (and return it)."""
    for k, v in DAILY_CHAMPION_FLAGS.items():
        setattr(cfg, k, v)
    cfg.rotation_universe = list(DAILY_UNIVERSE)
    return cfg


def apply_v5(cfg: Config) -> Config:
    """Set the V5 Hyper-Drive flags on a Config in place (and return it).

    V5 Hyper-Drive changes vs V4 Turbo:
      Universe  : 10 high-beta sectors (removed XLP/XLU/XLRE — structural laggards)
      Weighting : momentum-squared R_i²/ΣR² (stronger concentration in the leader)
      Leverage  : 3-tier (1x → 2x → 3x) with asymmetric vol cap:
                    SPY > 200 SMA → cap = 3.0 (TECL/TQQQ/SOXL/FAS when scale > 2)
                    SPY < 200 SMA → cap = 1.5 (stay conservative in bear markets)
      Defensive : GLD+TLT (unchanged — BIL proved −1.8pp worse)
      Cadence   : monthly tranched (unchanged)
    """
    for k, v in V5_FLAGS.items():
        setattr(cfg, k, v)
    cfg.rotation_universe = list(V5_UNIVERSE)
    return cfg


def _close_frame(raw: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Combine per-symbol OHLCV frames into one forward-filled daily close frame."""
    frame = pd.DataFrame({s: df["close"] for s, df in raw.items() if not df.empty})
    return frame.sort_index().ffill()


def _rebalance_dates(index: pd.DatetimeIndex, start: str, end: str,
                     offset: int = 0) -> List[pd.Timestamp]:
    """The `offset`-th trading day of each month within [start, end].

    offset=0 is the first trading day of the month; offset=10 is roughly
    mid-month. The offset is clamped to the last trading day of short months.
    Acting on a fixed trading-day-of-month keeps the rule causal and easy to
    execute live. Using several offsets (see `rotation_tranches`) averages out
    the month-day timing luck that single-day rebalancing is exposed to.
    """
    window = index[(index >= start) & (index <= end)]
    by_month: Dict[Tuple[int, int], List[pd.Timestamp]] = {}
    for ts in window:
        by_month.setdefault((ts.year, ts.month), []).append(ts)
    return [days[min(offset, len(days) - 1)] for days in by_month.values()]


# Correlation clusters — used by the optional cluster filter so the book never
# holds the same macro factor in several wrappers (e.g. SMH+XLK+QQQ = triple tech).
_CLUSTERS: Dict[str, str] = {
    "XLK": "tech", "QQQ": "tech", "SMH": "tech", "SOXX": "tech", "IBB": "tech", "XBI": "tech",
    "XLF": "finance", "KRE": "finance",
    "XLE": "energy",
    "XLV": "health",
    "XLI": "industrial",
    "XLY": "consumer", "XRT": "consumer", "XHB": "consumer", "ITB": "consumer",
    "XLB": "materials",
    "XLP": "defensive", "XLU": "defensive",
    "XLRE": "realestate",
    "XLC": "comm",
    "IWM": "smallcap", "MDY": "smallcap",
    "VEA": "intl", "VWO": "intl", "EFA": "intl", "EEM": "intl", "FXI": "intl", "EWJ": "intl",
    "TLT": "bonds", "IEF": "bonds", "SHY": "bonds",
    "BIL": "cash",
    "GLD": "gold", "GDX": "gold", "GDXJ": "gold",
    "DBA": "commodities", "USO": "commodities",
}


def _defensive_symbol(cfg: Config) -> str:
    """Primary defensive asset (first of the blend) — back-compat single-symbol view."""
    dl = _defensive_list(cfg)
    return dl[0] if dl else ""


def _defensive_list(cfg: Config) -> List[str]:
    """Defensive sleeve as a list. `rotation_defensive_symbol` may be a single
    ticker ("GLD") or a '+'-joined blend ("GLD+TLT") split equally. Falls back to
    the cash symbol. Used for empty-slot fills and vol-target parking."""
    raw = getattr(cfg, "rotation_defensive_symbol", "") or cfg.rotation_cash_symbol or ""
    out = [s for s in raw.split("+") if s]
    return out or ([cfg.rotation_cash_symbol] if cfg.rotation_cash_symbol else [])


# Per-frame numpy cache for the hot path (momentum / SMA / realized-vol lookups).
# Keyed by id(close); each entry holds the positional date map and, per symbol,
# the raw value array plus its first-valid position (frames are ffill'd, so the
# only NaNs are a leading block before the symbol started trading).
_ARR_CACHE: Dict[int, dict] = {}


def _arrays(close: pd.DataFrame) -> dict:
    key = id(close)
    cached = _ARR_CACHE.get(key)
    if cached is not None and cached["nrows"] == len(close.index):
        return cached
    pos_map = {ts: i for i, ts in enumerate(close.index)}
    cols = {}
    for s in close.columns:
        arr = close[s].to_numpy(dtype="float64")
        valid = np.where(~np.isnan(arr))[0]
        first = int(valid[0]) if len(valid) else len(arr)
        cols[s] = (arr, first)
    cached = {"pos_map": pos_map, "cols": cols, "nrows": len(close.index)}
    _ARR_CACHE[key] = cached
    return cached


def _above_sma(close: pd.DataFrame, sym: str, asof: pd.Timestamp, window: int) -> bool:
    """True if `sym`'s last close at `asof` is at/above its `window`-day SMA.

    If there isn't enough history yet, returns True (don't filter on missing data).
    Causal: only data up to `asof` is used.
    """
    if sym not in close.columns:
        return False
    cache = _arrays(close)
    pos = cache["pos_map"].get(asof)
    if pos is None:
        return True
    arr, first = cache["cols"][sym]
    if pos - first + 1 < window:
        return True
    return float(arr[pos]) >= float(arr[pos - window + 1:pos + 1].mean())


def momentum_scores(close: pd.DataFrame, symbols: List[str], asof: pd.Timestamp,
                    lookbacks: Tuple[int, ...], skip: int = 0) -> pd.Series:
    """Blended total-return momentum (mean of per-lookback returns), high → strong.

    Only data up to and including `asof` is used, so there is no look-ahead:
    the close observed on the rebalance day is the close we trade at.

    `skip` implements 12-1 ("skip-month") momentum: the most recent `skip` days
    are excluded from the measurement window to avoid short-term mean reversion.
    skip=0 reproduces the original blended momentum exactly.
    """
    cache = _arrays(close)
    pos = cache["pos_map"].get(asof)
    if pos is None:
        # asof not on the index (shouldn't happen in the loop) — safe fallback.
        pos = int(np.searchsorted(close.index.values, np.datetime64(asof), side="right")) - 1
    cols = cache["cols"]
    need = max(lookbacks) + skip
    mx = max(lookbacks)
    scores: Dict[str, float] = {}
    for s in symbols:
        cs = cols.get(s)
        if cs is None:
            continue
        arr, first = cs
        # length of the dropna'd series at `pos` is (pos - first + 1); require > need
        if pos - first < need or pos - mx - skip < first:
            continue
        num = arr[pos - skip]
        rets = [num / arr[pos - lb - skip + 1] - 1.0 for lb in lookbacks]
        scores[s] = float(np.mean(rets))
    return pd.Series(scores).sort_values(ascending=False)


def momentum_scores_ema(close: pd.DataFrame, symbols: List[str], asof: pd.Timestamp,
                        lookbacks: Tuple[int, ...], ema_span: int = 1) -> pd.Series:
    """Like momentum_scores() but smooths each symbol's rolling momentum with an EMA.

    ema_span=1 reproduces momentum_scores() exactly (no smoothing).
    ema_span=9 is the daily-champion setting: takes the last `ema_span` days of
    232-day momentum readings and exponentially smooths them, cutting day-to-day
    noise that causes spurious rotations.
    """
    cache = _arrays(close)
    cols = cache["cols"]
    pos = cache["pos_map"].get(asof)
    if pos is None:
        pos = int(np.searchsorted(close.index.values, np.datetime64(asof), side="right")) - 1

    mx = max(lookbacks)
    scores: Dict[str, float] = {}
    for s in symbols:
        cs = cols.get(s)
        if cs is None:
            continue
        arr, first = cs
        if pos - first < mx:
            continue
        # Build a short window of raw momentum readings ending at `pos`, then EMA-smooth.
        window_size = max(ema_span, 1)
        raw_series = []
        for lag in range(window_size - 1, -1, -1):
            p = pos - lag
            if p - mx < first:
                continue
            rets = [arr[p] / arr[p - lb + 1] - 1.0 for lb in lookbacks if p - lb + 1 >= first]
            if rets:
                raw_series.append(float(np.mean(rets)))
        if not raw_series:
            continue
        if len(raw_series) == 1 or ema_span <= 1:
            scores[s] = raw_series[-1]
        else:
            # exponential smoothing: α = 2/(span+1)
            alpha = 2.0 / (ema_span + 1)
            ema = raw_series[0]
            for v in raw_series[1:]:
                ema = alpha * v + (1 - alpha) * ema
            scores[s] = ema
    return pd.Series(scores).sort_values(ascending=False)


def select_targets(close: pd.DataFrame, cfg: Config,
                   asof: pd.Timestamp) -> List[str]:
    """Pick the symbols to hold this month.

    Baseline (all v2 flags off): top-N positive-momentum sectors, parking the
    remainder in the cash symbol when fewer than N qualify — identical to the
    original rule. The optional v2 filters layer on top in this order:
      regime overlay → per-sector trend filter → abs/dual momentum gate →
      cluster de-duplication → take top-N → fill empty slots defensively.
    """
    skip = getattr(cfg, "rotation_skip_days", 0)
    dl = [s for s in _defensive_list(cfg) if s in close.columns]
    top_n = cfg.rotation_top_n

    # Regime overlay: SPY below its SMA → risk-off, hold the defensive sleeve.
    if getattr(cfg, "rotation_regime_filter", False):
        if not _above_sma(close, cfg.regime_symbol, asof,
                          getattr(cfg, "rotation_regime_sma", 200)):
            if dl:
                return [dl[i % len(dl)] for i in range(top_n)]
            return []

    ema_span = getattr(cfg, "rotation_signal_ema", 1)
    if ema_span > 1:
        ranked = momentum_scores_ema(close, cfg.rotation_universe, asof,
                                     cfg.rotation_lookbacks, ema_span)
    else:
        ranked = momentum_scores(close, cfg.rotation_universe, asof,
                                 cfg.rotation_lookbacks, skip)

    # NEW FACTOR (selection): rank by risk-adjusted momentum (mom / vol) instead
    # of raw momentum. Dividing by positive vol preserves sign, so the >0 gate
    # below still works. This favors sectors trending up *smoothly*.
    if getattr(cfg, "rotation_rank_metric", "mom") == "sharpe" and len(ranked):
        fw = getattr(cfg, "rotation_factor_window", 63)
        adj = {}
        for s in ranked.index:
            v = _realized_vol(close, s, asof, fw)
            adj[s] = (float(ranked[s]) / v) if v > 0 else float(ranked[s])
        ranked = pd.Series(adj).sort_values(ascending=False)

    # Per-sector trend filter: must be above its own SMA.
    if getattr(cfg, "rotation_trend_filter", False):
        w = getattr(cfg, "rotation_trend_sma", 200)
        mask = [_above_sma(close, s, asof, w) for s in ranked.index]
        ranked = ranked[mask]

    # Absolute / dual momentum gate.
    if getattr(cfg, "rotation_dual_momentum", False):
        spy_s = momentum_scores(close, [cfg.regime_symbol], asof,
                                cfg.rotation_lookbacks, skip)
        spy_mom = float(spy_s.iloc[0]) if len(spy_s) else 0.0
        ranked = ranked[ranked > spy_mom]
    elif cfg.rotation_abs_momentum:
        ranked = ranked[ranked > 0.0]

    # Cluster de-duplication: keep only the strongest ETF from each cluster.
    if getattr(cfg, "rotation_cluster_filter", False):
        seen, keep = set(), []
        for s in ranked.index:
            c = _CLUSTERS.get(s, s)
            if c in seen:
                continue
            seen.add(c)
            keep.append(s)
        ranked = ranked[keep]

    picks = list(ranked.index[: top_n])

    # Fill empty slots with the defensive sleeve when not enough qualify.
    empty_slots = top_n - len(picks)
    if empty_slots > 0 and dl:
        picks += [dl[i % len(dl)] for i in range(empty_slots)]
    return picks


def momentum_weights(close: pd.DataFrame, cfg: Config, asof: pd.Timestamp,
                     targets: List[str]) -> Optional[Dict[str, float]]:
    """Weights ∝ momentum score (floored, renormalized) for the held sleeve.

    Returns a per-symbol weight dict summing to ~1.0, or None to fall back to
    equal weight. The defensive/cash symbol is scored 0 (gets the floor).
    """
    uniq = list(dict.fromkeys(targets))
    if not uniq:
        return None
    skip = getattr(cfg, "rotation_skip_days", 0)
    dset = set(_defensive_list(cfg))
    scored = momentum_scores(close, [s for s in uniq if s not in dset], asof,
                             cfg.rotation_lookbacks, skip)
    floor = getattr(cfg, "rotation_weight_floor", 0.15)
    scheme = getattr(cfg, "rotation_weight_scheme", "momentum")
    fw = getattr(cfg, "rotation_factor_window", 63)

    def _raw(s: str) -> float:
        # NEW FACTOR (sizing): blend momentum with the risk structure (vol).
        if s in dset:
            return 0.0
        mom = max(float(scored.get(s, 0.0)), 0.0)
        if scheme == "momentum":
            return mom
        v = _realized_vol(close, s, asof, fw)
        if v <= 0:
            return mom  # not enough data → fall back to momentum sizing
        if scheme == "invvol":
            return 1.0 / v                      # risk parity: size by 1/vol
        if scheme == "sharpe":
            return mom / v                      # size by realized risk-adjusted momentum
        if scheme == "rp_blend":
            return (mom * (1.0 / v)) ** 0.5      # geometric blend of momentum & 1/vol
        return mom

    base = {s: _raw(s) for s in uniq}
    tot = sum(base.values())
    if tot <= 0:
        return {s: 1.0 / len(uniq) for s in uniq}
    w = {s: base[s] / tot for s in uniq}
    w = {s: max(wt, floor) for s, wt in w.items()}
    t2 = sum(w.values())
    return {s: wt / t2 for s, wt in w.items()}


def _realized_vol(close: pd.DataFrame, sym: str, asof: pd.Timestamp, window: int) -> float:
    """Annualized realized vol of `sym`'s daily returns over the trailing `window`
    days, using only data up to `asof` (causal). Returns 0.0 if unavailable."""
    if sym not in close.columns:
        return 0.0
    cache = _arrays(close)
    pos = cache["pos_map"].get(asof)
    if pos is None:
        return 0.0
    arr, first = cache["cols"][sym]
    if pos - first + 1 < window + 2:
        return 0.0
    seg = arr[pos - window:pos + 1]
    rets = np.diff(seg) / seg[:-1]
    if len(rets) < 2:
        return 0.0
    sd = float(np.std(rets, ddof=1))
    if sd == 0:
        return 0.0
    return sd * np.sqrt(252.0)


def _basket_realized_vol(close: pd.DataFrame, basket: List[str], asof: pd.Timestamp,
                         window: int) -> float:
    """Annualized realized vol of the EQUAL-WEIGHT basket's daily returns over the
    trailing `window` days (causal). Same convention as `_realized_vol` so it is
    directly comparable to the vol_target. Returns 0.0 if unavailable."""
    cache = _arrays(close)
    pos = cache["pos_map"].get(asof)
    if pos is None:
        return 0.0
    rows = []
    for s in basket:
        col = cache["cols"].get(s)
        if col is None:
            continue
        arr, first = col
        if pos - first + 1 < window + 2:
            continue
        seg = arr[pos - window:pos + 1]
        rows.append(np.diff(seg) / seg[:-1])
    if not rows:
        return 0.0
    basket_ret = np.vstack(rows).mean(axis=0)   # equal-weight daily basket return
    if len(basket_ret) < 2:
        return 0.0
    sd = float(np.std(basket_ret, ddof=1))
    return sd * np.sqrt(252.0) if sd > 0 else 0.0


def _cap_weights(w: Dict[str, float], cap: float) -> Dict[str, float]:
    """Cap any single weight at `cap`, pushing the excess onto uncapped names.
    Iterative so the result still sums to ~1.0 with no weight above the cap."""
    if cap >= 1.0 or not w:
        return w
    w = dict(w)
    for _ in range(20):
        over = {s: v for s, v in w.items() if v > cap + 1e-9}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for s in over:
            w[s] = cap
        under = {s: v for s, v in w.items() if v < cap - 1e-9}
        pool = sum(under.values())
        if pool <= 0:
            break
        for s in under:
            w[s] += excess * (under[s] / pool)
    return w


def rebalance_weights(close: pd.DataFrame, cfg: Config, asof: pd.Timestamp,
                      targets: List[str]) -> Optional[Dict[str, float]]:
    """Final per-symbol target weights, honoring momentum-weighting, a position
    cap, and a volatility-target overlay. Returns None to mean plain equal-weight
    (the original behavior) when no weighting feature is active.
    """
    use_mom = (getattr(cfg, "rotation_momentum_weight", False)
               or getattr(cfg, "rotation_weight_scheme", "momentum") != "momentum")
    cap = getattr(cfg, "rotation_position_cap", 1.0)
    vt = getattr(cfg, "rotation_vol_target", 0.0)
    if not (use_mom or cap < 1.0 or vt > 0.0):
        return None
    if not targets:
        return None

    if use_mom:
        w = momentum_weights(close, cfg, asof, targets) or {}
    else:
        uniq = list(dict.fromkeys(targets))
        w = {s: 1.0 / len(uniq) for s in uniq}

    if cap < 1.0:
        w = _cap_weights(w, cap)

    # Volatility-target overlay: de-lever the equity sleeve toward `vt`, parking
    # the freed capital in the defensive asset. Never levers above 1.0.
    if vt > 0.0:
        rv = _realized_vol(close, getattr(cfg, "rotation_vol_proxy", "SPY"), asof,
                           getattr(cfg, "rotation_vol_window", 20))
        if rv > 0:
            expo = min(1.0, vt / rv)
            dl = [s for s in _defensive_list(cfg) if s in close.columns]
            if expo < 1.0 and dl:
                w = {s: v * expo for s, v in w.items()}
                share = (1.0 - expo) / len(dl)
                for d in dl:
                    w[d] = w.get(d, 0.0) + share
    return w


def rebalance_to(broker: PaperBroker, date_str: str, targets: List[str],
                 prices: Dict[str, float],
                 weights: Optional[Dict[str, float]] = None) -> None:
    """Move the book to a target allocation of `targets`.

    `weights` (if given) is a per-symbol weight dict summing to ~1.0 — used by
    momentum-weighted sizing. When None, the allocation is equal-weight across
    `targets` (duplicate cash entries collapse into one larger weight), which is
    the original behavior.

    Incremental: only the *difference* between current and desired holdings is
    traded, so we don't pay slippage churning a position we're keeping. Sells run
    before buys to free up cash.
    """
    if not targets:
        # No eligible target at all (no cash symbol available) → go fully to cash.
        for sym in list(broker.positions.keys()):
            if sym in prices:
                broker.sell(date_str, sym, prices[sym], "ROTATION → cash (no targets)")
        return

    # 0.5% buffer so per-side slippage on buys never overruns available cash.
    equity = broker.equity(prices) * 0.995
    if weights:
        target_weight: Dict[str, float] = dict(weights)
    else:
        weight = 1.0 / len(targets)
        # Count duplicates (cash symbol can appear more than once) into one weight.
        target_weight = {}
        for sym in targets:
            target_weight[sym] = target_weight.get(sym, 0.0) + weight

    desired_shares: Dict[str, float] = {}
    for sym, w in target_weight.items():
        px = prices.get(sym)
        if px and px > 0:
            desired_shares[sym] = (equity * w) / px

    # Sells first (positions we're dropping, or trimming below target).
    for sym in list(broker.positions.keys()):
        px = prices.get(sym)
        if px is None:
            continue
        held = broker.positions[sym].shares
        want = desired_shares.get(sym, 0.0)
        if want < held:
            reason = ("ROTATION exit" if want == 0.0 else "ROTATION trim")
            broker.sell(date_str, sym, px, reason, shares=held - want)

    # Buys second (new targets, or topping up below target).
    for sym, want in desired_shares.items():
        px = prices.get(sym)
        if px is None:
            continue
        held = broker.positions[sym].shares if sym in broker.positions else 0.0
        if want > held:
            broker.buy(date_str, sym, px, want - held,
                       "ROTATION enter/add", _NO_STOP, _NO_TP, "High")


class _CombinedBook:
    """Aggregates several tranche sub-brokers into one equity curve + trade log
    so metrics.summarize() and the CLI treat the whole strategy as one account."""

    def __init__(self):
        self.equity_curve: List[tuple] = []
        self.trades: List = []

    @staticmethod
    def positions_snapshot(brokers: List[PaperBroker],
                           prices: Dict[str, float]) -> Dict[str, float]:
        """Combined dollar value held per symbol across all tranches."""
        agg: Dict[str, float] = {}
        for b in brokers:
            for s, p in b.positions.items():
                agg[s] = agg.get(s, 0.0) + p.market_value(prices.get(s, p.avg_price))
        return agg


def _two_way_vol_scale(close: pd.DataFrame, asof: pd.Timestamp, cfg: Config,
                       basket: Optional[List[str]] = None) -> float:
    """Two-way exposure scale = target_vol / realized_vol, clipped to [floor, cap].

    V5 asymmetric cap: if SPY is below its 200-day SMA (bear regime), the cap is
    reduced to rotation_vol_cap_bear (default 1.5x) regardless of rotation_vol_cap.
    This prevents the system from reaching into 3x leverage during structural
    downtrends — exactly when leveraged ETFs suffer the worst decay.

    scale > 1 → lever up (calm market)   scale < 1 → de-lever (stressed market)
    scale = 1 → neutral (vol exactly at target)
    """
    target = getattr(cfg, "rotation_vol_target", 0.15)
    window = getattr(cfg, "rotation_vol_window", 20)
    cap    = getattr(cfg, "rotation_vol_cap", 2.0)
    floor_ = getattr(cfg, "rotation_vol_floor", 0.50)
    proxy  = getattr(cfg, "rotation_vol_proxy", "SPY")

    pos = close.index.get_loc(asof) if asof in close.index else -1

    def _below_sma(win: int) -> bool:
        """True if proxy (SPY) closed below its `win`-day SMA as of `asof`."""
        if win <= 0 or proxy not in close.columns or pos < win:
            return False
        price = float(close.iloc[pos][proxy])
        sma   = float(close[proxy].iloc[pos - win: pos].mean())
        return price < sma

    # ── V5 asymmetric cap: bear regime (SPY < 200-SMA) uses the lower cap. ──
    # cap_bear may be 0.0 (full risk-off vault → 100% defensive). When the bear
    # cap drops below the floor, the floor must follow it, otherwise np.clip with
    # floor > cap is undefined. A cap of 0.0 yields scale 0 → all capital parks
    # in the GLD+TLT sleeve inside turbo_allocation.
    cap_bear = getattr(cfg, "rotation_vol_cap_bear", cap)
    sma_win  = getattr(cfg, "rotation_vol_cap_sma", 0)
    if cap_bear < cap and _below_sma(sma_win):
        cap = cap_bear

    # ── 50-day circuit breaker: early de-lever when SPY < its 50-day SMA, ──
    # before 20-day realized vol has had time to spike. Caps exposure at
    # rotation_breaker_level (e.g. 1.0x or 0.5x) regardless of the vol target.
    brk_win   = getattr(cfg, "rotation_breaker_sma", 0)
    brk_level = getattr(cfg, "rotation_breaker_level", 1.0)
    if brk_win > 0 and _below_sma(brk_win):
        cap = min(cap, brk_level)

    # Floor can never exceed the (possibly lowered) cap.
    floor_ = min(floor_, cap)

    # ── basket-vol sizing (default off): size leverage to the HELD picks' own ──
    # volatility instead of the SPY proxy. A 2x-semis sleeve is ~3-4x as volatile
    # as SPY, so SPY-sizing under-prices its risk; basket sizing de-levers
    # volatile sleeves correctly. Regime (bear cap above) STAYS on SPY.
    if getattr(cfg, "rotation_basket_vol", False) and basket:
        rv = _basket_realized_vol(close, basket, asof, window)
        if rv <= 0:                       # fall back to proxy if basket vol unavailable
            rv = _realized_vol(close, proxy, asof, window)
    else:
        rv = _realized_vol(close, proxy, asof, window)

    scale = min(1.0, cap) if rv <= 0 else float(np.clip(target / rv, floor_, cap))

    # ── VIX gate (default off): de-lever on FORWARD-priced tail risk (implied ──
    # vol), before realized vol or the 200-SMA react. Two-step cap.
    if getattr(cfg, "rotation_vix_gate", False):
        vsym = getattr(cfg, "rotation_vix_symbol", "^VIX")
        if vsym in close.columns and pos >= 0:
            vv = close.iloc[pos][vsym]
            if not (isinstance(vv, float) and np.isnan(vv)):
                vv = float(vv)
                if vv >= getattr(cfg, "rotation_vix_hi", 35.0):
                    scale = min(scale, getattr(cfg, "rotation_vix_hi_cap", 0.5))
                elif vv >= getattr(cfg, "rotation_vix_lo", 25.0):
                    scale = min(scale, getattr(cfg, "rotation_vix_lo_cap", 1.0))
    return scale


def turbo_allocation(close: pd.DataFrame, cfg: Config, asof: pd.Timestamp,
                     targets: List[str],
                     lev_close: Optional[pd.DataFrame] = None,
                     lev3x_close: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    """Compute the final allocation dict for V4 Turbo and V5 Hyper-Drive.

    Weighting modes (mutually exclusive, checked in priority order):
      rotation_weight_squared=True  → V5: weight ∝ R_i² / ΣR²  (stronger leader concentration)
      rotation_return_prop=True     → V4: weight ∝ R_i / ΣR     (proportional)
      neither                       → equal weight per slot

    Leverage tiers (V5 three-tier system):
      scale > 2 AND rotation_use_3x=True AND 3x ETF available
            → capital = w × scale / 3  (one-third the dollars, triple the exposure)
      scale > 1 AND 2x ETF available
            → capital = w × scale / 2  (half the dollars, double the exposure)
      scale ≤ 1
            → capital = w × scale      (1x, de-levered; rest parks in defensive)

    The asymmetric vol cap (V5 feature) is applied inside _two_way_vol_scale:
      SPY > 200 SMA → use rotation_vol_cap (default 3.0)
      SPY < 200 SMA → use rotation_vol_cap_bear (default 1.5)

    Returns a {symbol: weight} dict summing to ~1.0. Symbols may be 2x/3x ETF
    tickers (ROM, QLD, TECL, TQQQ, etc.) when leverage is active.
    """
    use_sq     = getattr(cfg, "rotation_weight_squared", False)
    use_rp     = getattr(cfg, "rotation_return_prop", False) and not use_sq
    use_twoway = getattr(cfg, "rotation_two_way_vol", False)
    use_3x     = getattr(cfg, "rotation_use_3x", False)

    dl      = _defensive_list(cfg)
    def_set = set(dl)
    n_total = len(targets)

    equity_picks = [s for s in targets if s not in def_set]
    def_picks    = [s for s in targets if s in def_set]
    n_equity     = len(equity_picks)

    # --- Step 1: sector weights (before scale) ---
    if equity_picks:
        scores = momentum_scores(close, equity_picks, asof, cfg.rotation_lookbacks)
        if use_sq:
            # Momentum-squared: concentrates harder into the leader
            raw = {s: max(float(scores.get(s, 0.0)), 0.0) ** 2 for s in equity_picks}
        elif use_rp:
            # Return-proportional (V4 Turbo A)
            raw = {s: max(float(scores.get(s, 0.0)), 0.0) for s in equity_picks}
        else:
            raw = {s: 1.0 for s in equity_picks}   # equal weight
        tot_raw = sum(raw.values())
        base_equity_w = ({s: raw[s] / tot_raw for s in equity_picks} if tot_raw > 0
                         else {s: 1.0 / n_equity for s in equity_picks})
        # Per-sector diversification cap: no single sector exceeds rotation_position_cap.
        # Excess is redistributed to the other held sectors (keeps sum ~1.0).
        pos_cap = getattr(cfg, "rotation_position_cap", 1.0)
        if pos_cap < 1.0:
            base_equity_w = _cap_weights(base_equity_w, pos_cap)
    else:
        base_equity_w = {}

    slot_w        = 1.0 / n_total if n_total else 0.0
    equity_budget = slot_w * n_equity
    if base_equity_w:
        raw_sum  = sum(base_equity_w.values())
        sector_w = {s: v / raw_sum * equity_budget for s, v in base_equity_w.items()}
    else:
        sector_w = {}
    def_budget = slot_w * len(def_picks)

    # --- Step 2: vol scale (asymmetric cap applied inside) ---
    if use_twoway:
        scale = _two_way_vol_scale(close, asof, cfg, basket=equity_picks)
    else:
        vt = getattr(cfg, "rotation_vol_target", 0.0)
        if vt > 0.0:
            rv = _realized_vol(close, getattr(cfg, "rotation_vol_proxy", "SPY"), asof,
                               getattr(cfg, "rotation_vol_window", 20))
            scale = min(1.0, vt / rv) if rv > 0 else 1.0
        else:
            scale = 1.0

    # --- Step 3: apply scale with 3-tier leverage routing ---
    lev_avail  = set(lev_close.columns)   if lev_close   is not None else set()
    lev3x_avail = set(lev3x_close.columns) if lev3x_close is not None else set()
    final: Dict[str, float] = {}
    sector_capital_used = 0.0

    if scale > 1.0:
        for s, w in sector_w.items():
            allocated = False

            # Tier 3: try 3x ETF when scale > 2 and use_3x is enabled
            if use_3x and scale > 2.0 and lev3x_close is not None:
                lev3 = LEV3X_MAP.get(s)
                if lev3 and lev3 in lev3x_avail and asof in lev3x_close.index:
                    v = lev3x_close.loc[asof, lev3]
                    if not (isinstance(v, float) and np.isnan(v)):
                        cap_alloc = w * scale / 3.0   # ⅓ capital → 3× exposure
                        final[lev3] = final.get(lev3, 0.0) + cap_alloc
                        sector_capital_used += cap_alloc
                        allocated = True

            # Tier 2: try 2x ETF (fallback from 3x, or primary when scale ≤ 2)
            if not allocated and lev_close is not None:
                lev2 = LEV2X_MAP.get(s)
                if lev2 and lev2 in lev_avail and asof in lev_close.index:
                    v = lev_close.loc[asof, lev2]
                    if not (isinstance(v, float) and np.isnan(v)):
                        cap_alloc = w * scale / 2.0   # ½ capital → 2× exposure
                        final[lev2] = final.get(lev2, 0.0) + cap_alloc
                        sector_capital_used += cap_alloc
                        allocated = True

            # Tier 1: hold 1x at full unscaled weight (no leveraged ETF available)
            if not allocated:
                final[s] = final.get(s, 0.0) + w
                sector_capital_used += w
    else:
        for s, w in sector_w.items():
            alloc = w * scale
            final[s] = final.get(s, 0.0) + alloc
            sector_capital_used += alloc

    # --- Step 4: defensive sleeve absorbs freed capital ---
    total_def = max(0.0, 1.0 - sector_capital_used)
    if total_def > 0.001 and dl:
        avail_dl = [s for s in dl if s in close.columns]
        if avail_dl:
            per = total_def / len(avail_dl)
            for d in avail_dl:
                final[d] = final.get(d, 0.0) + per

    # --- Step 5: normalize to 1.0 ---
    tot = sum(final.values())
    if tot > 0:
        final = {s: v / tot for s, v in final.items()}
    return final


def run_rotation_backtest(cfg: Config, verbose: bool = False) -> Dict:
    """Event-driven monthly sector rotation with tranched rebalancing.

    The book is split into ``len(cfg.rotation_tranches)`` equal sub-portfolios,
    each rebalanced monthly on its own trading-day-of-month offset. Splitting the
    rebalance across several days removes the single-day timing luck that makes a
    one-day monthly rule swing ~11–19% CAGR depending on which day you happen to
    pick. The combined equity is the sum of the tranches.

    Returns {broker, cfg, benchmark} where ``broker`` is a combined view exposing
    ``equity_curve`` and ``trades`` for metrics.summarize().
    """
    data = get_data_source("yfinance")
    use_turbo = (getattr(cfg, "rotation_return_prop", False)
                 or getattr(cfg, "rotation_two_way_vol", False))

    symbols = list(dict.fromkeys(
        cfg.rotation_universe
        + ([cfg.rotation_cash_symbol] if cfg.rotation_cash_symbol else [])
        + _defensive_list(cfg)
        + [cfg.regime_symbol]  # SPY, for the benchmark column
        + ([getattr(cfg, "rotation_vix_symbol", "^VIX")]
           if getattr(cfg, "rotation_vix_gate", False) else [])  # VIX gate input (not ranked)
    ))
    # When using two-way vol + leveraged ETFs, also fetch 2x and/or 3x universe.
    lev_syms: List[str] = []
    lev3x_syms: List[str] = []
    if getattr(cfg, "rotation_two_way_vol", False):
        lev_syms = list(dict.fromkeys(
            v for k, v in LEV2X_MAP.items()
            if k in symbols or k in cfg.rotation_universe
        ))
        symbols = list(dict.fromkeys(symbols + lev_syms))
    if getattr(cfg, "rotation_use_3x", False):
        lev3x_syms = list(dict.fromkeys(
            v for k, v in LEV3X_MAP.items()
            if k in cfg.rotation_universe
        ))
        symbols = list(dict.fromkeys(symbols + lev3x_syms))

    # Pull extra history before the start so the 12-month lookback is warm on day 1.
    warmup_start = (pd.Timestamp(cfg.backtest_start)
                    - pd.Timedelta(days=cfg.rotation_warmup_days)).strftime("%Y-%m-%d")
    raw = data.history(symbols, warmup_start, cfg.backtest_end)
    if not raw:
        raise RuntimeError("No historical data returned.")

    close = _close_frame(raw)
    # Drop universe symbols with no data so ranking never sees a NaN column.
    cfg.rotation_universe = [s for s in cfg.rotation_universe if s in close.columns]

    # Separate 2x and 3x ETF frames (used for allocation only, not ranking)
    lev_close: Optional[pd.DataFrame] = None
    if lev_syms:
        avail = [s for s in lev_syms if s in close.columns]
        lev_close = close[avail].copy() if avail else None

    lev3x_close: Optional[pd.DataFrame] = None
    if lev3x_syms:
        avail3 = [s for s in lev3x_syms if s in close.columns]
        lev3x_close = close[avail3].copy() if avail3 else None

    dates = close.index[(close.index >= cfg.backtest_start)
                        & (close.index <= cfg.backtest_end)]

    tranches = list(cfg.rotation_tranches) or [0]
    n = len(tranches)
    # One sub-broker per tranche, each funded with an equal share of the capital.
    brokers: List[PaperBroker] = []
    rebal_sets: List[set] = []
    for offset in tranches:
        b = PaperBroker(cfg)
        b.cash = cfg.starting_cash / n
        brokers.append(b)
        rebal_sets.append(set(_rebalance_dates(close.index, cfg.backtest_start,
                                               cfg.backtest_end, offset)))

    book = _CombinedBook()
    started = [False] * n
    for dt in dates:
        prices = {s: float(close.loc[dt, s]) for s in close.columns
                  if not pd.isna(close.loc[dt, s])}
        # Overlay 2x ETF prices so rebalance_to can fill orders for leveraged tickers.
        if lev_close is not None and dt in lev_close.index:
            for s in lev_close.columns:
                v = lev_close.loc[dt, s]
                if not pd.isna(v):
                    prices[s] = float(v)
        # Overlay 3x ETF prices (V5)
        if lev3x_close is not None and dt in lev3x_close.index:
            for s in lev3x_close.columns:
                v = lev3x_close.loc[dt, s]
                if not pd.isna(v):
                    prices[s] = float(v)
        ds = dt.strftime("%Y-%m-%d")

        for i, b in enumerate(brokers):
            if not started[i] or dt in rebal_sets[i]:
                started[i] = True
                if use_turbo:
                    targets  = select_targets(close, cfg, dt)
                    weights  = turbo_allocation(close, cfg, dt, targets,
                                               lev_close, lev3x_close)
                else:
                    targets  = select_targets(close, cfg, dt)
                    weights  = rebalance_weights(close, cfg, dt, targets)
                rebalance_to(b, ds, list(weights.keys()) if weights else targets,
                             prices, weights)

        total_eq = sum(b.equity(prices) for b in brokers)
        book.equity_curve.append((ds, total_eq))
        if verbose and any((dt in rs) for rs in rebal_sets):
            held = book.positions_snapshot(brokers, prices)
            held = {s: round(v) for s, v in sorted(held.items(), key=lambda kv: -kv[1])}
            print(f"{ds} REBALANCE  equity=${total_eq:,.0f}  holdings={held}")

    for b in brokers:
        book.trades.extend(b.trades)

    benchmark = _spy_buy_hold(close, cfg)
    return {"broker": book, "cfg": cfg, "benchmark": benchmark, "tranche_brokers": brokers}


def _spy_buy_hold(close: pd.DataFrame, cfg: Config) -> Dict:
    """Same-window, same-data SPY buy-and-hold for an apples-to-apples benchmark."""
    spy = close[cfg.regime_symbol]
    spy = spy[(spy.index >= cfg.backtest_start) & (spy.index <= cfg.backtest_end)]
    if spy.empty:
        return {}
    shares = cfg.starting_cash / float(spy.iloc[0])
    curve = [(d.strftime("%Y-%m-%d"), shares * float(p)) for d, p in spy.items()]
    final = curve[-1][1]
    years = max((spy.index[-1] - spy.index[0]).days / 365.25, 1e-9)
    cagr = (final / cfg.starting_cash) ** (1.0 / years) - 1.0
    # Max drawdown on the SPY curve.
    peak, mdd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e)
        mdd = min(mdd, (e - peak) / peak)
    return {"final_equity": final, "cagr": cagr, "max_drawdown": mdd,
            "total_return": final / cfg.starting_cash - 1.0, "equity_curve": curve}
