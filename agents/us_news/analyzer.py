from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

from agents.shared.tone import get_tone_block, SERA_PERSONA
from agents.shared.gemini import _gemini_generate

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

MODEL = "gemini-2.5-flash"

_client: genai.Client | None = None


def _fmt_date(raw: str) -> str:
    """Convert any date string to DD-Mon-YYYY (e.g. 30-May-2026)."""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    s = raw[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    return raw


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=120000),
        )
    return _client


# ── System prompt ─────────────────────────────────────────────────────────────

_PROMPT_HEAD = """\
You are a US equity post-market analyst writing
a daily recap brief for a Discord community.
Members range from active traders to newer
investors learning US markets.

Your job is NOT to list headlines.
Your job is to ANALYZE what the session means —
index moves in context, sector rotation signals,
earnings implications, and what to watch next.

Think like a sell-side analyst writing an
end-of-day note, not a news aggregator.\
"""

_PROMPT_TAIL = """\
OUTPUT LENGTH RULE:
Hard limit: 550 words maximum. No exceptions.
Always complete every section before the limit.
If running long: cut sentences, never cut sections.

US NEWS SPECIFIC RULES:
- NO markdown tables. Line-by-line only.
- 🟢 positive/gainers  🔴 negative/losers  🟡 neutral
- ALL % changes in brackets: (+1.23%)
- REGIME OPTIONS (pick one):
  Structural Bull | Recovering | Choppy |
  Distributing | Structural Bear
- Show regime ONCE in header only. Never repeat.

FORMAT (follow exactly):

**🇺🇸 US MARKET NEWS — {DD-Mon-YYYY}**
Regime: {regime} | {CONT./SHIFT}

──────────────────────
**📊 INDEX SUMMARY**
S&P 500   {price}  ({1d_chg}%)  5d: ({5d_chg}%)
NASDAQ    {price}  ({1d_chg}%)  5d: ({5d_chg}%)
DOW       {price}  ({1d_chg}%)  5d: ({5d_chg}%)
VIX       {level}  Fear/Greed: {label}

{2 sentences: what drove the session — was it
macro data, Fed, earnings, or flows. What the
spread between indexes tells us about rotation.
Overall session tone: risk-on / risk-off / mixed.}

──────────────────────
**🗺️ SECTOR ROTATION**
Strong: {top 3 sectors + ETF + chg%}
Weak:   {bottom 3 sectors + ETF + chg%}
▸ {1 sentence: what this rotation signals about
  institutional positioning today.}

──────────────────────
**🔥 TOP MOVERS (S&P 500)**
{Top 5 gainers, top 5 losers. Only vol_ratio >= 1.5 movers.
 If fewer than 5 on either side, show what's available.}

🟢 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🟢 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🟢 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🟢 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🟢 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🔴 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🔴 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🔴 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🔴 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x
🔴 {TICKER}  ({chg}%)  ${price}  Vol: {ratio}x

▸ {1-2 sentences: what the mover pattern reveals —
  is strength broad or narrow, sector concentrated?}

──────────────────────
**📋 EARNINGS**
{For each report with actual vs estimate data:}
🟢/🔴/🟡 {TICKER} — EPS: {actual} vs {est} ({beat/miss/inline}) | Rev: {actual} vs {est}
▸ {1 sentence: market implication.}

{If no earnings data: "No major earnings this session."}

──────────────────────
**📰 MARKET NEWS**
{Top 4-5 news items most relevant to US markets.}
🟢/🔴/🟡 {Headline — rewritten concisely}
▸ {1-2 sentences: what happened and why it matters
  for US equities or a specific sector/stock.}

──────────────────────
**🔭 SESSION OUTLOOK**
{2-3 sentences: key levels, upcoming catalysts,
 and the single most important risk or opportunity
 heading into the next session. Be specific —
 name a level, event, or data release.}\
"""


# ── Payload trimmer ───────────────────────────────────────────────────────────

def _slim_payload(snapshot: dict) -> dict:
    """Whitelist the exact fields sent to Gemini — cap movers at 10 each side."""
    def _mover(m):
        return {k: m[k] for k in ("ticker", "close", "day_chg_pct", "vol_ratio") if k in m}

    def _sector(s):
        return {k: s[k] for k in ("ticker", "name", "chg_1d_pct") if k in s}

    def _earnings(e):
        return {k: e[k] for k in (
            "ticker", "company", "eps_actual", "eps_estimate",
            "rev_actual", "rev_estimate", "beat_miss",
        ) if k in e}

    def _news(n):
        return {k: n[k] for k in ("headline", "summary", "source") if k in n}

    movers  = snapshot.get("top_movers", {})
    sectors = snapshot.get("sectors", {})

    return {
        "date":       snapshot.get("date"),
        "fetched_at": snapshot.get("fetched_at"),
        "indices":    snapshot.get("indices", {}),
        "sectors": {
            "top3":    [_sector(s) for s in sectors.get("top3", [])],
            "bottom3": [_sector(s) for s in sectors.get("bottom3", [])],
        },
        "top_movers": {
            "gainers": [_mover(m) for m in movers.get("gainers", [])[:10]],
            "losers":  [_mover(m) for m in movers.get("losers",  [])[:10]],
        },
        "earnings": [_earnings(e) for e in snapshot.get("earnings", [])[:10]],
        "news":     [_news(n) for n in snapshot.get("news", [])[:5]],
    }


# ── Analyzer ──────────────────────────────────────────────────────────────────

def analyze_us_news(snapshot: dict, prior_regime: str = "UNKNOWN") -> str:
    system_prompt = f"{SERA_PERSONA}\n\n{_PROMPT_HEAD}\n\n{get_tone_block()}\n\n{_PROMPT_TAIL}"
    payload  = _slim_payload(snapshot)
    contents = (
        f"Prior regime: {prior_regime}\n\n"
        f"US market data ({_fmt_date(snapshot.get('fetched_at', ''))}):\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
        f"Produce the US Market News brief."
    )
    print(f"[us_news] Prompt payload: {len(contents):,} chars (~{len(contents) // 4:,} tokens)")

    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=1200,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
