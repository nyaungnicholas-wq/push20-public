# Sector momentum, honestly measured

A cross-sectional momentum strategy on ten US sector ETFs with a
volatility-targeted leverage overlay, and — more to the point — the evidence
protocol around it.

**The strategy is not novel.** Dual-momentum sector rotation is one of the most
replicated anomalies in the literature. What this repository is actually for is
the measurement discipline: a pre-registered sealed holdout, selection on the
median of a perturbation set rather than an argmax, a regression proving the
backtest executes the same rules as the live system, and a written record of the
one time a window had to be graded twice and why.

Write-up with the full argument, charts and derivations: **[Sector Momentum,
Honestly Measured](docs/sector-momentum-honestly-measured.html)**.

## Headline

**Corrected 2026-09-19.** The figures first published here were produced by an
engine that filled at the same close it decided on. That is not a price the live
loop can obtain, so the numbers below supersede them. What changed and why is in
[Corrections](#corrections).

2006-01-01 → 2026-09-02, costs charged at 9.5 bps per side, filling at the next
open:

| | CAGR | Max drawdown | Sharpe | Calmar |
|---|---:|---:|---:|---:|
| strategy | 17.29% | −30.70% | 0.842 | 0.563 |
| SPY buy & hold | 11.07% | −55.19% | 0.640 | 0.201 |

Beats SPY in 14 of 21 years. Returned +4.6% in 2008 while SPY lost 36.2%, and
−13.4% in 2022 against SPY's −18.6%.

**And 9.5 bps per side is an assumption, not a measurement.** Execution cost
scales with account size, and once it is modelled per instrument and per size
rather than as one flat constant:

| account | cost bps/side | CAGR |
|---|---:|---:|
| $10,000 | 8.20 | 17.56% |
| $100,000 | 17.65 | 15.61% |
| $1,000,000 | 47.70 | 9.62% |

The strategy stops beating SPY somewhere around **$690,000**. The spread term is
modelled from measured SIP quotes; the impact term uses a coefficient that has
never been fitted to a real fill, which puts roughly a 3× band on it.

**Read these before any table above means anything:**

- This system **has never placed a live order.** `trader/alpaca_broker.py` in the
  private repo hardcodes Alpaca's paper endpoint as the only URL it has. Every
  fill ever logged is simulator output, so the cost assumption has never met a
  real spread.
- Monte Carlo puts **P(max drawdown worse than −40%) at about 62%**, not the
  32% a weaker estimator reported. Size to the distribution.
- The strategy is **below a prior high 89.5% of days.** The annual figure is
  earned in bursts separated by long flat stretches.
- Minimum backtest length is **14.1 years against 20.6 available.** The data is
  nearly exhausted for further searching, and the holdout is spent.

## Corrections

Kept because a repository about measurement discipline that quietly edits its own
numbers has none.

### 2026-09-19 — the volatility overlay was not targeting volatility

An external forensic audit of the execution and risk layers turned up three
things the configuration's own comments got wrong. The headline table above is
unaffected — it was measured on the configuration this repository ships — but
the reasoning attached to that configuration was not sound.

**The overlay is a two-state switch.** `rotation_vol_target` is 0.50 against a
median basket volatility of 26.2%, so it asks for 1.91x and is clipped by
`rotation_vol_cap = 1.25`. Measured across 5,199 sessions (2006-2026) the
exposure scale sat exactly on its cap **64.0%** of the time, exactly on 1.00
**20.6%**, and moved freely on only **5.5%**. What actually drives it is two
binary flags: SPY below its 200-day average (armed 20.4% of sessions) and
VIX >= 25 (17.0%). The live book has since moved to `vol_target = 0.30`, where
the volatility term binds on 53.6% of sessions; this repository keeps 0.50
because that is what the published numbers were measured on.

**`rotation_position_cap` is inert.** At `top_n = 3` with equal weighting each
position is 0.333, below the 0.60 cap, and the renormalisation after the capping
loop cancels it regardless. The documented "0.50 → 0.60 genuine Pareto move" was
therefore measuring something else. The same applies to the `vol_window 12 → 8`
leg of that claim: reverting it moves the full-window result by −0.11pp of CAGR
and −0.08pp of drawdown, which is noise. Both legs are retracted.

**2022 was attributed to the wrong mechanism.** The configuration recorded
"Cost: 2022 grind year ~−6%" against the GATED v2 risk gates. Measured: the
residual GLD+TLT sleeve averaged **36.8%** of capital that year, half of it TLT,
and TLT returned **−29.4%**. That is −5.3pp — essentially the entire figure. The
risk gates did not cause it, and the revert path documented alongside it would
not have fixed it. Three separate attempts to improve that sleeve (replace its
contents, swap TLT to bills dynamically, route more capital to it under stress)
were graded and all three lost.

**A reproducibility note — corrected 2026-09-20.** The first version of this
note was wrong, and wrong in the direction that made the table look shakier than
it is. It said re-running "the default config" gives 17.19% / −31.19% against the
published 17.29% / −30.70%, and blamed the gap on seventeen extra sessions.

The defaults are not the config that produced the table. `reports/opt_harness.py`
ships `DEFAULTS` that differ from the live strategy in **ten** settings —
including `cost_bps` 5 against 9.5, `vol_target` 0.15 against 0.30,
`vol_cap_bull` 2.0 against 1.25, `weight_scheme` `return_prop` against `equal`,
and no VIX gate, no basket-vol sizing and no defensive-sleeve residual. Comparing
the headline against a defaults run compares two different books and the
similarity of the two numbers is a coincidence.

The table was produced from `reports/live_checkup.py:live_sim_config()`, which
enumerates the live flags (`docs/research/A2a_fill_convention.md` builds its
config that way). Rebuilt from that function today, the same book prints
**17.46% CAGR and −30.70% max drawdown**: the drawdown matches the published
figure exactly, and the CAGR differs only because `FETCH_END` is `today()`, so
the window has grown since the measurement date of 2026-09-02.

The genuine caveats are unchanged: the window grows daily, so pin an end date
before comparing anything here to anything else, and build any comparison config
from `live_sim_config()` rather than typing one — a hand-typed config that omits
a flag inherits a harness default silently, which is exactly the mistake this
note itself made.


**The fill convention (2026-09-19).** `reports/opt_harness.py` set
`FILL_MODE = "close"`: the certifying engine, including the sealed holdout,
transacted at the closing print its decision was computed from. The live loop
queues for the next open. Filling at the next open instead costs **−0.92pp of
CAGR and 2.33pp of drawdown** — paired per-year delta −0.94pp, sd 1.73, t −2.50,
p 0.0126, 14 of 21 years hurt. The original 18.52% / −28.37% / 0.892 / 0.653
reproduces exactly under the old convention (18.58% / −28.37% / 0.894 / 0.655),
so this is a correction of the convention, not of the arithmetic.

**The momentum window.** `trader/rotation.py` computes a 231-bar return where the
configuration, the docstrings and the grid that selected it all mean 232. Every
published number used 232; the live book runs 231. The table above reports the
window the live loop actually computes, which costs a further 0.37pp.

**The cost model.** A flat 9.5 bps per side ignores that sleeves differ (measured
SIP half-spread 7.4 bps/side, worst sleeve 15.8), that impact scales with
size/ADV, and that four of the traded 2x sleeves turn under $1m a day.

**What has not changed:** the evidence protocol, the sealed holdout result, the
PBO null band, and the list of what failed. Those were measured correctly.

## What is worth stealing from here

**PBO needs an error bar, and almost nobody publishes one.** The Probability of
Backtest Overfitting is normally quoted bare. Simulating pure-noise grids of the
same shape — where the true value is 0.5 by construction — gives a null of
**0.494 ± 0.112** at 135 configurations over 5,198 observations. This strategy
measures 0.306, which is 1.68 sd inside that band, as was the 0.425 previously
reported. The honest reading is that *the test lacks the resolution to decide*,
not that the configuration is a coin flip and not that it is identifiable.

The 12,870 CSCV splits are not 12,870 independent observations; they reuse the
same 16 blocks of one realisation, so the effective sample is the blocks. More
splits buys precision on the combinatorics, not on the estimate.

`reports/overfitting_audit.py` computes it, band included, in about ninety
seconds.

**Report what failed.** Fifteen candidate changes were measured; three survived.
The twelve that did not are in the write-up with their numbers. A paper listing
only its successes has told you nothing about its search.

**The biggest single gain came from deleting machinery,** not adding it:
retiring an upper VIX de-leverage tier was worth +1.51pp CAGR *and* 4.6 points
less drawdown. It de-levered at maximum fear and sat there through the rebound.

## Layout

```
trader/            strategy, broker adapter, live loop, metrics
reports/           every experiment that produced a number in the write-up
tests/             165 tests; several fail if a documented defect is reintroduced,
                   and the newest ones are mutation-proven — mutate the momentum
                   comparator, the selection, the weighting or a guard and they fail
dashboard/         local status page
docs/              the write-up, and docs/research/ — the measurements behind
                   the corrections above, including what was refuted
```

| Command | Produces |
|---|---|
| `python reports/holdout_v2.py` | the sealed out-of-sample test |
| `python reports/plateau_v2.py` | the fifteen candidate changes |
| `python reports/overfitting_audit.py` | PBO, DSR, MinBTL and the null band |
| `python reports/harness_regress_wide_v2.py` | proof the engine is unchanged where it claims |
| `python reports/dualmom_spec.py` | comparison against the textbook specification |

Most carry an offline `__selfcheck`. The audit's validates its CSCV against
cases with known answers, because a plausible-looking PBO is indistinguishable
from a wrong one by inspection.

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in only if you want live/paper execution
python -m pytest tests -q
python reports/overfitting_audit.py __selfcheck
```

Backtests need no credentials. Price data is fetched through `yfinance` and
cached under `data/`, which is gitignored.

## Provenance

This repository was created without history, deliberately. Its predecessor had
`.env` committed in four early commits, so that history is not carried over and
this tree starts from a single clean commit. `.gitignore` now blocks `.env`
structurally, and a test asserts the cost assumption cannot silently drift below
measured fills.

If you are forking a private trading repo into a public one, check
`git log --all -- .env` first, and then check the blobs directly — a later
`.gitignore` hides the file from that command while leaving every earlier blob
in place.

## Licence and disclaimer

MIT, see `LICENSE`.

Backtested results are hypothetical and carry no implication of future
performance. This repository documents a measurement. It is not investment
advice, and its author is not a licensed adviser.
