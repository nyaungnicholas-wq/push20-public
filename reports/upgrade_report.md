# Sector Rotation v2 — Full 20-Year Research Report

**Study window:** 2005-01-01 → 2024-12-31 (+ sub-windows 2005–14, 2015–24, 2018–24, and a 2005–16 train / 2017–24 test walk-forward)
**Method:** 4 rounds of hill-climbing (≈60 configs) with an anti-overfit robustness gate, then a 6-part stress battery.
**Benchmark:** SPY buy-and-hold, same window/data, 10bps/side slippage.

---

## TL;DR — the honest, nuanced verdict

A clear champion emerged that **beats SPY on every standard window with much higher Sharpe and lower drawdown**. Stress testing then revealed the raw-*return* edge is **fragile** (it leans on gold and on the pre-2010 era), so the final recommended champion uses a **GLD+TLT blend** that trades a little CAGR for a much more robust, lower-drawdown profile. The honest, defensible claim is **risk-adjusted** improvement — SPY-or-better returns at roughly half the drawdown — not reliable raw outperformance.

| 2005–2024 | Baseline | **Recommended: GLD+TLT blend** | Max-CAGR: GLD only | SPY |
|---|---|---|---|---|
| Final equity | $831k | **$940k** | $1.16M | $712k |
| CAGR | 11.2% | **11.9%** | 13.0% | 10.3% |
| Max drawdown | -30.5% | **-22.5%** | -31.1% | -55.2% |
| Sharpe | 0.68 | **0.83** | 0.82 | 0.61 |
| Calmar | — | **0.53** | 0.42 | — |

**Recommended champion = momentum-weighted top-3 sectors by 12-month momentum, monthly tranched rebalance, vol-targeted to ~10% annualized, parking in a 50/50 GLD+TLT sleeve when defensive.** It beats SPY on the 20-year window (+1.5pp) with **-22.5% max drawdown vs SPY's -55%** and Sharpe 0.83 vs 0.61, and holds ~-22% drawdown across *every* sub-window.

Run it: `python main.py rotation-v2`. Original `python main.py rotation` is unchanged ($274,744 / 15.6% over 2018–24, verified).

> **Update (v3 — see Appendix):** after diagnosing the plateau, a *new factor* (risk-parity `rp_blend` weighting) plus vol_target 0.12 became the **`rotation-v2` default**. It beats SPY on **all 4 windows AND out-of-sample** (vs 2/4 for the momentum blend below), at the cost of slightly more drawdown (-25.3% vs -22.5%). The momentum GLD+TLT blend described next is the **lower-drawdown variant** (`--scheme momentum --vol-target 0.10`). Both are valid; pick by whether you weight consistency or minimum drawdown.

---

## How we got here (4 optimization rounds)

**Round 1 (14 single-factor moves).** Found the core: momentum-weighted sizing + 12-month-only momentum + GLD-as-defensive. The robustness gate **rejected 4 of your 5 suggested upgrades** (trend filter, cluster filter, regime overlay, expanded universe) because each quietly broke a sub-period. Result: $1.37M / 14.0% / **-39% MDD** — high return but ugly drawdown.

**Round 2 (vol-targeting — the drawdown fix).** A volatility-target overlay (de-lever toward ~10% annualized vol, park the freed capital in GLD) **cut drawdown -39% → -31% and raised Sharpe 0.74 → 0.82**, giving up only ~1pp CAGR. Position caps, TLT/IEF defensive, and cluster/trend all reverted.

**Rounds 3–4 (fine-tuning + structural).** Everything from here was **noise** (objective 0.876 → 0.880). Dropping QQQ, dropping SMH, regime overlays, extra tranches, longer-lookback blends, gold-as-ranked-position — all reverted. The strategy had converged. (Notably, `lookback 210d` scored highest in-sample but the robustness gate rejected it for failing a sub-period — the gate doing its job.)

**Simple ≈ tuned** (13.0% vs 12.8%), so the recommended config is the SIMPLE one; the round-3/4 micro-tweaks are not worth the added complexity/overfit risk.

---

## The stress battery — where the honesty lives

### 🚩 1. The edge depends on GOLD, not the rotation mechanism
Swapping only the risk-off asset (2005–2024):

| Risk-off asset | CAGR | MDD | vs SPY |
|---|---|---|---|
| **GLD (gold)** | 13.0% | -31.1% | **+2.7pp WIN** |
| TLT (long treasuries) | 10.4% | -26.5% | +0.1pp tie |
| IEF (interm. treasuries) | 10.2% | -19.2% | -0.1pp LOSE |
| BIL (cash) | 9.1% | -18.9% | **-1.3pp LOSE** |

**With cash instead of gold, the strategy loses to SPY.** So most of the return edge is a bet that gold keeps being a great crisis hedge (as it was in 2008, 2011, 2020). That is *not* guaranteed going forward. Note the tradeoff: cash/IEF give far lower drawdown (-19%) but no return edge.

### 🚩 2. The CAGR edge is concentrated before 2010
Same champion, different start dates → 2024:

| Start | CAGR | MDD | vs SPY |
|---|---|---|---|
| 2005 | 13.0% | -31.1% | +2.7pp WIN |
| 2007 | 12.9% | -31.1% | +2.6pp WIN |
| 2008 | 12.5% | -31.1% | +1.8pp WIN |
| **2010** | 13.1% | **-22.5%** | **-0.5pp LOSE** |
| **2012** | 14.3% | **-22.5%** | **-0.3pp LOSE** |

**Post-2010, it does not beat SPY's return** — it *matches* it with roughly half the drawdown (-22.5%) and a much better Sharpe (0.87–0.95). The raw outperformance lives in the 2005–2009 GFC/commodity-boom window.

### ✅ 3. It survives realistic trading costs
Edge holds to ~30bps/side (+1.1pp) and only breaks even near ~45–50bps. Monthly trading in liquid ETFs costs far less than that, so cost is not a threat.

### ✅ 4. Parameters sit on a broad plateau (not overfit to a knife-edge)
vol_target 0.08–0.15 all win (+2.1 to +3.1pp); lookback 210–273d all win. Small parameter changes don't flip the result — a good robustness sign.

### ✅ 5. Wins every standard window
2005–24 +2.7pp, 2005–14 +3.7pp, 2015–24 +1.7pp, 2018–24 +2.7pp. Out-of-sample walk-forward (train 2005–16 → test 2017–24): **17.2% vs SPY 14.7%.**

---

## What this means (the real takeaway)

- **As a "beat SPY's return" strategy, the edge is fragile** — it depends on gold and on the pre-2010 regime. Be skeptical of the headline +2.7pp CAGR from the pure-GLD version.
- **As a "match SPY's return with materially lower drawdown and higher Sharpe" strategy, it is solid and robust** — exactly the risk-adjusted profile you said you valued over chasing raw CAGR.
- **The GLD+TLT blend (now the recommended champion) resolves the gold-dependence directly.** Tested after the stress battery, it cuts max drawdown to **-22.5% across every window**, keeps Sharpe at 0.83, still beats SPY over 20 years (+1.5pp), and halves the single-asset bet. It gives up ~1.1pp of 20-year CAGR vs pure GLD — a good trade for the robustness.

### Blend champion across all windows
| Window | Blend CAGR | Blend MDD | Sharpe | SPY CAGR | vs SPY |
|---|---|---|---|---|---|
| 2005–2024 | +11.9% | -22.5% | 0.83 | +10.3% | **+1.5pp** |
| 2005–2014 | +10.9% | -22.5% | 0.81 | +7.7% | **+3.3pp** |
| 2015–2024 | +12.9% | -22.2% | 0.86 | +13.1% | -0.2pp (tie, half the DD) |
| 2018–2024 | +13.6% | -22.0% | 0.84 | +13.7% | -0.0pp (tie, half the DD) |

Like pure GLD, the blend's raw-CAGR *edge* is concentrated in 2005–2014; in the modern era it **matches** SPY's return with roughly half the drawdown and a higher Sharpe. That is the honest profile.

---

## Recommended next steps
1. ✅ **Done — gold dependence reduced** via the 50/50 GLD+TLT sleeve (now the recommended champion): -22.5% MDD, Sharpe 0.83, still +1.5pp over SPY over 20yr, half the single-asset risk.
2. **Decide your goal honestly:** for steadier compounding use the blend (default) or even BIL/IEF defensive (`--defensive BIL` → ~-19% max DD, but no CAGR edge — you only get the lower-drawdown benefit). For max return you're implicitly betting on gold (`--defensive GLD`).
3. **Paper-trade live** via the existing Alpaca paper account before any real capital (per your standing rule — no autonomous live orders).
4. **Walk-forward re-fit** on a rolling basis to confirm the plateau holds out-of-sample each year — the scheduled 6 AM run is set up to do exactly this kind of deeper validation.

---

## Artifacts (`reports/`)
- `upgrade_report.md` — this report.
- `iteration_log.md` — full per-iteration journal, all 4 rounds (~60 configs), KEEP/REVERT reasoning.
- `all_results.jsonl` — every config evaluated with metrics across 6 windows (machine-readable).
- `champion_config.json` — champion flags + metrics + walk-forward.
- `stress_results.md` — the full robustness battery (cost / gold-dependence / start-date / parameter sensitivity).
- `research_harness.py`, `run_round.py`, `run_iterations.py`, `stress_test.py`, `final_report.py` — reusable, re-runnable tooling.
- `main.py rotation-v2` runs the champion; `main.py rotation` is the original, unchanged.

### Champion flags (recommended / deployable)
```python
rotation_momentum_weight  = True       # size ∝ 12-month momentum (floor 15%)
rotation_lookbacks        = (252,)     # 12-month momentum only
rotation_defensive_symbol = "GLD+TLT"  # 50/50 gold + long-treasury risk-off sleeve (robust)
rotation_vol_target       = 0.10       # de-lever toward 10% annualized vol; park the rest in the sleeve
rotation_vol_window       = 20         # realized-vol estimate window (days)
# top-3 sectors, monthly tranched rebalance (days 0/10/20), 13-ETF base universe, 10bps/side
# alternatives: defensive "GLD" (higher CAGR, more fragile) | "BIL" (lowest risk, no CAGR edge)
```

## Year-by-year — recommended blend champion vs SPY (2005-2024)

| Year | Blend | SPY | Diff | Winner |
|---|---|---|---|---|
| 2005 | +21.1% | +5.3% | +15.8% | BLEND |
| 2006 | +5.3% | +13.8% | -8.5% | SPY |
| 2007 | +12.7% | +5.3% | +7.4% | BLEND |
| 2008 | +3.5% | -36.2% | +39.8% | BLEND |
| 2009 | +6.5% | +22.7% | -16.2% | SPY |
| 2010 | +15.3% | +13.1% | +2.2% | BLEND |
| 2011 | +3.6% | +0.9% | +2.7% | BLEND |
| 2012 | +5.0% | +14.2% | -9.2% | SPY |
| 2013 | +21.4% | +29.0% | -7.6% | SPY |
| 2014 | +10.4% | +14.6% | -4.2% | SPY |
| 2015 | -2.9% | +1.3% | -4.1% | SPY |
| 2016 | +13.5% | +13.6% | -0.1% | SPY |
| 2017 | +21.3% | +20.8% | +0.6% | BLEND |
| 2018 | +1.6% | -5.2% | +6.9% | BLEND |
| 2019 | +15.6% | +31.1% | -15.5% | SPY |
| 2020 | +19.4% | +17.2% | +2.2% | BLEND |
| 2021 | +17.1% | +30.5% | -13.4% | SPY |
| 2022 | -0.1% | -18.6% | +18.6% | BLEND |
| 2023 | +18.2% | +26.7% | -8.6% | SPY |
| 2024 | +27.8% | +26.0% | +1.8% | BLEND |

Blend beats SPY in **10/20 years**. Crucially it wins the crisis years (2008, 2018, 2022) — the source of its lower drawdown — while giving some back in strong-tech bull years (2019, 2021, 2023).

---

## Appendix — Why it plateaued, and the multi-factor fix (v3)

**Why the plateau happened (root causes):**
1. **Single-factor tuning.** Every move was a re-arrangement of one signal — cross-sectional sector momentum. Monthly sector momentum has a documented Sharpe ceiling ~0.8; we hit it. Re-arranging a factor can't add information it doesn't contain.
2. **Greedy search → local optimum.** The hill-climb tested one delta at a time vs the champion and kept the best single change, so it never found combination-effects (a change bad alone but good paired with another).
3. **Flat objective ridge.** Once drawdown fell below SPY's, the excess-drawdown term went to zero and the surface flattened.
4. **One historical path.** Near the optimum, obj 0.876 vs 0.880 is within sampling noise — no real gradient left.

**The fixes (implemented):**
- **New factor:** risk-parity weighting (`rp_blend` = size ∝ √(momentum × 1/vol)) and risk-adjusted selection — uses the *risk structure*, genuinely orthogonal to return-momentum.
- **Global joint search + walk-forward** (`reports/joint_search.py`): sampled 110 full configs across the joint space, selected on TRAIN 2005–16, judged on untouched TEST 2017–24.

**What the fix proved:**
- The honest walk-forward pick did **not** beat the champion out-of-sample → **the single-factor plateau is real**, not a search artifact. (Configs that "beat" it only do so when you select on the test set — overfitting.)
- The **risk-parity factor is a small but real gain**: `rp_blend + GLD+TLT + 12mo + vol_target 0.12` now **beats SPY on all 4 windows AND out-of-sample** (15.8% vs 14.7% on 2017–24), vs 2/4 for the single-factor champion. It is the new `rotation-v2` default. **Tradeoff:** drawdown -25.3% vs -22.5%.

**The honest ceiling that remains:** the multi-factor fix improves *consistency*, but it does **not** remove the structural post-2010 raw-return lag or the dependence on the defensive asset (gold/treasuries). Those are properties of the *information set*, not the math. The only way past that ceiling is **fundamentally new information**, e.g.:
- Different/more asset classes (credit, FX, individual-stock momentum, factor ETFs) so momentum has more to rotate into.
- An orthogonal signal family (valuation/carry, breadth, macro/rates regime, earnings revisions).
- Different instruments (long/short to harvest the bottom of the cross-section; modest leverage on the risk-targeted sleeve).
Each of those adds information; another weighting tweak does not.

### v3 champion flags
```python
rotation_momentum_weight  = True
rotation_weight_scheme    = "rp_blend"   # NEW factor: size ∝ √(momentum × 1/vol)
rotation_lookbacks        = (252,)
rotation_defensive_symbol = "GLD+TLT"
rotation_vol_target       = 0.12
rotation_vol_window       = 20
# Run: python main.py rotation-v2   (flags: --scheme, --defensive, --vol-target)
```
