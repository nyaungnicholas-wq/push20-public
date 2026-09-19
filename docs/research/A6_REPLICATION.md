# A6 REPLICATION — drawdown brake, pre-registered out-of-sample re-run

**Engine pin:** `git rev-parse HEAD` = `fd5cee486f1f23c26b2e7a0078a890db3df93982`, branch `v3-research`
(verified in the main tree and in all eight isolated worktrees `.claude/worktrees/wf_45fd1058-8af-{1..8}`).
**Config hash:** `spec_hash()` = `cc9f54eb0341141f849a6d4c16c69b073b0fc6d192813d2f0b4e37d242558ed8`,
identical in the committed artifact and in all three replications, and identical to the value written
into `research/v3/REGISTER.md` **before** any A6 result existed.

Three isolated worktree replications of the pre-registered test, five attack tracks, every finding put
to three adversarial verifiers. The pre-registration at `research/v3/REGISTER.md` (heading
"PRE-REGISTRATION — A6, drawdown brake on unfitted Fama-French data (2026-09-17)") was read and obeyed.
It was **not edited**. Neither was `trader/rotation.py`, any live config, or `reports/holdout_v2.py`.
No orders were placed.

Every number below is labelled **measured** (a command printed it), **estimated** (derived or
extrapolated from measured quantities) or **assumed** (a modelling input nobody measured).

---

## 1. THE VERDICT

**The three replications do not disagree. They agree exactly, so the provenance question this re-run
existed to answer is closed, and the result itself is what has to be judged.** All three regenerate the
committed per-path artifact to the 10-decimal bound of the file's own serialization — replicate-c's run
has since completed and regenerates **all 20,000 rows** (5 cells x 8 variants x 500 seeds), with
`max|new − committed|` = 4.9999e-11 (cagr), 4.9994e-11 (mdd), 4.9997e-11 (sharpe), 4.9899e-11 (lev),
4.9996e-11 (turnover), zero `rebals` and zero `ruin` mismatches, and zero mismatches after rounding the
new values to the stored 10 decimal places (**measured**, my own diff). Every register headline returns:
gap **+25.33pp ± 1.91, z +13.25** at block 126; **+40.33 / +36.24 / +25.33 / +18.24 pp** across blocks
21/63/126/252; **+18.85pp, z +10.83** at 19.0 bps; median MC maxDD **−43.14% vs −65.05%**; paired dSharpe
**+0.0781 ± 0.0018 (z +44.25)** vs live and **+0.0181 ± 0.0020 (z +8.88)** vs plain de-levering;
dominance over cap 0.75x **9.94% vs 8.76% CAGR, 73.8% vs 85.0% tail, −43.1% vs −48.1% medDD**
(**measured**, reproduced by replicate-c's independently-written scorer on its own independently-generated
paths). The concurrent-edit race that voided other FF tracks did not reach A6's numbers. **The
pre-registered decision rule PASSES all four criteria, in all three runs.**

**And the result still does not clear the bar for a live change.** Four things came out of the attacks,
all measured. (i) Every `z` in that row is Monte-Carlo precision, not evidence: `score_block` computes
`se = d.std(ddof=1)/sqrt(n_paths)`, and re-scoring the identical design at 500/1000/2000/4000 paths moves
the gap 25.3 -> 26.3pp while z goes **13.25 -> 18.87 -> 26.24 -> 37.28**, with `se·sqrt(n)` flat at
42.75/43.42/44.32/44.60. Read the way register rule 2 and the sibling A6-PRE and A7 tracks read it — 1 SE
of the paired difference **being the bootstrap SD** — the brake is **+1.98 SE vs the live baseline** but
**+0.40 SE vs plain de-levering (cap 0.75x)**, i.e. it fails rule 2 on the comparison the whole frontier
design exists to make. (ii) The −40% metric is saturated on this panel: four of six frontier points sit at
99.4–100.0%, so "+25.3pp" is close to *100 minus the brake's own tail*; the unsaturated statement is the
**11.2pp** paired tail advantage over cap 0.75x. (iii) At an era-realistic cost schedule the
pre-registered rule **FAILS** (gap +3.3pp, z +1.75 at block 126, n=90). (iv) On the live ETF book the same
test gives **+2.05pp, z +0.40** at 9.5 bps and **−8.33pp, z −2.86** at 19.0 bps — no measured transfer, and
negative under the cost stress. Underneath all of it the honest sample is **3 distinct drawdown episodes
reaching −40% in 79 years** (1929-09-10, 1966-04-18, 2001-05-22), not 500 paths. The one genuinely new
positive finding is a third, never-fitted, **unsaturated** cross-section (FF Developed ex-US 25 Size/BM,
1990–2005: +45.82pp, z +8.68 against a baseline tail of 39.5%, positive at all four blocks and at 19.0
bps) — but it is post-hoc and cannot found a proposal. **Verdict: replicated, not established. HOLD.**

---

## 2. REPLICATION TABLE

All three worktrees at the pin, each with its **own copied** `data/ff_cache` (34 MB, 3 CSVs; md5
`0c01a18e298cb5738dba0b1fe944859d` for `30_Industry_Portfolios_daily.csv` confirmed identical to the main
repo's in every worktree) — no shared cache, which is the race this re-run existed to remove.

### 2a. Per-cell scores, 10/20 brake (`dd_brake [(0.10,0.75x),(0.20,0.5x)]`)

| cell | quantity | ORIGINAL (committed) | replicate-a | replicate-b | replicate-c | mark |
|---|---|---|---|---|---|---|
| 126d @ 9.5 | gap | +25.3290pp | +25.3290 | +25.3290 | +25.33 | **exact** |
| 126d @ 9.5 | se | 1.9118 | 1.9118 | 1.9118 | 1.9 | **exact** |
| 126d @ 9.5 | z | +13.2488 | +13.2488 | +13.2488 | +13.25 | **exact** |
| 126d @ 9.5 | med MC CAGR | 9.94% | 9.94% | 9.94% | 9.94% | **exact** |
| 126d @ 9.5 | med MC maxDD | −43.14% | −43.14% | −43.14% | −43.1% | **exact** |
| 126d @ 9.5 | brake tail P(maxDD<=−40%) | 73.8% | 73.8% | 73.8% | 73.8% | **exact** |
| 21d @ 9.5 | gap / z | +40.3344pp / +13.3522 | identical | identical | +40.3 / +13.35 | **exact** |
| 63d @ 9.5 | gap / z | +36.2366pp / +16.5909 | identical | identical | +36.2 / +16.59 | **exact** |
| 252d @ 9.5 | gap / z | +18.2417pp / +10.0600 | identical | identical | +18.2 / +10.06 | **exact** |
| 126d @ 19.0 | gap / se / z | +18.8537pp / 1.7412 / +10.8281 | identical | identical | +18.9 / 1.7 / +10.83 | **exact** |
| 126d @ 9.5 | dSharpe vs live | +0.0781 ± 0.0018, z +44.25 | +0.0781 / +44.25 | +0.0781 / +44.25 | +0.0781 / +44.25 | **exact** |
| 126d @ 9.5 | dSharpe vs cap 0.75x | +0.0181 ± 0.0020, z +8.88 | +0.0181 / +8.88 | +0.0181 / +8.88 | +0.0181 / +8.88 | **exact** |
| 126d @ 9.5 | dominance vs cap 0.75x | 9.94/8.76 %, 73.8/85.0 %, −43.1/−48.1 % | identical | identical | identical | **exact** |
| — | verdict c1/c2/c3/c4 | all `true`, PASS | all `true`, PASS | all `true`, PASS | all `true`, PASS | **exact** |
| — | 15/25 brake gap / z @126 | +6.26pp / +5.73 | +6.3 / +5.73 | +6.3 / +5.73 | +6.3 / +5.73 | **exact** |
| — | 15/25 dSharpe vs cap 0.75x | −0.0290 ± 0.0021, z −13.99 | identical | identical | identical | **exact** |
| — | `spec_hash` | `cc9f54eb…58ed8` | same | same | same | **exact** |

All **measured**. Nothing in the register's A6 row diverged in any run.

### 2b. Coverage and method of each replication

| | replicate-a (`wf…-1`) | replicate-b (`wf…-2`) | replicate-c (`wf…-3` + scratchpad) |
|---|---|---|---|
| paths regenerated | 20,000 (all 5 cells x 500 seeds) | 20,000 (`a6_repB_paths.jsonl`, 20,009 lines incl. realised) | **20,016** (all 5 cells x 500 seeds + seed 0), run completed 11,701 s |
| workers | 4 (per CPU cap) | 4 | 4 |
| wall clock | 11,445 s, 4 stages, ended `A6 REPLICATION A OK` | ~2,350 s at 4 workers | 11,701 s, `ALL PANELS DONE` |
| scorer | pinned `score_block` | pinned `score_block` + own field diff | **own scorer written from pre-registration clause (e)**, `repC_score.py` |
| diff vs committed | byte-identical paths file (md5 `473c5587deca9b87257c5255df1863c9`) | 558 comparable scalar fields, **16 differ, max 4.6185e-14** | 20,000-row overlap, max 4.9999e-11, **0 mismatches after 10-dp rounding** |

**Mark for all three: exact**, with one wording correction the register should carry: **nothing is
bit-exact.** `reports/a6_dd_brake.py:319` writes the per-path table with `D.to_json(..., orient="records")`
and no `double_precision` argument, so pandas' default of **10 decimal places** applies and every stored
float sits on a 1e-10 grid. The honest statement is *reproducible to ~1e-15 relative, identical to
0.00e+00 after the file's own 10-dp rounding* (**measured**: replicate-b's recomputation drifts by ~2e-15
relative on 9 realised-path turnover values, and my own 20,000-row diff shows 422 turnover values
differing by up to 1.776e-15 after rounding — exactly the signature of genuine recomputation rather than a
file copy).

### 2c. Was the bootstrap seeded, and what that implies

**Seeded, per path, deterministically.** `boot()` at `reports/a6_dd_brake.py:97-105` is
`rng = np.random.default_rng(seed); starts = rng.integers(0, n, size=ceil(n/block))`; `run_seed()` builds
**one** synthetic panel per `(block, seed)` and runs all 8 variants x both costs on it; `score_block()`
resamples the seed set with a separately fixed `np.random.default_rng(3)`, B = 1000.
`reports/ff_industry.py` contains no RNG at all. The process pool is processes, not threads, and both
places ordering could leak are explicitly sorted (**measured**, read at the pin).

Three implications, and they cut in opposite directions.

1. **Agreement was near-guaranteed, so it is a determinism check, not a statistical replication.** A
   matching re-run establishes that the committed artifact came from the pinned engine. It cannot detect a
   design error: a mis-specified statistic reproduces to 14 digits exactly as readily as a correct one.
   What it *does* retire — and this was the stated purpose — is the mixture hypothesis: a mid-flight edit
   to `reports/ff_industry.py` (mtime 01:38, inside A6's run window; the artifact was written 01:47) could
   not have produced a table that the pinned code now regenerates row for row. Note `spec_hash()` hashes
   only the *design* (LIVE flags, variants, panel, window, blocks, path count, costs, axis, metric) —
   **not** engine source — so the hash is not evidence against the race. The row-level numeric match is.
2. **The variants are genuinely paired** (one panel per seed, every variant on it), which is correct
   variance reduction and is why the dSharpe SEs are small.
3. **Reusing seeds 1..500 at every block length makes the four blocks non-independent.** `boot()` keys on
   `seed` alone, so a longer block simply takes fewer draws from the same stream and the start-index
   sequences are nested prefixes (**measured**: seeds 1..20, all four blocks, first five starts identical
   at every block; `nb` = 1000/334/167/84 at blocks 21/63/126/252). Criterion 2's "sign stability across
   four blocks" is therefore closer to one experiment viewed at four smoothing levels than to four
   confirmations — and all four resample the same 1926–2005 history regardless.

---

## 3. THE ATTACKS

### 3.1 Path count and path independence

**Paths are independent of each other; that is precisely why the z is meaningless.**
`reports/a6_pathcount_n4000.out` (worktree `wf…-4`, 4,000 complete seeds at block 126 / 9.5 bps), all
**measured**:

| n paths | gap | se | z | se·sqrt(n) | med MC CAGR | tail |
|---|---|---|---|---|---|---|
| 500 | 25.3pp | 1.91 | **+13.25** | 42.75 | 9.94% | 73.8% |
| 1000 | 25.9pp | 1.37 | +18.87 | 43.42 | 10.03% | 73.6% |
| 2000 | 26.0pp | 0.99 | +26.24 | 44.32 | 10.09% | 73.7% |
| 4000 | 26.3pp | 0.71 | **+37.28** | 44.60 | 10.10% | 73.4% |

The point estimate moves 1.0pp; the z triples. Paired dSharpe does the same: +0.0781 (z +44.25) at n=500
-> +0.0758 (**z +117.81**) at n=4000, with `se·sqrt(n)` flat at 0.0395 -> 0.0407. Independence is
confirmed, not refuted — lag-1 autocorrelation of the per-path difference is **+0.0256**, batch-means ESS
4002/4410/4001 at batch 25/50/100 — and the pairing is legitimate (shuffling one arm leaves the mean
unmoved at +0.0758 and widens the se 0.0006 -> 0.0030, a **21.4x** variance ratio from
`corr(A,B) = +0.9536`). All **measured**.

**Consequence for register rule 2.** Under `se = sd/sqrt(n_paths)`, rule 2 ("improvement at least 1 SE of
the paired difference") is satisfiable for any non-zero mean by buying CPU. Under the convention the
sibling tracks actually used — `reports/ff_verify.py:143` `se_sharpe = float(ds.std(ddof=1))`,
`reports/a7_residual_ff.py:277` `se = float(np.std(bs, ddof=1))`, which is why A6-PRE reported SE 0.0862
and A7 SE 0.0765 on 2000 paths — the same 500 paired paths read (**measured**, my own recomputation from
the committed path table):

| comparison | mean dSharpe | bootstrap SD | SD/sqrt(500) | z as published | **z at 1 bootstrap SD** | P(d <= 0) |
|---|---|---|---|---|---|---|
| 10/20 vs live baseline | +0.0781 | 0.0395 | 0.0018 | +44.25 | **+1.98** | 0.016 |
| 10/20 vs **cap 0.75x** | +0.0181 | 0.0455 | 0.0020 | +8.88 | **+0.40** | 0.344 |
| 15/25 vs live baseline | +0.0310 | 0.0403 | 0.0018 | +17.22 | +0.77 | 0.226 |
| 15/25 vs cap 0.75x | −0.0290 | 0.0464 | 0.0021 | −13.99 | −0.63 | 0.730 |
| paired dP(maxDD<=−40%) vs baseline | −26.20pp | 44.02pp | 1.97pp | −13.31 | −0.60 | — |

The brake clears rule 2 against the *deployed* configuration by 1.98 SE. It does **not** clear it against
*plain de-levering*, which is the comparison the matched-return frontier was built to make.

### 3.2 Era-realistic cost

`reports/a6_eracost.py` (worktree `wf…-5`) re-runs the whole pre-registered measurement with a
time-varying cost schedule in place of the flat 9.5 bps, **90 paired paths at block 126** (not the
pre-registered 500; blocks 21/63/252 got 15 paths). Frontier gap for the 10/20 brake versus flat one-way
cost, all **measured**:

| cost | 10 bps | 19 bps | 40 bps | 80 bps | era schedule |
|---|---|---|---|---|---|
| gap | +28.1pp | +23.3pp | +12.2pp | +0.0pp | **+3.3pp** |
| z | +5.60 | +5.11 | +3.37 | +nan | **+1.75** |

**Re-applying the pre-registered decision rule with the era schedule: FAIL for both brakes** (10/20:
criterion 1 FAIL at z +1.75, criterion 2 FAIL; criterion 4 PASS at −52.6% vs −79.4%. 15/25: +0.0pp,
FAIL). **Measured**, printed verbatim in `a6_era_score_final.out`.

Two corrections to how that is read. The reported "breakeven 80.0 bps" is a **saturation artefact**, not a
crossing: at 80 bps **all eight variants** sit at P(maxDD<=−40%) = 100.0%, so the gap is identically zero
on every bootstrap resample (that is why its SE printed 0.0 and its z printed `+nan`), and the script's
linear crossing rule returns the grid endpoint. At 80 bps the brake still has the shallower median
drawdown (−55.4% vs cap 0.75x's −71.6%) and the higher median CAGR (2.54% vs 1.81%) — the *indicator* ran
out of resolution, the brake was not overtaken (**measured**). And on the unsaturated cross-read the
brake's advantage over plain de-levering **increases** with cost: dSharpe vs cap 0.75x = +0.0179 (9.5),
+0.0283 (19.0), +0.0521 (40), +0.0813 (era), +0.0847 (80 bps) (**measured**) — but at `se = sd/sqrt(90)`
those z's carry the same defect as §3.1, and the mechanism is partly an exposure-ladder artefact rather
than trading skill: the brake's per-unit-of-exposure turnover is *higher* than the baseline's, and its
lower dollar turnover comes from holding less (**measured** in a verifier re-run: turn/gross 13.56 for the
brake vs 9.77 for the baseline and 14.17 for cap 0.75x, at gross 0.646 vs 1.084 vs 0.687).

The era failure is at n=90, which is 18% of the pre-registered design, so it is **not** a pre-registered
failure and should not be reported as one. It is, however, the regime A6-PRE used to declare the
underlying momentum selection dead, and the direction is unambiguous.

### 3.3 Subwindows: 1946–2005 and 1975–2005

`reports/a6r_legs.py` (worktree `wf…-6`), post-hoc, n=250 per leg, all **measured**:

| leg | block / cost | baseline tail | baseline med maxDD | brake tail | gap | z |
|---|---|---|---|---|---|---|
| 1926–1945 (Depression only) | 126 / 9.5 | 100.0% | −73.1% | 92.0% | **+8.00pp** | +4.04 |
| 1946–2005 | 126 / 9.5 | 92.4% | −49.8% | 5.2% | **+75.74pp** | +16.20 |
| 1975–2005 | 126 / 9.5 | 78.4% | −46.4% | 5.6% | **+43.49pp** | +6.35 |
| 1975–2005 | 126 / 19.0 | 82.4% | −47.9% | 6.4% | **+53.79pp** | +9.10 |
| 1975–2005 | 21 / 9.5 | 77.2% | −45.2% | 1.6% | **+38.77pp** | +5.47 |

**The effect is not a Depression artefact.** The Depression-only window is the *weakest* leg of the five;
the modern, Depression-free windows are the strongest, on frontiers that are no longer pinned at 100%.
That is the opposite of what the "one crash wearing 500 hats" objection predicts, and it held under every
attack aimed at it. An independent re-run on 1946–2005 with 120 paths reproduced the direction at
+71.3pp ± 4.8, z +14.73, against an unsaturated baseline tail of 92.5% (**measured**).

**The one place the subwindows do bite is Sharpe.** Paired dSharpe vs cap 0.75x, same legs (**measured**):
1926–1945 **+0.0214 ± 0.0053**, 1946–2005 **−0.0507 ± 0.0030**, 1975–2005 **−0.0906 ± 0.0040** — against
+0.0181 on the full window. So on the tail metric the brake gets *better* without the Depression and on
risk-adjusted return against plain de-levering it gets *worse*, and both readings come from the same 250
paths. (Those SEs are the `sd/sqrt(n)` kind; at one bootstrap SD none of them is significant. The *sign*
flip is the finding, not the z.)

### 3.4 How many distinct drawdown episodes actually carry the effect

`reports/a6r_episodes.json` (worktree `wf…-6`), realised FF panel, **measured**:

| window | episodes where the brake binds (baseline dd <= −10%) | reaching −20% | **reaching −40%** |
|---|---|---|---|
| 1926–2005 (pre-registered) | 57 | 24 | **3** |
| 1946–2005 | 50 | 21 | **2** |
| 1975–2005 | 24 | 11 | **1** |

The three −40% episodes are 1929-09-10 -> 1945-12-08 (base −86.9%, brake −66.1%), 1966-04-18 ->
1967-07-10 (−40.0% -> −29.8%) and 2001-05-22 -> 2005-02-16 (−41.8% -> −30.3%). Two of the three clear the
threshold by 0.03pp and 1.8pp respectively — knife-edge. **The pre-registered metric is priced off three
events, one of them a single 16-year never-recovered run.**

Two things sharpen this. The bootstrap *manufactures* tail events rather than replaying those three: the
1975–2005 baseline breaches −40% on 78.4% of paths with a median path drawdown of −46.4%, deeper than
anything that window realised (−41.8%) (**measured**). And the block bootstrap cannot carry the episode
that drives the real answer — the realised longest underwater spell is 4,846 bars against a 126-bar
block. Separately, on the realised single path **every** variant breaches −40% (baseline −86.90%, 10/20
brake −66.06%), so the realised-path gap is 0.0pp for every variant and the entire +25.3pp exists only in
resamples (**measured**; the pre-registration disqualified the realised axis in advance, for a defensible
reason, but the consequence stands).

Counterweight, also **measured**, and it is why I am not calling this a one-episode result: the gap does
not track Depression content. Sorting the 500 paths by their 1929–32 block share gives +41.1pp ± 4.53 in
the bottom quartile against +19.6pp ± 2.57 above the median — *larger* where the crash is scarcer — and 0
of 500 paths contain none of it. The scarcity argument rests on the episode **count**, not on 1929
dominance.

### 3.5 Does it show up in any third cross-section?

Yes, once, cleanly — and that is the strongest new evidence this round produced. `reports/a6rr_panel.py`
(worktree `wf…-7`) runs the identical variants, engine, bootstrap and scorer on non-US Fama-French panels,
1990-07-02 -> 2005-12-30 (4,045 rows), **measured**:

| panel | n paths | block/cost | baseline tail | brake tail | gap | z |
|---|---|---|---|---|---|---|
| **Developed ex-US 25 Size/BM** (25 assets) | 200 | 126 / 9.5 | **39.5%** | 0.0% | **+45.82pp** | **+8.68** |
| Developed ex-US 25 | 200 | 21 / 9.5 | 23.5% | 0.0% | +7.71pp | +1.51 |
| Developed ex-US 25 | 200 | 63 / 9.5 | 30.5% | 0.0% | +24.51pp | +5.76 |
| Developed ex-US 25 | 200 | 252 / 9.5 | 62.5% | 1.0% | +64.64pp | +15.24 |
| Developed ex-US 25 | 200 | 126 / **19.0** | 46.0% | 0.0% | **+53.64pp** | +9.22 |
| Europe 6 Size/BM (6 assets) | **24** | 126 / 9.5 | 12.5% | 0.0% | +8.33pp | +1.13 |
| Europe 6 | 24 | 21 / 63 / 252 | — | — | +10.30 / +14.89 / +24.69pp | +0.86 / +1.95 / +2.18 |

The Developed ex-US baseline tail is **39.5%, not 100%** — a genuinely graded frontier (1.5 / 17.5 / 28.0
/ 34.0 / 39.5 / 55.5% across the six caps), so the saturation objection does not apply there. All four
blocks positive, 2x cost positive, median MC maxDD −26.3% vs baseline −37.4%.

Five caveats, all of which matter. (a) The panel is **post-hoc** — it is not in the pre-registration, so it
is direction, not evidence under rule 6. (b) n = 200, below the pre-registered 500. (c) The brake's own
tail is pinned at **0.0%** in four of five cells, so saturation has moved from the comparator to the
treated arm and the ± contains no uncertainty from the brake side. (d) Europe 6 is **all-positive at every
block** — which corrects a verifier's claim that it "does not replicate" — but at n = 24 nothing there is
distinguishable from zero. (e) The RF column is the US one-month T-bill (95.4% of overlapping days exactly
equal to the US series, **measured**) and the portfolios are USD, so the panel is less currency-independent
than "local RF" implies; 1990–2005 is also a strict subset of the FF30 window, so the 2000–2002 bear is
shared.

**The live ETF book is the cross-section that matters, and there it does not show up.**
`reports/a6rr_etfmc.py`, 2006-01-01 -> 2026-09-02, 120 paired paths, block 126, XLC/BIL/SHY dropped,
`fill_mode=close` — **in-sample**, so direction only (**measured**):

| cost | baseline tail | 10/20 gap | z | 15/25 gap | z |
|---|---|---|---|---|---|
| 9.5 bps | 63.3% | **+2.05pp** | **+0.40** | −1.74pp | −0.31 |
| 19.0 bps | 66.7% | **−8.33pp** | **−2.86** (flat-extrapolated) | −29.94pp | −4.73 |

On the realised live ETF path (**measured**): baseline 17.75% CAGR / −30.70% maxDD / Sharpe 0.860; 10/20
brake 15.60% / −28.87% / 0.841; **cap 0.75x 13.49% / −24.26% / 0.924**. Plain de-levering buys a deeper
drawdown reduction *and* a higher Sharpe than the brake on the book the change would ship to. The 15/25
brake leaves realised maxDD **identical** to baseline (−30.70%) while costing 0.56pp of CAGR. And the
metric itself has no resolution there: across 500 FF paths the *shallowest* baseline drawdown is −40.05%
and the shallowest braked path −31.54%, against the live book's realised **−28.37%** — **zero**
observations in the live book's drawdown regime (**measured**).

### 3.6 What implementing it live would actually cost in state and failure surface

The brake is driven by a running equity high-water mark: `reports/ff_industry.py:186` `peak = equity`,
`:206` `if equity > peak: peak = equity`, `:225-229` `dd = equity/peak - 1.0` then `cap = min(cap, lvl)`.
In simulation `peak` is a local float seeded at bar 0 of a continuous 79-year path: it cannot be lost,
reset, or moved by a deposit.

**The live loop cannot compute it today.** `grep -c dd_brake trader/rotation.py` -> **0**;
`grep -ic "high_water|hwm|equity_peak" trader/rotation.py` -> **0** (**measured**).
`reports/ff_industry.py:73` carries `dd_brake=None,  # portfolio drawdown brake; OFF in the live book`.
The nearest live state is `trader/risk_gate.py`, which persists `peak_equity` to `data/risk_state.json`
(currently `peak_equity: 102488.18`) for a **−45%** halt — a different threshold and a different object.

**Measured cost of losing that state** (`reports/a6_ops_stateloss.py`, worktree `wf…-8`, 80 paired
Method-B paths, block 126, pre-registered 10/20 brake):

| arm | med MC CAGR | med MC maxDD | P(maxDD<=−40%) | brake binds | ΔP(<=−40%) | z |
|---|---|---|---|---|---|---|
| state persists (control) | 10.10% | −41.57% | 68.8% | 67.2% | — | — |
| one reset per year | 12.03% | −54.35% | 98.8% | 29.2% | **+30.0pp** | +5.82 |
| one reset per quarter | 11.66% | −59.80% | 100.0% | 9.7% | +31.2pp | +5.99 |
| never persists (= no brake) | 11.26% | −63.19% | 100.0% | 0.0% | +31.2pp | +5.99 |
| one reset at the path's deepest bar | 10.32% | −42.43% | 71.2% | 64.5% | +2.5pp | +1.42 |

An **annual** state loss — an ordinary redeploy cadence — returns **30.0 of the 31.2pp** the brake buys.
Read the bound correctly: `never_saved` reproduces the unbraked baseline exactly, so total state loss *is*
"no brake" and can never cost more than the brake's whole effect. The "one badly-timed reset is harmless"
reading does **not** survive: that arm places the reset at `argmin(dd)`, the bar where maxDD is already
realised, so it cannot deepen the metric by construction. Moved into the decline, an independent re-run
measured +15.0pp (z +3.73) at the first bar dd <= −10%, +16.2pp (z +3.60) at −20%, +18.8pp (z +3.96)
mid-decline (**measured**).

**Failure surface, read at the pin.** `trader/rotation_live.py:122-129` `_load_state()` returns `{}` on
`(OSError, ValueError)`; `:131-138` `_save_state()` is `try/except OSError: pass` with a bare `pass`, no
log, no alert, and a non-atomic `open(path,"w") + json.dump` (a torn write is a `JSONDecodeError`, which
subclasses `ValueError`, so it reads back as `{}`). `trader/risk_gate.py` has the same pattern. A brake on
that storage reads **dd = 0.0** when its storage breaks — indistinguishable from an all-time high — and
silently returns to full leverage at exactly the moment it should be binding. External cash flows are a
second hazard with an asymmetric sign, also **measured** on the realised FF panel with a 10%-of-equity
annual flow: a raw-broker-equity peak treats a deposit as a gain and cuts the duty cycle 58.8% -> 42.2%,
and treats a withdrawal as a loss and jams the brake on, 58.7% -> 97.4%.

**And it is not a circuit breaker on this panel.** Realised FF, 10/20 brake: dd <= −10% on **58.2%** of
bars and dd <= −20% on **33.7%**; it binds on 58.9% of decision bars realised and 67.2% in the bootstrap
(**measured**). On the live ETF panel the same measurement gives dd <= −10% on 32.8% of bars, dd <= −20%
on **2.0%**, dd <= −40% on **0.0%** (**measured**). So the thing that was tested is a near-permanent
de-lever; the thing that would ship is a rarely-armed tier whose behaviour is unmeasured. That gap is not
priced anywhere in +25.3pp.

**Live-computability, the one honest information cost.** The backtest decides on bar *t*'s close-marked
equity; the live loop decides on *t*'s close and fills at *t+1*'s open. A 1-bar stale brake state costs
**−0.238pp CAGR (z −7.48)** in 80 paired paths; peak updated only on decision bars +0.028pp (z +1.38), 5%
of sessions missed entirely +0.019pp (z +0.39) (**measured**). Note the realised single path prints
**+0.17pp** for the same lag — opposite sign. The brake contains **no look-ahead**: a future-perturbation
test (scramble and 1.5x-scale every panel return after bar T, require the prefix bit-identical) passes at
T = 4000/10000/16000, brake on and off, under both perturbation and truncation, 12 bit-exact cases with
the suffix demonstrably moved (**measured**) — though that test has low power against short-horizon peeks,
and the stronger evidence is the code read: no `pos+k` index read exists anywhere in the loop body.

---

## 4. THE SIX CRITERIA, SCORED STRICTLY AFTER THE ATTACKS

**Framing first, and it is not optional.** The 10%/20% and 15%/25% thresholds were proposed **in-sample on
2006–2026** (T7, +11.8pp, z 7.1). This run is therefore at best a **pre-registered out-of-sample
replication of an in-sample discovery. It is not a discovery and must never be written up as one.**

| # | criterion | verdict | why |
|---|---|---|---|
| 1 | **Fresh evidence** — never fitted | **CLEARS** | FF 30-industry daily 1926-07-01 -> 2005-12-31, 21,120 rows; the 2006–2026 window is excluded entirely. Confirmed by re-running the panel load: `21120 rows x 32 cols, 1926-07-01 -> 2005-12-30`. A second never-fitted panel (Developed ex-US 25, 1990–2005) agrees. **Measured.** |
| 2 | **Bigger than the noise** — >= 1 SE of the *paired* difference, block bootstrap, 1000+ paths | **FAILS** | Against the deployed config: +1.98 SE (clears). Against **plain de-levering**, the comparator the frontier design exists to construct: **+0.40 SE, P(d <= 0) = 0.344** (**measured**). The published +8.88 is `sd/sqrt(500)`, a Monte-Carlo precision number that triples with CPU and that the sibling A6-PRE and A7 tracks did not use. The 1000+ path requirement is separately unmet at the pre-registered 500 — though the n=4000 run gives the same point estimate, so path count is not the binding problem. |
| 3 | **Survives 2x the assumed 9.5 bps** | **CLEARS AS WRITTEN, FAILS ON REALISM** | At 19.0 bps: +18.85pp, z +10.83 on FF and +53.64pp, z +9.22 on Developed ex-US (**measured**) — the rule asks for exactly this and it passes. Under the era-realistic schedule the pre-registered rule returns **FAIL** (+3.3pp, z +1.75, n=90, **measured**), and 9.5 bps remains **assumed**, resting on 105 paper fills. |
| 4 | **No drawdown regression; P(maxDD <= −40%) no higher than 63.4%** | **CLEARS IN ITS OWN TERMS, DOES NOT TRANSFER** | On FF: median MC maxDD −43.14% vs baseline −65.05% (**measured**) — passes as the pre-registration defines it. The 63.4% **level** does not transfer: the FF baseline is 100% and the live book's realised maxDD is −28.37%, with 0 of 500 paths in that regime. A5 already put the one-history SE on the tail *level* at ~±19pp. On the realised live ETF path, plain de-levering beats the brake on both drawdown and Sharpe. |
| 5 | **Mechanism stated first** | **CLEARS** | Pre-registration clause (a), one sentence, written before the run: an 8-day realised-vol target sizes off volatility, which can stay moderate through a slow multi-month bleed, so a brake keyed to our own equity drawdown is the only control in the stack that observes that state. |
| 6 | **Pre-registered** | **CLEARS** | `spec_hash` `cc9f54eb…58ed8` recomputed and matched in all three replications and present in `REGISTER.md` before the run. The register was not edited in this re-run. |

**Score: 4 of 6 clear, 1 fails, 1 clears only inside a transplant whose regime the live book has never
entered.** The pre-registered A6 decision rule (its own four criteria) passes; the register's six "clearly
better" criteria do not.

---

## 5. WHAT DIED

Findings from this round that adversarial verification refuted, with the measurement that killed each.

1. **"Bit-exact replication."** The per-path artifact is written at 10 decimal places
   (`a6_dd_brake.py:319`, pandas `double_precision` default). Agreement is to ~1e-15 relative and to
   0.00e+00 only *after* that rounding. The word overstates the artifact. (Note: the byte-comparison half
   of the "naive comparison fails" warning is also wrong — re-serializing the parsed file through the same
   call reproduces it byte-for-byte, sha256 `4c1a87534aa2a05c…` both ways, **measured**.)
2. **"The concurrent-edit race did not touch A6."** A6 imports `reports/ff_industry.py` directly, and the
   register's own A6-PRE row records two sessions editing it concurrently. The correct statement is
   narrower: *the pinned engine regenerates the pinned artifact row for row, so a mid-flight engine version
   cannot have produced it.* The cited `spec_hash` is **not** evidence for this — it hashes the design, not
   engine source, and an independent verifier mutated `ff_industry.py`'s levered expense drag and the hash
   still printed as matching while realised baseline CAGR moved 0.15938892 -> 0.13952894.
3. **"z +13.25 / +44.25 / +8.88 are evidence strength."** Dead. `se = sd/sqrt(n_paths)`; z = 13.25 ->
   37.28 and 44.25 -> 117.81 purely by raising the path count on the same data (**measured**).
4. **"Criterion 2's four blocks are four independent confirmations."** Dead. `boot()` keys on `seed` alone,
   so block start sequences are nested prefixes of one RNG stream (**measured**), and all four resample one
   history regardless.
5. **"Criteria 1–4 are four independent tests."** Dead. c1, c3 and c4 all read block 126 (`c1`/`c4` from
   the same dict), the 19.0 bps cell **re-prices the identical 500 synthetic panels** rather than drawing
   new ones (`run_seed` builds one panel then loops variants x costs), and c2's four block gaps include
   c1's own number.
6. **"The +25.3pp is a clean effect size."** Dead as stated. The FF frontier is 85.0 / 97.6 / 99.4 / 99.8 /
   100.0 / 100.0%, so the gap is approximately 100 − brake tail; regressing gap on ceiling across the ten
   scored cells gives R2 = 0.976 (**measured**). The defensible number is the **11.2pp** paired tail
   advantage over cap 0.75x.
7. **"Breakeven flat one-way cost = 80.0 bps."** Dead — a saturation artefact. At 80 bps every variant
   including both brakes is at P(maxDD <= −40%) = 100.0%, the paired difference is identically zero on all
   90 paths, SE printed 0.0 and z printed `+nan`, and the linear crossing rule returned the grid endpoint.
   The 15/25 brake's "40.0 bps" is the same artefact.
8. **"On the live ETF book the brake is a straightforward bad trade."** Refuted as stated: the comparison
   was against the *levered* baseline at unmatched return. On the matched-return axis the pre-registration
   actually uses, the realised-path 10/20 gap is **+1.08pp** on all six frontier points — but that frontier
   contains a strictly dominated point (cap 1.00x: lower CAGR *and* deeper maxDD than cap 1.10x), and
   removing it moves the gap to **−0.06pp**, a wash (**measured**).
9. **"Half the pre-registered brake family is inert on the live book."** Refuted. The 15/25 brake's
   realised maxDD equals baseline's only because the deepest live episode (COVID, trough 2020-03-18) is a
   vertical move its −15% rung cannot get ahead of; it differs from baseline on **36.6%** of sessions, cuts
   levered sessions 74.2% -> 68.1%, and burns 9.33% of terminal wealth (**measured**).
10. **"Losing the high-water mark is larger than the effect the brake was credited with."** Refuted on the
    arithmetic: `never_saved` *is* the no-brake baseline, so state loss is bounded **at** the brake's whole
    effect (+31.2pp), never above it; and the +30.0pp annual-reset arm is unmatched on return (12.03% vs
    10.10% median CAGR), so part of it is simply more leverage. The operational warning survives intact;
    the inequality does not.
11. **"A single badly-timed reset is harmless."** Refuted — the "adversarial" arm placed the reset at the
    path's deepest bar, where maxDD is already realised and the metric cannot move (its realised ΔmaxDD
    printed **+0.00pp**). Moved into the decline it costs +15.0 to +18.8pp of tail, z +3.6 to +4.0
    (**measured**).
12. **"The brake's edge shrinks as the bootstrap preserves more serial dependence."** Refuted at the
    thresholds where the comparator has headroom: re-scoring the same 500 paths at −45%/−50% gives gaps that
    **grow** from block 21 to block 126 (+60.8 -> +60.4 and +54.1 -> +74.0), and on log-odds the block trend
    is monotone *increasing*. The −40% pp decline is a ceiling effect.
13. **"The advantage is entirely a Depression artefact."** Refuted, decisively and repeatedly: the
    Depression-only leg is the **weakest** (+8.00pp), the Depression-free legs are the strongest (+75.74pp /
    +43.49pp), and sorting paths by 1929–32 content gives a **larger** gap where the crash is scarcer
    (**measured**).
14. **"The paths are correlated, which inflates the z."** Refuted. Lag-1 autocorrelation +0.0256,
    batch-means ESS 4002 of 4000 (**measured**). The z is inflated by *path count*, not dependence — which,
    as one verifier put it, removes the only mechanism that would have capped it.
15. **"The pre-registered −40% threshold understates the brake at every unsaturated threshold."** Refuted
    by its own table: the gap is smaller at −70% (+16.0pp) and −80% (+3.6pp) than at −40% (+25.3pp), because
    the *brake* is then pinned at the floor. The gap is single-peaked in threshold, not monotone.
16. **"A6 reproduces exactly, therefore nothing was corrupted."** Dead as an inference — a deterministic
    engine reproduces a wrong number as faithfully as a right one. Reproducibility is provenance, never
    validity. Every replication claim in this file is scoped accordingly.
17. **"The scoring code is not the problem, proved by independent re-implementation."** Weakened, not dead:
    re-implementation tests transcription, not specification. Two encodings of one spec agreeing says
    nothing about whether the spec is sound — and the spec's `sd/sqrt(n)` SE is exactly what failed above.
    (Replicate-c's scorer needed `rng_seed=3` to match digit for digit; 3 is the seed hardcoded in
    `score_block`, so its independence is of the formula, not of every constant.)

---

## 6. RECOMMENDATION

**HOLD. Do not propose. Do not kill outright. Record A6 as REPLICATED BUT NOT ESTABLISHED, and close the
FF 1926–2005 direction.**

**Why not propose.** It fails register rule 2 on the comparison that decides whether the brake is a
mechanism rather than just less leverage: paired dSharpe vs plain de-levering is **+0.0181 at 0.40
bootstrap SD, P(d <= 0) = 0.344** (**measured**). The published z +8.88 is `sd/sqrt(500)` and triples with
CPU. On top of that: the pre-registered rule **FAILS** under an era-realistic cost schedule (+3.3pp, z
+1.75, n=90, **measured**); the live ETF book measures **+2.05pp, z +0.40** at 9.5 bps and **−8.33pp, z
−2.86** at 19.0 bps (**measured**, in-sample, direction only); the headline +25.3pp is read against a
frontier saturated at 99.4–100.0% and the honest unsaturated number is 11.2pp; and the whole thing is
priced off **3 distinct −40% drawdown episodes in 79 years**, two of them clearing the threshold by 0.03pp
and 1.8pp. Layer on A6-PRE: the base strategy has no selection edge in this data (dSharpe +0.0313, SE
0.0862, z +0.36; **negative at zero cost** post-1975). A risk control *can* be real on a zero-alpha base —
that is not automatically fatal, and I am not treating it as such — but it means nothing here has been
shown to improve a book that makes money.

**Why not kill.** Three measured facts survived every attack aimed at them, and a kill would be
overclaiming in the other direction. (i) The replication is clean at the row level, so the numbers are
attributable to a known engine and the race concern is retired for A6. (ii) The effect is **not** a
1929–32 artefact — the Depression-only window is the weakest leg (+8.00pp) and the Depression-free windows
the strongest (+75.74pp, +43.49pp), which is the opposite of what the artefact hypothesis predicts. (iii)
It reproduces on a genuinely independent, never-fitted, **unsaturated** third cross-section: FF Developed
ex-US 25 Size/BM 1990–2005, baseline tail 39.5%, gap **+45.82pp, z +8.68**, positive at all four block
lengths and at 19.0 bps. That is the only new positive evidence this round produced, and it is post-hoc,
n=200, with the brake's own tail pinned at 0.0% — so it cannot found a proposal, but it is too clean to
discard.

**Explicitly not recommended, and not mine to recommend: applying this to the live config.** That is
Nicholas's decision, and it is not currently available in any case — `trader/rotation.py` contains zero
occurrences of `dd_brake` and zero of high-water-mark state (**measured**), so the live loop cannot compute
the quantity the brake reads. `trader/risk_gate.py` persists a `peak_equity` for a −45% halt, which is a
different threshold and a different object, and its `_load_state()` returns `{}` on any
`OSError`/`ValueError` while `_save_state()` swallows `OSError` with a bare `pass` and writes
non-atomically. On that storage a fault reads **dd = 0.0**, indistinguishable from an all-time high, and
the control silently disappears when the system is unhealthy. The measured price of that is the whole
effect: an annual state loss returns **+30.0pp of the +31.2pp** the brake buys (z +5.82), and a reset
placed in a decline rather than at the trough costs +15.0 to +18.8pp (z +3.6 to +4.0). The brake also
binds on **58.9%** of realised FF decision bars — it is a near-permanent de-lever there, not an emergency
control — while on the live ETF panel the −20% tier is reached on **2.0%** of bars and the −40% threshold
on **0.0%**, so the thing tested and the thing that would ship are different mechanisms.

**What would settle it, if anyone wants to reopen the direction.** One cheap, pre-registered test, on the
instrument that is already known to be unsaturated: re-run the Developed ex-US 25 panel (and a second
non-US panel with enough breadth to be powered, which Europe 6 at 6 assets is not) at the full n = 500,
with **two changes fixed in advance** — (1) rule 2's SE is the **bootstrap SD** of the paired difference,
not `sd/sqrt(n_paths)`, and (2) the comparator is **plain de-levering at matched median MC CAGR**, not the
levered baseline. Add the era-realistic cost schedule at the pre-registered path count rather than n=90. If
the brake clears 1 bootstrap SD against plain de-levering on an unsaturated panel at 2x cost, that is a
real finding and worth the operational argument. If it does not, the direction closes for good. Until
then, A6 is a reproducible number measured on a saturated metric, on a base with no demonstrated edge, in
a drawdown regime the live book has never entered, requiring persistent state the live book does not have.

---

### Provenance of this document

Written from the three replications (`wf…-1`, `wf…-2`, `wf…-3`), five attack tracks (`wf…-4` path count,
`wf…-5` era cost, `wf…-6` subwindows/episodes, `wf…-7` base validity/cross-sections, `wf…-8`
implementability), and 48 adversarial verifications. Numbers I re-measured directly for this write-up: the
pin and branch; the committed `a6_dd_brake.json` headline cells; replicate-a's and replicate-b's result
files; replicate-c's **completed** 20,016-row path file, its cell census, its re-score through its own
independent scorer, and a full 20,000-row diff against the committed artifact; both SE conventions on the
paired dSharpe and the paired tail metric; the Developed ex-US 25, Europe 6 and live-ETF Monte Carlo score
files; the era-cost score block and its 90-path count; the episode census; the state-loss arms; the ops
live-computability arms; the realised live-ETF rows; and the absence of `dd_brake`/HWM state in
`trader/rotation.py`.

**Corrections to the verification record, found while writing this.** (1) Replicate-c's run was reported as
partial (9,408 of 20,000 rows, blocks 63 and 252 empty). It has since **completed**: 20,016 rows, all five
cells, 500 seeds each, `ALL PANELS DONE in 11701s`. Its block-21 cell now reads **+40.3pp, z +13.35** on
500 paths, not the +28.5pp / z +2.23 that a 32-path fragment showed. Every "coverage" objection raised
against replicate-c is therefore now moot, and it is the **strongest** of the three replications:
independently generated paths scored by an independently written scorer. (2) Europe 6 was reported as
failing to replicate. It is **positive at all four blocks** (+10.30 / +14.89 / +8.33 / +24.69 pp) and
merely underpowered at n = 24; "underpowered" and "does not replicate" are different claims and the
register should carry the former.
