"""Regenerate the overfitting statistics from source.

The repo has published PBO 0.425, Deflated Sharpe 0.999 and Minimum Backtest
Length 16.1 years since 2026-08-03, but only the JSON outputs were ever
committed - the script that produced them was not. A headline statistic nobody
can rerun is an assertion, not a measurement. This is the missing step.

The two numbers answer different questions and routinely disagree, which is
exactly why both are reported:

  PBO   "can these configurations be told apart out of sample?"
        Combinatorially Symmetric Cross-Validation (Bailey, Borwein, Lopez de
        Prado, Zhu 2014). Split the sample into S blocks, take every way of
        choosing S/2 of them as in-sample, pick the in-sample winner, and see
        where it lands among the others out of sample. If the winner is a coin
        flip against the field, the specific settings carry no information even
        when the strategy family does.

  DSR   "does the family have edge once the search is paid for?"
        Deflated Sharpe (Bailey & Lopez de Prado 2014). Deflate the observed
        Sharpe by the Sharpe you would expect the best of N random trials to
        reach by luck alone, then ask whether what remains is still positive.

PUSH-20 scores high on the second and poorly on the first. That is coherent:
sector momentum with a volatility overlay works, and which exact knob settings
you ship is close to arbitrary.

Usage
  python reports/overfitting_audit.py              full 135-config run
  python reports/overfitting_audit.py --quick      6-config smoke run
  python reports/overfitting_audit.py __selfcheck  offline, writes nothing
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
import warnings
from typing import Dict, List, Sequence, Tuple

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "reports"))

import numpy as np

START, END = "2006-01-01", "2026-09-02"
ANN = math.sqrt(252.0)

# The grid the published audit searched. Recovered from the labels stored in
# reports/overfitting_audit_pbo.json, which is the only surviving record of it.
VOL_WINDOW = [6, 8, 10, 12, 16]
POSITION_CAP = [0.50, 0.60, 0.70]
VOL_CAP_BULL = [1.25, 1.50, 1.75]
TOP_N = [2, 3, 4]

# Documented search effort, from reports/overfitting_audit_dsr.json.
TRIAL_COUNTS = {"this grid": 135, "risk_dial_sweep": 1260, "all documented": 1386}

S_BLOCKS = 16          # CSCV partitions; C(16,8) = 12870 splits


def _label(vw: int, pc: float, vc: float, tn: int) -> str:
    return f"vw{vw}_pc{pc:.2f}_vc{vc:.2f}_tn{tn}"


def build_grid(base: Dict, quick: bool = False) -> Dict[str, Dict]:
    """label -> config, overriding only the four searched axes."""
    vws = [8, 12] if quick else VOL_WINDOW
    tns = [3] if quick else TOP_N
    pcs = [0.60] if quick else POSITION_CAP
    vcs = [1.25, 1.50] if quick else VOL_CAP_BULL
    grid: Dict[str, Dict] = {}
    for vw, pc, vc, tn in itertools.product(vws, pcs, vcs, tns):
        cfg = dict(base)
        cfg.update(vol_window=vw, max_weight=pc, vol_cap_bull=vc, top_n=tn)
        grid[_label(vw, pc, vc, tn)] = cfg
    return grid


def run_grid(grid: Dict[str, Dict]) -> Tuple[Dict[str, np.ndarray], int]:
    """Simulate every config once. Returns label -> per-observation returns."""
    import opt_harness as H
    H.FETCH_END = "2026-09-02"
    df = H.load_data()

    series: Dict[str, np.ndarray] = {}
    skipped = 0
    for i, (label, cfg) in enumerate(grid.items(), 1):
        print(f"[{i}/{len(grid)}] {label}", file=sys.stderr, flush=True)
        curve = H.simulate(df, cfg, START, END)["curve"]
        eq = np.asarray([v for _, v in curve], dtype=float)
        eq = eq[np.isfinite(eq) & (eq > 0)]
        if len(eq) < 101:
            skipped += 1
            continue
        r = np.diff(eq) / eq[:-1]
        r = r[np.isfinite(r)]
        if len(r) < 100:
            skipped += 1
            continue
        series[label] = r
    return series, skipped


def _align(series: Dict[str, np.ndarray]) -> Tuple[List[str], np.ndarray]:
    """Truncate every series to the shortest, keeping the LAST n observations.

    Ranking configurations against each other is only meaningful on a shared
    window; comparing a 5000-observation config against a 4800-observation one
    silently compares two different periods.
    """
    labels = sorted(series)
    n = min(len(series[k]) for k in labels)
    return labels, np.vstack([series[k][-n:] for k in labels])


def _sharpe_from_moments(n: np.ndarray, s1: np.ndarray, s2: np.ndarray) -> np.ndarray:
    """Annualised Sharpe from per-block sums, so a split costs no re-scan.

    var uses the ddof=1 form written out in moments:
        var = (sum(x^2) - n*mean^2) / (n - 1)
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = s1 / n
        var = (s2 - n * mean * mean) / (n - 1.0)
        sd = np.sqrt(np.maximum(var, 0.0))
        out = np.where(sd > 0, mean / sd * ANN, -np.inf)
    return out


def cscv_pbo(mat: np.ndarray, s_blocks: int = S_BLOCKS) -> Dict:
    """Probability of Backtest Overfitting via CSCV.

    `mat` is (n_configs, n_obs). Returns pbo, the logit series, and the split
    count. Rank convention: 1 = worst out-of-sample, N = best, so a relative
    rank above the median gives a positive logit and a GOOD outcome.
    """
    n_cfg, n_obs = mat.shape
    if n_cfg < 2:
        raise ValueError("CSCV needs at least 2 configurations")
    if s_blocks % 2:
        raise ValueError("s_blocks must be even")

    per = n_obs // s_blocks
    if per < 2:
        raise ValueError("not enough observations for this many blocks")
    # Drop the remainder from the FRONT so blocks divide evenly and the most
    # recent observations are the ones kept.
    trimmed = mat[:, n_obs - per * s_blocks:]
    blocks = trimmed.reshape(n_cfg, s_blocks, per)

    # Per-config, per-block moments. Every split is then a sum over 8 columns.
    b_n = np.full(s_blocks, float(per))
    b_s1 = blocks.sum(axis=2)                     # (n_cfg, s_blocks)
    b_s2 = (blocks ** 2).sum(axis=2)

    half = s_blocks // 2
    all_idx = set(range(s_blocks))
    lams: List[float] = []
    n_worse = 0
    splits = 0

    for combo in itertools.combinations(range(s_blocks), half):
        is_idx = list(combo)
        oos_idx = sorted(all_idx - set(combo))

        n_is = b_n[is_idx].sum()
        sr_is = _sharpe_from_moments(n_is, b_s1[:, is_idx].sum(1), b_s2[:, is_idx].sum(1))
        n_star = int(np.argmax(sr_is))

        n_oos = b_n[oos_idx].sum()
        sr_oos = _sharpe_from_moments(n_oos, b_s1[:, oos_idx].sum(1), b_s2[:, oos_idx].sum(1))

        # rank 1 = worst .. N = best. Ties share the lower rank, which is the
        # conservative direction (it cannot flatter the winner).
        rank = int((sr_oos < sr_oos[n_star]).sum()) + 1
        w = rank / (n_cfg + 1.0)
        lam = math.log(w / (1.0 - w))
        lams.append(lam)
        if lam <= 0.0:
            n_worse += 1
        splits += 1

    arr = np.asarray(lams, dtype=float)
    return {"pbo": n_worse / splits, "splits": splits,
            "lambda_mean": float(arr.mean()), "lambda_median": float(np.median(arr)),
            "all_finite": bool(np.isfinite(arr).all())}


def null_band(n_cfg: int, n_obs: int, reps: int = 24, seed: int = 1000) -> Dict:
    """What PBO looks like when the configurations are KNOWN to be alike.

    A PBO is worthless without this. Simulate `reps` independent pure-noise
    grids of the same shape, where the true PBO is 0.5 by construction, and
    report the spread of the estimate. At 135 configs over 5146 observations
    that spread is about +/- 0.12, so a reported 0.425 sits inside one standard
    deviation of the null and cannot on its own separate "these settings are
    indistinguishable" from "we lacked the resolution to tell".
    """
    vals = []
    for s in range(reps):
        rng = np.random.default_rng(seed + s)
        vals.append(cscv_pbo(rng.normal(0.0, 0.01, size=(n_cfg, n_obs)))["pbo"])
    arr = np.sort(np.asarray(vals, dtype=float))
    return {"reps": reps, "mean": float(arr.mean()), "sd": float(arr.std(ddof=1)),
            "p10": float(np.quantile(arr, 0.10)), "p90": float(np.quantile(arr, 0.90)),
            "min": float(arr[0]), "max": float(arr[-1])}


def _expected_max_z_local(n: int) -> float:
    from statistics import NormalDist
    if n < 2:
        return 0.0
    g, nd = 0.5772156649015329, NormalDist()
    return (1.0 - g) * nd.inv_cdf(1.0 - 1.0 / n) + g * nd.inv_cdf(1.0 - 1.0 / (n * math.e))


def dsr_table(champ_returns: np.ndarray, var_sharpe_per_obs: float,
              champ_sharpe_annual: float) -> List[Dict]:
    from trader.metrics import deflated_sharpe, min_backtest_length
    try:
        from trader.metrics import _expected_max_z as emz
    except ImportError:
        emz = _expected_max_z_local

    rets = [float(x) for x in champ_returns]
    rows = []
    for source, n in TRIAL_COUNTS.items():
        rows.append({
            "n_trials": n, "var_source": source,
            "sr_star_annual": math.sqrt(var_sharpe_per_obs) * emz(n) * ANN,
            "dsr": deflated_sharpe(rets, n, var_sharpe_per_obs),
            "min_backtest_years": min_backtest_length(n, champ_sharpe_annual),
            "min_backtest_years_at_sr1": min_backtest_length(n, 1.0),
        })
    return rows


def compare_to_stored(out: Dict) -> None:
    """Print stored vs rebuilt. Silence here would hide exactly the drift the
    rebuild exists to expose."""
    def _load(name):
        p = os.path.join(ROOT, "reports", name)
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    pbo_j, dsr_j = _load("overfitting_audit_pbo.json"), _load("overfitting_audit_dsr.json")
    if not pbo_j and not dsr_j:
        print("\nNo stored artifacts to compare against.")
        return

    print(f"\n{'':<32}{'stored':>14}{'rebuilt':>14}{'diff':>12}")
    print("-" * 72)

    def row(name, stored, rebuilt, fmt="{:.4f}"):
        if stored is None or rebuilt is None:
            print(f"{name:<32}{'-':>14}{'-':>14}{'-':>12}")
            return
        d = rebuilt - stored
        print(f"{name:<32}{fmt.format(stored):>14}{fmt.format(rebuilt):>14}{d:>+12.4f}")

    if pbo_j:
        row("PBO", pbo_j.get("pbo"), out["pbo"])
        row("champion Sharpe (annual)", pbo_j.get("champion_sharpe_annual"),
            out["champion_sharpe_annual"])
        row("var of Sharpe (per obs)", pbo_j.get("var_sharpe_per_obs"),
            out["var_sharpe_per_obs"], "{:.3e}")
        row("observations", pbo_j.get("n_obs"), out["n_obs"], "{:.0f}")
    if dsr_j:
        stored_1386 = next((r for r in dsr_j.get("rows", [])
                            if r.get("n_trials") == 1386), None)
        mine_1386 = next((r for r in out["dsr_rows"] if r["n_trials"] == 1386), None)
        if stored_1386 and mine_1386:
            row("DSR @ 1386 trials", stored_1386.get("dsr"), mine_1386["dsr"], "{:.6f}")
            row("MinBTL years @ 1386", stored_1386.get("min_backtest_years"),
                mine_1386["min_backtest_years"], "{:.2f}")

    print("\nThe stored artifacts were produced on the pre-2026-09-13 engine: 5bps")
    print("costs, SPY-volatility sizing, no VIX>=35 tier modelled and no defensive-")
    print("sleeve fidelity. This run uses the CURRENT live config. The figures are")
    print("EXPECTED to differ; the table above is how much, and nothing here")
    print("supersedes the stored audit as the record of what was published then.")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="6-config smoke run; NOT the published number")
    ap.add_argument("--out", default="overfitting_audit_rebuilt.json")
    ap.add_argument("--null-reps", type=int, default=24,
                    help="pure-noise datasets used to size the PBO error bar; 0 to skip")
    args = ap.parse_args(argv)

    from live_checkup import live_sim_config
    base, _ = live_sim_config()

    grid = build_grid(base, quick=args.quick)
    print(f"running {len(grid)} configurations over {START}..{END}", file=sys.stderr)
    series, skipped = run_grid(grid)
    if len(series) < 2:
        print("too few configurations produced a usable series", file=sys.stderr)
        return 1

    labels, mat = _align(series)
    n_obs = mat.shape[1]

    sr_annual = {}
    for i, lab in enumerate(labels):
        r = mat[i]
        sd = r.std(ddof=1)
        sr_annual[lab] = float(r.mean() / sd * ANN) if sd > 0 else float("-inf")

    # The champion is what is LIVE, not what scored best - that distinction is
    # the entire point of PBO.
    want = _label(int(base["vol_window"]), float(base["max_weight"]),
                  float(base["vol_cap_bull"]), int(base["top_n"]))
    if want in sr_annual:
        champ = want
    else:
        champ = max(sr_annual, key=sr_annual.get)
        print(f"\n!! live config {want} is not on the grid; falling back to the "
              f"best-scoring label {champ}. The PBO below then describes a "
              f"configuration you do not trade.", file=sys.stderr)

    best = max(sr_annual, key=sr_annual.get)
    sr_per_obs = [sr_annual[k] / ANN for k in labels]
    var_per_obs = float(np.var(sr_per_obs, ddof=1))

    pbo = cscv_pbo(mat)
    rows = dsr_table(mat[labels.index(champ)], var_per_obs, sr_annual[champ])
    null = None
    if args.null_reps > 0:
        print(f"sizing the PBO error bar over {args.null_reps} null datasets",
              file=sys.stderr)
        null = null_band(len(labels), n_obs, reps=args.null_reps)

    out = {
        "n_obs": n_obs, "n_configs": len(labels), "n_skipped": skipped,
        "years_of_data": n_obs / 252.0, "s_blocks": S_BLOCKS,
        "splits": pbo["splits"], "quick": bool(args.quick),
        "champion_label": champ, "champion_sharpe_annual": sr_annual[champ],
        "best_label": best, "best_sharpe_annual": sr_annual[best],
        "pbo": pbo["pbo"], "lambda_mean": pbo["lambda_mean"],
        "lambda_median": pbo["lambda_median"],
        "var_sharpe_per_obs": var_per_obs, "pbo_null_band": null,
        "dsr_rows": rows, "sharpe_annual_by_label": sr_annual,
    }

    path = os.path.join(ROOT, "reports", args.out)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    print(f"\nconfigs {len(labels)}   observations {n_obs}   "
          f"years {n_obs/252.0:.2f}   splits {pbo['splits']}")
    print(f"champion {champ}  Sharpe {sr_annual[champ]:.4f}")
    print(f"best     {best}  Sharpe {sr_annual[best]:.4f}")
    print(f"PBO      {pbo['pbo']:.4f}   (lambda median {pbo['lambda_median']:+.3f})")
    if null:
        print(f"         null band: mean {null['mean']:.3f}  sd {null['sd']:.3f}  "
              f"p10-p90 {null['p10']:.3f}-{null['p90']:.3f}  over {null['reps']} noise grids")
        z = (pbo["pbo"] - null["mean"]) / null["sd"] if null["sd"] > 0 else 0.0
        print(f"         the measured PBO is {abs(z):.2f} sd from the coin-flip null "
              f"-> {'INSIDE' if abs(z) < 2 else 'outside'} the noise band")
    for r in rows:
        print(f"  n={r['n_trials']:<5} DSR {r['dsr']:.6f}   "
              f"SR* {r['sr_star_annual']:.4f}   MinBTL {r['min_backtest_years']:.2f}y")
    if args.quick:
        print("\n--quick: a reduced grid. NOT the published number.")
    compare_to_stored(out)
    print(f"\nwrote reports/{args.out}")
    return 0


def selfcheck() -> int:
    """Offline. Validates the CSCV implementation against cases with a KNOWN
    answer, because a plausible-looking PBO is indistinguishable from a wrong
    one by inspection."""
    rng = np.random.default_rng(0)
    n_cfg, n_obs = 20, 2000

    # 1) One genuinely superior config. Its edge survives out of sample, so the
    #    in-sample winner should usually rank well: PBO below 0.5.
    mat = rng.normal(0.0, 0.01, size=(n_cfg, n_obs))
    mat[7] += 0.0015                      # ~0.15 sigma of real daily edge
    good = cscv_pbo(mat)
    assert 0.0 <= good["pbo"] <= 1.0, good["pbo"]
    assert good["all_finite"], "every lambda must be finite"
    assert good["pbo"] < 0.5, f"a real edge should not look overfit: {good['pbo']}"

    # 2) Pure noise. The true PBO is 0.5 by construction, but a SINGLE dataset
    #    estimates it terribly: the 12870 splits all reuse the same 16 blocks of
    #    the same one realization, so the effective sample is the blocks, not
    #    the splits. Measured spread under the null at this size is sd ~0.19,
    #    with draws seen from 0.19 to 0.87. Asserting on one draw is how the
    #    first version of this check failed on an unlucky seed and got blamed
    #    on the implementation. Average across independent datasets instead.
    draws = [cscv_pbo(np.random.default_rng(100 + s).normal(0.0, 0.01,
                                                            size=(n_cfg, n_obs)))["pbo"]
             for s in range(8)]
    mean_null = sum(draws) / len(draws)
    assert 0.35 <= mean_null <= 0.65, (
        f"pure noise must average to a coin flip: {mean_null:.3f} from {draws}"
    )

    assert good["splits"] == math.comb(S_BLOCKS, S_BLOCKS // 2) == 12870, good["splits"]

    print(f"SELFCHECK OK overfitting_audit splits={good['splits']} checks=5")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "__selfcheck":
        raise SystemExit(selfcheck())
    raise SystemExit(main())
