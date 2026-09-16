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

2006-01-01 → 2026-09-02, costs charged at 9.5 bps per side:

| | CAGR | Max drawdown | Sharpe | Calmar |
|---|---:|---:|---:|---:|
| strategy | 18.52% | −28.37% | 0.892 | 0.653 |
| SPY buy & hold | 11.07% | −55.19% | 0.640 | 0.201 |

Beats SPY in 15 of 21 years, median annual excess +5.0pp. Returned +5.5% in 2008
while SPY lost 36.2%, and −13.7% in 2022 against SPY's −18.6%.

**Read these before the table above means anything:**

- This configuration **has never placed a live order.** It was selected
  2026-09-13; first trade 2026-09-16.
- Monte Carlo puts **P(max drawdown worse than −40%) at 63.4%.** The realised
  −28.4% is one lucky path. Size to the distribution.
- The strategy is **below a prior high 89.5% of days.** The annual figure is
  earned in bursts separated by long flat stretches.
- Minimum backtest length is **14.1 years against 20.6 available.** The data is
  nearly exhausted for further searching, and the holdout is spent.

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
tests/             79 tests; several fail if a documented defect is reintroduced
dashboard/         local status page
docs/              the write-up
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
