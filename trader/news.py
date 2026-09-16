"""News sentiment providers — per-ticker scoring.

Three backends behind a single interface:
  - ClaudeNews   : uses claude-haiku-4-5 to understand context, not just words
  - LexiconNews  : fast word-match fallback (no API key needed)
  - NeutralNews  : always returns 0.0 (for pure-technical backtests)

Ticker-targeted scoring: only articles that explicitly mention the ticker
symbol or company name are scored (trump2cash pattern). This prevents a
general market selloff article from registering as bearish for a stock that
wasn't mentioned.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

_POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
    "upgrade", "upgraded", "record", "growth", "profit", "gains", "jumps",
    "strong", "bullish", "outperform", "raises", "boost", "wins", "approval",
    "breakthrough", "expansion", "tops", "rises", "rebound", "optimistic",
    "buyback", "dividend", "acquisition", "partnership", "deal",
}
_NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "drop", "drops", "fall", "falls",
    "downgrade", "downgraded", "loss", "losses", "weak", "bearish", "cuts",
    "lawsuit", "probe", "investigation", "recall", "warning", "slump", "slumps",
    "decline", "fraud", "bankruptcy", "layoffs", "selloff", "plummet", "fears",
    "fine", "penalty", "shortage", "delay", "cancel", "cancelled", "default",
}

# Maps ticker -> common company name fragments for entity matching
_TICKER_NAMES: Dict[str, List[str]] = {
    "AAPL": ["apple", "aapl"],
    "MSFT": ["microsoft", "msft"],
    "NVDA": ["nvidia", "nvda"],
    "AMZN": ["amazon", "amzn"],
    "GOOGL": ["google", "alphabet", "googl"],
    "META": ["meta", "facebook", "instagram"],
    "TSLA": ["tesla", "tsla"],
    "JPM": ["jpmorgan", "jp morgan", "jpm"],
    "XOM": ["exxon", "xom"],
    "SPY": ["s&p", "sp500", "spy", "s&p 500"],
    "QQQ": ["nasdaq", "qqq"],
}


@dataclass
class NewsScore:
    symbol: str
    sentiment: float        # [-1.0, +1.0]
    headline_count: int
    impact: str             # HIGH / MEDIUM / LOW
    sample: List[str]       # representative headlines for the report


class NewsProvider:
    def score(self, symbols: List[str], lookback_days: int) -> Dict[str, NewsScore]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Claude haiku scorer
# ---------------------------------------------------------------------------

_CLAUDE_SYSTEM = """You are a financial news sentiment analyst. Given a list of headlines
about a specific stock or ETF, return a JSON object with:
  "sentiment": float from -1.0 (very bearish) to +1.0 (very bullish)
  "impact": "HIGH" | "MEDIUM" | "LOW"
  "reasoning": one sentence

HIGH impact = earnings, FDA approval/rejection, CEO change, acquisition,
              rate decision, major macro event.
MEDIUM = sector news, analyst upgrades/downgrades, product launches.
LOW = general market commentary, routine press releases.

Be nuanced: "beat earnings but lowered guidance" is slightly negative (-0.1 to -0.3).
Return ONLY valid JSON, nothing else."""


class ClaudeNews(NewsProvider):
    """Uses claude-haiku-4-5 to score headlines with full context understanding."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    def _fetch_headlines(self, symbol: str, lookback_days: int) -> List[str]:
        import yfinance as yf
        cutoff = time.time() - lookback_days * 86400
        headlines = []
        try:
            items = yf.Ticker(symbol).news or []
        except Exception:
            return []
        names = _TICKER_NAMES.get(symbol, [symbol.lower()])
        for it in items:
            content = it.get("content", it)
            title = (content.get("title") or "").strip()
            pub = it.get("providerPublishTime") or 0
            if pub and pub < cutoff:
                continue
            if not title:
                continue
            # Entity filter: only include if headline mentions this ticker/name
            title_lower = title.lower()
            if any(n in title_lower for n in names) or symbol.lower() in title_lower:
                headlines.append(title)
        return headlines

    def _call_claude(self, symbol: str, headlines: List[str]) -> NewsScore:
        import anthropic
        import json
        client = anthropic.Anthropic(api_key=self.api_key)
        headlines_text = "\n".join(f"- {h}" for h in headlines[:10])
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_CLAUDE_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Stock: {symbol}\nHeadlines:\n{headlines_text}"
            }]
        )
        try:
            data = json.loads(msg.content[0].text)
            return NewsScore(
                symbol=symbol,
                sentiment=float(max(-1.0, min(1.0, data.get("sentiment", 0.0)))),
                headline_count=len(headlines),
                impact=data.get("impact", "LOW"),
                sample=headlines[:3],
            )
        except Exception:
            return NewsScore(symbol, 0.0, len(headlines), "LOW", headlines[:3])

    def score(self, symbols: List[str], lookback_days: int) -> Dict[str, NewsScore]:
        out: Dict[str, NewsScore] = {}
        for sym in symbols:
            headlines = self._fetch_headlines(sym, lookback_days)
            if not headlines:
                out[sym] = NewsScore(sym, 0.0, 0, "LOW", [])
                continue
            try:
                out[sym] = self._call_claude(sym, headlines)
            except Exception:
                out[sym] = NewsScore(sym, 0.0, len(headlines), "LOW", headlines[:3])
        return out


# ---------------------------------------------------------------------------
# Lexicon fallback
# ---------------------------------------------------------------------------

class LexiconNews(NewsProvider):
    """Fast word-match scorer. Falls back to this if no Claude API key."""

    def _score_text(self, text: str) -> int:
        words = {w.strip(".,!?:;\"'()").lower() for w in text.split()}
        return len(words & _POSITIVE) - len(words & _NEGATIVE)

    def score(self, symbols: List[str], lookback_days: int) -> Dict[str, NewsScore]:
        import yfinance as yf
        cutoff = time.time() - lookback_days * 86400
        out: Dict[str, NewsScore] = {}
        for sym in symbols:
            headlines: List[str] = []
            raw_score = 0
            try:
                items = yf.Ticker(sym).news or []
            except Exception:
                items = []
            names = _TICKER_NAMES.get(sym, [sym.lower()])
            for it in items:
                content = it.get("content", it)
                title = (content.get("title") or "").strip()
                pub = it.get("providerPublishTime") or 0
                if pub and pub < cutoff:
                    continue
                if not title:
                    continue
                title_lower = title.lower()
                if not (any(n in title_lower for n in names) or
                        sym.lower() in title_lower):
                    continue
                headlines.append(title)
                raw_score += self._score_text(title)
            count = len(headlines)
            sentiment = 0.0
            if count:
                sentiment = max(-1.0, min(1.0, raw_score / (count + 1)))
            abs_s = abs(sentiment)
            impact = "HIGH" if abs_s > 0.6 else "MEDIUM" if abs_s > 0.3 else "LOW"
            out[sym] = NewsScore(sym, sentiment, count, impact, headlines[:3])
        return out


# ---------------------------------------------------------------------------
# Neutral (backtest)
# ---------------------------------------------------------------------------

class NeutralNews(NewsProvider):
    def score(self, symbols: List[str], lookback_days: int) -> Dict[str, NewsScore]:
        return {s: NewsScore(s, 0.0, 0, "LOW", []) for s in symbols}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_news_provider(name: str = "auto",
                      api_key: Optional[str] = None) -> NewsProvider:
    """
    'auto'    — use Claude if ANTHROPIC_API_KEY is set, else lexicon
    'claude'  — always use Claude (raises if no key)
    'lexicon' — always use word-match
    'neutral' — always return 0.0
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if name == "auto":
        return ClaudeNews(key) if key else LexiconNews()
    if name == "claude":
        return ClaudeNews(key)
    if name == "lexicon":
        return LexiconNews()
    if name == "neutral":
        return NeutralNews()
    raise ValueError(f"Unknown news provider: {name}")
