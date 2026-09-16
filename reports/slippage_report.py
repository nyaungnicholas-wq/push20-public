"""Summarize the live slippage log (data/slippage_log.jsonl).

Shows realized fill slippage vs the decision-time price — the live-vs-backtest
reality check. The backtest assumes 5bps/side; this tells you the true number.

Run: .venv/bin/python reports/slippage_report.py
"""
import os, json, statistics as st

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "slippage_log.jsonl")

if not os.path.exists(PATH):
    print("No slippage log yet. It populates after the first live MOC fills "
          "are reconciled on the next scheduled run.")
    raise SystemExit(0)

rows = [json.loads(l) for l in open(PATH) if l.strip()]
if not rows:
    print("Slippage log is empty."); raise SystemExit(0)

slips = [r["slippage_bps"] for r in rows]
buys = [r["slippage_bps"] for r in rows if r["side"] == "buy"]
sells = [r["slippage_bps"] for r in rows if r["side"] == "sell"]

print("="*60)
print(f"LIVE SLIPPAGE REPORT  ({len(rows)} fills)")
print("="*60)
print(f"  Avg slippage     : {st.mean(slips):+.1f} bps  (backtest assumes 5.0)")
if len(slips) > 1:
    print(f"  Median / stdev   : {st.median(slips):+.1f} / {st.pstdev(slips):.1f} bps")
print(f"  Worst fill       : {max(slips):+.1f} bps")
print(f"  Best fill        : {min(slips):+.1f} bps")
if buys:  print(f"  Buys  (n={len(buys):<3}) avg : {st.mean(buys):+.1f} bps")
if sells: print(f"  Sells (n={len(sells):<3}) avg : {st.mean(sells):+.1f} bps")
print("\n  Recent fills:")
for r in rows[-8:]:
    print(f"    {r['submit_date']} {r['side']:<4} {r['qty']:>5} {r['symbol']:<5} "
          f"exp ${r['expected']:.2f} → fill ${r['fill']:.2f}  ({r['slippage_bps']:+.1f}bps)")
print("\n  Interpretation: if avg slippage >> 5bps, the backtest is too optimistic "
      "and live edge is thinner than modeled. MOC fills should keep this small.")
