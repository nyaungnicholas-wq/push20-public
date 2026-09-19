# A2a — the fill convention, resolved

**Date:** 2026-09-17 · **Track:** A2a · **Engine:** `reports/opt_harness.py` (md5 `c0bf66b3b4a4`
at measurement time) · **Script:** `reports/a2a_fill_convention.py` → `reports/a2a_fill_convention.out`
· **Test:** `tests/test_fill_convention.py` (6 asserts, no network)

Nothing in `trader/` was edited. No order was placed. The sealed holdout was not re-opened.

---

## 1. The verdict in one table

Live config, 2006-01-01 .. 2026-09-02, 9.5 bps per side, momentum window pinned to the
`canonical` (232-bar) convention every published number was produced under, so the **only**
variable is the fill.

| | CAGR | Sharpe | maxDD | Calmar | Sortino | years beating SPY |
|---|---|---|---|---|---|---|
| README / published | 18.52% | 0.892 | −28.37% | 0.653 | — | 15 of 21 |
| harness, decision close (reproduced) | **18.58%** | **0.894** | **−28.37%** | **0.655** | 1.169 | **15 of 21** |
| harness, next open (**corrected**) | **17.66%** | **0.855** | **−30.70%** | **0.575** | 1.103 | **14 of 21** |
| delta | **−0.92pp** | **−0.039** | **−2.33pp** | **−0.080** | −0.066 | **−1 year** |

So every published figure moves, and the honest ones are:

> **CAGR 17.66% · maxDD −30.70% · Sharpe 0.855 · Calmar 0.575 · 14 of 21 years beat SPY.**

To the last available bar (2026-09-16) the same pair is 18.40% → **17.49%**, maxDD −28.37% →
−30.70%, Sharpe 0.887 → 0.848, Calmar 0.648 → 0.570. SPY buy-and-hold over the same window
is 11.10% (Sharpe 0.645, maxDD −55.19%).

The close-fill row reproducing 18.58% / 0.894 / −28.37% against a published 18.52% / 0.892 /
−28.37% is the fidelity anchor: the 0.06pp is data refresh (yfinance adjustment drift since
the README was written), not a different engine.

---

## 2. Which side is correct, and why — decided before the numbers were looked at

**The signal is not determined until the closing print exists.**

- The registered scheduled task is `main.py rotation-live --scheduled --session next-open --live`
  (`register-tasks.ps1:93`), on an at-logon trigger (~18:30 PT) and a 19:30 daily trigger.
- That flag selects EVENING MODE in `trader/rotation_live.py:507-600`. The gate is *inverted*:
  it **refuses to run while the market is open** (`if alpaca.is_market_open(): ... return skip`,
  :514-517).
- It then takes `closed = _s.last_closed_session()` and `target = _s.next_session(closed)`
  (:519-520) and calls `run_rotation_cycle(..., require_asof=closed)` (:573).
- `_assert_fresh` (:674-690) exists precisely to guarantee the price frame's last row **is**
  that completed session, because "a stale yfinance frame would size positions" off the wrong
  bar. The momentum score, the basket-vol estimate, the 200-SMA regime read and the VIX tier
  are all computed from that row — session *t*'s **official close**.
- Orders are queued, unfilled, and execute at session *t+1*'s open. `reconcile_fills`
  documents this in the live code itself (:285-296): "This system decides after the close and
  the order fills at the NEXT session's open."

Earliest wall-clock moment the decision is fully determined: **16:00 ET on day *t*, at the
closing auction print** — in practice ~18:30-19:30 PT when the box boots. The first price the
system can transact at after that instant is the **open of *t+1***.

A backtest that transacts at the close of *t* is therefore consuming a price that only comes
into existence once the market is shut, and paying it. That is look-ahead of exactly one
overnight gap, on every leg of every rebalance. **`next_open` is correct; `close` is not.**

This is the side that backtests **worse** (−0.92pp CAGR, +2.33pp drawdown), which is the point:
the correct convention was chosen on the mechanism, and the number followed.

### The honest counter-argument, and why it does not rescue the close fill

A same-session design *could* legitimately fill near the close: decide at ~15:50 ET from a
pre-close snapshot and send MOC or market orders. `run_scheduled`'s day-mode branch (:599-616)
is that design, and the comment there records that MOC was tried and abandoned on 85 live orders.
But that is **a different signal** — momentum computed off a 15:50 price, not off the official
close — and it is not what the registered job runs. Certifying the deployed signal at a price
the deployed loop cannot reach is the break. If Nicholas ever moves the live loop back to a
pre-close decision, `FILL_MODE="close"` becomes the right grader again and is still there.

---

## 3. The harness change

`reports/opt_harness.py`, no behaviour change to the close path (verified byte-identical
equity curves over 5,200 bars against the pre-change engine):

```python
FILL_MODE = os.environ.get("PUSH20_FILL_MODE", "next_open")   # was: "close"
```

- **Both paths kept.** `"close"` is unchanged and still reachable.
- **The flag is explicit, with a stated precedence:**
  `cfg["fill_mode"]` > `--fill-mode {close,next_open}` > `$PUSH20_FILL_MODE` > the module default.
  The CLI prints `# FILL_MODE=<mode>` to stderr on every run, so no result is ambiguous about
  which convention produced it.
- **`cfg["fill_blend"]` (new, default 0.0)** prices partial-session participation on the
  next-open path: `(1-b)·open(t+1) + b·close(t+1)`. `b=0` is the opening print, `b=1` is an
  MOC queued overnight.
- **An unknown `fill_mode` raises** instead of silently falling through to close fills.
- **Fallback fixed.** When *t+1* has no open (the 2x sleeves carry *synthesized closes* only
  before their inception, so opens are NaN for ROM/USD/UYG/DIG/RXL/UXI/UCC/UYM up to 2007-02,
  UGL to 2008-12, UBT to 2010-01), the old code fell back to **close(*t*)** — i.e. it quietly
  restored the look-ahead on exactly the bars where data was missing. It now falls forward to
  **close(*t+1*)**. 52 trades are affected, worth −0.030pp CAGR (17.69% → 17.66%). Small, but
  it was a hole in the fix, and `FILL_FALLBACKS` still counts every one.

Two frozen artifacts were pinned so the default flip cannot rewrite history:

- `reports/holdout_v2.py` now sets `H.FILL_MODE = "close"` with a comment saying the 2026-09-13
  grading ran at close fills. **This does not re-open or re-run the holdout**; it keeps a spent
  record reproducible.
- `reports/t7_harness.py` is a standalone copy with its own `FILL_MODE = "close"` and is
  untouched, so T7's tail numbers still reproduce as published.

### A test, because the suite had none

`tests/test_fill_convention.py` — 6 asserts on a synthetic 400-bar frame (no yfinance, no
parquet cache, 0.7s): the default is `next_open`; next-open trades are logged at exactly
`open(t+1)`; close mode still lands on `close(t)`; the blend is exactly
`(1-b)·open + b·close`; an unknown mode raises; a missing open falls **forward** and is
counted. Mutation-checked: changing `v=a[pos+1]` to `v=a[pos]` in a scratch copy makes
**661 of 661** logged trades fail the assertion. `reports/a2a_fill_convention.py __selfcheck`
prints `A2A SELFCHECK OK (400 next-open fills verified against the t+1 open, close mode still
fills at close(t))`.

---

## 4. Corrected headline, per year, versus SPY

`close` and `next_open` are the same strategy; only the execution price differs. 1,652
rebalances either way.

| year | close | next_open | delta pp | SPY | next_open beats SPY |
|---|---|---|---|---|---|
| 2006 | 1.0% | −0.1% | −1.1 | 13.8% | no |
| 2007 | 25.0% | 23.7% | −1.3 | 5.3% | yes |
| 2008 | 5.5% | 4.6% | −0.9 | −36.2% | yes |
| 2009 | 8.4% | 7.5% | −0.9 | 22.7% | no |
| 2010 | 26.2% | 24.3% | −1.9 | 13.1% | yes |
| 2011 | −0.8% | −2.0% | −1.2 | 0.9% | no |
| 2012 | 22.3% | 21.2% | −1.1 | 14.2% | yes |
| 2013 | 34.0% | 34.3% | +0.3 | 29.0% | yes |
| 2014 | 32.6% | 34.0% | +1.3 | 14.6% | yes |
| 2015 | 0.1% | −1.3% | −1.4 | 1.3% | no |
| 2016 | 22.1% | 23.5% | +1.5 | 13.6% | yes |
| 2017 | 32.4% | 32.8% | +0.4 | 20.8% | yes |
| 2018 | −13.4% | −15.1% | −1.7 | −5.2% | no |
| 2019 | 31.4% | 30.2% | −1.2 | 31.1% | no |
| 2020 | 52.7% | 46.4% | **−6.3** | 17.2% | yes |
| 2021 | 32.7% | 33.6% | +0.9 | 30.5% | yes |
| 2022 | −13.7% | −13.4% | +0.2 | −18.6% | yes |
| 2023 | 29.7% | 29.2% | −0.5 | 26.7% | yes |
| 2024 | 34.0% | 32.3% | −1.7 | 25.6% | yes |
| 2025 | 12.5% | 8.9% | **−3.6** | 18.0% | no |
| 2026 | 29.9% | 30.4% | +0.5 | 12.6% | yes |

- Paired per-year delta: **mean −0.94pp, sd 1.73, t −2.50, p 0.0126, 14 of 21 years hurt.**
- Years beating SPY: **15 → 14**. 2019 is the year that flips (30.2% against SPY's 31.1%).
- The damage is fat-tailed, not uniform: 2020 (−6.3pp) and 2025 (−3.6pp) are 53% of the total.
  Both are high-gap-volatility years, which is the shape you expect — the close convention
  de-levers or rotates *before* the overnight gap, and the live loop eats it.

**Significance of the Sharpe gap** (block bootstrap on paired daily returns, block 20,
2,000 paths, n=5,199): observed **dSharpe −0.0390, se 0.0169, z −2.31**, P(dSharpe > 0) = 0.009,
5th/50th/95th pct −0.0667 / −0.0380 / −0.0109. By the register's own rule 2 (delta Sharpe of at
least one paired standard error) the fill convention is **more than twice the noise floor**. The
published Sharpe is overstated by a margin the register would call decisive if it were pointing
the other way.

---

## 5. The honest number is a band, not a point

The open print is the *best* realistic case: it assumes the whole book fills at one price with
no participation cost. Two brackets, both at the corrected next-open path:

**Participation** — `fill_blend` b, price `(1-b)·open(t+1) + b·close(t+1)`:

| b | fill | CAGR | Sharpe | maxDD | Calmar |
|---|---|---|---|---|---|
| 0.00 | opening print | 17.66% | 0.855 | −30.70% | 0.575 |
| 0.10 | ~first minutes | 17.46% | 0.847 | −30.75% | 0.568 |
| 0.25 | early-session VWAP proxy | 17.24% | 0.838 | −30.80% | 0.560 |
| 0.50 | half-session VWAP proxy | **17.08%** | 0.832 | −30.82% | 0.554 |
| 1.00 | MOC queued overnight | 17.59% | 0.850 | −30.66% | 0.574 |

The curve is U-shaped: participating **into** the session is worse than either endpoint, so
"wait for the auction instead of the open" recovers nothing (+0.51pp back to b=1.0 but still
−0.99pp against the close fill). **Fill timing alone spans 17.08% – 17.66%.**

*Method note:* this is a proxy, not measured intraday VWAP. yfinance serves 1-minute bars for
about 30 days, so a genuine first-N-minutes VWAP over 2006-2026 is not obtainable on free data;
the open/close blend brackets it. It is honest about direction and magnitude, not about the
exact path within the session.

**Cost** — next-open fills, only `cost_bps` moves:

| bps/side | CAGR | Sharpe | maxDD |
|---|---|---|---|
| 0.0 | 19.66% | 0.933 | −30.47% |
| **9.5 (assumed)** | **17.66%** | **0.855** | **−30.70%** |
| 14.5 | 16.62% | 0.814 | −30.81% |
| 19.0 (register rule 3: 2× assumed) | **15.69%** | 0.777 | −30.92% |
| 29.5 | 13.54% | 0.691 | −31.16% |
| 50.0 | 9.45% | 0.522 | −35.14% |

Combining both: **the defensible band for this strategy is roughly 15.7% – 17.7% CAGR at
−30.7% drawdown**, against a published 18.52% / −28.37%. At 50 bps/side — which is not absurd
for UCC/UXI/UYM/RXL, where T6 measured a peak position at 13.71% of 3-month ADV — it prints
**9.45% against SPY's 11.10%** and the edge is gone. And note the 9.5 bps assumption itself
rests on **no real execution data**: all 105 logged fills are Alpaca *paper* fills
(`trader/alpaca_broker.py:24`).

---

## 6. Mechanism — what the close fill was actually harvesting

From the engine's own trade log (close-fill run, 7,982 legs), the move from the decision close
to the next open on the names traded:

| | n | mean | t | p |
|---|---|---|---|---|
| names BOUGHT at the close | 3,994 | **+8.67 bps** | +4.42 | 0.0000 |
| names SOLD at the close | 3,988 | +7.84 bps | +3.97 | 0.0001 |

Both legs sit in the well-documented positive overnight drift. The close convention buys before
it and sells before it, so the asymmetry it books is **+0.83 bps per rotated pair** — about
14 bps/yr at the strategy's 17.3× annual turnover. That is a *minority* of the 92 bps/yr gap.
The rest is not a per-trade edge at all: it is that the **levered** book is repositioned before
every overnight gap, so the exposure changes (de-levering into a VIX spike, rotating out of a
name that gaps down) all land one gap early. That is why the drawdown moves 2.33pp — a bigger
relative hit than the CAGR — and why 2020 alone is −6.3pp.

---

## 7. Scope, caveats, and what is NOT claimed

- **No live change is proposed and none was made.** `trader/rotation.py` and every live config
  are untouched. The live loop's behaviour (queue for the next open) is the *correct* side of
  this break; the certifier was wrong, and the certifier is what moved.
- **This is not new evidence of an edge.** It clears none of the six register criteria because
  it proposes none — it is a fidelity correction that makes every existing number smaller.
  Nothing here was selected on; no holdout was consumed.
- **The other two fidelity breaks are still open and interact with this one.** During this
  session concurrent tracks changed the harness under me: A2b flipped the default momentum
  window to the live 231-bar convention, and A2c added `unlev_live_size`. Every row above
  pins `MOM_WINDOW="canonical"` and `unlev_live_size=0` so A2a moves one variable. For the
  record, at the A2b default (`live`, 231-bar) the same pair is **17.97% → 17.29%**, maxDD
  −28.37% → −30.70%, i.e. the fill break costs −0.68pp there. **Stacking all three corrections
  is a separate run and has not been made here.**
- **`reports/live_checkup.py` now measures the executable convention** (it inherits the default
  and never pinned `FILL_MODE`). Its alert thresholds are loose enough to absorb this —
  `EXPECT_CAGR 0.185 − 0.03 = 0.155 < 0.1766` and `EXPECT_MDD −0.284 − 0.05 = −0.334 < −0.307`
  — so **no false alarm fires**, and I left it alone deliberately rather than re-anchoring a
  constant that records what the holdout graded.
  *Proposed, not applied:* at the next review, re-anchor `EXPECT_CAGR = 0.177` and
  `EXPECT_MDD = -0.307` (and the matching `0.185` assertion in
  `tests/test_checkup_matches_live.py:72`), with the old pair kept in the comment block as the
  close-fill record. That is a decision about what the checkup is measuring against, not a
  measurement, so it is Nicholas's call.
- The README still publishes 18.52% / −28.37% / 0.892 / 0.653 / 15-of-21. **Those five numbers
  are now known to be the un-executable convention** and should be restated as
  17.66% / −30.70% / 0.855 / 0.575 / 14-of-21, or the band in §5.

## 8. Reproduce

```
python reports/a2a_fill_convention.py __selfcheck     # A2A SELFCHECK OK
python reports/a2a_fill_convention.py                 # every table above -> a2a_fill_convention.out
python -m pytest tests/test_fill_convention.py -q     # 6 passed
python reports/opt_harness.py --config '{}' --fill-mode close    # stderr: # FILL_MODE=close
```
