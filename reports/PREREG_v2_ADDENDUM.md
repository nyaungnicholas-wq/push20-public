# Addendum to PREREG_v2.md — the holdout was graded twice

Written 2026-09-13, after the first grading. `PREREG_v2.md` is left byte-identical
so its recorded sha256 (`c5bdee5e…`) still verifies. Nothing below changes a pass
criterion; they are exactly as registered.

## What happened

The holdout was graded, and then an adversarial review of the diff found a
fidelity bug in the engine that graded it.

`reports/opt_harness.py` built its 2x-routing map from `sec + dfc`, so `GLD→UGL`
and `TLT→UBT` were routable, and a defensive fill appended into `picks` took a
full weighted slot. `trader/rotation.py` `turbo_allocation` does neither: it
separates `equity_picks` from `def_picks`, routes only equity legs through
`LEV2X_MAP`, and gives the defensive sleeve whatever capital is left over, always
unlevered.

This mattered asymmetrically. Under `return_prop` a defensive fill scored 0.01
and was negligible; under `equal` it took a full slot. So the bug flattered the
candidate — the arm that changed to equal weight — more than the baseline.

## What was done

`defensive_live` was added to the harness (default OFF, so all prior results still
reproduce — `reports/harness_regress_wide_v2.py`, 15/15 identical) and switched ON
in `live_sim_config()`. The holdout was then re-run under the SAME criteria.

## Both gradings, same criteria

    buggy engine    baseline  22.01% / -32.18% / Sharpe 0.820 / Calmar 0.684
                    candidate 21.01% / -28.27% / Sharpe 0.901 / Calmar 0.743

    fixed engine    baseline  21.70% / -32.18% / Sharpe 0.818 / Calmar 0.674
                    candidate 20.51% / -28.27% / Sharpe 0.886 / Calmar 0.725

All three gates pass on both. The in-sample selection was re-checked under the
fixed rule and is unchanged: all three changes still improve, and equal weight
improves slightly MORE than it appeared to (Calmar +0.035 vs +0.025).

## Why this is not a second attempt at the same question

The instrument was wrong, so the first reading measured something the account
cannot do. Re-running a fixed instrument against unchanged, pre-registered
criteria is repair, not search. The distinction that matters: no criterion moved,
no candidate was added or dropped, and the direction of the bug was against the
shipped config, not for it.

It is nonetheless a second look at the same window, and is recorded as such here
rather than quietly overwritten. The window is spent. Anyone reading
`HOLDOUT_RESULT_v2.json` should read this file alongside it.

## Also corrected in the same pass

- `reports/harness_regress_v2.py` and `harness_regress_wide_v2.py` imported a
  scratch copy of the old module that was never committed, so the claim they made
  was not verifiable from a clean checkout. They now reconstruct the pre-change
  module from `git show ecbc95d:reports/opt_harness.py`.
- `reports/holdout_v2.py` read `PREREG.md` and wrote `HOLDOUT_RESULT.json`; the
  committed files are `PREREG_v2.md` and `HOLDOUT_RESULT_v2.json`. It raised
  `FileNotFoundError` on line 12.
- `plateau_v2.py`, `combo_v2.py` and `holdout_v2.py` derived `BASE` from
  `live_sim_config()`, which now returns the POST-switch config — re-running them
  would have compared the candidate against itself. `BASE` is now pinned literally.
- `dashboard/server.py` still reported raw unflipped mean slippage against a 5.0
  assumption: +15.7 vs 5.0, while the checkup read +9.8 vs 9.5. Same 97 fills,
  two contradictory verdicts, and the dashboard is the one looked at daily.
