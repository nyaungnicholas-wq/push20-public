# PUSH-20 v3 — Round 1 synthesis

Written 2026-09-17. Twelve tracks ran (T1, T2, T3, T5, T4a-e, T6, T7, T8). Every finding was
attacked by three adversarial verifiers on different lenses. 25 findings survived, 58 were
majority-refuted.

**Nothing here is a live configuration change.** v2 is trading real money as of 2026-09-16. This
round produces candidates and corrections to the record, nothing else.

**Three labels are used throughout and never mixed:**

- **MEASURED** — a number this repo printed. Artifact path given. Where I re-ran it while writing
  this synthesis, it says *(re-run here)*.
- **CITED** — from outside literature. Not measured on this strategy. Several citations in this
  round could not be verified at source; those are flagged individually.
- **SPECULATED** — reasoning with no measurement behind it. Said out loud every time.

---

## 1. THE HEADLINE

**1. In a taxable account this strategy's edge over buy-and-hold is somewhere between mostly gone
and comfortably intact, and which one depends on facts not in this repo.** MEASURED
(`reports/tax_drag.json`, re-run here): pre-tax 18.52% CAGR / -28.37% maxDD / 0.892 Sharpe, which
reproduces the published headline exactly. After tax the same trade log gives **13.89% at a 22%/15%
bracket and 11.21% at 37%/20%** — a drag of 4.63pp to 7.31pp. The mechanism is measured, not
asserted: **6,975 of 7,304 lot closes (95.5%) and $3,429,702 of $3,520,302 of realised gains are
short-term**, median holding period 53 days. Three things must travel with that number or it will be
read as worse than it is. The SPY after-tax comparison is an **assumed** dividend haircut, not a
measured benchmark tax simulation — under it the edge falls from +7.42pp to +0.59pp, but under the
like-for-like "both liquidate at the end" framing the same script prints **+1.84pp**, and a verifier
computed **+3.17pp held / +4.13pp liquidated at the 22% bracket**, which is the realistic row for a
$100k account whose 2006 realised short-term gain was $14. **0.67pp of the 7.31pp** is the
partial-2026 tax bill landing on the final bar (`cagr_ex_final_year_37_20` = 0.1188 in the same
artifact). And the wash-sale cost is bracketed **[0.41pp, 3.26pp]** at 37/20 depending on a reading
of IRC 1091 that the module's stated limits do not disclose. This authorises no code change. It is a
question about where the money sits, and the repo contains no record of the answer (see §6).

**2. The pre-registered risk gate is pointed at the wrong configuration, and the two estimators for
the right one disagree by a factor of two.** MEASURED (`reports/t7_sizing.out`): the register's rule
4 bar, "P(maxDD worse than -40%) no higher than the current 63.4%", reproduces as **62.7% for the
pre-v2 config retired on 2026-09-16** and **32.4% for live v2** on the same returns-bootstrap
estimator. But the panel bootstrap that re-runs the strategy on resampled prices
(`reports/t7_mc.out`, 16,750 simulations) puts **live v2 at 64.0% / 60.4% / 48.8% / 42.4%** at block
21/63/126/252. Same config, same window, 32.4% against 42-64%. The returns bootstrap is the weaker
instrument by its own author's docstring — it cannot rank any control that reacts to the equity path
— but the panel bootstrap destroys the 232-day momentum signal it resamples, so it is not obviously
the right one either. **Do not re-pin rule 4 to 32.4%.** For scale, SPY buy-and-hold on the same
panels prints 64.2% (block 126) and 64.0% (block 252), so the strategy is not worse than the
benchmark on this axis under either reading.

**3. The graded 18.52% is not the number the live account can realise, and the cost assumption
underneath it has never been measured on a real fill.** MEASURED (re-run here,
`reports/t6_execution.py --part ceiling`): the certifying engine fills at the close it decided on
(`reports/opt_harness.py:92`, `FILL_MODE = "close"`); the live loop queues for the next open.
Next-open fills print **17.69% CAGR / -30.70% maxDD / 0.856 Sharpe** — **-0.89pp CAGR and 2.33pp of
drawdown**, per-year mean -0.91pp, sd 1.74, t -2.41, p 0.0160, 14 of 21 years hurt. Separately,
**1 bps per side is worth 0.208pp of CAGR** at this turnover, so the whole cost question spans 8.4pp:
at 50 bps/side with live fill timing the strategy prints **9.49% CAGR / 0.523 Sharpe / -35.14%
maxDD** (re-run here), **below SPY's published 11.07%**. The only evidence for 9.5 bps is 105 logged
fills, and **every one of them went to the hardcoded Alpaca paper endpoint**
(`trader/alpaca_broker.py:24`, `_PAPER_BASE = "https://paper-api.alpaca.markets/v2"`, the only URL in
the file; the configured `ALPACA_KEY` also carries Alpaca's `PK` paper prefix). On the 11 fills whose
dates align correctly, measured execution was **+43.6 bps/side** — which is a measurement of Alpaca's
matching simulator, not of a spread anyone paid.

**Fourth, and it should be said plainly: the round found no new alpha.** Every mechanism the
literature sweep proposed was already implemented, already rejected here, or measured null. The one
attempt at genuinely fresh data — the Fama-French 1926-2005 industry transplant — produced a positive
result that did not survive adversarial review (§4). What the round produced instead is a corrected
record: three live-vs-harness fidelity breaks, a mis-aimed risk gate, an unmeasured cost assumption,
and a tax number nobody had computed.

---

## 2. WHAT WAS MEASURED

Only things this repo actually ran. Artifact paths are absolute-from-repo-root.

### T1 — After-tax drag · `reports/tax_drag.py` (502 lines), `reports/tax_drag.json`

MEASURED, re-verified by three verifiers and re-read from the artifact here.

| bracket (ST/LT) | CAGR | Sharpe | maxDD | Calmar |
|---|---|---|---|---|
| pre-tax | 18.52% | 0.892 | -28.37% | 0.653 |
| 22 / 15 | 13.89% | 0.695 | -30.54% | 0.455 |
| 32 / 15 | 12.14% | 0.617 | -32.99% | 0.368 |
| 37 / 20 | 11.21% | 0.576 | -34.22% | 0.327 |

- Long-term rate is near-irrelevant: 37/15 = 11.26% vs 37/20 = 11.21%, a 0.05pp difference.
- Tax makes **drawdown worse** by 2.2-5.9pp, because a good year's bill is debited before the
  following bad year. One verifier showed the size of that effect is partly a modelling convention —
  amortising the same bill across the year instead of debiting it on one bar cuts the maxDD penalty
  from -5.85pp to -1.65pp at 37/20 — so treat the maxDD column as convention-dependent.
- Wash sales (`wash` block in the JSON): gross realised losses **$9,170,375**, disallowed
  **$6,957,832 (75.9%)**, permanently stranded **$281,321**, still deferred at window end **$0**.
  Cost of the rule as shipped: **+0.28pp (22/15) / +0.41pp (37/20)**. Under a stricter,
  acquisition-triggered reading of IRC 1091 that one verifier implemented as a minimal diff, the
  same cost is **2.08pp / 3.26pp**. Both readings reconcile exactly to the allowed-gain totals, so
  this is a statutory-interpretation fork, not a bug.
- Not measured: state tax, HIFO/specific-ID lot selection, a real SPY after-tax simulation.

### T2 — Deep history · `reports/ff_industry.py` (535 lines), `data/ff_cache/`

MEASURED and reproduced independently by all three verifiers: transplanting the live flag set into
the real engine on ETF data prints **CAGR 18.58% / Sharpe 0.894 / maxDD -28.37% / 1,652 rebalances**
over 2006-01-01..2026-09-02, against the published 18.52% / 0.892 / -28.37%. The ~0.06pp gap is a
data-vintage drift that appears in every track's re-run this round and in both legs (SPY reprints as
11.10% against the published 11.07%), so it is a cache refresh, not a config mismatch.

What that anchor does **not** cover, and this is a real defect: `ff_industry.simulate` is an
independent ~108-line reimplementation, and its self-check case (g) — the only case that names the
leverage identity — is vacuous. Read at `reports/ff_industry.py:519-521` (confirmed here):

```
# (g) leverage identity: at scale 1.0 the levered path must equal the unlevered one
r1 = simulate(px, idx, mkt, rf, list(ind.columns), dict(LIVE, lever=True),  0, 2000)
r0 = simulate(px, idx, mkt, rf, list(ind.columns), dict(LIVE, lever=False), 0, 2000)
assert len(r1["curve"]) == len(r0["curve"])
```

It compares path lengths, never values. A verifier ran those two simulations: final equity 53,615.05
levered vs 48,082.08 unlevered, max relative difference 0.343 — and the assert still passes. A
separate verifier mutation-tested the whole selfcheck: 50x expense ratio, zeroed trading cost, 2x→5x
multiplier and zeroed risk-free accrual each still printed `SELFCHECK OK (7 cases)`. So the FF
transplant's levered path is unchecked, and that is the path every deep-history return number rides
on. **"7 cases" is 6 real cases.**

The deep-history *results* did not survive. See §4.

### T3 — Survivorship gate · `reports/pit_universe.py`, `data/pit/`

MEASURED (artifacts read here):

- Point-in-time S&P 500 membership reconstructed free from Wikipedia revision history: **3,144
  revisions 2005-09-14..2026-09-04; 234 of 248 month ends** covered (2007-03-31..2026-08-31); 974
  distinct tickers ever members, 503 at the final month end.
- **yfinance recovers 128 of 471 removed tickers = 27.2%** (`data/pit/coverage.json`: n_removed 471,
  n_recovered 128, n_sparse 337, n_ticker_reuse 6). Against the script's 90% gate: **BLOCKED.** A
  verifier re-fetched 100 sparse tickers one at a time and 0 of 100 would pass the span + jump test,
  so the batch-download objection does not rescue it.
- **Ticker reuse silently concatenates two companies.** 10 of 134 naively-recovered removed tickers
  show a one-day move above 200% (0 of 503 current members do): CBE 3,399,900%, TIE 810,711%, BOL
  706,122%, CFC 449,900%. Unfiltered, the point-in-time leg printed 41.25% CAGR at Sharpe 0.24 with a
  single +8,739.84% month; filtered, 11.91% at 0.62. Two verifiers established the corruption is
  interleaved wrong-issuer prints, not a chronological splice at a reuse seam — which matters,
  because a jump filter cannot detect a smooth wrong-issuer series.
- The bias legs in `data/pit/bias.json`: survivors-always **29.69% CAGR / -50.96% / Sharpe 1.17**;
  point-in-time fetchable subset **11.91% / -54.37% / 0.62**; gap **17.79pp**.

Two honesty notes. The often-quoted intermediate "leg C" (13.91%, survivors with point-in-time
timing, giving the +15.78pp membership-timing split) is **prose only** — `bias.json` contains exactly
two legs and `run_bias()` builds exactly two universes, so that decomposition is not reproducible
from what shipped. And a verifier showed the live change-log table was **moved, not deleted** — it
lives at *Historical components of the S&P 500*, one link away, with 407 change rows back to 1976 —
so the 2007 floor is the parser's, not Wikipedia's.

### T5 — Leveraged-ETF decay · `reports/lev_decay.py`, `reports/lev_decay.json`

MEASURED (fields read from the JSON here):

- `^IRX` 2006-2026: mean **1.686%**, median **0.503%**, **53.6% of days below 1.0%**, last value
  **3.772%** — a **+208.6 bps** gap between the average financing rate the backtest enjoyed and
  today's. Re-running the whole history with only the rate level changed costs **-1.02pp of CAGR**
  (`rate_level_cost_pp`).
- Sleeve variance drag **158.65 bps/yr** (225.73 on levered days). Mean 2x sleeve weight **0.4207**.
- **The drag is not recoverable by changing instrument.** Over 3,500 real holding spells averaging
  3.0 days, daily reset versus a never-rebalanced static margin position is **-6.2 bps/yr** — the
  reset *lost* 6 bps, it did not cost them. Verifiers extended this to min_hold 5 and 10: -13.8 and
  -23.1 bps/yr, i.e. it grows with hold length but stays an order of magnitude below the drag rate.
- The counterfactual block confirms the direction: real 2x funds **18.52%**, margin-1x at 0%
  financing **19.14%** (unattainable), margin-1x at **^IRX+50bps 18.12%**, at +100bps **17.87%**.
  **At any purchasable rate the 2x funds win.** (The *significance* of that gap was refuted — see §4.)
- Delivered daily beta of the funds against the ranked ETFs ranges **1.616 (UCC) to 2.095 (UYM)**,
  mean 1.92. Two verifiers showed the decision-relevant statistic is the volatility ratio, which is
  **1.976** — so the funds deliver ~99% of the sized risk, not 96%.

### T4a — Momentum crashes · `reports/t4a_boot.py`, `reports/t4a_ff_momvol_probe.py`

MEASURED on FF 10-Industry daily, **25,933 usable days = 102.9 years**: swapping the live
basket-volatility scaler for a Barroso/Santa-Clara momentum-volatility scaler moves paired Sharpe
**+0.035 in favour of the existing rule**, boot p5 -0.042, p95 +0.119, P(>0) 75.3%, stable across
seeds 7/11/23/99. **Null. Do not ship.** One verifier added the tail test the script omits: the
challenger is worse on every tail axis (maxDD -96.75% vs -93.41%, worst day -58.03% vs -34.10%).

Caveat that must travel with it: the probe widens the leverage clip to [0.25, 3.00] to make the
comparison visible. Under the live [0.50, 1.25] band the exposure is pinned at the cap 91.4% of days
and the difference shrinks to +0.021. The null holds; the test is less powerful than "102.9 years"
suggests.

### T4b — Residual momentum · `reports/t4b_residual_momentum_power.py`, `..._beta_bet.py`

MEASURED, 2006-01-03..2026-09-01:

- At the live 3-day horizon, raw momentum's information coefficient is **+0.0403 (NW t +3.99)**;
  beta-residualised is **+0.0241 (t +2.19)**. **Paired difference -0.0162, NW se 0.0049, t -3.31, p
  0.001.** At H=21 the paired difference is -0.0314 (t -2.89). Residualising this cross-section makes
  the ranking measurably *worse*.
- The premise checks out: PC1 of the 9-sector correlation matrix is **74.3%**, mean pairwise
  correlation **0.706**, mean single-factor R² vs SPY 71.9%.
- Market-adjusted momentum with beta forced to 1 is an **exact rank no-op** (mean Spearman +1.0000 on
  all 5,198 days, top-3 overlap 100.0%) — but a verifier showed it is *not* a trade no-op, because
  the live `>0` absolute-momentum gate reads levels: the gated basket differs on **634 of 5,198 days
  (12.2%)**, and that variant is algebraically the `rotation_dual_momentum` flag the repo already
  rejected three times.
- Power: the paired design resolves gaps down to 0.0098 at H=3; a levels comparison would have been
  2x looser and would have missed this result. A verifier's block bootstrap independently reproduced
  the NW standard error to four decimals, so the significance is not a bandwidth artifact.

Scope limit, established by verifiers and worth carrying: the effect is era-concentrated
(2011-2015 paired difference +0.0012, t +0.25), the only slice of the cache outside the fitted window
(2003-12..2005-12, 524 days) shows the **opposite sign**, and in return space rather than IC space
the same paired design gives |t| 1.38-2.09 — i.e. the window cannot resolve the 1.6%/yr return
difference the IC result implies.

### T4c — Time-series momentum · `reports/t4c_absmom_gate.py`, `reports/t4c_gate_attrib.py`

MEASURED:

- **The brief's premise was wrong.** PUSH-20 already has an absolute-momentum gate:
  `trader/rotation.py:180` sets `rotation_abs_momentum: True`, applied at line 531 as
  `ranked = ranked[ranked > 0.0]`; the harness mirror is `min_score=0.0` at
  `reports/opt_harness.py:269`. It binds on **580 of 5,200 sessions (11.15%)** and puts the book
  fully defensive on 297 (5.71%).
- Ablation: gate ON 18.58% / -28.37% / 0.894; gate OFF 17.78% / **-48.91%** / 0.825. 2008: **+5.55%
  ON vs -35.24% OFF**.
- Attribution of the 2008 headline, measured by swapping the defensive sleeve: gate alone moves 2008
  from -35.24% to **-10.54%** (BIL cash sleeve), and the GLD+TLT sleeve adds a further **+16.09pp** to
  +5.55%. On drawdown the split is the reverse: the gate supplies 22.03pp of protection and the sleeve
  gives back 1.49pp.
- Threshold sensitivity: maxDD is identical at -28.37% for `min_score` in {-0.05, -0.02, 0.00,
  +0.02, +0.05}; CAGR over that band runs 16.97%-18.88%.

Verifiers established that the gate's *benefit* is one episode: excluding 2008-2009 both arms draw
-28.37% at the same 2020 trough, and ex-2008 the gate costs ~2.15pp of CAGR per year. Paired block
bootstrap of the daily difference gives dCAGR +0.78pp ± 2.54 (z 0.30). Keep the gate — it is live and
cheap tail insurance — but it is n=1 evidence, not a distributional property.

### T4d — Formation window · `reports/formation_window_probe.py`

MEASURED. Two of these are code facts and they are the useful output of the track:

- The live window is **232 trading days = 336.8 calendar days = 11.06 months** with no skip; EMA(9)
  pulls the measurement endpoint back only **3.33 trading days**. So the deployed signal is a 12-0
  (strictly 11-0), not the stock canon's 12-1.
- **`rotation_skip_days` is inert in the live book.** At the live `rotation_signal_ema = 9`,
  `trader/rotation.py` calls `momentum_scores_ema(...)` without a skip argument — the function has no
  skip parameter at all — while `reports/opt_harness.py` *does* honour skip under the EMA. Measured:
  setting skip=21 changed the live picks on **0 of 20 dates** at ema_span 9 and on 16 of 20 at
  ema_span 1 (0/500 vs 363/500 over the full sample). A harness-measured 12-1 improvement would not
  be executable live.
- **Live and harness measure different lookback lengths for the same flag.** `trader/rotation.py:460`
  computes `arr[p]/arr[p-lb+1]` (231 returns); `reports/opt_harness.py:144` computes `a[p]/a[p-lb]`
  (232 returns). On 2026-09-16 at ema_span 1: XLF live 0.093733 vs harness 0.063328. The top-3 pick
  set differs on **85 of the last 2,000 sessions (4.25%)**, and 258 of 5,723 (4.51%) at the live
  ema_span 9.
- Signal sensitivity to the window choice: moving to 12-1 changes the top-3 on **46% of days**; the
  Novy-Marx echo window changes it on **81%**.
- Data hygiene: `data/_cache` contains a corrupt duplicate batch — 13 `*_1997-11-07_2024-12-31`
  files share one identical 6,547-row price series. Unreachable from the canonical engine (which
  fetches from 2003-01-01 and keys the cache by exact filename), so no published number is affected,
  but it broke an ad-hoc probe before it was found.

### T4e — Regime filters · `reports/regime_state_power.py`

MEASURED — this is a power result, and it is the honest answer to the whole regime direction:

- 2006-2026, SPY monthly, T=248. Trailing-36m return < 0: **27 months, 1 contiguous episode**,
  AR(1) 0.96, N_eff 5.3. Trailing-24m: 2 episodes. Trailing-12m: 7 episodes.
- Cross-sectional dispersion above median: 124 months, **45 episodes**, AR(1) 0.29, N_eff 137.3.
  Minimum detectable difference in mean monthly return at 80% power: **2.09%/month
  autocorrelation-adjusted**, roughly 25pp annualised in SPY-volatility units.
- The same measurements on Fama-French 30-industry 1926-2005 (954 months): 36m DOWN **11 episodes**,
  N_eff 49.1; dispersion **160 episodes**, N_eff 480.0.

Read plainly: **the 2006-2026 window cannot test a market-state conditioner at all**, and can test a
dispersion conditioner only for effects far larger than any the literature reports. Any conditioner
work has to be built on the pre-1990 panel first. (One verifier correctly notes the dispersion
advantage of the FF panel is ~3.5x, not the 5-11x the bear-state counts suggest.)

### T6 — Execution · `reports/t6_execution.py` (writes no artifact — see §5)

MEASURED, re-run here:

```
fill at decision close (headline)  CAGR 18.58%  Sharpe 0.894  maxDD -28.37%  fallbacks 0
fill at next open (live today)     CAGR 17.69%  Sharpe 0.856  maxDD -30.70%  fallbacks 52
fill at next close (MOC queued)    CAGR 17.59%  Sharpe 0.850  maxDD -30.66%  fallbacks 0
cost sensitivity: 0.0 bps -> 20.57% | 9.5 -> 18.58% | 15.0 -> 17.44% | 40.0 -> 12.38%
                  (-0.2083pp of CAGR per bps per side at the 9.5 operating point)
```

Scenario grid, also re-run here:

| fill mode | 9.5 bps | 20 bps | 50 bps |
|---|---|---|---|
| decision close | 18.58% | 16.41% | 10.41% |
| next open (live) | **17.69%** | 15.51% | **9.49%** |

SPY buy-and-hold published: 11.07%. The bottom-right cell loses to it.

- Annual one-way turnover **17.30x equity** (mean; range 1.01x-30.02x over 8,009 logged trades).
- Capacity: at the live equity of **$97,399**, a peak position is **13.71% of UCC's 3-month average
  dollar volume** ($20,311 against $148,174 ADV), 7.40% of UXI, 5.58% of UYM, 2.45% of RXL. Four of
  the ten sleeves route to 2x ETFs under $1m/day. A verifier measured that 95.9% of all dollars the
  strategy puts through UCC arrive in orders above 10% of ADV, and that the four thin names carry
  9.55% of traded dollars since 2019 — so an extra 50 bps/side on them is ~0.83pp/yr. The live
  account has never actually traded UCC; only UXI of the four, twice, at $7,702.
- **All 105 logged fills are paper.** `trader/alpaca_broker.py:24` defines the paper base and line 43
  uses it unconditionally; there is no live trading host anywhere in the repo (a verifier's
  `git log -S` across all reachable commits found none has ever existed), and the configured key
  carries the `PK` paper prefix. The 2026-09-16 orders are in `data/pending_orders.json` with Alpaca
  order ids from that same path.
- The fill instrumentation benchmarks most rows against the wrong session (54% / 54% / 25% / 100% of
  fills inside their recorded day's High/Low by month). The misalignment is real; the *named* root
  cause (a UTC date slice) was refuted — that code postdates every row in the log.

### T7 — Risk and sizing · `reports/t7_sizing.py/.out`, `reports/t7_mc.py/.out`, `reports/t7_frontier.py`

MEASURED (both .out files read here):

- Method A (returns bootstrap, block 15, 5,000 paths): **live v2 32.4%, pre-v2 62.7%** P(maxDD<-40%),
  median maxDD -36.3% and -42.7%.
- Method B (panel bootstrap, 16,750 strategy re-runs): **live v2 baseline 64.0% / 60.4% / 48.8% /
  42.4%** at block 21/63/126/252, median maxDD -43.2% to -38.6%.
- **`rotation_position_cap = 0.60` is inert, not weak.** max_weight from 0.20 to 1.00 produces
  identical CAGR/maxDD/Sharpe, and the 0.20 and 1.00 equity curves are bit-identical over 5,199 bars.
  Equal weighting plus the renormalisation after the capping loop cancels it exactly. Verified
  independently in the live allocator too (0 of 1,048 rebalance dates differ). Under the pre-v2
  return-proportional weighting it did bind (0.34 → 18.16%/-31.26%; 1.00 → 19.30%/-32.88%).
- **Every slower volatility estimator worsens the tail.** At block 126: EWMA hl=5 +3.8pp (z +4.44),
  hl=10 +6.4pp, hl=21 +7.8pp; fixed window 20d +5.2pp, 40d +8.2pp, 60d +9.2pp (z +6.95). Keep the
  8-day window. (Scope correction from verifiers: the full six-rung ladder only ran at block 126;
  blocks 21/63/252 carry two rungs, so "monotone at every block length" is not established.)
- The volatility overlay is **not really a volatility target**. It is pinned at its effective cap or
  floor on ~97% of acting days; only **43 of 1,651 acting days (2.6%)** have exposure genuinely set
  by the volatility estimate. (The .out file's "26.1%" residual counts days pinned at the 1.0x
  bear/VIX caps as estimator-determined; three verifiers independently corrected this, and the
  correction makes the finding stronger, not weaker.)
- Kelly: unlevered base CAGR 15.50%, arithmetic mean 16.35%/yr, vol 19.52%/yr, **f\* = 3.93x ± 1.13**,
  block-bootstrap p5 2.25 / p50 3.96 / p95 5.86. Deployed exposure mean 1.179x → **c ≈ 0.30**. (The
  second route to c, via the drawdown tail, was refuted — see §4.)
- Breadth: mean pairwise sector correlation **0.705**; effective N is **1.25 at top_n=3 and 1.36 for
  all ten**. The whole universe is worth 1.36 independent bets. top_n is an exposure dial, not a
  diversification control.

### T8 — Adversarial audit · `reports/t8_audit.py`, `reports/t8_fidelity.py`, `reports/t8_xlc.py`

MEASURED. Three live-vs-certifier fidelity breaks, all confirmed by reading the code:

1. **Fill convention** (above): -0.89pp CAGR, 2.33pp deeper drawdown. `reports/holdout_v2.py` and
   `live_checkup.live_sim_config()` never change `FILL_MODE`, so the sealed holdout was graded at
   the decision close too.
2. **Momentum window off-by-one** (T4d above): 4.25% of pick sets differ.
3. **XLC leverage sizing.** XLC is the only traded sector with no 2x ETF. The harness sizes that 1x
   fallback at *levered* notional (`opt_harness.py:534`), the live allocator at the *unlevered*
   weight (`rotation.py:983`, under its own comment "hold 1x at full unscaled weight"). Measured gap
   **8.32pp of equity on 410 of 4,902 scored sessions (8.4%)**, all of them post-June-2018. A
   verifier priced the convention swap: Calmar 0.653 → 0.593, maxDD -28.37% → -31.05%.

And three process defects:

4. **`live_checkup.live_sim_config()` reads 18 of the 26 `DAILY_CHAMPION_FLAGS`.** A verifier
   corrupted six of the unread flags in-process — including `rotation_two_way_vol`, which gates the
   entire leverage path — and the **whole 86-test suite still passed**. Two of the eight are
   conditionally read and separately pinned, so the genuinely blind set is six.
5. **The test suite has zero coverage of the strategy math.** 86 tests pass in ~1.5s (re-run here).
   A verifier traced the suite with `sys.settrace` filtered to `rotation.py`, `rotation_live.py` and
   `opt_harness.py`: of `select_targets`, `momentum_scores`, `momentum_scores_ema`,
   `turbo_allocation`, `_two_way_vol_scale`, `_cap_weights`, `_basket_realized_vol`, `_realized_vol`,
   `opt_harness.simulate` and `run_rotation_cycle`, **the hit set is empty**. A second verifier ran
   seven separate mutations — sort direction flipped, top_n off-by-one, position cap no-op'd, vol
   scale pinned to 1.0, a 2x ETF re-routed to the wrong ticker, weights skewed, and the 2x divisor
   changed so live leverage doubles — and **all 86 tests passed on every one**. Flag *values* are
   pinned; the code that consumes them is not. This is why the three fidelity breaks survived.
6. **`python main.py rotation-live --live` without `--scheduled` sends real orders while bypassing
   the risk gate, the market-open check, min_hold_days, the data-freshness assert and the session
   idempotency key.** A verifier drove `run_rotation_cycle` twice against a stub broker with
   unchanged positions: **`run 1 orders: 5, run 2 orders: 5, identical: True`**, 1,664 shares ordered
   across two runs against a single-run intent of 832, with `risk_gate` called 0 times. The
   registered scheduled task does pass `--scheduled` (`register-tasks.ps1:93`, confirmed here), so
   the cron path is safe; the hazard is a hand-typed command.
7. A silent fail-open: `trader/rotation.py:856` guards the VIX gate with
   `if vsym in close.columns and pos >= 0:` and has no else branch, so a missing `^VIX` download
   returns the ungated scale with no log or alert. Measured by a verifier: `^VIX` missing produces
   1.2500x, byte-identical to `^VIX = 12`. The gate's own worth is modest — removing it entirely
   buys 0.32pp of CAGR and costs 1.53pp of maxDD (18.90% / -29.90% vs 18.58% / -28.37%, re-run
   here) — but the failure mode is invisible either way.

Also measured and **clean**: the yfinance XLF/XLRE 2016 spin-off does **not** corrupt the momentum
signal (largest XLF daily move in the window -1.88%; the ex-date itself -0.95%), and live-vs-harness
price frames reconcile to 0.000015 (XLK) and 0.000122 (SPY). The memory note
`reference_yfinance_xlf_spinoff_is_a_split` **does** still reproduce — `yf.Ticker("XLF").splits`
returns a 1.231 factor on 2016-09-19 and the raw closes carry it — so that note should be kept, not
retired.

---

## 3. WHAT THE LITERATURE SAYS IS MISSING

Short answer: almost nothing this strategy does not already have. Four of the five sweep tracks
concluded the mechanism was already implemented, already rejected here, or measured null. What
remains, honestly scored:

**Caveat on this whole section.** Several papers could not be read at source (ScienceDirect, Wiley,
SSRN and T&F returned 403s during the sweep). Verifiers confirmed the Barroso/Santa-Clara,
Zaremba 2019, Medhat-Schmeling, Hanauer-Windmuller, Cederburg and Nucera citations exist and match
the quoted text where the abstracts were retrievable, and found that **two quoted figures could not
be located in their stated sources** (a "half the volatility" line attributed to Blitz/Hanauer/
Vidojevic 2020, whose abstract contains no numbers; and a "Sharpe 0.47-0.68" range attributed to
Hanauer-Windmuller). Treat every number in this section as CITED and unverified unless stated.

**(a) Idiosyncratic momentum.** *Mechanism:* rank sectors on the component of momentum orthogonal to
the market factor rather than on total return, on the theory that the common-factor component carries
crash risk without carrying information. *CITED:* Hanauer & Windmuller report global iMOM Sharpe 1.65
against plain momentum's 0.57, maxDD -7.04% vs -36.96%. *MEASURED here, against it:* on this
ten-ETF cross-section beta-weighted residualisation reduces ranking IC by 40-60% (paired -0.0162,
t -3.31). *Exact test that would settle it:* run the same residualised ranking on the Fama-French
30- and 49-industry daily panels, 1926-2005, paired against the raw ranking, scored with a block
bootstrap on the paired Sharpe difference. That is a cross-section this config was never fit to, and
the loader already exists (`reports/ff_industry.py`). **Nothing on 2006-2026 can settle it.**

**(b) Positive-semivariance volatility scaling.** *Mechanism:* scale exposure by downside deviation
rather than full variance, so upside volatility does not de-lever the book. *CITED:* Nucera reports
WMLvar+ at Sharpe 1.07 / 1.10 / 1.07 (full leverage / capped 1.5x / unlevered) against WMLvar's 1.08
/ 1.08 / 1.02, i.e. +0.02 to +0.05 under a binding cap. *Exact test:* change `roll_vol` in
`reports/t4a_ff_momvol_probe.py` to a downside semivariance of the held basket, re-run
`reports/t4a_boot.py`, and require the p5 of the paired difference against `basket8` to clear zero.
*Caveat a verifier established:* under the live [0.50, 1.25] clip the scaler is pinned ~91-97% of
days, so the live configuration structurally cannot express it — the test would have to be read as a
statement about the mechanism, not about a shippable change.

**(c) Cross-sectional dispersion as a conditioner.** *Mechanism:* momentum's subsequent premium is
lower when the cross-section is widely dispersed, which is a measurement the current overlay is
structurally blind to (it sizes on basket volatility, a second moment in time, not across names).
*CITED:* Stivers & Sun, JFQA 45(4) 2010, report dispersion negatively related to the subsequent
momentum premium. *MEASURED here:* dispersion is genuinely distinct from realised volatility on this
data (a verifier measured R² of 18.0% regressing log dispersion on realised vol and VIX), but the
2006-2026 window's minimum detectable effect is 2.09%/month — it cannot settle it either way.
*Exact test:* build the conditioner on FF 1926-2005 (160 dispersion episodes vs 45 here), freeze the
rule and its parameters in writing, then take **one** confirmatory read on 2006-2026. A verifier
found dispersion is *more* entangled with realised volatility in the FF panel (r = 0.602) than here,
so the lab has its own confound and the pre-registration must say how it will be handled.

**(d) Turn-of-the-month.** *Mechanism:* month-end-adjacent daily returns are systematically larger,
so where a trade lands relative to month end is a cost, not a signal. *CITED:* a 2026 30-country
study reports ~10 bps/day at the turn of the month. *MEASURED:* 991 of 5,199 days (19.1%) fall in
D-1..D+3. *Exact test:* bucket realised fill slippage by D-1..D+3 versus the rest of the month. **Do
not run it yet** — a verifier ran it on the current log and got TOM n=13 (clustered on 3 dates) vs
non-TOM n=92, difference +5.62 bps with SE 86.03, minimum detectable effect 244 bps against a
target effect of 10 bps. It needs the real-fill instrumentation from §5 first.

**Explicitly closed by this round** (so nobody re-opens them):

- *Barroso/Santa-Clara volatility scaling* — already implemented as `rotation_basket_vol: True`
  since 2026-06-23; the swap measures null over 102.9 years.
- *Absolute momentum / TSMOM gate* — already implemented and live; ablation shows it is worth 20.5pp
  of drawdown in 2008 and ~2.15pp/yr of CAGR in every other year.
- *Dual momentum (beat SPY's momentum)* — already implemented behind `rotation_dual_momentum`,
  already tested and reverted three times (`reports/iteration_log.md` iterations 4, and
  `reports/validation_fix4.json`: CAGR 19.17% vs baseline 19.13% for 5.2pp more drawdown). And the
  gate is *looser*, not stricter, in a crash, because `spy_mom` is negative there.
- *January momentum seasonality* — the tax-loss-selling mechanism cannot operate on sector ETFs, and
  21 Januarys cannot detect the literature effect anyway.
- *Skip-month / 12-1 formation* — already swept and reverted twice, and structurally inert in the
  live loop at `ema_span = 9`.

---

## 4. WHAT DIED AND WHY

58 findings were majority-refuted. The pattern is worth stating before the list: **the verifiers
almost never broke the arithmetic.** In case after case all three reproduced the printed numbers
exactly and then killed the sentence built on top of them. The round's characteristic failure was a
correct measurement wearing an overstated inference.

**The one piece of genuinely fresh evidence did not survive.** The Fama-French 1926-2005 transplant
claimed the cross-sectional industry-momentum effect is present in 79 unfitted years at **+5.70pp/yr
(t +3.67)** on 30 industries and +6.35pp (t +3.61) on 49. All three verifiers refuted it, on four
independent grounds:
- The quoted excess is a **levered** strategy measured against an **unlevered** equal-weight
  benchmark. The same script prints the like-for-like `selection 1x` line three rows above:
  **+2.95pp and +3.34pp**. About half the headline is a leverage overlay, not momentum.
- The reported t-statistics assume IID daily returns — the script's own docstring calls this "an
  UPPER bound on significance". Newey-West at lag 21 takes the selection-only figures to t ≈ +2.05.
  A **paired block bootstrap of the Sharpe difference**, which is what the register's rule 2 actually
  demands, gives **z +0.13 to +0.35** — i.e. the mechanism does not clear one standard error.
- **It inverts at 2x cost.** Turnover is 11.9-15.1x/yr and the run charges the 2026 ETF assumption of
  9.5 bps back to 1927. At 19 bps the paired dSharpe goes **negative** for both panels.
- **Drawdown is worse than the benchmark**: -86.54% vs -81.92% (30 industries), -84.34% vs -83.20%
  (49). Rule 4 fails outright.
- The "two independently-constructed partitions" claim is false: the 30- and 49-industry sets are two
  SIC cuts of the same CRSP universe. Measured correlations — equal-weight benchmarks +0.988,
  strategies +0.841, **daily excess series +0.691**. One cross-section counted twice.

Two follow-on deep-history claims died with it. The **-86.5% drawdown** headline was refuted 2-of-3:
it is a 3-of-30 book (10% of the cross-section) against a live 3-of-10 (30%), and the script's own
breadth control prints -77.18% with a 7.2-year recovery, better than both benchmarks. And the
**"most of the 1930s catastrophe is concentration"** rebuttal died too — the breadth control is
confounded with cash, because `budget = len(picks)/top_n` parks unfilled slots at the risk-free rate,
so raising top_n mostly de-risks. Closing that channel, the breadth effect on the full-period
drawdown falls from 9.36pp to **0.25pp**.

**Tax claims that died:**
- *"Short-term classification is the sole cause of the drag."* Refuted 3-of-3. Re-taxing the identical
  trade log with every close forced long-term moves 11.21% → 14.19-14.22%, so classification explains
  **~41% of the drag at 37/20 and ~27% at 22/15**. The dominant term is the loss of deferral: the book
  realises itself every year (residual unrealised at window end is **-$275,862**, i.e. a loss), so tax
  compounds out of the base at any rate. The artifact's own `hold_sweep` shows mhd=300 reaching 100%
  long-term and still dragging 14.46% → 12.38%.
- *"Moving to a tax-deferred account is the largest available improvement."* Refuted 3-of-3, on the
  cleanest possible ground: **the pre-tax row IS the tax-deferred case by construction**, and every
  published number for this strategy was computed there. Against the strategy as published and traded
  it is worth **0.00pp**. The 4.63-7.31pp exists only against an after-tax baseline created in the
  same session.
- *"min_hold_days 21 or 60 is better after tax."* Refuted 3-of-3. Paired 63-day block bootstrap of the
  after-tax daily difference: mhd=21 vs mhd=3 is **+0.90 pp/yr with SE 1.48 in H1 and +0.96 with SE
  2.01 in H2** — not one half clears one standard error at either bracket. And mhd=21's pre-tax maxDD
  is **-32.38% against -28.37%**, failing rule 4 in all three windows. The full-window argmax of 60 is
  a one-era artifact (it *loses* to mhd=3 in 2006-2015), which the track itself said.

**Leverage-decay claims that died** (the mechanism survives, the precision did not):
- *"Two independent measurements agree to 1 bp."* Refuted 3-of-3. They are the same quantity
  aggregated two ways — `margin_frame` with zero financing IS `prod(1+2r)`, which is the same series
  the per-fund residual subtracts. And the engine rounds CAGR to 4dp, so the 1 bp agreement tolerance
  *is* the display grid. Block bootstrap of the daily cost series: **mean -52.2 bps/yr, SE 53.7, 95%
  CI includes zero.**
- *"The cost layer is exactly expense ratio plus the short rate, matching to 4 bps."* Refuted 3-of-3.
  The 11 per-fund measurements have SD 121 bps and the predictions have SD 5.6 bps (it is one constant
  repeated), so cross-fund correlation between predicted and measured is **-0.17** — the wrong sign.
  Mean absolute miss is **101 bps**. The 4 bps is cancellation, not identification, and the rate
  regression in the same artifact (`b = -0.488, t = -1.134, R² = 0.0063`) cannot reject either zero or
  full pass-through.
- *"The overlay de-levers ahead of panic."* Refuted 3-of-3, and this one matters for risk. The
  forward-volatility sort is mostly the trailing sort smeared by volatility persistence
  (corr(trailing, forward) = +0.640). On the genuine surprise days — trailing volatility calm, forward
  21-day volatility in the top quintile — **mean sleeve weight is 58.5% against 54.5% on calm-to-calm
  days**. The book is fully levered going into every spike it has not already seen. It reacts; it does
  not anticipate.
- *"1x on margin would be worse at any realistic rate, and is not legally available at retail."*
  Refuted 3-of-3. Gross book exposure measured from the trade log is **mean 1.37-1.44x, max 1.666x,
  0 of 5,199 days above 2.00x** — inside Reg T. The direction (2x funds beat purchasable margin)
  survives in the artifact; the legality claim and the significance do not.

**Survivorship claims that died:**
- *"A survivorship-biased universe inflates momentum CAGR by 15.78pp."* Refuted 2-of-3 — not on the
  arithmetic but on the label. Legs A and C use the **identical** 503-name survivor set, so
  survivorship cancels in A−C by construction; what that gap measures is forward knowledge of *future
  index additions*, a right-tail look-ahead. The left-tail survivorship channel the mechanism names is
  C−B = **+2.01pp**, 11% of the total. Also: the biased leg's Calmar is 0.583 against PUSH-20's 0.653
  and its maxDD is -50.96% against -28.37%, so it would have failed rule 4 outright — it would never
  have "looked like a large upgrade".
- *"The gap is closable for $299 because the free reconstruction already solved membership."*
  Refuted 2-of-3. Sharadar's $299 Prices plan **ships the `sp500` constituents table**, so the free
  Wikipedia work lowers the bill by exactly $0. And the expected outcome of spending it is measured to
  be a **kill shot, not an upgrade**: restoring the other 343 dead names moves the honest estimate
  *down* from 11.91%, toward or below SPY's 11.08% on the same window.

**Risk-and-sizing claims that died:**
- *"Effective Kelly fraction by the drawdown tail is 0.62."* Refuted 3-of-3, and this is the clearest
  error of the round. `c_from_p` inverts `P = x^(2/c-1)` — the **infinite-horizon** probability of
  ever falling to a fraction of **initial wealth** — against a **20.7-year running-peak maximum
  drawdown**. Two verifiers simulated the exact GBM the formula assumes: at c = 0.30 the closed form
  says 5.5% while the 20.7-year running-peak probability is **67.2%**, rising to 100% at 200 years for
  every c. Inverting the statistic that was actually measured gives **c ≈ 0.235, below the leverage
  route's 0.30**, not double it. The tell was already in the output: pre-v2 at 1.34x of a 3.93x full
  Kelly was reported as c = 1.05, beyond full Kelly. The script's self-check only asserts that its own
  algebra round-trips, which is a tautology. **The `c(tail)` row in `reports/t7_sizing.out` and
  `c_from_tail` in the JSON should be treated as void.**
- *"The dd_brake is the only control that beats plain de-levering, at every block length."* Refuted
  3-of-3 — and it was the round's single most promising candidate, so this one is worth stating in
  full. The +11.8pp (z 7.11) figure comes from `--x realised`, one of two axes, and
  `reports/t7_frontier.py`'s own docstring says "a verdict that flips between them is reported as not
  established." On the self-consistent `--x mc` axis the same brake gives **+12.0 (z 3.66) / +6.5
  (z 1.97) / +2.0 (z 0.40) / +10.0 (z 2.73)** at block 21/63/126/252 — it flips at two of four. The
  mixed axis takes the variant's return from the real path, where de-levering costs 3.12pp, and its
  tail from bootstrapped panels, where the same de-levering costs 1.26pp; that inconsistency is what
  manufactures the gap. Worse, a verifier decomposed the brake: the deep tier that carries the whole
  effect (`dd <= -25% → 0.5x`) produces an equity curve **bit-identical to baseline on real data**
  (max difference 0.000e+00), because only **6 of 5,481 real days** sit at dd ≤ -25% against **16.3%
  of bootstrapped days**. And on the one real path with an intact momentum signal, the brake changes
  maxDD by **0.00pp** while costing 1.18pp of CAGR. It also fails rule 1 outright (in-sample) and
  rule 6 (the 10/20 variant was a new grid point added after the pre-registered 15/25 and 20/30
  underperformed).
- *"The volatility target is a constant cap 73.6% of the time."* Refuted 3-of-3 **upward** — it is
  ~97%, see §2.
- *"P(maxDD<-40%) = 63.4% describes the retired config, so re-pin rule 4 to 32.4%."* The first half
  survived; the prescription was refuted 2-of-3 because the same track's Method B puts live v2 at
  42-64%, and because 63.4% appears in no artifact anywhere in the repo — it is prose in
  `REGISTER.md`, `ff_industry.py:338` and three v3 write-ups, so which config it described is inferred
  from a nearest-match median, not recovered.

**Execution claims that died:**
- *"Queue drift is the hard ceiling of the execution track."* Refuted 3-of-3. The same track measures
  +43.6 bps/side of execution cost against 9.5 assumed, which at 0.208pp/bps is worth ~7pp — roughly
  8x the drift term. The track's own §6 says so in writing. Also, "drift cannot go below zero" is
  false: **7 of 21 years are positive**, and dropping 2020 and 2025 takes the mean from -0.91pp to
  -0.48pp.
- *"Execution cost is far above 9.5 bps while drift is indistinguishable from zero — the premise is
  inverted."* Refuted 3-of-3. Notional-weighted the two are a dead tie (+51.73 vs +51.10 bps); the
  "inversion" is an equal-weighting artifact of two odd lots carrying +564 and +1,002 bps on $277 and
  $542. Eight of eleven fills are under $600 against a $97,399 account. And they are simulator fills
  with no recorded fill timestamp.
- *"The misalignment is caused by a UTC date slice in `_fill_session_open`."* Refuted 3-of-3 on
  repository history: that code entered the repo after the newest row in the log and wrote none of
  the dates, the dates came from a backfill script's logged-at heuristic, and a market-on-open fill
  at 13:30 UTC can never cross the date boundary the claim describes. **The proposed fix would change
  nothing and then read a guaranteed-green confirmation as proof.**

**Audit claims that died:**
- *"The momentum off-by-one costs 0.61pp of CAGR."* Refuted 2-of-3. The measurement reproduces, but a
  verifier swept lookback 226-239 and found adjacent one-bar steps move CAGR by **0.458pp on average
  and up to 1.50pp**, with lb=233 (18.59%) and lb=238 (18.91%) both *above* the graded 232. And
  ~40% of the 0.61pp is a one-day warm-up start-date artifact, not signal. The fidelity break is real;
  the 0.61pp is not an effect size, and "patching it" toward the harness would be a re-grid of the
  spent window.
- Several literature claims died as **restatements of the live config**: that PUSH-20 "already
  implements Barroso/Santa-Clara" (true, but the overlay is a regime-gated near-constant 1.18x, not a
  constant-volatility target); that market-adjusted momentum "cannot change any trade" (false — the
  level gate is not rank-invariant, 12.2% of days differ); that the residual-momentum literature's
  gain is "mostly the volatility-scaling channel PUSH-20 already owns" (the cross-sectional σ-divide
  is rank-invariant to any per-day scalar, so it carries no timing information at all — a different
  object from portfolio-level vol scaling).

**Two process notes, reported because they affect how much weight this round carries.** A shared
scratchpad race caused at least one verifier's script to be overwritten mid-session by a parallel
verifier's file of the same name; both reported it and re-ran under unique names. And one verifier's
own 16-ticker hand-check did not reproduce on re-run (2 of the tickers it reported as returning zero
rows returned data), which is exactly the kind of thing the three-lens design exists to catch.

---

## 5. RANKED NEXT ACTIONS

Scored against the six pre-registered criteria for "clearly better": **(1) fresh data · (2) effect >
1 SE of the paired difference · (3) survives 2x cost · (4) no drawdown regression · (5) mechanism
stated first · (6) pre-registered.**

Actions A1-A5 are **category (c)** — execution, tax, risk engineering and correctness, where nothing
is being selected. Criteria 1-4 govern *claimed strategy improvements*; they do not bind a
measurement that proposes no edge, and that is marked **n/a**, not "passed".

---

**A1 — Answer the account-wrapper question, then finish the tax measurement.**
*What:* establish what account the live money sits in (nothing in the repo records it — see §6), then
re-run `reports/tax_drag.py` with state tax added and with HIFO/specific-ID lot selection, on the
2006-2026 trade log it already emits, and replace the assumed SPY dividend haircut with a measured
after-tax SPY simulation.
*Why first:* it is the largest number in the round (up to 7.31pp of CAGR) and the cheapest to resolve
— no new data, no selection, no holdout.
- (1) n/a — nothing selected · (2) n/a — no effect claimed · (3) n/a · (4) n/a · (5) **CAN**, the
  mechanism (95.5% short-term classification) was stated and measured before the sweep · (6) n/a
- **CANNOT** become a config change on this evidence, and should not be framed as one.

**A2 — Close the three live-vs-certifier fidelity breaks and restate the published headline.**
*What:* pick one fill convention, one momentum-window convention and one XLC sizing convention, make
both files share each, add the equality test, and re-publish the headline at whatever it then prints.
Measured today, the live-faithful number is **17.69% / -30.70%**, not 18.52% / -28.37%.
*Critical constraint:* the choice must be made on which side is *correct*, never on which side
backtests better — picking the better-backtesting window is a re-grid of the exhausted window wearing
a fidelity costume. And the fill convention cannot be "fix live" without solving the machine's power
schedule (see §6).
- (1) n/a · (2) n/a · (3) n/a · (4) n/a · (5) **CAN** · (6) n/a
- **CANNOT** be done by editing `trader/rotation.py` while the fix is undecided; the harness side is
  the safe side to move first.

**A3 — Instrument execution for real.**
*What:* record `filled_at` (currently discarded by a `[:10]` slice), log the NBBO at submit and at
fill, and fix the fill-session benchmark — then re-measure after 30+ fills on whatever account is
actually trading. Until then, `9.5 bps` is an assumption and the 8.4pp cost span is the largest open
number in the project after tax.
*Do not* run the turn-of-month bucketing until this lands: measured minimum detectable effect on the
current log is 244 bps against a 10 bps target.
- (1) **CAN** — forward fills are data the config was never fit to · (2) not yet: current sample gives
  SE 86 bps · (3) n/a · (4) n/a · (5) **CAN** · (6) **CAN**, and should be, with a stated sample size
  before looking
- **CANNOT** produce a strategy claim; it produces the cost input everything else is priced against.

**A4 — One pinning test on `select_targets` + `turbo_allocation`.**
*What:* a fixed synthetic price frame with a known answer, asserted end-to-end. The completion
condition is that mutating the momentum comparator makes that one test — and only that test — fail.
Seven separate mutations currently leave all 86 tests green.
- (1) n/a · (2) n/a · (3) n/a · (4) n/a · (5) **CAN** · (6) n/a
- Cheapest item on this list and the one that prevents the next fidelity break.

**A5 — Resolve the tail estimator before re-pinning register rule 4.**
*What:* do **not** replace 63.4% with 32.4%. Method A cannot rank equity-path-dependent controls (its
own docstring); Method B destroys the momentum signal it resamples. Decide which one the gate is
written against, state why, and record the live v2 baseline under *that* estimator. Also void the
`c(tail)` / `c_from_tail` figures in `reports/t7_sizing.out` and `reports/t7_sizing_results.json`
(§4).
- (1) n/a · (2) n/a · (3) n/a · (4) this **is** criterion 4's instrument · (5) **CAN** · (6) n/a
- **CANNOT** be settled by more bootstrapping of the same window; it is a choice about what the gate
  means, and it needs Nicholas's call on whether the bar is "no worse than the config we retired" or
  "no worse than what we run now".

---

*Below this line the actions are category (a)/(b) — they could in principle produce a live candidate.*

**A6 — dd_brake on Fama-French industry data.**
*What:* pre-register the mechanism (a drawdown-triggered de-lever protects a levered book against
grinding declines the volatility estimator does not see), the config hash, the block lengths and the
axis, then run the 15/25 and 10/20 brakes plus the leverage-cap frontier on FF 30-industry daily
1926-2005, scored on P(maxDD<-40%) at matched return **on the self-consistent MC axis only**.
*Why it is still worth running despite §4:* it is the only control in the round that beat the
leverage frontier on any axis, and the refutation was about axis selection and in-sample provenance,
not about the mechanism being incoherent.
- (1) **CAN** — 1926-2005 is genuinely unfitted · (2) **CAN and MUST** — report the paired SE and z on
  the MC axis; the realised axis is disqualified · (3) **CAN**, and must be run at 19 bps given the
  1927-era turnover problem · (4) **CAN** — this is a tail control, drawdown is the objective ·
  (5) **CAN** · (6) **CAN**
- **CANNOT** use any 2006-2026 number as support. The in-sample +11.8pp is spent and must not be
  re-cited.
- **Known trap before it runs:** the FF transplant's leverage path is unchecked (selfcheck case (g) is
  vacuous, §2). Fix that assert first or the result is unreadable.

**A7 — Idiosyncratic momentum on the FF industry panel.**
*What:* the residualised ranking, paired against raw, on 1926-2005, with a block-bootstrap Sharpe
difference. Mechanism and pre-registration as in A6.
- (1) **CAN** · (2) **CAN** · (3) **CAN** · (4) **CAN** · (5) **CAN** · (6) **CAN**
- **CANNOT** be supported by the 2006-2026 IC result, which points the other way (-0.0162, t -3.31) —
  and that is a reason to expect a negative, not a reason to skip it. A clean negative on 79 unfitted
  years closes the direction permanently, which is worth the run.

**A8 — The individual-stock direction: decide, do not drift.**
*What:* T3's gate answer is **BLOCKED on free data** (27.2% vs a 90% bar). The only unblock is paid
data. Measured expectation if bought: the honest point-in-time leg is **11.91% against SPY's 11.08%**
on the same window with 27% coverage, and restoring the missing names moves it *down*.
- (1) **CAN** in principle · (2-4) unmeasurable until the data exists · (5) **CAN** · (6) **CAN**
- **CANNOT** be justified as an upgrade path on current evidence. Frame the purchase as buying a
  definitive kill, not a candidate.

**A9 — T9, the honest re-grid.** Authorized direction 4, and unchanged by this round: if run, it is a
measurement of how much the remaining search space can still move the number, and **never** a source
of a live change (rule 1). Nothing in round 1 raises its priority.

**Not on this list, deliberately:** anything that edits `trader/rotation.py`. Every candidate above
either measures something or moves the harness.

---

## 6. OPEN QUESTIONS FOR NICHOLAS

Only the forks where the answer changes the work.

**1. What kind of account is the live money in?** Nothing in the repo records it — a grep for
`ira|roth|401k|tax-deferred` across the whole tree hits only two files written by this round. If it
is tax-deferred, T1 is worth **0.00pp** against the published numbers and A1 collapses to a footnote.
If it is taxable, the published 18.52% is 13.89% or 11.21% depending on your bracket, and the
comparison against SPY buy-and-hold is genuinely close rather than a 7.42pp rout. Everything in A1 is
downstream of this one answer.

**2. Is the live account actually live?** Every order path in this codebase points at
`https://paper-api.alpaca.markets/v2`, no live trading host has ever existed in any reachable commit,
and the configured `ALPACA_KEY` carries Alpaca's `PK` paper prefix. The 2026-09-16 orders carry
Alpaca order ids from that same paper path. Two readings are consistent with what I can see: either
real orders are being placed somewhere this repo cannot reach, or the go-live has not actually
happened yet. I am reporting the code evidence, not asserting either. It changes what T6 can ever
measure — every execution number in this round is currently a property of Alpaca's matching
simulator — and it changes how carefully A2's fill-convention change has to be handled.

**3. Which fill convention should certify the strategy?** Aligning the harness to the live loop takes
the published headline to **17.69% / -30.70%** and makes `live_checkup`'s expectations honest.
Aligning the live loop to the harness means deciding on an intraday price near 15:50 ET, which the
machine cannot do — `register-tasks.ps1` documents that the box is powered off through the session —
so it would need hosting. The third option, leaving them different, is what produced this finding. I
will not pick for you: option two costs money to host and changes the signal by an unmeasured amount;
option one costs 0.89pp of reported return and nothing else.

**4. What should register rule 4's tail bar be measured with?** 32.4% (returns bootstrap, which
cannot see path-dependent controls) or 42-64% (panel bootstrap, which degrades the momentum signal
it resamples). Every future risk candidate is graded against whichever you choose, and the dd_brake
in A6 is the first thing it will judge. A third option is to keep 63.4% as a deliberately
conservative bar and say so explicitly, which has the virtue of not being re-fitted to this round's
results.

**5. Spend $299 on Sharadar to close the stock-universe gate?** The measured expectation is that it
confirms individual stocks are not worth pursuing on this framework, rather than unlocking them. That
may still be worth $299 to close the direction permanently. Your call on whether a definitive
negative is worth the money.

---

## Round 1 in one line

The measurements were good; a lot of the sentences written on top of them were not, and the
adversarial pass earned its keep. **No candidate in this round clears all six criteria today.** The
two that could — dd_brake and idiosyncratic momentum on Fama-French — both need pre-registration and
a run on data this configuration has never seen, and one of them needs a broken self-check fixed
first.
