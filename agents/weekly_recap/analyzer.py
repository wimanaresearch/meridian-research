from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

from agents.shared.tone import get_tone_block, NEO_PERSONA
from agents.shared.gemini import _gemini_generate

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

MODEL = "gemini-2.5-flash"

_client: genai.Client | None = None


def _fmt_date(raw: str) -> str:
    """Convert any date string to DD-Mon-YYYY (e.g. 30-May-2026)."""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%Y %H:%M WIB", "%Y-%m-%dT%H:%M:%SZ"):
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

# ── System prompt ─────────────────────────────────────────────────────────────

_PROMPT_HEAD = """\
You are a senior macro analyst writing a weekly
recap for a Discord community tracking US stocks,
IDX, and crypto.

This is a SUMMARY document — not a data dump.
Your job: tell the story of the week in clear,
analytical prose. Numbers support the narrative.
The narrative is the product.

Think: a smart analyst's end-of-week debrief
over coffee. Concise, insightful, forward-looking.\
"""

_PROMPT_TAIL = """\
OUTPUT LENGTH RULE:
Target: 3,000 to 3,800 characters total.
Hard limit: 4,000 characters.
2-3 Discord chunks maximum.
If you are running long: cut data lines,
keep analysis sentences.
Data without analysis = cut it.
Analysis without data = keep it.

WEEKLY RECAP RULES:
- NO markdown tables anywhere.
- NO bullet lists of movers or sectors.
  Weave numbers into prose sentences instead.
- 🟢 positive  🔴 negative  🟡 mixed
- All % changes in brackets: (+1.23%)
- Weekly moves only — no daily breakdown.
- REGIME OPTIONS (pick one per market):
  Structural Bull | Recovering | Choppy |
  Distributing | Structural Bear
- Cross-market connections are mandatory.
  Every market section must reference how
  global or cross-market forces affected it.
- Every number cited must include context:
  WRONG: "S&P gained (+2.1%) this week"
  RIGHT: "S&P gained (+2.1%) — best week in
  two months, driven by cooling CPI that
  pushed rate cut odds back into play"
- Forward implication required in every section.
  End each market section with what it means
  for next week, not just what happened.

FORMAT:

**📅 WEEKLY RECAP — {DD-Mon-YYYY}**

**📊 REGIME**
🇺🇸 US      {regime} | {CONT./SHIFT}
🇮🇩 IDX     {regime} | {CONT./SHIFT}
₿  Crypto  {regime} | {CONT./SHIFT}

{1-2 sentences: the week in one headline.
What was the single dominant macro theme
that connected all three markets this week.}

──────────────────────
**🇺🇸 US EQUITIES**
{3-4 sentences of analytical prose.
 Lead with the weekly return of S&P 500
 and Nasdaq embedded in a sentence.
 Explain what drove the move — was it macro
 data, Fed, earnings, or positioning.
 Note what sector rotation revealed about
 institutional intent — one or two sectors
 worth naming, not all eleven.
 End with: what the weekly structure implies
 for next week — is momentum intact, is the
 trend extended, what would change the picture.

 Example style:
 "S&P 500 added (+1.8%) while Nasdaq led at
 (+2.4%) — tech outperformance for the third
 straight week signals growth is back in favor
 as the 10Y yield retreated (-12bps) to 4.18%,
 its lowest since March. Financials (-0.4%)
 lagged as the yield curve flattened, hinting
 that the rate-cut trade is crowding. Heading
 into next week, the setup is constructive but
 extended — a hot jobs number Friday could
 rapidly reprice the rate cut narrative."}

──────────────────────
**🇮🇩 IDX**
{3-4 sentences of analytical prose.
 Lead with IHSG and LQ45 weekly returns.
 Connect IDR move to foreign flow dynamics.
 Name one or two sectors or stocks that drove
 the week — commodity move, earnings, policy.
 End with: what IDR and global cues imply
 for IDX open next week.

 Example style:
 "IHSG slipped (-1.2%) for the week as LQ45
 underperformed at (-1.8%) — the gap signals
 institutional de-risking in large caps while
 broader market held. IDR weakened to 16,340
 (+0.8% USD/IDR) as dollar strength post-NFP
 pressured EM currencies broadly. Commodity
 stocks were the bright spot: coal names held
 on Newcastle prices staying above $130, but
 banking heavyweights BBCA and BBRI saw
 continued foreign selling for the third week.
 Watch IDR at 16,400 — a break there would
 likely accelerate foreign outflows next week."}

──────────────────────
**₿ CRYPTO**
{3-4 sentences of analytical prose.
 Lead with BTC and ETH weekly performance.
 Note TOTAL market cap change and what
 dominance shift (if any) signals.
 Identify one theme that drove crypto this week
 — macro correlation, protocol news, regulatory,
 sector rotation within crypto.
 End with: key level or catalyst for next week.

 Example style:
 "BTC ended the week at $103,400 (+4.2%) as
 falling real yields and dollar softness gave
 crypto a macro tailwind — the clearest
 correlation trade of the week. ETH lagged at
 (+2.1%) while TOTAL2 gained (+5.8%), suggesting
 selective altcoin rotation rather than broad
 risk-on. BTC dominance ticked down to 62.4%
 from 63.1% — early sign of alt season rotation
 but not yet confirmed. The $100,000 level held
 as support all week; a hold above there into
 Monday open keeps the Structural Bull thesis
 intact."}

──────────────────────
**🌍 CROSS-MARKET READ**
{3-4 sentences. This is the most important
 section. Connect all three markets explicitly.

 Answer these in flowing prose:
 - Did US, IDX, and crypto move together or
   diverge this week — and what does that tell us
 - How did the dollar and yields transmit across
   markets
 - If one market diverged from the others, why
   and what does it signal about relative strength
 - The single most important cross-market
   signal from this week in one clear sentence

 No data lines here — pure analytical prose.
 This section should feel like the insight
 members could not get just from reading
 the daily briefs.}

──────────────────────
**🔭 NEXT WEEK**
{2 sentences + calendar.

 Sentence 1: overall setup and key risk or
 opportunity going into next week.
 Sentence 2: what single event or level could
 shift the regime and in which direction.}

Key events:
{High impact only, max 5 events.}
{day} {flag} {event} — Fcst: {val}

──────────────────────
**📊 REGIME**
🇺🇸 US      {regime}
🇮🇩 IDX     {regime}
₿  Crypto  {regime}
Overall: {1 sentence portfolio-level read}\
"""


# ── Gemini client ─────────────────────────────────────────────────────────────

def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=180000),
        )
    return _client


# ── Analyzer ──────────────────────────────────────────────────────────────────

def analyze_weekly_recap(snapshot: dict) -> str:
    system_prompt = f"{NEO_PERSONA}\n\n{_PROMPT_HEAD}\n\n{get_tone_block()}\n\n{_PROMPT_TAIL}"

    # Strip raw snapshot blobs from agent snapshots to keep payload lean
    payload = dict(snapshot)
    if "snapshots" in payload:
        payload["snapshots"] = {
            k: {kk: vv for kk, vv in v.items() if kk != "raw"}
            for k, v in payload["snapshots"].items()
        }

    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=(
            f"Weekly data (week ending {_fmt_date(snapshot.get('week_ending', ''))}):\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
            f"Produce the weekly recap brief."
        ),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=1500,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
