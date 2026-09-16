"""Performance metrics computed from an equity curve and trade log."""

from __future__ import annotations

import math
from typing import Dict, List, Optional

def norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF via Acklam's rational approximation with Halley refinement."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")
    
    # Coefficients for rational approximation
    a = [
        -3.969683028665376e1,
         2.209460984245205e2,
        -2.759285104469687e2,
         1.383577518672690e2,
        -3.066479806614716e1,
         2.506628277459239e0,
    ]
    b = [
        -5.447609879822406e1,
         1.615858368580409e2,
        -1.556989798598866e2,
         6.680131188771972e1,
        -1.328068155288572e1,
    ]
    c = [
        -7.784894002430293e-3,
        -3.223964580411365e-1,
        -2.400758277161838e0,
        -2.549732539343734e0,
         4.374664141464968e0,
         2.938163982698783e0,
    ]
    d = [
         7.784695709041462e-3,
         3.224671290700398e-1,
         2.445134137142996e0,
         3.754408661907416e0,
    ]
    
    # Define the break points
    p_low = 0.02425
    p_high = 1.0 - p_low
    
    # Rational approximation for lower region
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    # Rational approximation for central region
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
            (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)
    # Rational approximation for upper region
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
             ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    
    # One step of Halley refinement
    err = norm_cdf(x) - p
    e1 = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    x_new = x - err / e1  # Newton step
    e2 = math.exp(-0.5 * x_new * x_new) / math.sqrt(2.0 * math.pi)
    x = x_new - (norm_cdf(x_new) - p) / e2  # Halley step
    
    return x

def _expected_max_z(n_trials: int) -> float:
    """Expected maximum standard normal over n_trials (shared helper)."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    EULER = 0.5772156649015329
    if n_trials == 1:
        return 0.0
    return ((1.0 - EULER) * norm_ppf(1.0 - 1.0 / n_trials)
            + EULER * norm_ppf(1.0 - 1.0 / (n_trials * math.e)))

def probabilistic_sharpe(returns: List[float], benchmark_skill: float) -> float:
    """Bailey & Lopez de Prado (2012) Probabilistic Sharpe Ratio."""
    n = len(returns)
    if n < 4:
        raise ValueError("returns must have at least 4 observations")
    if not returns:
        raise ValueError("returns list is empty")
    
    mean = sum(returns) / n
    m2 = sum((x - mean) ** 2 for x in returns) / n
    m3 = sum((x - mean) ** 3 for x in returns) / n
    m4 = sum((x - mean) ** 4 for x in returns) / n
    
    if m2 == 0.0:
        raise ValueError("variance is zero")
    
    sr_hat = mean / math.sqrt(m2)
    
    # Bias-corrected skew (g3)
    g1 = m3 / (m2 ** 1.5)
    g3 = g1 * math.sqrt(n * (n - 1)) / (n - 2)
    
    # Bias-corrected kurtosis (g4) – not excess
    excess_biased = m4 / (m2 ** 2) - 3.0
    G2 = ((n + 1) * excess_biased + 6.0) * (n - 1) / ((n - 2) * (n - 3))
    g4 = G2 + 3.0
    
    denom = 1.0 - g3 * sr_hat + (g4 - 1.0) / 4.0 * sr_hat ** 2
    if denom <= 0.0:
        raise ValueError("denominator in PSR must be positive")
    
    return norm_cdf((sr_hat - benchmark_skill) * math.sqrt(n - 1) / math.sqrt(denom))

def deflated_sharpe(returns: List[float], n_trials: int, var_sharpe: float) -> float:
    """
    Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio.
    
    Parameters
    ----------
    returns : list of float
        Daily (or per-observation) returns.
    n_trials : int
        Number of strategy configurations searched.
    var_sharpe : float
        Variance of Sharpe ratios across the searched configurations,
        in per-observation units (i.e. non-annualised).
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if var_sharpe < 0.0:
        raise ValueError("var_sharpe must be non-negative")
    
    if n_trials == 1:
        return probabilistic_sharpe(returns, 0.0)
    
    expected_max_z = _expected_max_z(n_trials)
    sr_star = math.sqrt(var_sharpe) * expected_max_z
    return probabilistic_sharpe(returns, sr_star)

def min_backtest_length(n_trials: int, target_sharpe: float = 1.0) -> float:
    """Minimum backtest length in years for a given annualised Sharpe and n_trials."""
    if n_trials < 2:
        raise ValueError("n_trials must be >= 2")
    if target_sharpe <= 0.0:
        raise ValueError("target_sharpe must be > 0")
    return (_expected_max_z(n_trials) / target_sharpe) ** 2

def summarize(
    equity_curve: List[tuple],
    trades: List,
    starting_cash: float,
    n_trials: Optional[int] = None,
    var_sharpe: Optional[float] = None,
) -> Dict:
    if not equity_curve:
        return {}
    equities = [e for _, e in equity_curve]
    final = equities[-1]
    total_return = (final - starting_cash) / starting_cash

    # Daily returns
    rets = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            rets.append(equities[i] / equities[i - 1] - 1.0)

    # Derive actual calendar years from equity_curve dates
    dates = [d for d, _ in equity_curve]
    try:
        from datetime import date as _date
        def _p(s): return _date.fromisoformat(str(s)[:10])
        first, last = _p(dates[0]), _p(dates[-1])
        years = max((last - first).days / 365.25, 1 / 365.25)
        bars_per_year = len(equities) / years
    except Exception:
        bars_per_year = 52
        years = len(equities) / 52

    annualise = math.sqrt(bars_per_year)

    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var  = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std  = math.sqrt(var)
        sharpe = (mean / std * annualise) if std > 0 else 0.0

        down_rets = [r for r in rets if r < 0]
        if down_rets:
            down_var = sum(r ** 2 for r in down_rets) / len(down_rets)
            down_std = math.sqrt(down_var)
            sortino = (mean / down_std * annualise) if down_std > 0 else 0.0
        else:
            sortino = float("inf")
    else:
        sharpe = sortino = 0.0

    # Max drawdown
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        peak  = max(peak, e)
        if peak > 0:
            max_dd = min(max_dd, (e - peak) / peak)

    cagr   = (final / starting_cash) ** (1.0 / years) - 1 if years > 0 and final > 0 else 0.0
    calmar = (cagr / abs(max_dd)) if max_dd < 0 else float("inf")

    # Trade stats
    sells       = [t for t in trades if t.side == "SELL"]
    wins        = [t for t in sells if t.pnl > 0]
    losses      = [t for t in sells if t.pnl <= 0]
    win_rate    = len(wins) / len(sells) if sells else 0.0
    gross_win   = sum(t.pnl for t in wins)
    gross_loss  = -sum(t.pnl for t in losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    avg_win  = gross_win  / len(wins)   if wins   else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    avg_rr   = (avg_win / avg_loss) if avg_loss > 0 else float("inf")

    result = {
        "starting_cash":  starting_cash,
        "final_equity":   final,
        "total_return":   total_return,
        "cagr":           cagr,
        "sharpe":         sharpe,
        "sortino":        sortino,
        "calmar":         calmar,
        "max_drawdown":   max_dd,
        "num_trades":     len(trades),
        "num_closed":     len(sells),
        "win_rate":       win_rate,
        "profit_factor":  profit_factor,
        "avg_rr_achieved": avg_rr,
    }
    
    if n_trials is not None and var_sharpe is not None:
        result["n_trials"] = n_trials
        try:
            result["deflated_sharpe"] = deflated_sharpe(rets, n_trials, var_sharpe)
        except Exception:
            result["deflated_sharpe"] = None
        try:
            result["probabilistic_sharpe"] = probabilistic_sharpe(rets, 0.0)
        except Exception:
            result["probabilistic_sharpe"] = None
        try:
            annualised_sharpe = sharpe
            if annualised_sharpe > 0:
                result["min_backtest_years"] = min_backtest_length(n_trials, annualised_sharpe)
            else:
                result["min_backtest_years"] = None
        except Exception:
            result["min_backtest_years"] = None
    
    return result

def format_summary(m: Dict) -> str:
    if not m:
        return "No data."

    def _fmt(v):
        return "inf" if v == float("inf") else f"{v:.2f}"

    base = (
        f"  Starting cash   : ${m['starting_cash']:,.0f}\n"
        f"  Final equity    : ${m['final_equity']:,.0f}\n"
        f"  Total return    : {m['total_return']:+.1%}\n"
        f"  CAGR            : {m['cagr']:+.1%}\n"
        f"  Sharpe          : {_fmt(m['sharpe'])}\n"
        f"  Sortino         : {_fmt(m['sortino'])}\n"
        f"  Calmar          : {_fmt(m['calmar'])}\n"
        f"  Max drawdown    : {m['max_drawdown']:.1%}\n"
        f"  Trades          : {m['num_trades']} ({m['num_closed']} closed)\n"
        f"  Win rate        : {m['win_rate']:.0%}\n"
        f"  Profit factor   : {_fmt(m['profit_factor'])}\n"
        f"  Avg R:R achieved: {_fmt(m['avg_rr_achieved'])}"
    )
    
    if "deflated_sharpe" in m:
        n = m.get("n_trials", "?")
        ds = m.get("deflated_sharpe")
        ps = m.get("probabilistic_sharpe")
        mb = m.get("min_backtest_years")
        ds_str = f"{ds:.4f}" if ds is not None else "n/a"
        mb_str = f"{mb:.1f} yrs" if mb is not None else "n/a"
        # Build the lines separately. Chaining f-strings across an `if/else`
        # implicitly concatenates them into one literal, which silently drops
        # whichever branch is not taken.
        if ps is not None:
            base += f"\n  Probabilistic   : {ps:.4f}"
        base += f"\n  Deflated Sharpe : {ds_str}  (over {n} trials)"
        base += f"\n  Min backtest    : {mb_str}"
    
    return base