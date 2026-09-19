# EXECUTION -- what PUSH-20 actually pays to trade

Round 3, 2026-09-19. Six tracks (X1 release timing, X2 order type, X3 1x/2x routing,
X4 slicing, X5 cost function, X6 measurement design), each finding put through two
adversarial verifiers. This file is the adjudication.

Every number below was **printed by a command run for this document**, in isolated copies
of the six worktrees. The commands are listed in section 7 so any of them can be re-run.
Every figure carries **MEASURED** (came off data), **MODELLED** (came out of a model) or
**ASSUMED** (a constant nobody fitted).

This is category (c) work -- execution engineering, nothing selected from the return
series, so no holdout is spent and the six pre-registered criteria do not bind. The limit
on that licence is respected: everything is ranked on measured cost. Where a CAGR figure
appears it is a *conversion* of a cost, printed so the size of the cost is legible, never
an objective.

**No live config edit is recommended here and no trading decision is taken. The costs are
reported; the decisions are yours.**

---

## TL;DR

1. **At your size the correction is not order size. It is the instant of release.** Your
   account is **$98,278.79** (MEASURED, read-only `GET /v2/account`, today). Held at that
   size, the settled-book cost is **17.65 bps/side** (MODELLED) against **9.5 ASSUMED** --
   a 1.68pp haircut, 17.29% -> **15.61%**, still clear of SPY's 11.10% (MEASURED). Price
   the same account at the 09:30 book with the MEASURED per-symbol open multiple and it
   pays **28.06 bps/side** and **13.49%**. Size costs you 8 bps. Timing costs you 10.

2. **The opening print is the worst instant of the session and the live loop queues market
   orders straight into it.** MEASURED for this document, whole book, 806 legs over 150
   release dates 2017-2026, SIP NBBO, notional-weighted quoted half-spread:
   **27.63 bps/side at 09:30:00 against 7.23 at 09:35:00.** That is **2.91x the 9.5 bps
   assumption at the open and 0.76x it five minutes later.** The assumption is not too low.
   It is measured at the wrong moment.

3. **Part of the raw saving is not payable, and you should know which part.** MEASURED by
   me off the raw tape: the first post-open NBBO print stands for a median of **0.7
   milliseconds** (146 of 180 sleeve-days under 10ms). An order arriving anywhere in the
   first half second meets a time-weighted **35.62 bps/side** on the sleeves, not the
   instantaneous 57.03. So take the low end of the range in section 1f, not the high end.

4. **Nothing else is free.** Order type: no limit policy separates from a plain delay, and
   a limit must be priced off the previous close, whose gap is a MEASURED mean |160| bps
   against the 57 it is meant to cap. Slicing: it attacks impact, which *is* your dominant
   term (78.4% of cost at $100k) -- but 34% of orders cannot be sliced at all and the ones
   that can are the cheap ones. Routing 1x/2x: the headline +3.675 pp/yr double-counts
   financing; corrected it is **+2.929**, and the collectable part is a *holdings* change.

5. **There is still no real fill.** All 110 rows of `data/slippage_log.jsonl` are Alpaca
   paper-simulator output (key prefix `PK`, MEASURED). The cheapest honest route to a real
   number is **279 orders = 2.9 months** if the same batch goes out every session, at about
   **$419** of round-trip spread on $250 clips.

**Blunt version.** Two days produced no alpha because there is none left to find; that part
of the book is shut and the tracks confirmed it. What the work did produce is this: your
headline CAGR is quoted with no account size attached, your engine's single cost constant
is wrong in four independent directions at once, and you are paying a ~4x spread premium
for the convenience of a scheduled task that fires at 19:30 the night before and lets the
order sit into the widest instant of the session. That premium is the only lever left, it
is worth roughly 2 to 4 points of CAGR, and collecting it is an engineering job, not a
strategy change.

---

## 1. THE NUMBER

### 1a. Your size, held constant -- the table that applies to you

Cost per side, notional-weighted over 8,008 orders, 2006-01-01 -> 2026-09-02, harness at
`mom_window=live` and `fill_mode=next_open` (what `trader/rotation.py` runs). The account
is **held at A**: order size is `Q = (order / equity_t) x A`. Cost feeds back into equity,
so it is iterated to a fixed point; four passes, each pass's CAGR printed.

| account | spread | impact | fill risk | **TOTAL bps/side** | vs 9.5 ASSUMED | CAGR | delta | beats SPY 11.10%? |
|---|---|---|---|---|---|---|---|---|
| **$10,000** | 3.82 | 4.39 | not priced | **8.20** | 0.86x | **17.56%** | +0.27pp | YES |
| **$100,000** <- you | 3.82 | 13.87 | not priced | **17.65** | 1.86x | **15.61%** | -1.68pp | YES |
| **$1,000,000** | 3.82 | 43.87 | not priced | **47.70** | 5.02x | **9.62%** | -7.67pp | **NO** |

Baseline (flat 9.5 bps/side, same harness): CAGR **17.29%**, maxDD -30.70%, Sharpe 0.842.
SPY buy-and-hold on the same bars: **11.10%** MEASURED (published 11.07%).

| cell | basis | why |
|---|---|---|
| spread 3.82 | **MODELLED** off **MEASURED** anchors | `log(half_spread_bps) = 7.1104 - 0.3645*log(ADV$)`, fitted on 21 MEASURED SIP points (4 sessions, Sept 2026), carried back 20 years by a Corwin-Schultz era factor. Multiplicative residual on the fitted points 0.54x-2.09x, geometric RMSE 1.47x in-sample on 21 points with 2 parameters -- worse out of sample. No free NBBO exists before 2016, so the time path cannot be checked. Section 1d suggests this term is **understated**. |
| impact 4.39 / 13.87 / 43.87 | **MODELLED**, coefficient **ASSUMED** | `1e4 * Y * sigma_21d * sqrt(Q/ADV_21d)`, **Y = 1.0 ASSUMED**. Nobody has fitted Y to a fill because no fill exists. At Y=0.5 / 1.5 the $100k impact term is **6.94 / 20.81** -- a 3x band on the dominant term. |
| fill risk | **UNMEASURED** | The cost of a leg that does not fill, or fills short, and leaves the book off-target for a session. Zero real fills exist, so this is not estimated anywhere in the project. It is not zero. |
| 9.5 bps | **ASSUMED** | `reports/opt_harness.py` `cost_bps`: one global constant for every symbol, size, date and instant. |
| CAGR | **MODELLED** | backtest output. |
| SPY 11.10% | **MEASURED** | buy-and-hold on the same bars. |

Two things are deliberately **not** in the table. The 2x funds' expense ratio and financing
(~132 bps/yr per dollar of exposure, MEASURED ER) is already inside the funds' own price
series, so it is not a missing execution cost -- it is a holdings question and it lives in
section 3. Taxes are T1's problem, not this one.

The impact term at $100k comes out at 13.87 against S1's published **13.83** -- this is
S1's own arithmetic re-run, not a competing model.

**The linear conversion is safe at your size.** T6 calibrated 0.2083 pp of CAGR per bps per
side. Checked against my own runs: implied 0.2077 ($10k), 0.2061 ($100k), 0.2019 ($1m). It
holds to ~0.01 pp/bps up to $100k and slightly *overstates* the damage at $1m, where
compounding feedback cushions it. **1 bps/side = 0.21pp of CAGR at your size.**

### 1b. Capacity, in the convention that answers "what may I run today"

Bisecting the constant account size at which corrected CAGR falls to SPY's 11.10%:

| pricing | crossing | cost at the crossing |
|---|---|---|
| settled book | **~$690,000** | 40.1 bps/side |
| 09:30 book (sleeve multiple 4.94x on every leg, upper bound) | **~$235,000** | 40.1 bps/side |

MODELLED, Y=1.0 ASSUMED. The settled figure sits inside S1's published $0.4m-$1.2m band.
Note both cross at the same ~40 bps/side -- the wall is a *cost* level, and how much money
it corresponds to depends entirely on when you release.

### 1c. Two conventions -- and why the published capacity numbers disagree

X5's own headline uses `Q = order x A/100k`: a 2006 **seed** of A that compounds. The
harness equity runs **$100,000 -> $2,699,463 (27.0x MEASURED)**, so that convention prices
late orders at up to 27x the nominal account. Printed, canonical window: 17.14 / 36.51 /
67.77 bps/side, CAGR 16.69% / 13.12% / 6.44%, SPY crossing bisected at $227,172 (settled)
and $89,160 (open spread).

Those answer *"what may I start with in 2006 and never withdraw from"*. Sections 1a and 1b
answer *"what may I run today"*. They are not in conflict; they are different questions,
and 1a/1b is yours. **Do not quote $227,172 as a capacity limit** -- it is a 2006 seed that
ends near $2m.

### 1d. Whole book at the release instant -- MEASURED for this document

X1 measured only the nine 2x sleeves, which are 51.9% of traded notional. The engine's 9.5
bps is one *global* knob applied to every leg, so the comparison needs the 1x sectors and
the defensive sleeve too. Built for this file: the harness's own net order book on X1's 150
sampled release dates is **806 legs -- 442 sleeve (51.9% of notional) and 364 non-sleeve
(48.1%)**. SIP NBBO, first two-sided quote at or after each ET instant, `feed=sip`
hardcoded in both request paths, no IEX fallback anywhere. 1,456 quotes fetched, **0 empty,
0 rate-limited**, complete case on **806 of 806 legs (100%)**.

Notional-weighted quoted **half-spread**, bps/side, MEASURED:

| offset | **WHOLE BOOK** | 9 sleeves | other 11 | vs 9.5 ASSUMED |
|---|---|---|---|---|
| **09:30:00** | **27.63** | 48.65 | 4.94 | **2.91x** |
| **+5m** | **7.23** | 13.02 | 0.97 | 0.76x |
| +15m | 5.35 | 9.61 | 0.75 | 0.56x |
| +60m | 4.36 | 7.91 | 0.54 | 0.46x |

Saving from waiting, whole book: **09:30 -> +5m = -20.40 bps/side**, -> +15m = -22.28,
-> +60m = -23.27.

Two things follow that the sleeve-only work could not see:

- **The multiple is not 5x, it is about 3.8x** on the whole book (27.63/7.23). The
  non-sleeve half of the notional widens by a larger *ratio* (5.1x) but from a trivial base
  (0.97 bps/side), so it contributes almost nothing in absolute terms.
- **The model's historical spread term is probably understated.** Section 1a's fitted
  spread is 3.82 bps/side; the direct measurement of the same book at +5m is 7.23. The gap
  is the era factor (which shrinks pre-2024 spreads by up to 0.67x) plus the fact that
  +5m is still early-session while the fit's "settled" is an all-day median. Section 1a's
  totals are therefore **conservative**, not inflated.

### 1e. What the 9.5 bps assumption actually gets wrong

Not the level so much as the shape. It is flat in **instrument** (MEASURED median 09:30
half-spread: QLD 0.94 bps/side, UXI 112.80), flat in **size** (the impact term runs 4.39 ->
43.87 across a 100x in account), flat in **time** (X5's per-year modelled cost at its $100k
seed runs 3.7 bps in 2006 to 79.2 in 2023), and flat in **instant** (27.63 at 09:30 against
7.23 at 09:35, whole book, MEASURED). Every one of those four spreads is larger than the
gap between 9.5 and the whole-book average.

One consolation, and it is real: **at $10,000 on the settled book the 9.5 bps assumption is
conservative** -- MODELLED cost 8.20 bps/side, corrected CAGR 0.27pp *higher* than the
headline. The assumption is not wrong because the strategy is expensive. It is wrong
because it is a constant.

### 1f. The timing cost, priced through the engine

Re-running the fixed-account harness with each symbol charged its **own MEASURED** 09:30/+5m
multiple (per-symbol where the leg trades at least 10 times in the sample; the class
aggregate -- 3.74x sleeves, 5.10x others -- where it does not):

| account | +5m / settled | 09:30, MEASURED per symbol | **the release instant costs** |
|---|---|---|---|
| $10,000 | 8.20 bps, **17.56%** | 18.61 bps, **15.41%** | 10.41 bps/side, **-2.15pp** |
| **$100,000** | 17.65 bps, **15.61%** | 28.06 bps, **13.49%** | 10.41 bps/side, **-2.12pp** |
| $1,000,000 | 47.48 bps, **9.62%** | 57.88 bps, **7.60%** | 10.41 bps/side, **-2.02pp** |

**The honest range for the timing cost is 10 to 20 bps/side, i.e. 2.1 to 4.2pp of CAGR.**
The low end is this table -- the model's own spread base scaled by the measured multiple,
so it is consistent with everything else in the harness. The high end is the direct
measurement in 1d (20.40 bps/side x 0.2083). The low end is the one to plan on; the high
end is what the book actually quoted on 150 mornings.

---

## 2. WHAT IS FREE

"Free" = lowers measured cost with no strategy change, no capital displacement, no holdout.
Ranked by pp of CAGR per unit of effort. Two of the four save nothing and are dropped.

### #1 -- RELEASE TIMING. The only lever with a real number on it.

| | |
|---|---|
| **bps saved** | Whole book, MEASURED, notional-weighted: **-20.40 bps/side** moving 09:30 -> +5m; -22.28 at +15m. Priced through the engine at each symbol's own measured multiple: **-10.41 bps/side** at every account size. |
| **pp of CAGR** | **+2.12pp at your size** (MODELLED, engine-consistent), up to +4.25pp if the direct measurement is the better estimate of the historical spread. |
| **statistical strength** | Sleeves only, paired on the same order, clustered on the morning, 442 orders over 150 mornings: -36.58 bps at +5m (SE 4.46, **t -8.20**), -43.96 at +15m (SE 6.17, t -7.13). Discard the first print entirely and re-anchor at +10s and it is still -14.16 at +5m (p 0.0009). Controls behave: TLT and GLD quote a MEASURED 0.67 and 0.59 bps/side at the open and barely move. |
| **reachability haircut** | The 09:30:00.000 quote is not payable in full. MEASURED by me: first post-open NBBO print dwell **median 0.7ms**, p90 22.8ms, 146 of 180 sleeve-days under 10ms; instantaneous sleeve half-spread 57.03 against a time-weighted **35.62** over [0, 0.5s] and 32.94 over [0, 5s]. This is why the plan number is the low end of the range. |
| **what has to change** | A process alive at ~09:35 ET, and a queue/submit split in `rotation_live.py` (there is no "computed but unsent" state today). `register-tasks.ps1:44,90-93` gives the rotation exactly **one** trigger: daily at 19:30 local. |
| **and this too** | `reports/opt_harness.py` fills at the next **open**, and so does the live loop. Move the release and that correspondence breaks; it has to move on **both** sides and be re-measured. The harness has no "open + N minutes" fill price and the project does not load intraday bars. |
| **hardness** | Medium. Not a config edit: a scheduling change, plus a harness fill convention, plus a re-certification. Call it a day of engineering. |

**Which offset is NOT established.** X1's "+15m minimum" is an argmin over eleven
correlated noisy estimates and does not survive: paired against +15m, +10m is -2.14
(p 0.34), +30m +2.72 (p 0.53), +60m +0.39 (p 0.94). On the half-spread alone -- the part
that is genuinely a cost -- the whole-book series keeps falling to +60m (4.36). On the
notional-weighted **net** including signed drift, the sleeve series bottoms at +5m/+15m
(13.92 / 11.01) and gives it all back by +60m (19.08). The defensible statement is
**"somewhere between +5m and +15m, and do not cross the first print"** -- not "+15m".

**Per-instrument, not global.** MEASURED 09:30 mean net cost by sleeve: QLD 1.1 (n=69),
ROM 34.1, USD 49.6, UYG 52.8, DIG 60.7, RXL 76.0, UYM 87.3, UCC 101.2, UXI 114.0. A global
delay buys QLD essentially nothing. A threshold on the sleeve's own opening quote is the
defensible shape -- but it needs a live intraday decision, which is more machinery than a
flat delay, and per-sleeve argmins read off 6 UYM orders are selection in cost clothing.

### #2 -- ORDER TYPE. Saves nothing. Drop it.

MEASURED, 20 sessions x 9 sleeves x both sides = 360 legs, replayed against the SIP tape,
bps/side vs the 09:30:00 mid:

| policy | fill% | median | mean | escalated |
|---|---|---|---|---|
| market at the release **[current]** | 100% | 47.9 | 57.0 | - |
| market, 5s later | 100% | 28.4 | **31.9** | - |
| market, 300s later | 100% | 17.3 | **23.3** | - |
| mid peg 60s -> market | 100% | 9.7 | 32.8 | 52.8% |
| limit +25bps 300s -> market | 100% | 18.9 | 27.0 | 15.0% |

Every limit policy lands within a few bps of a plain delay, and paired differences at n=360
with day-clustered SEs of ~1.5-4 bps do not separate them. The mid peg is chosen by its
median (9.7) and punished by its mean (32.8) -- and its escalations cost *exactly* what the
delay costs, because they take the same touch at the same instant.

The decisive fact is structural: **a limit can only be priced off the previous close,**
because the order is submitted the evening before with the market shut
(`rotation_live.py:713`, `moc=False`). MEASURED overnight move, previous close -> 09:30
mid, 180 sleeve-sessions: mean |gap| **160 bps**, median 115, p90 371, max 659 -- against
the 57 bps it is supposed to cap. Fill rates of a limit k bps through the close: k=25 ->
79%, k=200 -> 94% at 33.2 bps on fills, k=400 -> 99% at 50.4 bps. At the width that fills
it caps nothing; at the width that caps, it does not fill. And the unfilled legs concentrate
exactly where it matters -- MEASURED unfilled rate at lim25: **UXI 40.0%, UYM 37.5%, UCC
25.0%, DIG 12.5%**. Those are the names the strategy just bought because they ran.

**Nothing to change. Do not spend another session on order types.** One note for anyone who
tries: full exits go through `alpaca.place_market_sell` (`rotation_live.py:1027`) ->
`DELETE /v2/positions/{sym}` (`alpaca_broker.py:144`), which takes no `tif` and no price, so
an order-type change needs a second implementation for the largest legs.

### #3 -- SLICING. Right target, wrong reach. Drop it, with one condition.

Slicing attacks impact, and impact **is** your dominant term -- MEASURED-model share of
total cost: 53.5% at $10k, **78.4% at $100k**, 92.0% at $1m. So the idea is not silly. It
fails on reach, three times over:

- **34% of orders cannot be sliced.** At W=30 k=8, **1,531 of 4,487** orders put a child in
  a minute with no print at all. MEASURED median print-minutes per session: UCC **3**, UXI
  **4**, RXL 13, UYM 13. UCC and UXI print in **zero** minutes of a median first half-hour.
- **The sliceable orders are the cheap ones.** Whole-book spread at the release is 19.44
  bps/side against 4.69 on the sliceable subset. The orders that carry the spread are the
  orders that cannot be sliced.
- **On the orders it *can* reach, a plain delay is cheaper.** At $100k on the common subset:
  status quo 7.50 bps/side, best schedule W=30 k=8 **4.06**, a single order delayed five
  minutes **3.03**. One order instead of eight, for the same money.

That last comparison is inside its own noise and both verifiers broke it in opposite
directions, so do not bank the ranking -- bank the feasibility, which is robust.

**Nothing to change now. The one condition:** if the account ever runs at $1m+, impact is
92% of cost and this question deserves re-opening -- but only *after* the release instant is
settled, because at $1m the timing term is still worth 2.02pp and costs nothing extra.

### #4 -- ROUTING 1x/2x. Not free. See section 3.

### Ranking, at your size

| lever | pp of CAGR | effort | verdict |
|---|---|---|---|
| **release timing** | **+2.1 to +4.2** | scheduling + harness convention + re-cert (~1 day) | **the only one worth doing** |
| order type | 0.0 | small | drop |
| slicing | ~0 net of a plain delay; blind to 34% of orders | large | drop until $1m+ |
| routing | +1.2 to +2.9 gross, and not free | large, and it is a holdings change | section 3 |

---

## 3. WHAT IS NOT FREE

### 3a. Routing the 2x sleeves to their 1x parents

**The saving, corrected.** X3 prints +3.675 pp/yr at $100k if fully routed: spread 0.421 +
impact 2.222 + carry 1.032. The carry column is wrong. `carry_bps()` charges the 2x route
`(ER2 + r)/2` per dollar of exposure and the 1x route `ER1`, with the docstring "the 1x
fund borrows nothing." But `trader/rotation.py:991-992` has the defensive sleeve absorb the
capital the 2x route frees, and the 2x path is only reached when the book wants gross above
1.0 -- so the 1x route needs an **extra dollar of capital that has a price**, and it is
priced at zero. Credit the 2x route the same rate on the half-capital it frees and the
short rate cancels on both sides. COMPUTED, reproducing X3's own 1.032 and 1.950 first so
the only change is the one correction:

| carry saving, pp/yr | r = 1.69% (backtest mean) | r = 3.77% (today) |
|---|---|---|
| X3 as coded ("the 1x fund borrows nothing") | +1.032 | +1.950 |
| corrected, funded from GLD/TLT at the short rate | **+0.286** | **+0.286** |
| corrected, funded on retail margin at r+100bps | **-0.155** | **-0.155** |
| corrected, retail margin at r+150bps | **-0.376** | **-0.376** |

The rate cancels exactly (0.286197 at both). So X3's most striking claim -- that the layer
is worth nearly twice as much at today's short rate -- is entirely the artifact.
**Corrected full-route total at $100k: +2.929 pp/yr, not +3.675.** X3's collectable policy
rows (thin2 +1.237, greedy +2.101, all nine +0.044) still contain the inflated carry and
are upper bounds.

**The other side of the ledger, honestly:**

- **It is a holdings change, not an execution change.** Funding one routed sleeve consumes a
  MEASURED median **0.208 of a 0.375** defensive weight -- a 56% cut in the GLD/TLT
  drawdown-control sleeve. X3 rules the return consequence out of scope. That makes it a
  strategy change subject to the six pre-registered criteria, and there is no holdout left.
- **Exposure is not preserved.** T5's per-fund betas (`T5_leverage.md:34,286-289`):
  **UCC/XLY 1.62** (R2 0.81), so the 1x route delivers ~23% *more* index exposure on the
  sleeve any sensible policy routes first; **UYM/XLB 2.09**, where it delivers *less*. The
  1.992 realised vol ratio X3's selfcheck prints is a volatility ratio, not a beta, and does
  not establish equivalence.
- **The capital premise is false, in both directions.** X3's feasibility argument is built
  on "a cash account cannot borrow". MEASURED by me, read-only `GET /v2/account` on the only
  endpoint the live loop talks to: `multiplier 4`, `shorting_enabled true`, equity
  $98,278.79, cash $57.45, `regt_buying_power` **$35,961.30**. It is a margin-enabled
  account with ~0.37x of equity in borrowing capacity with the book already on. So "all
  nine is fundable on only 1.2% of rebalances" is not your constraint -- and neither is the
  cash-account arithmetic that makes single-sleeve routing look free.
- **Unpriced legs.** Every routing event adds a defensive sell to fund it and a defensive
  buy to unwind it, each with its own spread and impact, and it makes the sector entry
  conditional on the defensive fill clearing first. None of that is in the +2.929.
- **The harness must move with it**, or the certifier stops corresponding to the live loop.

**Both sides priced:** roughly +1.2 to +2.9 pp/yr of measured cost saving, against a 56%
cut in the drawdown sleeve, a 23% exposure error on the flagship sleeve, a second
implementation in `opt_harness.py`, extra defensive turnover, and a strategy change that
needs the full criteria on a spent holdout. **It is not an execution decision and should not
be taken as one.**

### 3b. Moving the release off 09:30

- **New failure mode.** Today a DAY order queued the evening before fills at the open
  whether or not any process is alive. A delayed release creates a "did not fire" state that
  does not exist today, on a box that is off through the session. That risk is real and it
  is **not priced anywhere in this document**.
- **Correspondence.** The harness fills at the next open and so does the live loop. That is
  what A2a established, and it breaks the moment release moves. Both sides, re-measured.
- **Sequencing.** It also resets A3's pre-registered `arrival_bps` baseline before it has a
  single real observation. If the probe in section 4 is going to run, decide the release
  instant **first**.

### 3c. The probe programme

Real orders, real money, in a live account. Under $25k equity, 9 round trips a session is
squarely inside the pattern-day-trader rule, and a cash account settles T+1, which caps the
very cadence the design leans on. It needs probe-order tagging in `rotation_live.py` so a
probe cannot enter the book or the risk gate -- a mis-tagged probe is an untracked
leveraged position.

---

## 4. THE MEASUREMENT -- the shortest defensible path to a real number

**Where you are.** MEASURED today from `data/slippage_log.jsonl`: 110 rows, 24 submit
batches, mean 4.58 orders/batch, evening-regime sd **41.33 bps/side**, current cadence
**33.5 fills/month**. Usable sample for the pre-registered endpoint: **15 orders**, MDE
**43 bps/side** (9.0pp of CAGR). Every one of those rows is simulator output, so even the
sd is a property of Alpaca's matching engine, not of a market.

**The design (A3, pre-registered, unchanged).** Primary endpoint `arrival_bps` = fill vs the
NBBO mid in force when the order became live, signed adverse-positive, notional-weighted,
batch-clustered SE. Null: 9.5 bps/side. Two-sided alpha 0.05, power 0.80, delta 10 bps.

| route | n | calendar | spread it pays |
|---|---|---|---|
| do nothing, current cadence | 279 | **8.3 months** | nothing |
| **same 4.58-order batch every session (21/mo)** | 279 | **2.9 months** | ~$419 round trip at $250/clip |
| 9-order probe every session | 456 | **2.4 months** | ~$684 |
| 18-order probe every session | 818 | 2.2 months | ~$1,227 |

n from `a3_execution.n_needed` at the **PRE-REGISTERED ICC 0.3** and sd 41.33. Dollar
columns price the entry leg at the MEASURED 09:30 half-spread (52.6 bps/side on the sleeves,
261 cells, 29 sessions) and the exit at A3's MEASURED settled 7.4 bps/side.

**The no-full-deployment option exists and it is the second row.** You do not need to deploy
at size to measure the spread-crossing term. You need the same batch, released every
session, at $250 a clip: **2.9 months and about $419**, with no estimator change, no new
statistics and no ICC substitution. Cadence is the lever, not cleverness.

**Three things this path cannot do, stated up front:**

1. **It cannot measure impact.** A $250 order is ~0.1% participation (MODELLED on MEASURED
   ADV), so it measures spread crossing and is structurally blind to the term that sets the
   capacity wall -- which is 78.4% of your cost. Estimating Y needs a dose-response ladder at
   real participation: a different, far more expensive programme, and one that would put
   orders through a book where UCC's trailing-21d dollar volume is a MEASURED **$46,343 a
   day** and a $100k account's own p95 participation in it is already 19.87%.
2. **The clever-estimator routes do not deliver.** Regression control on the freely-observed
   quoted half-spread buys 2.2% of the variance *under the null* (279 -> ~294 orders at
   ICC 0.3) -- it helps only if the book turns out expensive, i.e. the gain is a function of
   the answer. Same-day matched pairing removes at most the date-common 3.8% of spread
   variance and doubles the orders placed.
3. **Do not substitute ICC 0.038.** It is the date-common share of *spread* variance, not of
   realised cost, and A3's own printout says the planned n is the ICC=0.3 column, "not the
   flattering one". Taking 0.038 turns 2.4 months into 0.9 and is exactly the selection this
   licence forbids.

**One instrument bug to fix first, whatever you decide.** `trader/alpaca_feed.py:49-55` is
`try: urlopen(...) except Exception: return None`, so an HTTP 429 and an empty book are
indistinguishable to `get_quote`, and the live reconcile path writes a silently thinner
benchmark rather than saying it was throttled. Proposed (NOT applied -- it is in the live
path): catch `HTTPError` in `_get`, count `e.code == 429` as `self.throttled`, and surface
`throttled` on the fill row. Note the fix must also cover socket timeouts, `URLError`, and
the sub-15-minute SIP 403, all of which currently collapse to `None` as well, and it must
preserve `get_quote`'s documented never-raises contract.

---

## 5. WHAT DIED

Twenty-three findings were refuted -- by both verifiers, or by one whose refutation I
re-ran and confirmed. Recorded so the next pass does not re-derive them.

**Release timing**

- *"The minimum net cost is at +15m, monotone to +15m and flat after."* Dead. Paired against
  +15m: +10m -2.14 (p 0.34), +30m +2.72 (p 0.53), +60m +0.39 (p 0.94). The +15m minimum is
  made largely of a drift term the same report calls statistically zero. Survives only as
  "+5m to +15m, do not cross the first print".
- *"Roughly half the open's cost is the first print, the rest is genuine settling."* The
  arithmetic reproduces; the attribution does not. Of the -21.54 re-anchored saving, -14.59
  is half-spread (t -13.09) and -6.95 is drift at t -1.18.
- *"QLD is the control that proves the wide reads are real."* Dead. QLD is one of the nine
  sleeves, not a control, and shows the same widening ratio. The real controls are TLT and
  GLD, and they hold (MEASURED 0.67 and 0.59 bps/side at 09:30).
- *"The momentum drift the brief warned about is not there."* Half-dead. True
  equal-weighted; notional-weighted it is costly at every offset and grows with the offset.
  Survives only as "no drift effect is established at +5m".

**Order type**

- *"The current instrument fills completely -- 45 orders, 1352/1352 shares, 100%."* Dead.
  `reconcile_fills` (`rotation_live.py:345`) never logs an order that executed zero shares,
  so the denominator is conditioned on the outcome; and all 45 rows are paper-simulator
  fills, where 100% is the simulator's fill *policy*. COMPUTED exact one-sided 95% bound on
  the per-order miss rate at 0/45: **6.4% iid, 12.9% after clustering** -- which implies a
  **28.3% to 49.8%** chance of at least one missing leg in a 5-leg rebalance. Not zero.
- *"Crossing at the release costs 57.0 bps and 22.3 of it is paid in the first half
  second."* Half-dead: 57.0 reproduces but is not payable (0.7ms dwell). The payable figure
  is ~35.6 over [0, 0.5s].
- *"No limit order type beats a plain delay once the clip has to fit the displayed size."*
  Conclusion stands; stated mechanism does not -- the depth constraint was applied to the
  limit arm only, and the deciding margins are inside the noise.
- *"A mid peg is chosen by its median and punished by its mean -- adverse selection."* Dead
  as stated. The escalated legs cost *exactly* what the delay costs; the 1.35 bps residual
  sits on the peg-filled legs and is not distinguishable from zero.

**Routing**

- *"There is no account size at which the 2x route is cheaper."* Dead. The 1x route's second
  dollar of capital is priced at zero; at any real margin spread the floor inverts.
- *"The avoided 2x expense/financing layer is worth 1.03-1.95 pp/yr."* Dead. Corrected:
  **0.286 pp/yr, rate-invariant**, and negative at retail margin. The "independent
  cross-check against T5" is T5's own predictor re-run with a coarser rate.
- *"The capital constraint binds on leverage, not account size."* Premise dead: MEASURED,
  the account is margin-enabled (multiplier 4, Reg-T buying power $35,961.30). "Routing
  everything dies above E=1.00x" is also definitional, not measured, and no rebalance ever
  holds nine sleeves.
- *"The like-for-like spread saving is 6.5x and worth 0.68 pp/yr."* The 6.5x reproduces;
  0.68 does not. X3's own verdict table sums to **0.4211** pp/yr.
- *"Above $100k the 2x route is blocked -- no fill at any price."* Dead. That is a max cell,
  not the route: MEASURED p95 participation at $100k is UCC 19.87%, UXI 56.73%, RXL 7.13%,
  against maxima of 118.9 / 128.4 / 360.5. Screen volume is also not an ETF's liquidity --
  an AP hedges the creation basket -- so a >100% cell is a cost and a delay, not a refusal.

**Slicing**

- *"A single order delayed five minutes beats every sliced schedule at $100k."* Dead as
  stated: the two arms were charged drift measured on different order populations (2,892 vs
  4,487). Matched, the slice wins by 0.71 bps -- also inside the noise. Nothing separates
  them.
- *"The sqrt(k) is worth almost nothing and turns negative once any impact is permanent."*
  Same population defect; matched, the net stays positive at every permanent fraction.
- *"A third of the book cannot be sliced at all, and it is the expensive third."* Half-dead.
  34% is the worst cell of a 3x3 table, and the status quo itself is "infeasible" on 16% by
  the same metric. UCC/UXI genuinely cannot be sliced; the *marginal* infeasibility from
  raising k lands on the liquid names.
- *"Executing at the open is, as an impact question, slightly worse than across the
  session."* Dead. The ratio inverts below 1.0 on every instrument once the volatility
  numerator is sampled at 5 or 15 minutes instead of 1 -- most of the open's excess variance
  is bid-ask bounce, i.e. the spread counted twice.
- *"The live book already pays 26.5 bps/side at the release."* Half-dead: the impact half
  uses a different law from S1's (window sigma over window volume) and comes out at 0.51x
  S1's, and "(A3-measured)" overstates a 3-session snapshot assumed constant over 2016-2026.
  The whole-book release cost is now directly MEASURED at 27.63 bps of half-spread (1d),
  which lands close by a different route.

**Cost function**

- *"The strategy stops beating SPY at $227,172."* Dead as a capacity statement -- that is a
  2006 seed under full reinvestment. Constant-account, the crossing is **~$690,000**
  (settled), inside S1's published band.
- *"The flat 9.5 bps understates the deployed book by ~4x."* Half-dead. True of a compounding
  seed; at the *deployed* order sizes (median $2,971, MEASURED) the same model gives ~1.3x.
  The genuine ~3x is an **open-spread** finding (27.63/9.5 = 2.91x), not a size finding --
  right magnitude, wrong mechanism.
- *"The spread model's error bar is a factor of 1.47."* That is the in-sample geometric
  RMSE on 21 points with 2 parameters, and the printed residual range is already 0.54x to
  2.09x; out of sample it is worse. The deeper problem: a cross-sectional elasticity is
  applied as a within-name time-series elasticity, where the measured slope has the opposite
  sign -- which is why the model prices **2020 (1.7 bps) and 2008 (1.9 bps) as the two
  cheapest years in the whole window**, against 6.6 in calm 2023.
- *"X5 and S1 disagree by exactly one order-size convention."* Mostly true, but two variables
  moved: X5 also switched `MOM_WINDOW` from `live` (what S1 and the live loop run) to
  `canonical`, worth 0.37pp of baseline CAGR. **Every table in section 1 uses `live`.**
- *"MEASURED SIP spreads reproduce A3's controls exactly."* The controls are penny-tick books
  and reproduce by arithmetic; on A3's own session the two instruments disagree by 1.8x on
  the sleeves.

**Measurement design**

- *"The 43 bps sd is not dominated by cross-sectional spread differences."* Dead: the test
  used symbol *identity* and never a quoted spread, and 11 of the 15 rows carry no spread at
  all. Restricted to the traded sleeves the between-symbol share falls to 9.8%.
- *"Same-day matched pairing is the worst of the four routes."* Dead by X6's own printout --
  regression control under the null is n=294 against pairing's 289. Rejecting pairing
  survives on the operational argument (it doubles the real orders placed), not the
  statistical one.
- *"The biggest variance reduction is already banked -- a 47x cut."* Dead as framed. The 296
  bps sd is the **broken-benchmark** sd (a UTC date-truncation bug), not a foregone design.
  Like-for-like the benchmark choice is worth ~2.7x -- the same class as the routes it was
  used to dismiss.
- *"A micro probe measures a lower bound and cannot falsify the capacity wall."* Half-dead.
  It cannot test the sqrt *exponent*, which is a narrower claim, and a UCC-concentrated
  ladder reaches similar precision for roughly a third of the cost.

**And the one that would not die.** The 09:30 book is a MEASURED 2.9x-5x wider than minutes
later. It has now survived four independent attempts to break it, on 150 mornings, 806
legs, 100% complete case, with TLT and GLD flat as controls and every quote tagged `sip`.

---

## 6. THE ONE THING

**Stop crossing the opening print.**

Not because it is elegant. Because it is the only number in this entire pass that is (a)
large, (b) **MEASURED** rather than modelled, (c) reproducible -- every verifier who
attacked it reproduced it and none could break it -- and (d) collectable with no capital
displaced, no holdout spent and no strategy change.

The size: **27.63 bps/side at 09:30:00 against 7.23 five minutes later**, whole book,
notional-weighted, 806 legs over 150 mornings, MEASURED. Priced through the engine at each
symbol's own measured multiple, the release instant costs **10.41 bps/side and 2.12pp of
CAGR at your size**; on the direct measurement it is 20.40 bps and 4.25pp. Either way it is
three to five times larger than anything else on the table, and roughly the difference
between a 15.6% strategy and a 13.5% one.

Everything else here is zero (order type, slicing), a portfolio decision wearing an
execution costume (routing), or a bill (the probe).

**What it would take, honestly:** a process alive at ~09:35 ET, a queue/submit split in
`rotation_live.py`, and the matching fill convention in `opt_harness.py` so the certifier
keeps corresponding to the live loop. That last part is the real work -- the harness has no
"open + N minutes" fill price and the project does not load intraday bars. A day of
engineering, not a config line, and it has to be measured on both sides before it counts.

**And the honest counterweight:** today a queued DAY order fills at the open whether or not
anything is running. A delayed release introduces a failure mode that does not currently
exist, on a box that is off through the session, and that risk is priced nowhere in this
document. Whether 10-20 bps/side is worth buying with a new way for the rebalance to not
happen is your call, not mine.

**If you do exactly one smaller thing instead:** put the account size on the headline.
"18.5% CAGR" with no size attached is not a result -- it is 17.56% at $10k, 15.61% at $100k,
9.62% at $1m, and below SPY somewhere around $690,000. That correction costs nothing, takes
ten minutes, and removes the single most misleading number in the project.

---

## 7. HOW TO REPRODUCE

Everything ran in isolated copies of the six worktrees under the session scratchpad
(`.../scratchpad/synth/w1..w6`); the originals at
`.claude/worktrees/wf_96be5271-c01-{1..6}` are untouched, and nothing was written into
`trader/`, `reports/` or any live configuration. Every network call was a read-only GET; no
order was placed, and `reports/holdout_v2.py` was not opened.

**The `synth/*.py` scripts below and their captured stdout (`synth/out_*.txt`) live in the
session scratchpad, which is session-scoped and will be deleted.** The two measurements
that exist nowhere else -- the whole-book release-instant panel (1d) and the reachability
measurement (2 #1), together with their quote cache -- are in that directory. To keep them:

```
cp -r "<scratchpad>/synth" "<repo>/research/v3/execution_synth"
```

The `w*/reports/x*.py` artifacts are safe: they live in the six worktrees.

| section | command |
|---|---|
| 1a, 1f (fixed-account CAGR) | `synth/fixedcagr.py live`, `synth/openrun.py` |
| 1a decomposition, deployed order sizes | `synth/fixedsize.py` |
| 1b (constant-account capacity) | `synth/capacity.py` |
| 1c (seed convention, X5 headline) | `w5/reports/x5_cost_function.py --part run --mom-window canonical` |
| **1d (whole book at the release, new)** | `synth/wholebook.py --count \| --fetch`, `synth/wbreport.py`, `synth/wbmult.py` |
| 2 #1 (release timing) | `w1/reports/x1_release_timing.py --part report` |
| **2 #1 (reachability: dwell + time-weighted, new)** | `synth/reachable.py` |
| 2 #2 (order type) | `w2/reports/x2_order_type.py --part decay \| policy \| refprice` |
| 2 #3 (slicing) | `w4/reports/x4_slicing.py --part total \| delay \| net \| profile` |
| 3a (routing, and the carry correction) | `w3/reports/x3_routing.py --part verdict \| capital \| carry \| impact`, then `synth/carryfix.py` |
| 3a (the account) | `synth/acct.py` -- read-only `GET /v2/account` |
| 4 (measurement) | `w1/reports/a3_execution.py --part prereg`, `w6/reports/x6_power.py --part power \| micro`, then `synth/derived.py`, `synth/probecost.py`, `synth/misc.py`, `synth/misc2.py` |

Self-checks all pass: `X5 SELFCHECK OK`, `X1 SELFCHECK OK (8 cases)`, `X2 SELFCHECK OK
(12 cases)`, `X3 SELFCHECK OK (3 cases)`, `X4 SELFCHECK OK (10 cases)`, `X6 SELFCHECK OK
(10 cases)`, `CARRYFIX OK`.
