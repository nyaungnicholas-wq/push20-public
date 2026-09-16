# PUSH-20 v2 — pre-registration

Written 2026-09-13, BEFORE any 2019+ data was read by the selection process.

## What is being tested

Candidate = the live PUSH-20 config, with three changes and nothing else:

| change | value | why it was selected |
|---|---|---|
| drop the VIX>=35 tier | `vix_hi_level = 0.0` | in-sample: +0.053 Sharpe, +0.129 Calmar, MDD -32.90% -> -28.32% |
| equal weight the 3 picks | `weight_scheme = "equal"` | in-sample: +0.013 Sharpe, +0.025 Calmar, lower turnover |
| leverage ceiling 1.5x -> 1.25x | `vol_cap_bull = 1.25` | the repo's own monotonic-Calmar finding, confirmed on the plateau |

Baseline for comparison = the live config as it actually trades: basket-vol sizing on,
both VIX tiers on, 9.5 bps per side (the realised number across 85 live orders).

Both are run on the SAME corrected engine, so the comparison isolates the config.

## Selection evidence (in-sample 2006-01-01 .. 2018-12-31)

Median across 13 perturbations of the config (vol_target, vol_window, max_weight,
ema_span, lookback, top_n), because the audit shows 72 of 135 configs are within
0.05 Sharpe of each other and a single-point result is therefore uninformative.

    baseline    Sharpe 0.702   Calmar 0.413   CAGR 13.82%   MDD -32.90%
    candidate   Sharpe 0.786   Calmar 0.620   CAGR 14.26%   MDD  (lower)

Improves in BOTH sub-eras (crisis 2006-12 and grind 2013-18) and on 13 of 13
plateau configs. That is the signature of a mechanism rather than a period effect.

## Holdout

2019-01-01 .. 2026-09-02. Not read during selection.

## Pass criteria — fixed now, before the run

The question is only "should the live config be replaced", so the test is paired
on the same window and the same engine. Market regime affects both equally.

1. PASS requires holdout Calmar(candidate) > Calmar(baseline).
2. PASS requires holdout Sharpe(candidate) >= Sharpe(baseline) - 0.05.
3. PASS requires holdout max drawdown(candidate) no worse than baseline by more
   than 2 percentage points.
4. All three must hold. Any single failure = DO NOT SWITCH.

Explicitly NOT a criterion: the absolute CAGR. The candidate gives up return by
design (lower leverage ceiling). A candidate that passes 1-3 and earns less is
still the correct choice, because the whole finding of this work is that the
realised drawdown was better than the distribution and the risk was understated.

## Known contamination, disclosed

A fourth change, `risk_gate_always` (let a falling risk cap de-lever inside the
3-day hold window), scored BEST in-sample: Sharpe 0.810, Calmar 0.650.

It is excluded from the candidate. Before the in-sample work I had already run it
on the full 2006-2026 window while measuring engine fidelity, and it was worse
there (CAGR 19.27% -> 18.54%, MDD -32.88% -> -34.91%). Since in-sample it helps
and the full window it hurts, it must degrade in 2019+. I therefore cannot
evaluate it cleanly and will not ship it on a result I have already peeked at.

## One attempt

This holdout is spent when the run below executes. Any further change is tested
on 2019+ only as forward paper, never by re-opening this window.
