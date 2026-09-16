"""What if PUSH-20 listened to POLITICAL news for the most recent year?

Uses REAL dated headlines from Alpaca's historical news archive (not a proxy):
  1. Pull ~18 months of market news headlines.
  2. Keep the POLITICAL ones (tariffs, elections, Fed, war, sanctions, ...).
  3. Score each with the project's sentiment lexicon -> daily political mood.
  4. Replay PUSH-20 with a gate: bad political mood -> cut leverage.
  5. Compare vs baseline over the same dates.

Honesty: ~1 year of live-era data is an anecdote, not proof either way.

Run: .venv/bin/python reports/political_news_test.py
"""
from __future__ import annotations
import os, sys, json, math, time, warnings
from urllib.request import Request, urlopen
from urllib.parse import quote
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from opt_harness import load_data, simulate, FETCH_END
from trader.config import load_config

CFG = load_config()
NEWS_START = "2025-01-01"

POLITICAL_KW = ["tariff", "election", "president", "congress", "senate",
                "white house", "fed ", "federal reserve", "powell", "war",
                "sanction", "shutdown", "geopolit", "china", "trade war",
                "treasury", "government", "regulat", "nato", "ukraine",
                "israel", "opec", "debt ceiling", "tax", "policy"]
POS = {"rally","gains","rises","surges","optimism","growth","recovery",
       "bullish","strong","beat","cuts rates","deal","agreement","truce","resolve"}
NEG = {"crash","plunge","fears","recession","inflation","hike","tariff","war",
       "default","crisis","selloff","volatile","decline","warning","risk",
       "concern","slowdown","sanctions","shutdown","escalat","threat"}


def fetch_news():
    arts, token, pages = [], None, 0
    while pages < 150:
        url = (f"https://data.alpaca.markets/v1beta1/news?limit=50&sort=asc"
               f"&start={NEWS_START}T00:00:00Z&symbols=SPY,QQQ,XLK,XLE,XLF")
        if token:
            url += f"&page_token={quote(token)}"
        req = Request(url, headers={"APCA-API-KEY-ID": CFG.alpaca_key,
                                    "APCA-API-SECRET-KEY": CFG.alpaca_secret})
        try:
            with urlopen(req, timeout=15) as r:
                d = json.loads(r.read())
        except Exception as e:
            print(f"  news fetch stopped: {e}")
            break
        batch = d.get("news", [])
        arts += [(a.get("created_at", "")[:10], a.get("headline", "")) for a in batch]
        token = d.get("next_page_token")
        pages += 1
        if not token or not batch:
            break
        time.sleep(0.12)
    return arts


def headline_score(h: str) -> int:
    words = set(h.lower().replace(",", " ").split())
    hl = h.lower()
    s = len(words & POS) - len(words & NEG)
    # multiword negatives the set-split misses
    for kw in ("trade war", "rate hike", "debt ceiling"):
        if kw in hl:
            s -= 1
    return s


print("Fetching Alpaca historical news archive (this is REAL dated news)...")
arts = fetch_news()
print(f"  {len(arts)} headlines since {NEWS_START}")
pol = [(d, h) for d, h in arts if any(k in h.lower() for k in POLITICAL_KW)]
print(f"  {len(pol)} POLITICAL headlines ({len(pol)/max(len(arts),1):.0%})")

# daily political mood, 5-day smoothed
by_day = {}
for d, h in pol:
    by_day.setdefault(d, []).append(headline_score(h))
days = sorted(by_day)
mood = pd.Series({d: sum(v)/len(v) for d, v in by_day.items()})
mood.index = pd.to_datetime(mood.index)
mood = mood.resample("D").mean().rolling(5, min_periods=1).mean()

# worst political-news days (for the report)
daily_raw = pd.Series({d: sum(v)/len(v) for d, v in by_day.items()}).sort_values()
worst = daily_raw.head(5)

df = load_data()
SIM_START = "2023-06-01"          # warmup burns ~1y; both runs identical pre-gate
P = {"vol_cap_bull": 1.5, "vol_target": 0.22, "max_weight": 0.50}

def gate_from(threshold, cap):
    return {d.strftime("%Y-%m-%d"): cap for d, v in mood.items()
            if not math.isnan(v) and v <= threshold}

VARIANTS = {
    "baseline (ignores politics)":              None,
    "bad politics (<=-0.2) -> no leverage":     gate_from(-0.2, 1.0),
    "bad politics (<=-0.1) -> no leverage":     gate_from(-0.1, 1.0),
    "bad politics (<=-0.2) -> half exposure":   gate_from(-0.2, 0.5),
    "any negative day (<0) -> no leverage":     gate_from(-1e-9, 1.0),
}

def window_stats(curve, start):
    s = pd.Series([v for _, v in curve], index=pd.to_datetime([d for d, _ in curve]))
    s = s[s.index >= start]
    ret = s.iloc[-1]/s.iloc[0]-1
    mdd = float(((s-s.cummax())/s.cummax()).min())
    return ret, mdd

print()
print("="*96)
print(f"POLITICAL-NEWS REPLAY — PUSH-20, evaluated {NEWS_START} -> {FETCH_END} (real headlines)")
print("="*96)
print(f"\n  {'variant':<42} {'return':>8} {'maxDD':>8} {'gated days':>11}")
print("  " + "-"*72)
results = {}
for name, gate in VARIANTS.items():
    cfg = dict(P)
    if gate is not None:
        cfg["ext_gate"] = gate
    r = simulate(df, cfg, SIM_START, FETCH_END)
    ret, mdd = window_stats(r["curve"], NEWS_START)
    n = len(gate) if gate else 0
    print(f"  {name:<42} {ret:>+8.1%} {mdd:>+8.1%} {n:>11}")
    results[name] = {"return": ret, "mdd": mdd, "gated_days": n}

print(f"\n  Worst political-news days (lexicon score, sample headline):")
for d, v in worst.items():
    sample = next(h for dd, h in pol if dd == d)
    print(f"    {d}  score {v:+.2f}  \"{sample[:80]}\"")

with open(os.path.join(os.path.dirname(__file__), "political_news_results.json"), "w") as fh:
    json.dump({"results": results, "n_headlines": len(arts), "n_political": len(pol)},
              fh, indent=1)
print("\n  Saved -> reports/political_news_results.json")
