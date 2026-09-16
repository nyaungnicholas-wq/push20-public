"""Market-wide macro news monitor.

Pulls financial news from up to 4 sources (RSS, NewsAPI, Alpaca, FMP),
scores market-wide sentiment via Claude haiku (or lexicon fallback), and
returns a single macro_regime_score in [-1.0, +1.0].

If macro_regime_score < config.macro_freeze_threshold (-0.3 default),
the engine freezes ALL new buy signals for that cycle — no individual
ticker analysis can override this.

This is the "latest financial news" layer requested: macro events like
Fed decisions, CPI prints, recession fears, or geopolitical shocks will
show up here and block entries before they cause damage.
"""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

_RSS_FEEDS = [
    ("Yahoo Finance",   "https://finance.yahoo.com/rss/topstories"),
    ("MarketWatch",     "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Reuters",         "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC",            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
]

_MACRO_KEYWORDS = [
    "fed", "federal reserve", "interest rate", "rate hike", "rate cut",
    "cpi", "inflation", "jobs report", "unemployment", "gdp", "recession",
    "tariff", "trade war", "geopolitical", "ukraine", "china", "taiwan",
    "earnings season", "market crash", "bank failure", "debt ceiling",
    "fomc", "powell", "treasury", "yield curve", "inverted",
]

_MACRO_SYSTEM = """You are a macro financial analyst. Given recent top financial news headlines,
assess the overall market sentiment and return a JSON object with:
  "macro_score": float from -1.0 (extreme fear/bearish) to +1.0 (extreme optimism/bullish)
  "freeze_buys": boolean — true if conditions are severe enough to pause all new long entries
  "top_risk": string — the single biggest risk factor in one sentence
  "reasoning": string — two sentences max

Use these anchors:
  +1.0 : Fed cutting rates, strong jobs, earnings beats across the board
   0.0 : Mixed signals, neutral
  -0.3 : One significant negative catalyst (rate hike surprise, weak jobs)
  -0.6 : Multiple negative catalysts, elevated fear
  -1.0 : Systemic crisis (bank failure, sovereign default, war escalation)

Return ONLY valid JSON."""


@dataclass
class MacroRegime:
    score: float                        # [-1.0, +1.0]
    freeze_buys: bool                   # True = block all new longs
    top_risk: str
    reasoning: str
    headline_count: int
    sources_used: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def is_stale(self, max_age_seconds: int = 1800) -> bool:
        return (time.time() - self.timestamp) > max_age_seconds


_cached_regime: Optional[MacroRegime] = None


def _fetch_rss(url: str, max_items: int = 10) -> List[str]:
    """Pull headlines from an RSS feed. Returns [] on any failure."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:
            content = resp.read()
        root = ET.fromstring(content)
        titles = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
            if len(titles) >= max_items:
                break
        return titles
    except Exception:
        return []


def _fetch_newsapi(api_key: str, max_items: int = 10) -> List[str]:
    """Pull top business headlines from NewsAPI.org."""
    import json
    from urllib.parse import urlencode
    params = urlencode({
        "category": "business",
        "language": "en",
        "pageSize": max_items,
        "apiKey": api_key,
    })
    url = f"https://newsapi.org/v2/top-headlines?{params}"
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return [a["title"] for a in data.get("articles", []) if a.get("title")]
    except Exception:
        return []


def _fetch_alpaca_news(api_key: str, api_secret: str, max_items: int = 10) -> List[str]:
    """Pull latest news from Alpaca's news endpoint."""
    import json
    url = "https://data.alpaca.markets/v1beta1/news?limit=20&sort=desc"
    try:
        req = Request(url, headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        })
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return [n["headline"] for n in data.get("news", [])[:max_items] if n.get("headline")]
    except Exception:
        return []


def _fetch_fmp_news(api_key: str, max_items: int = 10) -> List[str]:
    """Pull latest general news from Financial Modeling Prep."""
    import json
    url = f"https://financialmodelingprep.com/api/v4/general_news?page=0&apikey={api_key}"
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return [n["title"] for n in data[:max_items] if n.get("title")]
    except Exception:
        return []


def _is_macro_relevant(headline: str) -> bool:
    """Filter to headlines that are macro-relevant (not just company-specific)."""
    hl = headline.lower()
    return any(kw in hl for kw in _MACRO_KEYWORDS)


def _score_with_claude(headlines: List[str], api_key: str) -> MacroRegime:
    import anthropic
    import json
    client = anthropic.Anthropic(api_key=api_key)
    text = "\n".join(f"- {h}" for h in headlines[:20])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=_MACRO_SYSTEM,
        messages=[{"role": "user", "content": f"Recent headlines:\n{text}"}]
    )
    try:
        data = json.loads(msg.content[0].text)
        score = float(max(-1.0, min(1.0, data.get("macro_score", 0.0))))
        return MacroRegime(
            score=score,
            freeze_buys=bool(data.get("freeze_buys", score < -0.3)),
            top_risk=data.get("top_risk", "Unknown"),
            reasoning=data.get("reasoning", ""),
            headline_count=len(headlines),
        )
    except Exception:
        return MacroRegime(0.0, False, "Parse error", "", len(headlines))


def _score_with_lexicon(headlines: List[str]) -> MacroRegime:
    """Simple lexicon fallback when no API key is available."""
    pos_words = {
        "rally", "gains", "rises", "surges", "optimism", "growth",
        "recovery", "bullish", "strong", "beat", "cuts rates",
    }
    neg_words = {
        "crash", "plunge", "fears", "recession", "inflation", "hike",
        "tariff", "war", "default", "crisis", "selloff", "volatile",
        "decline", "warning", "risk", "concern", "slowdown",
    }
    total, score = 0, 0
    for h in headlines:
        words = set(h.lower().split())
        score += len(words & pos_words) - len(words & neg_words)
        total += 1
    norm = max(-1.0, min(1.0, score / (total + 1))) if total else 0.0
    return MacroRegime(
        score=norm,
        freeze_buys=norm < -0.3,
        top_risk="Lexicon-based estimate" if norm < -0.3 else "None identified",
        reasoning="Lexicon fallback (no Claude API key).",
        headline_count=len(headlines),
    )


def get_macro_regime(
    anthropic_key: Optional[str] = None,
    newsapi_key: Optional[str] = None,
    alpaca_key: Optional[str] = None,
    alpaca_secret: Optional[str] = None,
    fmp_key: Optional[str] = None,
    max_cache_age: int = 1800,   # 30 minutes
    verbose: bool = False,
) -> MacroRegime:
    """Fetch and score current macro sentiment. Caches for max_cache_age seconds."""
    global _cached_regime
    if _cached_regime and not _cached_regime.is_stale(max_cache_age):
        return _cached_regime

    headlines: List[str] = []
    sources_used: List[str] = []

    # 1. RSS feeds (always free, no key)
    for name, url in _RSS_FEEDS:
        items = _fetch_rss(url)
        if items:
            headlines.extend(items)
            sources_used.append(name)
            if verbose:
                print(f"  [macro] {name}: {len(items)} headlines")

    # 2. NewsAPI.org
    key = newsapi_key or os.getenv("NEWSAPI_KEY")
    if key:
        items = _fetch_newsapi(key)
        if items:
            headlines.extend(items)
            sources_used.append("NewsAPI")
            if verbose:
                print(f"  [macro] NewsAPI: {len(items)} headlines")

    # 3. Alpaca
    ak = alpaca_key or os.getenv("ALPACA_KEY")
    ask = alpaca_secret or os.getenv("ALPACA_SECRET")
    if ak and ask:
        items = _fetch_alpaca_news(ak, ask)
        if items:
            headlines.extend(items)
            sources_used.append("Alpaca")
            if verbose:
                print(f"  [macro] Alpaca: {len(items)} headlines")

    # 4. FMP
    fk = fmp_key or os.getenv("FMP_KEY")
    if fk:
        items = _fetch_fmp_news(fk)
        if items:
            headlines.extend(items)
            sources_used.append("FMP")
            if verbose:
                print(f"  [macro] FMP: {len(items)} headlines")

    # Deduplicate and filter to macro-relevant
    seen = set()
    filtered = []
    for h in headlines:
        key_h = h.lower().strip()
        if key_h not in seen:
            seen.add(key_h)
            if _is_macro_relevant(h):
                filtered.append(h)

    if not filtered:
        # Fix #8: ALL sources failed or returned no macro-relevant headlines.
        # Default to NEUTRAL (score=0, freeze_buys=False) — never freeze on
        # absence of data. Only freeze on confirmed negative signal.
        if not sources_used:
            reasoning = "All news sources unreachable — defaulting to neutral (fail-safe)."
        else:
            reasoning = "No macro-relevant headlines found — neutral by default."
        regime = MacroRegime(0.0, False, "None identified", reasoning, 0, sources_used)
    else:
        claude_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
        try:
            regime = (_score_with_claude(filtered, claude_key)
                      if claude_key else _score_with_lexicon(filtered))
        except Exception:
            regime = _score_with_lexicon(filtered)
        regime.sources_used = sources_used

    _cached_regime = regime
    return regime
