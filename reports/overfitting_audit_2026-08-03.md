# Overfitting audit — V7 PUSH-20 GATED v2

2026-08-03. Deflated Sharpe / PBO / minimum-backtest-length audit of the live champion.

**Why:** `trader/metrics.py` reported a raw Sharpe with no correction for search effort, while
`trader/rotation.py:209` documents "Swept 1260 configs". Nothing distinguished *real edge* from
*best of N tries*. That gap is now closed — `metrics.py` computes DSR/PSR natively (stdlib, no new
dependencies).

## Method

1. 135-config reconstruction of the risk-dial grid (`rotation_vol_window` × `rotation_position_cap`
   × `rotation_vol_cap` × `rotation_top_n`) through `run_rotation_backtest`, 2006-01-01..2026-06-18,
   5,146 shared trading days per config. Champion reproduces at Sharpe 0.833 vs the recorded 0.83.
2. Trial counts and the real Sharpe dispersion recovered from this directory's saved searches:
   `risk_dial_sweep.json` (1,260), `joint_search_results.json` (110), `opt_search_results.json` +
   `opt_refine_results.json` (16) = **1,386 documented trials**. Real Sharpe spread across the
   1,260: min 0.754, median 0.835, max 0.941.
3. PBO via CSCV, DSR via Bailey & López de Prado, cross-checked against the `purgedcv` reference
   implementation. The stdlib versions now in `metrics.py` agree with it to <1e-9.

## Result 1 — the edge is real

| n_trials | deflated benchmark SR* | DSR | min backtest yrs |
|---|---|---|---|
| 135 (reconstruction) | 0.077 | 0.9996 | 10.0 |
| 1,260 (real) | 0.135 | 0.9991 | 15.9 |
| 1,386 (real) | 0.136 | 0.9991 | 16.1 |

Champion Sharpe 0.833 against a deflated benchmark of 0.136. The strategy family's edge is **not**
explainable by search luck.

**But minimum backtest length is now the binding constraint: 16.1 years required, 20.4 available.**
Further searching on this same window raises the requirement toward the data on hand.

## Result 2 — the risk-dial tuning is not identifiable

**PBO = 0.425.** The in-sample-best config lands below median out-of-sample 42.5% of the time.
Across the 135 configs the full Sharpe spread is 0.111 and **72 of 135 sit within 0.05 of the best**.

Caveat: these configs are one strategy at different risk settings, so they are highly correlated.
PBO here answers "can these configs be told apart", not "does the family have edge".

## Result 3 — the RISK-DIAL v3 claim

`trader/rotation.py:208-214` calls vol_window 12→8 + max_weight 0.50→0.60 "a genuine Pareto move".
Checked against `risk_dial_sweep.json` — **the recorded numbers reproduce exactly**:

    before  CAGR +21.32%  MDD -32.53%  Calmar 0.655  Sharpe 0.88
    after   CAGR +21.78%  MDD -31.52%  Calmar 0.691  Sharpe 0.89

The bookkeeping is correct. The inference does not hold:

- **91 of 1,260 configs (7.2%) reach Calmar ≥ 0.691.** The chosen config is in the top 7%, not unique.
- Paired stationary block bootstrap (10,000 resamples, mean block 21d) through
  `run_rotation_backtest`: Sharpe Δ −0.0011 (95% CI −0.0121..+0.0106), CAGR Δ +0.0006
  (CI −0.0027..+0.0044), MDD Δ ≈ 0. **P(CAGR up AND drawdown shallower, jointly) = 0.242.**
- The +0.036 Calmar was a single-path measurement with no error bar.

**Confirmed from the same data:** the documented `vol_cap_bull` monotonicity is real — median Calmar
falls 0.642 → 0.602 as the cap rises 1.25 → 2.25 while median CAGR rises 19.16% → 21.97%. The
"beyond 1.5x is vol decay" reasoning holds; the data points at **1.25**, not 1.5.

## Result 4 — left on the table

The best Calmar in your own sweep is **0.779** — vt=0.30, cap=1.25, win=8, n=3, maxw=0.60 →
CAGR +20.34%, MDD **−26.10%**, vs the shipped 0.691 / +21.78% / −32.53%.

**6.4pp less drawdown for 1.4pp less CAGR.** Missed because only two of the five dials were changed.

## Result 5 — two simulators disagree

On what should be the same live champion:

| path | CAGR | MDD | Sharpe |
|---|---|---|---|
| `trader.rotation.run_rotation_backtest` (production) | 19.78% | −35.49% | 0.83 |
| `reports/opt_harness.simulate` (research) | 21.32% | −32.53% | 0.88 |

`opt_harness.py:24` sets `cost_bps=5`; the production verify note records 10bps. With 3,972 trades
over 20 years that plausibly accounts for much of the gap. It matters because **every risk-dial
ranking decision was made on the cheaper-cost harness**, which biases selection toward
higher-turnover configs.

## Recommended

1. Stop tuning the risk dial on this window — the MinBTL headroom is 4.3 years and shrinking.
2. Reconcile the two simulators, or re-rank the risk-dial finalists at the production cost.
3. Evaluate vt=0.30 / cap=1.25 against the shipped config on the production harness.
4. Pass `n_trials=` / `var_sharpe=` to `metrics.summarize()` in future sweeps so results arrive
   pre-deflated.

## Reproducing

DSR/PSR/MinBTL are now native in `trader/metrics.py` (stdlib):

```python
from trader.metrics import summarize
m = summarize(book.equity_curve, book.trades, cfg.starting_cash,
              n_trials=1386, var_sharpe=6.544e-06)   # per-observation variance
print(m["deflated_sharpe"], m["min_backtest_years"])
```

PBO needs a full trial matrix and stays out of the production module; it requires `purgedcv`
(`pip install purgedcv`) in a scratch venv — do not add it to this repo's pinned `requirements.txt`.
