"""Monthly live checkup — the SAFE version of "constantly improving".

The strategy must NOT retune itself on live data (60-80 trades/yr is far too
small a sample; auto-tuning = chasing noise = overfitting). What works:
  1. Record the live account equity and benchmark each run.
  2. Re-run the deployed PUSH-20 config on data through TODAY.
  3. Compare live reality vs backtest expectation vs SPY.
  4. Raise explicit ALERT lines when something drifts outside expectations —
     so a HUMAN (you + Claude) decides whether to re-research.

Run: .venv/bin/python reports/live_checkup.py   (scheduled monthly via launchd)
Appends data/live_checkup_history.json, writes reports/checkup_<date>.md.
"""
from __future__ import annotations
import os, sys, json, math, warnings
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "reports"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # report text is UTF-8; Windows redirects default to cp1252

import pandas as pd

DEPLOY_DATE   = "2026-06-09"          # PUSH-20 went live
# Expectations for the config that is ACTUALLY live. Third revision of this pair,
# because the first two both described strategies that were not trading:
#
#   0.207 / -0.304  pre-GATED headline. Assumed 5bps; drawdown understated ~7pp.
#   0.197 / -0.355  came from run_rotation_backtest, which rebalances MONTHLY on
#                   tranched trading days. The live system rebalances DAILY under
#                   a 3-day hold, so this validated a different strategy entirely.
#   0.185 / -0.284  HOLDOUT v2 (2026-09-13). opt_harness.simulate on the live
#                   daily cadence, at the 9.5bps the account actually pays, with
#                   the VIX tiers, basket-vol sizing and the unlevered leftover
#                   defensive sleeve all modelled as rotation.py implements them.
#
# tests/test_checkup_matches_live.py fails if these drift apart from the flags.
EXPECT_CAGR   = 0.185                  # 2006-01-01..2026-09-02, live cadence, 9.5bps
MC_P5_CAGR    = 0.09                   # Monte Carlo 5th percentile (alt histories)
EXPECT_MDD    = -0.284                 # realized single path; MC median is still ~-0.43
# Kept at 9.5 deliberately, and it is now ABOVE what execution measures.
# Measured 2026-09-14 over 105 fills, notional-weighted: execution 3.51bps (fill
# vs the session open) and queue drift 5.27bps (fill vs the decision close).
# Lowering this to 3.5 would make the backtest look better on 105 fills with a
# ~70bp standard deviation across one quarter, so it stays where the graded
# holdout put it. 9.5 also happens to sit just above execution plus drift, which
# is the honest worst case if the overnight gap were treated as a cost.
SLIPPAGE_ASSUMED_BPS = 9.5


def live_sim_config():
    """Translate the LIVE flags into an opt_harness config.

    Derived, never hardcoded: the live strategy is edited in DAILY_CHAMPION_FLAGS,
    and a checkup that validates a different config is worse than no checkup — it
    reports "within expectations" about a strategy that is not trading. Returns
    (config, notes) where notes lists live behaviour the harness cannot model.
    """
    from trader.rotation import DAILY_CHAMPION_FLAGS as F
    lb = F["rotation_lookbacks"]
    cfg = {
        "lookback":      int(lb[0] if isinstance(lb, (list, tuple)) else lb),
        "ema_span":      int(F["rotation_signal_ema"]),
        "top_n":         int(F["rotation_top_n"]),
        "min_hold_days": int(F["rotation_min_hold_days"]),
        "vol_target":    float(F["rotation_vol_target"]),
        "vol_window":    int(F["rotation_vol_window"]),
        "vol_floor":     float(F["rotation_vol_floor"]),
        "vol_cap_bull":  float(F["rotation_vol_cap"]),
        "vol_cap_bear":  float(F["rotation_vol_cap_bear"]),
        "regime_sma":    int(F["rotation_vol_cap_sma"]),
        "max_weight":    float(F["rotation_position_cap"]),
        "defensive":     str(F["rotation_defensive_symbol"]),
        "weight_scheme": "return_prop" if F.get("rotation_return_prop") else "equal",
        "use_3x":        1 if F.get("rotation_use_3x") else 0,
    }
    notes = []
    # Costs: the harness default is 5bps. The live book fills at ~9.5. Checking a
    # 5bps backtest against a 9.5bps account is how a strategy looks healthy while
    # it quietly underperforms its own benchmark.
    cfg["cost_bps"] = SLIPPAGE_ASSUMED_BPS
    if F.get("rotation_vix_gate"):
        cfg["vix_gate_level"] = float(F["rotation_vix_lo"])
        cfg["vix_gate_cap"]   = float(F["rotation_vix_lo_cap"])
        hi_cap = float(F.get("rotation_vix_hi_cap", 1.0))
        if hi_cap < float(F["rotation_vix_lo_cap"]):
            cfg["vix_hi_level"] = float(F["rotation_vix_hi"])
            cfg["vix_hi_cap"]   = hi_cap
    if F.get("rotation_basket_vol"):
        # Modelled since 2026-09-13. Measured effect at this vol_target: +0.04pp
        # CAGR over the full window — near-inert, as the old note claimed, but now
        # it is claimed on evidence from this engine rather than asserted.
        cfg["basket_vol"] = 1
    # The defensive sleeve is not a weighted holding in turbo_allocation: it absorbs
    # whatever capital the equity legs leave, always unlevered. Without this the
    # harness gave a defensive fill a full weighted slot and could route it to
    # UGL/UBT, which the live allocator has no code path to do. Worth ~0.3pp CAGR.
    cfg["defensive_live"] = 1
    notes.append("min_hold_days suspends the risk cap as well as rotation: a VIX "
                 "spike on day 2 of a 3-day hold cannot de-lever. Modelled faithfully "
                 "here (harness does the same); it is a known property, not a bug fix.")
    return cfg, notes

HIST_PATH = os.path.join(ROOT, "data", "live_checkup_history.json")


def main():
    from opt_harness import load_data, simulate, metrics, spy_bh, FETCH_END
    from trader.alpaca_feed import AlpacaFeed
    from trader.config import load_config

    today = FETCH_END
    df = load_data()
    alerts, lines = [], []

    # ── 1. live account snapshot ─────────────────────────────────────────────
    cfg = load_config()
    feed = AlpacaFeed(cfg.alpaca_key, cfg.alpaca_secret)
    acct = feed.get_account() or {}
    live_equity = float(acct.get("equity", 0) or 0)
    spy_now = float(df["SPY"].dropna().iloc[-1])

    hist = []
    if os.path.exists(HIST_PATH):
        with open(HIST_PATH, encoding="utf-8") as fh:
            hist = json.load(fh)
    hist.append({"date": today, "alpaca_equity": live_equity, "spy": spy_now})
    with open(HIST_PATH, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, indent=1)

    lines.append(f"# Live checkup — {today}")
    lines.append(f"\nAlpaca paper equity: **${live_equity:,.0f}**"
                 if live_equity else "\nAlpaca equity: UNAVAILABLE (keys/network)")
    if len(hist) >= 2 and hist[0].get("alpaca_equity"):
        e0, s0 = hist[0]["alpaca_equity"], hist[0]["spy"]
        lines.append(f"Since first checkup ({hist[0]['date']}): account "
                     f"{live_equity/e0-1:+.1%} vs SPY {spy_now/s0-1:+.1%}")

    # ── 2. PUSH-20 re-validation on data through today ───────────────────────
    PUSH20, sim_notes = live_sim_config()
    full = metrics(simulate(df, PUSH20, "2006-01-01", today))
    spy_full = metrics(spy_bh(df, "2006-01-01", today))
    yr_ago = (pd.Timestamp(today) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    # trailing year via continuous run's per-year is noisy; use sub-sim with warmup caveat
    lines.append(f"\n## Strategy re-validation (full 2006 → {today})")
    lines.append(f"- Config under test (read from live DAILY_CHAMPION_FLAGS): `{PUSH20}`")
    for n in sim_notes:
        lines.append(f"- Caveat: {n}")
    lines.append(f"- PUSH-20: CAGR {full['cagr']:+.1%}  MDD {full['mdd']:+.1%}  "
                 f"Sharpe {full['sharpe']:.2f}  moWin {full['win_rate_monthly']:.0%}")
    lines.append(f"- SPY    : CAGR {spy_full['cagr']:+.1%}  MDD {spy_full['mdd']:+.1%}")
    py = full.get("per_year", {})
    recent_years = sorted(py)[-3:]
    lines.append("- Recent years: " + "  ".join(f"{y}: {py[y]:+.1%}" for y in recent_years))

    if full["cagr"] < EXPECT_CAGR - 0.03:
        alerts.append(f"Full-history CAGR degraded to {full['cagr']:+.1%} "
                      f"(expectation {EXPECT_CAGR:+.1%}) — edge may be eroding.")
    if full["mdd"] < EXPECT_MDD - 0.05:
        alerts.append(f"Max drawdown deepened to {full['mdd']:+.1%} "
                      f"(expectation {EXPECT_MDD:+.1%}).")

    # ── 3. live-vs-expected (only judge after enough elapsed time) ───────────
    days_live = (pd.Timestamp(today) - pd.Timestamp(DEPLOY_DATE)).days
    lines.append(f"\n## Live era ({DEPLOY_DATE} → today, {days_live}d)")
    if live_equity and days_live >= 60:
        # annualized live return vs MC 5th percentile band
        start_eq = next((h["alpaca_equity"] for h in hist
                         if h["date"] >= DEPLOY_DATE and h.get("alpaca_equity")), None)
        if start_eq:
            ret = live_equity / start_eq - 1
            ann = (1 + ret) ** (365 / days_live) - 1
            lines.append(f"- Live annualized: {ann:+.1%} (raw {ret:+.1%} over {days_live}d)")
            if ann < MC_P5_CAGR - 0.05 and days_live >= 180:
                alerts.append(f"Live annualized return {ann:+.1%} is below the Monte Carlo "
                              f"5th-percentile band ({MC_P5_CAGR:+.1%}) after {days_live}d — "
                              f"statistically unusual, review the strategy.")
    else:
        lines.append("- Too early to judge (needs 60+ days; variance dominates).")

    # ── 4. execution quality ─────────────────────────────────────────────────
    #
    # Two different numbers, and conflating them is how this section misreported
    # itself twice:
    #   slippage_bps  fill vs the OPEN of the session it filled in. Real execution
    #                 cost, and the only part the account controls.
    #   drift_bps     fill vs the decision close. The decision is taken after the
    #                 close and fills at the next open, so this carries an
    #                 overnight gap — and a whole weekend when the decision lands
    #                 on a Friday. Market movement, not a cost: symmetric, and it
    #                 averages out. It read +1002bps on a USD sell on 2026-09-14
    #                 purely because 2x semis fell 10% over that weekend.
    #
    # Both fields are ALREADY signed at write time (positive = worse for us), so
    # they must NOT be flipped again here. Flipping a second time was a real bug
    # introduced 2026-09-13; it reported 8.49bps for a book whose drift was 5.41.
    # Weight by notional, because cost lands on dollars traded.
    slip_path = os.path.join(ROOT, "data", "slippage_log.jsonl")
    if os.path.exists(slip_path):
        rows = [json.loads(l) for l in open(slip_path, encoding="utf-8") if l.strip()]
        if rows:
            def _wavg(key):
                g = [(r[key], abs(float(r.get("qty", 0)) * float(r.get("fill", 0))))
                     for r in rows if key in r]
                w = sum(n for _, n in g)
                return (sum(v * n for v, n in g) / w, len(g)) if w > 0 else (None, 0)

            exec_bps, n_exec = _wavg("slippage_bps")
            drift_bps, n_drift = _wavg("drift_bps")
            lines.append(f"\n## Execution\n- Fills logged: {len(rows)}")
            if exec_bps is not None:
                lines.append(f"- Execution cost (vs the open): {exec_bps:+.1f}bps "
                             f"over {n_exec} fills — backtest charges "
                             f"{SLIPPAGE_ASSUMED_BPS}bps")
            if drift_bps is not None:
                lines.append(f"- Queue drift (decision close → fill): {drift_bps:+.1f}bps "
                             f"over {n_drift} fills. Not a cost; the backtest does "
                             "not model the overnight gap at all.")
            if exec_bps is not None and exec_bps > SLIPPAGE_ASSUMED_BPS and n_exec >= 20:
                alerts.append(f"Live execution cost {exec_bps:+.1f}bps exceeds the "
                              f"{SLIPPAGE_ASSUMED_BPS}bps the backtest charges — "
                              "cost-adjusted CAGR is overstated; re-grade before trusting it.")

    # ── 5. verdict ───────────────────────────────────────────────────────────
    lines.append("\n## Verdict")
    if alerts:
        lines.append("**ALERTS — review with Claude before changing anything:**")
        for a in alerts:
            lines.append(f"- ⚠ {a}")
        lines.append("\nDo NOT hand-tune parameters off these alerts alone; "
                     "re-run the research harness and require multi-window robustness.")
    else:
        lines.append("All checks within expectations. No action needed. "
                     "(Discipline IS the edge — do not tune on noise.)")

    report = "\n".join(lines)
    out = os.path.join(ROOT, "reports", f"checkup_{today}.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
