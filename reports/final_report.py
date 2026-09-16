"""Cross-round analysis: read all_results.jsonl, rank every config evaluated by
several criteria, and pick the recommended champions. Writes upgrade_report.md.

Run:  .venv/bin/python reports/final_report.py
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ALL = os.path.join(HERE, "all_results.jsonl")
REPORT = os.path.join(HERE, "upgrade_report.md")

W = "2005-2024"
WF = "WF-test 2017-2024"


def load():
    rows = []
    seen = {}
    for line in open(ALL):
        r = json.loads(line)
        # de-dup by flag signature, keep the latest evaluation
        sig = json.dumps(r["flags"], sort_keys=True)
        seen[sig] = r
    return list(seen.values())


def beats_spy_all(r):
    return all(r["windows"][w]["cagr"] > r["windows"][w]["spy_cagr"]
               for w in r["windows"] if not w.startswith("WF-train"))


def main():
    rows = load()
    spy20 = rows[0]["windows"][W]["spy_cagr"] if rows else 0.0
    # Only configs that beat SPY on every real window are eligible.
    elig = [r for r in rows if beats_spy_all(r)]

    by_obj = sorted(elig, key=lambda r: -r["windows"][W]["obj"])
    by_sharpe = sorted(elig, key=lambda r: -r["windows"][W]["sharpe"])
    by_calmar = sorted(elig, key=lambda r: r["windows"][W]["cagr"] / abs(r["windows"][W]["mdd"]) if r["windows"][W]["mdd"] else 0, reverse=True)
    by_minmdd = sorted(elig, key=lambda r: abs(r["windows"][W]["mdd"]))
    by_oos = sorted(elig, key=lambda r: -r["windows"][WF]["cagr"])

    def line(r):
        w, wf = r["windows"][W], r["windows"][WF]
        return (f"`{r['name']}` — CAGR {w['cagr']:+.1%}, MDD {w['mdd']:.1%}, "
                f"Sharpe {w['sharpe']:.2f}, obj {w['obj']:.3f}, OOS(2017-24) {wf['cagr']:+.1%}\n"
                f"    flags: `{r['flags']}`")

    out = []
    out.append("## Cross-round leaderboard\n")
    out.append(f"SPY 2005-2024 CAGR baseline: {spy20:+.1%}. "
               f"{len(elig)}/{len(rows)} evaluated configs beat SPY on every real window.\n")
    out.append("**By objective (return-tilted):**\n" + "\n".join(f"  {i+1}. {line(r)}" for i, r in enumerate(by_obj[:5])))
    out.append("\n**By Sharpe:**\n" + "\n".join(f"  {i+1}. {line(r)}" for i, r in enumerate(by_sharpe[:5])))
    out.append("\n**By Calmar (CAGR/|MDD|):**\n" + "\n".join(f"  {i+1}. {line(r)}" for i, r in enumerate(by_calmar[:5])))
    out.append("\n**By lowest drawdown (still beating SPY):**\n" + "\n".join(f"  {i+1}. {line(r)}" for i, r in enumerate(by_minmdd[:5])))
    out.append("\n**By out-of-sample 2017-2024 CAGR:**\n" + "\n".join(f"  {i+1}. {line(r)}" for i, r in enumerate(by_oos[:5])))

    print("\n".join(out))
    return out


if __name__ == "__main__":
    main()
