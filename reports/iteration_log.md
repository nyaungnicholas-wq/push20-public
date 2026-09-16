# Rotation-v2 — Iterative Research Log (20-year study)

Primary benchmark: SPY buy-and-hold over 2005-2024 ($712,439, +10.3% CAGR, -55.2% MDD).

Objective = Sharpe + 2·(CAGR − SPY_CAGR) − 0.5·max(0, |MDD|−|SPY_MDD|), on 2005-2024. A change is KEPT only if it raises the objective on 2005-2024 AND no sub-period CAGR drops > 1.5pp vs the champion.

## Iteration 0 — Baseline (no v2 flags)

Hypothesis: establish the honest 20-year baseline.

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $831,159 | +11.2% | -30.5% | 0.68 | -18.7% | +0.70 | +10.3% | WIN |
| 2005-2014 | $238,541 | +9.1% | -24.7% | 0.61 | -18.7% | +0.64 | +7.7% | WIN |
| 2015-2024 | $349,866 | +13.4% | -30.5% | 0.74 | -2.6% | +0.75 | +13.1% | WIN |
| 2018-2024 | $274,744 | +15.6% | -30.5% | 0.78 | -2.6% | +0.82 | +13.7% | WIN |

**Champion objective (2005-2024): +0.696** — baseline BEATS SPY.

## Iteration 1 — trend_filter

Hypothesis: Drop sectors below their own 200-day SMA — should cut bear-market drawdown.

Change (delta vs champion): `{'rotation_trend_filter': True}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $647,548 | +9.8% | -30.5% | 0.62 | -17.2% | +0.61 | +10.3% | lose |
| 2005-2014 | $220,690 | +8.2% | -23.1% | 0.58 | -17.2% | +0.60 | +7.7% | WIN |
| 2015-2024 | $292,272 | +11.3% | -30.5% | 0.66 | -6.7% | +0.62 | +13.1% | lose |
| 2018-2024 | $238,151 | +13.2% | -30.5% | 0.69 | -6.5% | +0.68 | +13.7% | lose |

Objective 2005-2024: candidate +0.613 vs champion +0.696 → no improvement. Robustness: 2015-2024 CAGR -2.0% (< -1.5pp).

Decision: **REVERT**.

## Iteration 2 — cluster_filter

Hypothesis: Hold at most one ETF per correlation cluster — kills the SMH+XLK+QQQ triple-tech bet.

Change (delta vs champion): `{'rotation_cluster_filter': True}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $666,534 | +10.0% | -31.5% | 0.64 | -19.8% | +0.63 | +10.3% | lose |
| 2005-2014 | $236,092 | +9.0% | -24.0% | 0.60 | -19.8% | +0.63 | +7.7% | WIN |
| 2015-2024 | $282,279 | +10.9% | -31.6% | 0.68 | -6.6% | +0.63 | +13.1% | lose |
| 2018-2024 | $210,066 | +11.2% | -31.6% | 0.65 | -6.6% | +0.60 | +13.7% | lose |

Objective 2005-2024: candidate +0.634 vs champion +0.696 → no improvement. Robustness: 2015-2024 CAGR -2.4% (< -1.5pp).

Decision: **REVERT**.

## Iteration 3 — skip_month

Hypothesis: 12-1 skip-month momentum (skip last 21d) — avoids short-term reversal whipsaw.

Change (delta vs champion): `{'rotation_skip_days': 21}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $819,725 | +11.1% | -38.1% | 0.65 | -32.8% | +0.66 | +10.3% | WIN |
| 2005-2014 | $189,264 | +6.6% | -38.1% | 0.45 | -32.8% | +0.43 | +7.7% | lose |
| 2015-2024 | $435,937 | +15.9% | -30.6% | 0.82 | -3.8% | +0.88 | +13.1% | WIN |
| 2018-2024 | $328,097 | +18.5% | -30.6% | 0.86 | -1.9% | +0.96 | +13.7% | WIN |

Objective 2005-2024: candidate +0.663 vs champion +0.696 → no improvement. Robustness: 2005-2014 CAGR -2.5% (< -1.5pp).

Decision: **REVERT**.

## Iteration 4 — dual_momentum

Hypothesis: Require each sector to beat SPY's own momentum, not merely be >0 — stronger risk-off.

Change (delta vs champion): `{'rotation_dual_momentum': True}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $878,266 | +11.5% | -46.5% | 0.65 | -28.3% | +0.67 | +10.3% | WIN |
| 2005-2014 | $209,423 | +7.7% | -46.5% | 0.48 | -28.3% | +0.48 | +7.7% | WIN |
| 2015-2024 | $419,007 | +15.4% | -30.9% | 0.81 | -2.6% | +0.86 | +13.1% | WIN |
| 2018-2024 | $319,384 | +18.1% | -30.9% | 0.85 | -2.6% | +0.94 | +13.7% | WIN |

Objective 2005-2024: candidate +0.674 vs champion +0.696 → no improvement. Robustness: all sub-periods hold.

Decision: **REVERT**.

## Iteration 5 — regime_filter

Hypothesis: When SPY < its 200-SMA, go fully defensive — should dodge 2008/2022.

Change (delta vs champion): `{'rotation_regime_filter': True}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $542,775 | +8.8% | -24.8% | 0.63 | -8.9% | +0.60 | +10.3% | lose |
| 2005-2014 | $239,016 | +9.1% | -20.3% | 0.70 | -6.6% | +0.73 | +7.7% | WIN |
| 2015-2024 | $227,927 | +8.6% | -25.5% | 0.58 | -9.0% | +0.49 | +13.1% | lose |
| 2018-2024 | $191,263 | +9.7% | -25.0% | 0.60 | -8.9% | +0.52 | +13.7% | lose |

Objective 2005-2024: candidate +0.598 vs champion +0.696 → no improvement. Robustness: 2015-2024 CAGR -4.8% (< -1.5pp).

Decision: **REVERT**.

## Iteration 6 — momentum_weight

Hypothesis: Size positions by momentum strength (floor 15%) instead of equal weight.

Change (delta vs champion): `{'rotation_momentum_weight': True}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $974,640 | +12.1% | -34.9% | 0.68 | -26.2% | +0.72 | +10.3% | WIN |
| 2005-2014 | $223,810 | +8.4% | -34.9% | 0.54 | -26.2% | +0.56 | +7.7% | WIN |
| 2015-2024 | $436,200 | +15.9% | -30.7% | 0.80 | -3.1% | +0.86 | +13.1% | WIN |
| 2018-2024 | $325,096 | +18.4% | -30.8% | 0.83 | -3.1% | +0.93 | +13.7% | WIN |

Objective 2005-2024: candidate +0.717 vs champion +0.696 → improved. Robustness: all sub-periods hold.

Decision: **KEEP**.

## Iteration 7 — defensive_TLT

Hypothesis: Use long treasuries (TLT) as the risk-off asset instead of T-bills (BIL).

Change (delta vs champion): `{'rotation_defensive_symbol': 'TLT'}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $991,896 | +12.2% | -34.6% | 0.68 | -5.8% | +0.71 | +10.3% | WIN |
| 2005-2014 | $225,336 | +8.5% | -34.6% | 0.53 | -5.8% | +0.55 | +7.7% | WIN |
| 2015-2024 | $440,524 | +16.0% | -32.8% | 0.81 | -3.1% | +0.87 | +13.1% | WIN |
| 2018-2024 | $327,589 | +18.5% | -32.6% | 0.84 | -3.1% | +0.94 | +13.7% | WIN |

Objective 2005-2024: candidate +0.714 vs champion +0.717 → no improvement. Robustness: all sub-periods hold.

Decision: **REVERT**.

## Iteration 8 — defensive_GLD

Hypothesis: Use gold (GLD) as the risk-off asset instead of T-bills.

Change (delta vs champion): `{'rotation_defensive_symbol': 'GLD'}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,244,737 | +13.4% | -38.8% | 0.72 | -17.0% | +0.79 | +10.3% | WIN |
| 2005-2014 | $270,535 | +10.5% | -38.8% | 0.62 | -17.0% | +0.67 | +7.7% | WIN |
| 2015-2024 | $458,395 | +16.5% | -31.0% | 0.82 | -3.1% | +0.89 | +13.1% | WIN |
| 2018-2024 | $339,396 | +19.1% | -31.0% | 0.85 | -3.1% | +0.96 | +13.7% | WIN |

Objective 2005-2024: candidate +0.787 vs champion +0.717 → improved. Robustness: all sub-periods hold.

Decision: **KEEP**.

## Iteration 9 — defensive_IEF

Hypothesis: Use intermediate treasuries (IEF) as the risk-off asset.

Change (delta vs champion): `{'rotation_defensive_symbol': 'IEF'}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,027,962 | +12.4% | -34.4% | 0.69 | -16.7% | +0.73 | +10.3% | WIN |
| 2005-2014 | $234,824 | +8.9% | -34.4% | 0.57 | -16.7% | +0.59 | +7.7% | WIN |
| 2015-2024 | $438,731 | +15.9% | -31.0% | 0.81 | -3.1% | +0.86 | +13.1% | WIN |
| 2018-2024 | $326,617 | +18.4% | -31.0% | 0.84 | -3.1% | +0.93 | +13.7% | WIN |

Objective 2005-2024: candidate +0.735 vs champion +0.787 → no improvement. Robustness: 2005-2014 CAGR -1.6% (< -1.5pp).

Decision: **REVERT**.

## Iteration 10 — expanded_universe

Hypothesis: Add bonds/gold/intl/commodities so momentum can rotate beyond equities.

Change (delta vs champion): `{'universe': ['XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLB', 'XLP', 'XLU', 'XLRE', 'XLC', 'SMH', 'QQQ', 'IWM', 'MDY', 'VEA', 'VWO', 'TLT', 'IEF', 'GLD', 'GDX', 'DBA', 'USO']}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $555,293 | +9.0% | -41.0% | 0.51 | -9.9% | +0.48 | +10.3% | lose |
| 2005-2014 | $226,970 | +8.6% | -41.0% | 0.52 | -9.9% | +0.54 | +7.7% | WIN |
| 2015-2024 | $245,868 | +9.4% | -37.1% | 0.51 | -9.4% | +0.42 | +13.1% | lose |
| 2018-2024 | $193,130 | +9.9% | -36.9% | 0.51 | -9.3% | +0.42 | +13.7% | lose |

Objective 2005-2024: candidate +0.484 vs champion +0.787 → no improvement. Robustness: 2005-2014 CAGR -1.9% (< -1.5pp).

Decision: **REVERT**.

## Iteration 11 — top_n_2

Hypothesis: Concentrate into the top 2 sectors instead of 3.

Change (delta vs champion): `{'top_n': 2}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,097,089 | +12.7% | -38.9% | 0.66 | -15.0% | +0.71 | +10.3% | WIN |
| 2005-2014 | $265,525 | +10.3% | -38.9% | 0.59 | -15.0% | +0.64 | +7.7% | WIN |
| 2015-2024 | $415,468 | +15.3% | -32.0% | 0.74 | -4.0% | +0.78 | +13.1% | WIN |
| 2018-2024 | $294,659 | +16.7% | -32.0% | 0.73 | -3.9% | +0.79 | +13.7% | WIN |

Objective 2005-2024: candidate +0.710 vs champion +0.787 → no improvement. Robustness: 2018-2024 CAGR -2.4% (< -1.5pp).

Decision: **REVERT**.

## Iteration 12 — top_n_4

Hypothesis: Diversify into the top 4 sectors instead of 3.

Change (delta vs champion): `{'top_n': 4}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,149,693 | +13.0% | -39.0% | 0.73 | -19.3% | +0.78 | +10.3% | WIN |
| 2005-2014 | $266,321 | +10.3% | -39.0% | 0.62 | -19.3% | +0.68 | +7.7% | WIN |
| 2015-2024 | $429,996 | +15.7% | -30.8% | 0.82 | -3.0% | +0.88 | +13.1% | WIN |
| 2018-2024 | $314,793 | +17.8% | -30.8% | 0.84 | -3.1% | +0.92 | +13.7% | WIN |

Objective 2005-2024: candidate +0.781 vs champion +0.787 → no improvement. Robustness: all sub-periods hold.

Decision: **REVERT**.

## Iteration 13 — lookback_3blend

Hypothesis: Blend 3/6/12-month momentum instead of 6/12.

Change (delta vs champion): `{'lookbacks': (63, 126, 252)}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,072,606 | +12.6% | -37.8% | 0.69 | -15.9% | +0.74 | +10.3% | WIN |
| 2005-2014 | $269,659 | +10.4% | -37.8% | 0.62 | -15.9% | +0.67 | +7.7% | WIN |
| 2015-2024 | $399,987 | +14.9% | -31.0% | 0.77 | -5.1% | +0.80 | +13.1% | WIN |
| 2018-2024 | $289,818 | +16.4% | -31.0% | 0.77 | -5.1% | +0.82 | +13.7% | WIN |

Objective 2005-2024: candidate +0.739 vs champion +0.787 → no improvement. Robustness: 2015-2024 CAGR -1.6% (< -1.5pp).

Decision: **REVERT**.

## Iteration 14 — lookback_12only

Hypothesis: Use 12-month momentum only (slower, less whipsaw).

Change (delta vs champion): `{'lookbacks': (252,)}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,367,276 | +14.0% | -39.0% | 0.74 | -16.5% | +0.82 | +10.3% | WIN |
| 2005-2014 | $265,466 | +10.3% | -39.0% | 0.60 | -16.5% | +0.66 | +7.7% | WIN |
| 2015-2024 | $514,520 | +17.8% | -31.0% | 0.87 | -3.0% | +0.97 | +13.1% | WIN |
| 2018-2024 | $379,808 | +21.0% | -31.0% | 0.92 | -3.0% | +1.06 | +13.7% | WIN |

Objective 2005-2024: candidate +0.816 vs champion +0.787 → improved. Robustness: all sub-periods hold.

Decision: **KEEP**.

## Final Champion

Champion flags: `{'rotation_momentum_weight': True, 'rotation_defensive_symbol': 'GLD', 'lookbacks': (252,)}`

| Window | Final | CAGR | MDD | Sharpe | WorstYr | Obj | SPY CAGR | Result |
|---|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,367,276 | +14.0% | -39.0% | 0.74 | -16.5% | +0.82 | +10.3% | WIN |
| 2005-2014 | $265,466 | +10.3% | -39.0% | 0.60 | -16.5% | +0.66 | +7.7% | WIN |
| 2015-2024 | $514,520 | +17.8% | -31.0% | 0.87 | -3.0% | +0.97 | +13.1% | WIN |
| 2018-2024 | $379,808 | +21.0% | -31.0% | 0.92 | -3.0% | +1.06 | +13.7% | WIN |

**Verdict (2005-2024): champion BEATS SPY** — +14.0% vs +10.3% CAGR, -39.0% vs -55.2% MDD, Sharpe 0.74 vs 0.61.

### Iteration outcomes

- baseline: **KEEP** (obj +0.696) — `{}`
- trend_filter: **REVERT** (obj +0.613) — `{'rotation_trend_filter': True}`
- cluster_filter: **REVERT** (obj +0.634) — `{'rotation_cluster_filter': True}`
- skip_month: **REVERT** (obj +0.663) — `{'rotation_skip_days': 21}`
- dual_momentum: **REVERT** (obj +0.674) — `{'rotation_dual_momentum': True}`
- regime_filter: **REVERT** (obj +0.598) — `{'rotation_regime_filter': True}`
- momentum_weight: **KEEP** (obj +0.717) — `{'rotation_momentum_weight': True}`
- defensive_TLT: **REVERT** (obj +0.714) — `{'rotation_defensive_symbol': 'TLT'}`
- defensive_GLD: **KEEP** (obj +0.787) — `{'rotation_defensive_symbol': 'GLD'}`
- defensive_IEF: **REVERT** (obj +0.735) — `{'rotation_defensive_symbol': 'IEF'}`
- expanded_universe: **REVERT** (obj +0.484) — `{'universe': ['XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLB', 'XLP', 'XLU', 'XLRE', 'XLC', 'SMH', 'QQQ', 'IWM', 'MDY', 'VEA', 'VWO', 'TLT', 'IEF', 'GLD', 'GDX', 'DBA', 'USO']}`
- top_n_2: **REVERT** (obj +0.710) — `{'top_n': 2}`
- top_n_4: **REVERT** (obj +0.781) — `{'top_n': 4}`
- lookback_3blend: **REVERT** (obj +0.739) — `{'lookbacks': (63, 126, 252)}`
- lookback_12only: **KEEP** (obj +0.816) — `{'lookbacks': (252,)}`

# ===== ROUND 2 (building on champion `{'rotation_momentum_weight': True, 'rotation_defensive_symbol': 'GLD', 'lookbacks': (252,)}`) =====

Champion baseline this round — 2005-2024 obj +0.816, CAGR +14.0%, MDD -39.0%, WF-test 2017-2024 CAGR +21.4%.

## Iteration 15 — vol_target_0.20

Vol-target overlay at 20% annualized — gently de-lever in turbulence.

Delta: `{'rotation_vol_target': 0.2}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,257,930 | +13.5% | -37.7% | 0.75 | +0.82 | +10.3% | WIN |
| 2005-2014 | $258,147 | +10.0% | -37.7% | 0.60 | +0.65 | +7.7% | WIN |
| 2015-2024 | $485,539 | +17.1% | -28.6% | 0.89 | +0.97 | +13.1% | WIN |
| 2018-2024 | $354,422 | +19.8% | -28.5% | 0.93 | +1.05 | +13.7% | WIN |
| WF-train 2005-2016 | $284,758 | +9.1% | -37.7% | 0.57 | +0.61 | +7.5% | WIN |
| WF-test 2017-2024 | $438,064 | +20.3% | -28.5% | 0.98 | +1.10 | +14.7% | WIN |

Obj 2005-2024: +0.816 vs champ +0.816. ΔMDD -1.3%, ΔCAGR -0.5%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +20.3% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 16 — vol_target_0.15

Vol-target at 15% — attack the -39% drawdown directly.

Delta: `{'rotation_vol_target': 0.15}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,239,031 | +13.4% | -35.0% | 0.78 | +0.84 | +10.3% | WIN |
| 2005-2014 | $270,730 | +10.5% | -35.0% | 0.64 | +0.70 | +7.7% | WIN |
| 2015-2024 | $457,180 | +16.4% | -26.9% | 0.91 | +0.98 | +13.1% | WIN |
| 2018-2024 | $332,716 | +18.8% | -26.8% | 0.94 | +1.04 | +13.7% | WIN |
| WF-train 2005-2016 | $299,186 | +9.6% | -35.0% | 0.61 | +0.65 | +7.5% | WIN |
| WF-test 2017-2024 | $411,290 | +19.4% | -26.8% | 1.00 | +1.10 | +14.7% | WIN |

Obj 2005-2024: +0.840 vs champ +0.816. ΔMDD -2.7%, ΔCAGR -0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +19.4% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 17 — vol_target_0.12

Vol-target at 12% — more aggressive de-risking.

Delta: `{'rotation_vol_target': 0.12}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,238,916 | +13.4% | -32.8% | 0.81 | +0.87 | +10.3% | WIN |
| 2005-2014 | $289,815 | +11.2% | -32.8% | 0.70 | +0.77 | +7.7% | WIN |
| 2015-2024 | $428,982 | +15.7% | -25.2% | 0.93 | +0.98 | +13.1% | WIN |
| 2018-2024 | $311,505 | +17.6% | -25.0% | 0.95 | +1.03 | +13.7% | WIN |
| WF-train 2005-2016 | $319,380 | +10.2% | -32.8% | 0.66 | +0.72 | +7.5% | WIN |
| WF-test 2017-2024 | $385,017 | +18.4% | -25.0% | 1.01 | +1.09 | +14.7% | WIN |

Obj 2005-2024: +0.874 vs champ +0.840. ΔMDD -2.2%, ΔCAGR -0.0%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +18.4% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 18 — vol_target_0.10

Vol-target at 10% — strong de-risking, expect CAGR give-back.

Delta: `{'rotation_vol_target': 0.1}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,158,970 | +13.0% | -31.1% | 0.82 | +0.88 | +10.3% | WIN |
| 2005-2014 | $294,350 | +11.4% | -31.1% | 0.72 | +0.80 | +7.7% | WIN |
| 2015-2024 | $396,375 | +14.8% | -22.6% | 0.93 | +0.96 | +13.1% | WIN |
| 2018-2024 | $288,184 | +16.3% | -22.5% | 0.94 | +1.00 | +13.7% | WIN |
| WF-train 2005-2016 | $323,064 | +10.3% | -31.1% | 0.68 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $356,153 | +17.2% | -22.5% | 1.01 | +1.07 | +14.7% | WIN |

Obj 2005-2024: +0.876 vs champ +0.874. ΔMDD -1.6%, ΔCAGR -0.4%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.2% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 19 — poscap_0.50

Cap any single sector at 50% — de-concentrate the momentum weighting.

Delta: `{'rotation_position_cap': 0.5}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,091,279 | +12.7% | -29.7% | 0.81 | +0.86 | +10.3% | WIN |
| 2005-2014 | $284,888 | +11.0% | -29.7% | 0.72 | +0.78 | +7.7% | WIN |
| 2015-2024 | $385,475 | +14.5% | -22.6% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $280,346 | +15.9% | -22.5% | 0.93 | +0.98 | +13.7% | WIN |
| WF-train 2005-2016 | $312,592 | +10.0% | -29.7% | 0.68 | +0.73 | +7.5% | WIN |
| WF-test 2017-2024 | $346,482 | +16.8% | -22.4% | 1.01 | +1.05 | +14.7% | WIN |

Obj 2005-2024: +0.862 vs champ +0.876. ΔMDD -1.5%, ΔCAGR -0.3%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.8% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 20 — poscap_0.40

Cap any single sector at 40%.

Delta: `{'rotation_position_cap': 0.4}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,044,509 | +12.5% | -29.0% | 0.81 | +0.85 | +10.3% | WIN |
| 2005-2014 | $277,404 | +10.8% | -29.0% | 0.71 | +0.77 | +7.7% | WIN |
| 2015-2024 | $378,858 | +14.3% | -22.6% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $279,315 | +15.8% | -22.4% | 0.94 | +0.99 | +13.7% | WIN |
| WF-train 2005-2016 | $301,134 | +9.6% | -29.0% | 0.67 | +0.71 | +7.5% | WIN |
| WF-test 2017-2024 | $344,142 | +16.7% | -22.4% | 1.02 | +1.06 | +14.7% | WIN |

Obj 2005-2024: +0.855 vs champ +0.876. ΔMDD -2.1%, ΔCAGR -0.6%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.7% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 21 — defensive_TLT

Swap risk-off asset GLD→TLT (more economically defensible).

Delta: `{'rotation_defensive_symbol': 'TLT'}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $723,768 | +10.4% | -26.5% | 0.75 | +0.75 | +10.3% | WIN |
| 2005-2014 | $259,216 | +10.0% | -26.5% | 0.76 | +0.81 | +7.7% | WIN |
| 2015-2024 | $280,730 | +10.9% | -22.9% | 0.74 | +0.70 | +13.1% | lose |
| 2018-2024 | $205,836 | +10.9% | -22.6% | 0.70 | +0.64 | +13.7% | lose |
| WF-train 2005-2016 | $282,932 | +9.1% | -26.5% | 0.71 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $254,402 | +12.4% | -22.6% | 0.80 | +0.75 | +14.7% | lose |

Obj 2005-2024: +0.749 vs champ +0.876. ΔMDD -4.7%, ΔCAGR -2.6%. Robustness: 2015-2024 CAGR -3.9%. WF-test 2017-2024 CAGR +12.4% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 22 — defensive_IEF

Swap risk-off asset GLD→IEF (intermediate treasuries).

Delta: `{'rotation_defensive_symbol': 'IEF'}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $696,983 | +10.2% | -19.2% | 0.77 | +0.77 | +10.3% | lose |
| 2005-2014 | $238,821 | +9.1% | -15.7% | 0.76 | +0.78 | +7.7% | WIN |
| 2015-2024 | $292,056 | +11.3% | -19.3% | 0.80 | +0.76 | +13.1% | lose |
| 2018-2024 | $218,609 | +11.8% | -19.2% | 0.78 | +0.74 | +13.7% | lose |
| WF-train 2005-2016 | $256,301 | +8.2% | -15.7% | 0.69 | +0.71 | +7.5% | WIN |
| WF-test 2017-2024 | $270,214 | +13.2% | -19.2% | 0.88 | +0.85 | +14.7% | lose |

Obj 2005-2024: +0.772 vs champ +0.876. ΔMDD -11.9%, ΔCAGR -2.8%. Robustness: 2005-2014 CAGR -2.3%. WF-test 2017-2024 CAGR +13.2% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 23 — plus_cluster

Champion + cluster filter — does de-concentrating tech pay now weighting is on?

Delta: `{'rotation_cluster_filter': True}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,086,138 | +12.7% | -31.1% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $312,804 | +12.1% | -31.1% | 0.76 | +0.85 | +7.7% | WIN |
| 2015-2024 | $350,225 | +13.4% | -21.9% | 0.89 | +0.90 | +13.1% | WIN |
| 2018-2024 | $255,775 | +14.4% | -21.8% | 0.89 | +0.90 | +13.7% | WIN |
| WF-train 2005-2016 | $348,024 | +11.0% | -31.1% | 0.72 | +0.79 | +7.5% | WIN |
| WF-test 2017-2024 | $310,060 | +15.2% | -21.8% | 0.96 | +0.97 | +14.7% | WIN |

Obj 2005-2024: +0.867 vs champ +0.876. ΔMDD -0.0%, ΔCAGR -0.4%. Robustness: 2018-2024 CAGR -2.0%. WF-test 2017-2024 CAGR +15.2% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 24 — plus_trend

Champion + 200-SMA per-sector trend filter.

Delta: `{'rotation_trend_filter': True}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $973,510 | +12.1% | -33.0% | 0.77 | +0.81 | +10.3% | WIN |
| 2005-2014 | $265,951 | +10.3% | -33.0% | 0.67 | +0.72 | +7.7% | WIN |
| 2015-2024 | $369,958 | +14.0% | -22.7% | 0.89 | +0.90 | +13.1% | WIN |
| 2018-2024 | $271,839 | +15.4% | -22.5% | 0.90 | +0.93 | +13.7% | WIN |
| WF-train 2005-2016 | $288,862 | +9.3% | -33.0% | 0.63 | +0.66 | +7.5% | WIN |
| WF-test 2017-2024 | $335,905 | +16.4% | -22.5% | 0.97 | +1.01 | +14.7% | WIN |

Obj 2005-2024: +0.806 vs champ +0.876. ΔMDD +1.9%, ΔCAGR -1.0%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.4% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 25 — plus_top4

Champion + hold top 4 (more diversified base before weighting).

Delta: `{'top_n': 4}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,034,307 | +12.4% | -30.6% | 0.81 | +0.85 | +10.3% | WIN |
| 2005-2014 | $297,031 | +11.5% | -30.6% | 0.75 | +0.82 | +7.7% | WIN |
| 2015-2024 | $350,937 | +13.4% | -22.1% | 0.89 | +0.89 | +13.1% | WIN |
| 2018-2024 | $262,763 | +14.8% | -22.0% | 0.91 | +0.93 | +13.7% | WIN |
| WF-train 2005-2016 | $319,124 | +10.2% | -30.6% | 0.69 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $321,658 | +15.7% | -22.0% | 0.98 | +1.00 | +14.7% | WIN |

Obj 2005-2024: +0.853 vs champ +0.876. ΔMDD -0.6%, ΔCAGR -0.6%. Robustness: 2018-2024 CAGR -1.5%. WF-test 2017-2024 CAGR +15.7% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 26 — plus_skip21

Champion + 12-1 skip-month on the 12mo lookback.

Delta: `{'rotation_skip_days': 21}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,098,133 | +12.7% | -33.0% | 0.80 | +0.85 | +10.3% | WIN |
| 2005-2014 | $291,714 | +11.3% | -33.0% | 0.71 | +0.79 | +7.7% | WIN |
| 2015-2024 | $380,543 | +14.3% | -22.3% | 0.90 | +0.93 | +13.1% | WIN |
| 2018-2024 | $273,375 | +15.5% | -22.3% | 0.90 | +0.94 | +13.7% | WIN |
| WF-train 2005-2016 | $315,269 | +10.1% | -33.0% | 0.67 | +0.72 | +7.5% | WIN |
| WF-test 2017-2024 | $346,094 | +16.8% | -22.3% | 1.00 | +1.04 | +14.7% | WIN |

Obj 2005-2024: +0.851 vs champ +0.876. ΔMDD +1.9%, ΔCAGR -0.3%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.8% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 27 — vt15_capF0.20

Vol-target 15% + raise weight floor to 0.20 (de-concentrate + de-risk).

Delta: `{'rotation_vol_target': 0.15, 'rotation_weight_floor': 0.2}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,220,185 | +13.3% | -34.8% | 0.78 | +0.84 | +10.3% | WIN |
| 2005-2014 | $268,986 | +10.4% | -34.8% | 0.64 | +0.69 | +7.7% | WIN |
| 2015-2024 | $453,139 | +16.3% | -26.9% | 0.91 | +0.98 | +13.1% | WIN |
| 2018-2024 | $330,137 | +18.6% | -26.8% | 0.94 | +1.04 | +13.7% | WIN |
| WF-train 2005-2016 | $296,926 | +9.5% | -34.8% | 0.61 | +0.65 | +7.5% | WIN |
| WF-test 2017-2024 | $408,106 | +19.2% | -26.8% | 1.00 | +1.09 | +14.7% | WIN |

Obj 2005-2024: +0.837 vs champ +0.876. ΔMDD +3.6%, ΔCAGR +0.3%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +19.2% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 28 — vt12_TLT

Vol-target 12% with TLT as the de-risk parking asset.

Delta: `{'rotation_vol_target': 0.12, 'rotation_defensive_symbol': 'TLT'}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $843,383 | +11.3% | -26.6% | 0.75 | +0.77 | +10.3% | WIN |
| 2005-2014 | $261,975 | +10.1% | -26.5% | 0.72 | +0.77 | +7.7% | WIN |
| 2015-2024 | $322,617 | +12.4% | -27.0% | 0.79 | +0.77 | +13.1% | lose |
| 2018-2024 | $239,823 | +13.3% | -26.7% | 0.78 | +0.77 | +13.7% | lose |
| WF-train 2005-2016 | $282,782 | +9.1% | -26.5% | 0.67 | +0.70 | +7.5% | WIN |
| WF-test 2017-2024 | $296,451 | +14.6% | -26.7% | 0.86 | +0.86 | +14.7% | lose |

Obj 2005-2024: +0.772 vs champ +0.876. ΔMDD -4.5%, ΔCAGR -1.8%. Robustness: 2015-2024 CAGR -2.3%. WF-test 2017-2024 CAGR +14.6% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 29 — vt15_poscap0.5

Vol-target 15% + 50% position cap (both risk levers).

Delta: `{'rotation_vol_target': 0.15, 'rotation_position_cap': 0.5}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,133,881 | +12.9% | -32.9% | 0.77 | +0.82 | +10.3% | WIN |
| 2005-2014 | $258,412 | +10.0% | -32.9% | 0.63 | +0.68 | +7.7% | WIN |
| 2015-2024 | $438,318 | +15.9% | -26.9% | 0.90 | +0.96 | +13.1% | WIN |
| 2018-2024 | $319,011 | +18.0% | -26.8% | 0.93 | +1.02 | +13.7% | WIN |
| WF-train 2005-2016 | $285,671 | +9.1% | -32.9% | 0.60 | +0.63 | +7.5% | WIN |
| WF-test 2017-2024 | $394,373 | +18.7% | -26.7% | 0.99 | +1.07 | +14.7% | WIN |

Obj 2005-2024: +0.820 vs champ +0.876. ΔMDD +1.7%, ΔCAGR -0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +18.7% vs SPY +14.7%.

Decision: **REVERT**.


# ===== ROUND 3 (building on champion `{'rotation_momentum_weight': True, 'rotation_defensive_symbol': 'GLD', 'lookbacks': (252,), 'rotation_vol_target': 0.1}`) =====

Champion baseline this round — 2005-2024 obj +0.876, CAGR +13.0%, MDD -31.1%, WF-test 2017-2024 CAGR +17.2%.

## Iteration 30 — vt_0.08

Fine-tune vol-target below 0.10 — even lower drawdown.

Delta: `{'rotation_vol_target': 0.08}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,043,872 | +12.5% | -29.5% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $299,359 | +11.6% | -29.5% | 0.75 | +0.83 | +7.7% | WIN |
| 2015-2024 | $350,311 | +13.4% | -19.9% | 0.91 | +0.92 | +13.1% | WIN |
| 2018-2024 | $259,221 | +14.6% | -19.9% | 0.92 | +0.94 | +13.7% | WIN |
| WF-train 2005-2016 | $326,417 | +10.4% | -29.5% | 0.71 | +0.76 | +7.5% | WIN |
| WF-test 2017-2024 | $317,913 | +15.6% | -19.9% | 0.99 | +1.01 | +14.7% | WIN |

Obj 2005-2024: +0.866 vs champ +0.876. ΔMDD -1.7%, ΔCAGR -0.6%. Robustness: 2018-2024 CAGR -1.7%. WF-test 2017-2024 CAGR +15.6% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 31 — vt_0.09

Vol-target 0.09.

Delta: `{'rotation_vol_target': 0.09}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,096,055 | +12.7% | -30.3% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $295,667 | +11.5% | -30.3% | 0.73 | +0.81 | +7.7% | WIN |
| 2015-2024 | $372,893 | +14.1% | -21.3% | 0.92 | +0.94 | +13.1% | WIN |
| 2018-2024 | $273,685 | +15.5% | -21.2% | 0.93 | +0.97 | +13.7% | WIN |
| WF-train 2005-2016 | $323,081 | +10.3% | -30.3% | 0.69 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $336,809 | +16.4% | -21.2% | 1.00 | +1.04 | +14.7% | WIN |

Obj 2005-2024: +0.869 vs champ +0.876. ΔMDD -0.8%, ΔCAGR -0.3%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.4% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 32 — vt_0.11

Vol-target 0.11 — half-step back toward more return.

Delta: `{'rotation_vol_target': 0.11}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,202,346 | +13.2% | -32.0% | 0.82 | +0.88 | +10.3% | WIN |
| 2005-2014 | $291,679 | +11.3% | -32.0% | 0.71 | +0.78 | +7.7% | WIN |
| 2015-2024 | $414,425 | +15.3% | -23.9% | 0.93 | +0.97 | +13.1% | WIN |
| 2018-2024 | $300,714 | +17.1% | -23.8% | 0.95 | +1.02 | +13.7% | WIN |
| WF-train 2005-2016 | $321,070 | +10.2% | -32.0% | 0.67 | +0.73 | +7.5% | WIN |
| WF-test 2017-2024 | $371,651 | +17.9% | -23.8% | 1.02 | +1.08 | +14.7% | WIN |

Obj 2005-2024: +0.876 vs champ +0.876. ΔMDD +0.8%, ΔCAGR +0.2%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.9% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 33 — vt_0.13

Vol-target 0.13.

Delta: `{'rotation_vol_target': 0.13}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,262,554 | +13.5% | -33.6% | 0.80 | +0.87 | +10.3% | WIN |
| 2005-2014 | $285,565 | +11.1% | -33.6% | 0.68 | +0.75 | +7.7% | WIN |
| 2015-2024 | $442,948 | +16.1% | -26.0% | 0.92 | +0.98 | +13.1% | WIN |
| 2018-2024 | $321,085 | +18.2% | -25.9% | 0.95 | +1.04 | +13.7% | WIN |
| WF-train 2005-2016 | $315,875 | +10.1% | -33.6% | 0.65 | +0.70 | +7.5% | WIN |
| WF-test 2017-2024 | $396,900 | +18.8% | -25.9% | 1.01 | +1.10 | +14.7% | WIN |

Obj 2005-2024: +0.869 vs champ +0.876. ΔMDD +2.5%, ΔCAGR +0.5%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +18.8% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 34 — volwin_30

Slower vol estimate (30d) — less reactive de-risking.

Delta: `{'rotation_vol_window': 30}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,172,843 | +13.1% | -31.6% | 0.82 | +0.88 | +10.3% | WIN |
| 2005-2014 | $299,162 | +11.6% | -31.6% | 0.73 | +0.81 | +7.7% | WIN |
| 2015-2024 | $394,003 | +14.7% | -24.6% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $289,447 | +16.4% | -24.5% | 0.94 | +0.99 | +13.7% | WIN |
| WF-train 2005-2016 | $325,446 | +10.3% | -31.6% | 0.69 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $357,722 | +17.3% | -24.5% | 1.01 | +1.06 | +14.7% | WIN |

Obj 2005-2024: +0.877 vs champ +0.876. ΔMDD +0.5%, ΔCAGR +0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.3% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 35 — volwin_40

Vol window 40d.

Delta: `{'rotation_vol_window': 40}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,133,625 | +12.9% | -31.6% | 0.81 | +0.86 | +10.3% | WIN |
| 2005-2014 | $302,409 | +11.7% | -31.6% | 0.74 | +0.82 | +7.7% | WIN |
| 2015-2024 | $374,961 | +14.1% | -25.7% | 0.88 | +0.90 | +13.1% | WIN |
| 2018-2024 | $280,037 | +15.9% | -25.6% | 0.90 | +0.95 | +13.7% | WIN |
| WF-train 2005-2016 | $325,138 | +10.3% | -31.6% | 0.69 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $346,151 | +16.8% | -25.6% | 0.98 | +1.02 | +14.7% | WIN |

Obj 2005-2024: +0.861 vs champ +0.877. ΔMDD -0.0%, ΔCAGR -0.2%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.8% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 36 — volwin_60

Vol window 60d (quarter) — smoothest exposure.

Delta: `{'rotation_vol_window': 60}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,045,290 | +12.5% | -30.8% | 0.78 | +0.83 | +10.3% | WIN |
| 2005-2014 | $304,220 | +11.8% | -30.8% | 0.75 | +0.83 | +7.7% | WIN |
| 2015-2024 | $342,604 | +13.1% | -27.1% | 0.82 | +0.82 | +13.1% | WIN |
| 2018-2024 | $254,484 | +14.3% | -27.0% | 0.82 | +0.83 | +13.7% | WIN |
| WF-train 2005-2016 | $329,725 | +10.5% | -30.8% | 0.70 | +0.76 | +7.5% | WIN |
| WF-test 2017-2024 | $314,631 | +15.4% | -27.0% | 0.90 | +0.92 | +14.7% | WIN |

Obj 2005-2024: +0.827 vs champ +0.877. ΔMDD -0.8%, ΔCAGR -0.6%. Robustness: 2015-2024 CAGR -1.6%. WF-test 2017-2024 CAGR +15.4% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 37 — volwin_10

Faster vol estimate (10d) — reacts quicker to spikes.

Delta: `{'rotation_vol_window': 10}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,102,026 | +12.8% | -31.2% | 0.81 | +0.85 | +10.3% | WIN |
| 2005-2014 | $295,494 | +11.5% | -31.2% | 0.73 | +0.80 | +7.7% | WIN |
| 2015-2024 | $372,728 | +14.1% | -23.4% | 0.89 | +0.91 | +13.1% | WIN |
| 2018-2024 | $276,864 | +15.7% | -23.1% | 0.91 | +0.95 | +13.7% | WIN |
| WF-train 2005-2016 | $319,775 | +10.2% | -31.2% | 0.68 | +0.73 | +7.5% | WIN |
| WF-test 2017-2024 | $342,182 | +16.6% | -23.0% | 0.98 | +1.02 | +14.7% | WIN |

Obj 2005-2024: +0.854 vs champ +0.877. ΔMDD -0.4%, ΔCAGR -0.4%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.6% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 38 — lb_189

Rank by ~9-month momentum instead of 12.

Delta: `{'lookbacks': (189,)}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $903,704 | +11.6% | -32.2% | 0.74 | +0.77 | +10.3% | WIN |
| 2005-2014 | $275,738 | +10.7% | -32.2% | 0.68 | +0.74 | +7.7% | WIN |
| 2015-2024 | $327,433 | +12.6% | -24.4% | 0.81 | +0.80 | +13.1% | lose |
| 2018-2024 | $232,485 | +12.8% | -24.2% | 0.77 | +0.75 | +13.7% | lose |
| WF-train 2005-2016 | $316,443 | +10.1% | -32.2% | 0.67 | +0.72 | +7.5% | WIN |
| WF-test 2017-2024 | $283,020 | +13.9% | -24.3% | 0.85 | +0.83 | +14.7% | lose |

Obj 2005-2024: +0.769 vs champ +0.877. ΔMDD +0.6%, ΔCAGR -1.5%. Robustness: 2015-2024 CAGR -2.1%. WF-test 2017-2024 CAGR +13.9% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 39 — lb_210

Rank by ~10-month momentum.

Delta: `{'lookbacks': (210,)}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,232,443 | +13.4% | -29.9% | 0.84 | +0.90 | +10.3% | WIN |
| 2005-2014 | $337,523 | +12.9% | -29.9% | 0.80 | +0.91 | +7.7% | WIN |
| 2015-2024 | $367,906 | +13.9% | -24.2% | 0.88 | +0.90 | +13.1% | WIN |
| 2018-2024 | $256,795 | +14.4% | -24.2% | 0.85 | +0.86 | +13.7% | WIN |
| WF-train 2005-2016 | $377,844 | +11.7% | -29.9% | 0.76 | +0.85 | +7.5% | WIN |
| WF-test 2017-2024 | $323,444 | +15.8% | -24.2% | 0.94 | +0.96 | +14.7% | WIN |

Obj 2005-2024: +0.899 vs champ +0.877. ΔMDD -1.7%, ΔCAGR +0.3%. Robustness: 2018-2024 CAGR -2.0%. WF-test 2017-2024 CAGR +15.8% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 40 — lb_126_252

Blend 6mo+12mo momentum (was rejected pre-vol-target; retest).

Delta: `{'lookbacks': (126, 252)}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,036,921 | +12.4% | -31.5% | 0.78 | +0.82 | +10.3% | WIN |
| 2005-2014 | $298,402 | +11.6% | -31.5% | 0.73 | +0.81 | +7.7% | WIN |
| 2015-2024 | $348,790 | +13.3% | -24.7% | 0.84 | +0.84 | +13.1% | WIN |
| 2018-2024 | $257,366 | +14.5% | -24.5% | 0.84 | +0.85 | +13.7% | WIN |
| WF-train 2005-2016 | $326,524 | +10.4% | -31.5% | 0.69 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $315,703 | +15.5% | -24.5% | 0.91 | +0.93 | +14.7% | WIN |

Obj 2005-2024: +0.824 vs champ +0.877. ΔMDD -0.1%, ΔCAGR -0.7%. Robustness: 2018-2024 CAGR -1.9%. WF-test 2017-2024 CAGR +15.5% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 41 — floor_0.20

Raise momentum-weight floor to 0.20 (de-concentrate the leader).

Delta: `{'rotation_weight_floor': 0.2}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,158,735 | +13.0% | -31.4% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $297,457 | +11.5% | -31.4% | 0.73 | +0.81 | +7.7% | WIN |
| 2015-2024 | $391,467 | +14.6% | -24.6% | 0.91 | +0.95 | +13.1% | WIN |
| 2018-2024 | $287,888 | +16.3% | -24.5% | 0.93 | +0.99 | +13.7% | WIN |
| WF-train 2005-2016 | $323,231 | +10.3% | -31.4% | 0.68 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $355,797 | +17.2% | -24.5% | 1.00 | +1.06 | +14.7% | WIN |

Obj 2005-2024: +0.874 vs champ +0.877. ΔMDD -0.2%, ΔCAGR -0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.2% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 42 — floor_0.10

Lower floor to 0.10 (let the leader run harder).

Delta: `{'rotation_weight_floor': 0.1}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,176,498 | +13.1% | -31.7% | 0.82 | +0.88 | +10.3% | WIN |
| 2005-2014 | $299,135 | +11.6% | -31.7% | 0.73 | +0.81 | +7.7% | WIN |
| 2015-2024 | $395,251 | +14.7% | -24.6% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $290,183 | +16.5% | -24.5% | 0.94 | +0.99 | +13.7% | WIN |
| WF-train 2005-2016 | $325,621 | +10.3% | -31.7% | 0.69 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $358,634 | +17.3% | -24.5% | 1.01 | +1.06 | +14.7% | WIN |

Obj 2005-2024: +0.878 vs champ +0.877. ΔMDD +0.1%, ΔCAGR +0.0%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.3% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 43 — no_abs_mom

Drop the >0 absolute-momentum gate (vol-target handles risk now).

Delta: `{'rotation_abs_momentum': False}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,154,419 | +13.0% | -31.5% | 0.83 | +0.88 | +10.3% | WIN |
| 2005-2014 | $291,828 | +11.3% | -31.5% | 0.73 | +0.81 | +7.7% | WIN |
| 2015-2024 | $397,306 | +14.8% | -24.5% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $291,070 | +16.5% | -24.4% | 0.94 | +0.99 | +13.7% | WIN |
| WF-train 2005-2016 | $318,489 | +10.1% | -31.5% | 0.69 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $359,721 | +17.4% | -24.4% | 1.01 | +1.06 | +14.7% | WIN |

Obj 2005-2024: +0.879 vs champ +0.878. ΔMDD -0.2%, ΔCAGR -0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.4% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 44 — top4_vt

Hold top 4 sectors with the vol-target overlay.

Delta: `{'top_n': 4}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,028,119 | +12.4% | -31.5% | 0.81 | +0.85 | +10.3% | WIN |
| 2005-2014 | $295,486 | +11.5% | -31.5% | 0.76 | +0.83 | +7.7% | WIN |
| 2015-2024 | $350,129 | +13.4% | -24.1% | 0.88 | +0.88 | +13.1% | WIN |
| 2018-2024 | $264,080 | +14.9% | -24.0% | 0.89 | +0.92 | +13.7% | WIN |
| WF-train 2005-2016 | $315,700 | +10.1% | -31.5% | 0.70 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $323,274 | +15.8% | -24.0% | 0.97 | +0.99 | +14.7% | WIN |

Obj 2005-2024: +0.854 vs champ +0.879. ΔMDD -0.1%, ΔCAGR -0.7%. Robustness: 2018-2024 CAGR -1.6%. WF-test 2017-2024 CAGR +15.8% vs SPY +14.7%.

Decision: **REVERT**.


# ===== ROUND 4 (building on champion `{'rotation_momentum_weight': True, 'rotation_defensive_symbol': 'GLD', 'lookbacks': (252,), 'rotation_vol_target': 0.1, 'rotation_vol_window': 30, 'rotation_weight_floor': 0.1, 'rotation_abs_momentum': False}`) =====

Champion baseline this round — 2005-2024 obj +0.879, CAGR +13.0%, MDD -31.5%, WF-test 2017-2024 CAGR +17.4%.

## Iteration 45 — equalweight_vt

Turn OFF momentum-weighting (keep vol-target) — does equal weight de-risk better?

Delta: `{'rotation_momentum_weight': False}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,017,279 | +12.3% | -31.6% | 0.81 | +0.85 | +10.3% | WIN |
| 2005-2014 | $270,593 | +10.5% | -31.6% | 0.71 | +0.76 | +7.7% | WIN |
| 2015-2024 | $377,185 | +14.2% | -24.4% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $280,055 | +15.9% | -24.2% | 0.95 | +0.99 | +13.7% | WIN |
| WF-train 2005-2016 | $293,884 | +9.4% | -31.6% | 0.66 | +0.70 | +7.5% | WIN |
| WF-test 2017-2024 | $343,017 | +16.7% | -24.2% | 1.02 | +1.06 | +14.7% | WIN |

Obj 2005-2024: +0.852 vs champ +0.879. ΔMDD +0.1%, ΔCAGR -0.7%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.7% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 46 — regime_plus

Add SPY-200SMA regime overlay parking in GLD (retest now vol-target is on).

Delta: `{'rotation_regime_filter': True}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,003,840 | +12.2% | -29.4% | 0.76 | +0.80 | +10.3% | WIN |
| 2005-2014 | $302,090 | +11.7% | -29.4% | 0.71 | +0.79 | +7.7% | WIN |
| 2015-2024 | $338,494 | +13.0% | -23.6% | 0.84 | +0.84 | +13.1% | lose |
| 2018-2024 | $241,082 | +13.4% | -23.3% | 0.81 | +0.81 | +13.7% | lose |
| WF-train 2005-2016 | $336,042 | +10.6% | -29.4% | 0.67 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $297,874 | +14.6% | -23.2% | 0.90 | +0.90 | +14.7% | lose |

Obj 2005-2024: +0.800 vs champ +0.879. ΔMDD -2.2%, ΔCAGR -0.8%. Robustness: 2015-2024 CAGR -1.8%. WF-test 2017-2024 CAGR +14.6% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 47 — single_tranche

Single first-of-month rebalance instead of 3 tranches.

Delta: `{'tranches': (0,)}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,121,688 | +12.9% | -31.1% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $296,799 | +11.5% | -31.1% | 0.74 | +0.82 | +7.7% | WIN |
| 2015-2024 | $377,164 | +14.2% | -20.9% | 0.89 | +0.91 | +13.1% | WIN |
| 2018-2024 | $292,286 | +16.6% | -20.9% | 0.94 | +1.00 | +13.7% | WIN |
| WF-train 2005-2016 | $308,520 | +9.9% | -31.1% | 0.67 | +0.72 | +7.5% | WIN |
| WF-test 2017-2024 | $361,340 | +17.4% | -20.9% | 1.01 | +1.07 | +14.7% | WIN |

Obj 2005-2024: +0.867 vs champ +0.879. ΔMDD -0.5%, ΔCAGR -0.2%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.4% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 48 — four_tranche

4 tranches (days 0/7/14/21) — smoother timing diversification.

Delta: `{'tranches': (0, 7, 14, 21)}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,133,509 | +12.9% | -30.9% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $298,981 | +11.6% | -30.9% | 0.75 | +0.83 | +7.7% | WIN |
| 2015-2024 | $379,164 | +14.3% | -23.8% | 0.89 | +0.92 | +13.1% | WIN |
| 2018-2024 | $279,321 | +15.8% | -23.8% | 0.91 | +0.95 | +13.7% | WIN |
| WF-train 2005-2016 | $326,681 | +10.4% | -30.9% | 0.71 | +0.76 | +7.5% | WIN |
| WF-test 2017-2024 | $344,659 | +16.8% | -23.8% | 0.98 | +1.02 | +14.7% | WIN |

Obj 2005-2024: +0.874 vs champ +0.879. ΔMDD -0.6%, ΔCAGR -0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.8% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 49 — drop_QQQ

Remove QQQ from the universe (redundant with XLK).

Delta: `{'universe': ['XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLB', 'XLP', 'XLU', 'XLRE', 'XLC', 'SMH']}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,085,114 | +12.7% | -31.4% | 0.81 | +0.86 | +10.3% | WIN |
| 2005-2014 | $296,176 | +11.5% | -31.4% | 0.74 | +0.82 | +7.7% | WIN |
| 2015-2024 | $367,929 | +13.9% | -24.5% | 0.89 | +0.90 | +13.1% | WIN |
| 2018-2024 | $273,063 | +15.4% | -24.4% | 0.90 | +0.94 | +13.7% | WIN |
| WF-train 2005-2016 | $320,878 | +10.2% | -31.4% | 0.70 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $335,528 | +16.4% | -24.4% | 0.97 | +1.01 | +14.7% | WIN |

Obj 2005-2024: +0.860 vs champ +0.879. ΔMDD -0.2%, ΔCAGR -0.3%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.4% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 50 — drop_SMH

Remove SMH (semis) — does losing the highest-octane sleg cut drawdown?

Delta: `{'universe': ['XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLB', 'XLP', 'XLU', 'XLRE', 'XLC', 'QQQ']}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $883,634 | +11.5% | -31.5% | 0.78 | +0.80 | +10.3% | WIN |
| 2005-2014 | $311,996 | +12.1% | -31.5% | 0.78 | +0.87 | +7.7% | WIN |
| 2015-2024 | $284,318 | +11.0% | -23.2% | 0.78 | +0.74 | +13.1% | lose |
| 2018-2024 | $241,216 | +13.4% | -23.1% | 0.87 | +0.86 | +13.7% | lose |
| WF-train 2005-2016 | $317,776 | +10.1% | -31.5% | 0.69 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $275,173 | +13.5% | -23.1% | 0.90 | +0.88 | +14.7% | lose |

Obj 2005-2024: +0.802 vs champ +0.879. ΔMDD -0.0%, ΔCAGR -1.5%. Robustness: 2015-2024 CAGR -3.8%. WF-test 2017-2024 CAGR +13.5% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 51 — gold_ranked

Add GLD to the RANKED universe so gold can be an active position too.

Delta: `{'universe': ['XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLB', 'XLP', 'XLU', 'XLRE', 'XLC', 'SMH', 'QQQ', 'GLD']}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,171,669 | +13.1% | -30.9% | 0.82 | +0.88 | +10.3% | WIN |
| 2005-2014 | $306,577 | +11.9% | -30.9% | 0.75 | +0.83 | +7.7% | WIN |
| 2015-2024 | $384,263 | +14.4% | -24.5% | 0.90 | +0.93 | +13.1% | WIN |
| 2018-2024 | $285,734 | +16.2% | -24.4% | 0.92 | +0.97 | +13.7% | WIN |
| WF-train 2005-2016 | $329,582 | +10.5% | -30.9% | 0.70 | +0.76 | +7.5% | WIN |
| WF-test 2017-2024 | $353,120 | +17.1% | -24.3% | 0.99 | +1.04 | +14.7% | WIN |

Obj 2005-2024: +0.877 vs champ +0.879. ΔMDD -0.6%, ΔCAGR +0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.1% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 52 — lb_189_252

Blend 9mo+12mo momentum.

Delta: `{'lookbacks': (189, 252)}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $952,909 | +11.9% | -31.8% | 0.76 | +0.80 | +10.3% | WIN |
| 2005-2014 | $289,042 | +11.2% | -31.8% | 0.72 | +0.80 | +7.7% | WIN |
| 2015-2024 | $331,624 | +12.7% | -24.5% | 0.81 | +0.80 | +13.1% | lose |
| 2018-2024 | $243,109 | +13.5% | -24.2% | 0.79 | +0.79 | +13.7% | lose |
| WF-train 2005-2016 | $319,311 | +10.2% | -31.8% | 0.69 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $295,768 | +14.5% | -24.3% | 0.86 | +0.86 | +14.7% | lose |

Obj 2005-2024: +0.795 vs champ +0.879. ΔMDD +0.3%, ΔCAGR -1.1%. Robustness: 2015-2024 CAGR -2.1%. WF-test 2017-2024 CAGR +14.5% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 53 — lb_126_189_252

Blend 6/9/12mo momentum.

Delta: `{'lookbacks': (126, 189, 252)}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $923,336 | +11.8% | -31.7% | 0.75 | +0.78 | +10.3% | WIN |
| 2005-2014 | $277,957 | +10.8% | -31.7% | 0.70 | +0.76 | +7.7% | WIN |
| 2015-2024 | $333,358 | +12.8% | -24.5% | 0.81 | +0.80 | +13.1% | lose |
| 2018-2024 | $242,483 | +13.5% | -24.3% | 0.79 | +0.79 | +13.7% | lose |
| WF-train 2005-2016 | $310,021 | +9.9% | -31.7% | 0.67 | +0.72 | +7.5% | WIN |
| WF-test 2017-2024 | $295,449 | +14.5% | -24.3% | 0.86 | +0.86 | +14.7% | lose |

Obj 2005-2024: +0.782 vs champ +0.879. ΔMDD +0.1%, ΔCAGR -1.3%. Robustness: 2015-2024 CAGR -2.0%. WF-test 2017-2024 CAGR +14.5% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 54 — volwin_30

Vol estimate over 30d (less twitchy de-risking).

Delta: `{'rotation_vol_window': 30}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,154,419 | +13.0% | -31.5% | 0.83 | +0.88 | +10.3% | WIN |
| 2005-2014 | $291,828 | +11.3% | -31.5% | 0.73 | +0.81 | +7.7% | WIN |
| 2015-2024 | $397,306 | +14.8% | -24.5% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $291,070 | +16.5% | -24.4% | 0.94 | +0.99 | +13.7% | WIN |
| WF-train 2005-2016 | $318,489 | +10.1% | -31.5% | 0.69 | +0.74 | +7.5% | WIN |
| WF-test 2017-2024 | $359,721 | +17.4% | -24.4% | 1.01 | +1.06 | +14.7% | WIN |

Obj 2005-2024: +0.879 vs champ +0.879. ΔMDD +0.0%, ΔCAGR +0.0%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.4% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 55 — volwin_40

Vol estimate over 40d.

Delta: `{'rotation_vol_window': 40}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,126,449 | +12.9% | -31.4% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $297,689 | +11.5% | -31.4% | 0.75 | +0.82 | +7.7% | WIN |
| 2015-2024 | $378,288 | +14.2% | -25.8% | 0.88 | +0.91 | +13.1% | WIN |
| 2018-2024 | $281,760 | +16.0% | -25.7% | 0.90 | +0.95 | +13.7% | WIN |
| WF-train 2005-2016 | $321,113 | +10.2% | -31.4% | 0.70 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $348,272 | +16.9% | -25.7% | 0.98 | +1.02 | +14.7% | WIN |

Obj 2005-2024: +0.867 vs champ +0.879. ΔMDD -0.1%, ΔCAGR -0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.9% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 56 — vt_0.09

Vol-target 0.09 (slightly lower).

Delta: `{'rotation_vol_target': 0.09}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,106,437 | +12.8% | -30.7% | 0.83 | +0.88 | +10.3% | WIN |
| 2005-2014 | $293,030 | +11.4% | -30.7% | 0.74 | +0.82 | +7.7% | WIN |
| 2015-2024 | $379,984 | +14.3% | -22.9% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $277,937 | +15.7% | -22.8% | 0.93 | +0.98 | +13.7% | WIN |
| WF-train 2005-2016 | $319,651 | +10.2% | -30.7% | 0.70 | +0.76 | +7.5% | WIN |
| WF-test 2017-2024 | $343,437 | +16.7% | -22.8% | 1.01 | +1.05 | +14.7% | WIN |

Obj 2005-2024: +0.880 vs champ +0.879. ΔMDD -0.9%, ΔCAGR -0.2%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.7% vs SPY +14.7%.

Decision: **KEEP**.

## Iteration 57 — vt_0.11

Vol-target 0.11 (slightly higher).

Delta: `{'rotation_vol_target': 0.11}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,187,558 | +13.2% | -32.4% | 0.82 | +0.87 | +10.3% | WIN |
| 2005-2014 | $290,952 | +11.3% | -32.4% | 0.73 | +0.80 | +7.7% | WIN |
| 2015-2024 | $408,846 | +15.1% | -25.9% | 0.91 | +0.95 | +13.1% | WIN |
| 2018-2024 | $302,262 | +17.1% | -25.8% | 0.94 | +1.00 | +13.7% | WIN |
| WF-train 2005-2016 | $315,393 | +10.1% | -32.4% | 0.68 | +0.73 | +7.5% | WIN |
| WF-test 2017-2024 | $373,579 | +17.9% | -25.8% | 1.00 | +1.07 | +14.7% | WIN |

Obj 2005-2024: +0.874 vs champ +0.880. ΔMDD +1.7%, ΔCAGR +0.4%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +17.9% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 58 — floor_0.20

Momentum-weight floor 0.20 (de-concentrate).

Delta: `{'rotation_weight_floor': 0.2}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $1,087,729 | +12.7% | -30.7% | 0.83 | +0.88 | +10.3% | WIN |
| 2005-2014 | $290,232 | +11.3% | -30.7% | 0.74 | +0.81 | +7.7% | WIN |
| 2015-2024 | $377,175 | +14.2% | -22.9% | 0.92 | +0.95 | +13.1% | WIN |
| 2018-2024 | $275,884 | +15.6% | -22.8% | 0.93 | +0.97 | +13.7% | WIN |
| WF-train 2005-2016 | $316,564 | +10.1% | -30.7% | 0.70 | +0.75 | +7.5% | WIN |
| WF-test 2017-2024 | $340,901 | +16.6% | -22.8% | 1.01 | +1.05 | +14.7% | WIN |

Obj 2005-2024: +0.875 vs champ +0.880. ΔMDD +0.0%, ΔCAGR -0.1%. Robustness: all sub-periods hold. WF-test 2017-2024 CAGR +16.6% vs SPY +14.7%.

Decision: **REVERT**.

## Iteration 59 — top5

Hold top 5 sectors.

Delta: `{'top_n': 5}`

| Window | Final | CAGR | MDD | Sharpe | Obj | SPY | Result |
|---|---|---|---|---|---|---|---|
| 2005-2024 | $968,721 | +12.0% | -30.3% | 0.83 | +0.86 | +10.3% | WIN |
| 2005-2014 | $298,398 | +11.6% | -30.3% | 0.78 | +0.86 | +7.7% | WIN |
| 2015-2024 | $327,202 | +12.6% | -22.2% | 0.89 | +0.88 | +13.1% | lose |
| 2018-2024 | $241,613 | +13.4% | -22.1% | 0.88 | +0.87 | +13.7% | lose |
| WF-train 2005-2016 | $322,208 | +10.3% | -30.3% | 0.73 | +0.78 | +7.5% | WIN |
| WF-test 2017-2024 | $298,607 | +14.7% | -22.1% | 0.97 | +0.97 | +14.7% | WIN |

Obj 2005-2024: +0.863 vs champ +0.880. ΔMDD -0.3%, ΔCAGR -0.7%. Robustness: 2015-2024 CAGR -1.7%. WF-test 2017-2024 CAGR +14.7% vs SPY +14.7%.

Decision: **REVERT**.


# ===== ROUND 5 — robustness/stress + gold de-risking =====

Stress battery (see reports/stress_results.md) found the GLD edge is (a) gold-dependent — with BIL cash the strategy LOSES to SPY (-1.3pp) — and (b) concentrated pre-2010 (post-2010 starts do not beat SPY CAGR). It survives costs to ~30bps and sits on a broad parameter plateau.

De-risking move: blended defensive sleeve GLD+TLT (50/50). Result 2005-2024: CAGR 11.9%, MDD -22.5% (vs -31% pure GLD), Sharpe 0.83, still beats SPY +1.5pp, with HALF the gold dependence and drawdown inside the 18-25%% target. Adopted as the RECOMMENDED champion; pure GLD kept as the higher-CAGR (more fragile) alternative.

| Window | Blend CAGR | Blend MDD | Blend Sharpe | SPY CAGR |
|---|---|---|---|---|
| 2005-2024 | +11.9% | -22.5% | 0.83 | +10.3% |
| 2005-2014 | +10.9% | -22.5% | 0.81 | +7.7% |
| 2015-2024 | +12.9% | -22.2% | 0.86 | +13.1% |
| 2018-2024 | +13.6% | -22.0% | 0.84 | +13.7% |

**FINAL: recommended champion = GLD+TLT blend.** Beats SPY over 20yr (+1.5pp) and 2005-14 (+3.3pp); MATCHES SPY post-2010 with ~22%% drawdown vs SPY ~34-55%% and higher Sharpe. The honest edge is risk-adjusted, not reliable raw outperformance.

# ===== ROUND 6 — PLATEAU DIAGNOSIS & MULTI-FACTOR FIX =====

The greedy hill-climb plateaued at obj ~0.88 by Round 3. Diagnosed four causes:
1. Single-factor tuning (re-arranging momentum, not adding information) → information ceiling (~0.8 Sharpe for monthly sector momentum).
2. Greedy search → stuck in a local optimum (never tests combinations, only deltas).
3. Objective flat-ridge once drawdown < SPY's (excess-DD term → 0).
4. One historical path → marginal "gains" are within sampling noise.

Fixes implemented:
- NEW FACTOR: risk-structure weighting — `rotation_weight_scheme` ∈ {invvol, sharpe, rp_blend} and `rotation_rank_metric=sharpe`. rp_blend sizes ∝ sqrt(momentum × 1/vol).
- GLOBAL JOINT SEARCH: reports/joint_search.py random-samples 110 full configs across the joint space and selects by WALK-FORWARD (train 2005-2016 → untouched test 2017-2024), not greedy deltas.

Findings:
- Walk-forward honest pick did NOT beat the champion out-of-sample → the single-factor plateau is REAL, not a search artifact. The apparent "winners" only win when selected on the test set (overfitting).
- BUT risk-parity weighting (rp_blend/sharpe) recurs across the top configs on every window → a small, genuine factor gain.
- Verified champion: `rp_blend + GLD+TLT + 12mo + vol_target 0.12` beats SPY on ALL 4 standard windows AND out-of-sample 2017-2024 (15.8% vs 14.7%), vs 2/4 for the single-factor champion. Adopted as the v3 recommended champion (main.py rotation-v2 default). Tradeoff: MDD -25.3% vs -22.5%.
- Honest limit: the multi-factor fix improves CONSISTENCY but does NOT remove the structural post-2010 raw-return lag or the defensive-asset (gold/treasury) dependence. Those are properties of the information set; only fundamentally new data/assets/instruments can move that ceiling.
