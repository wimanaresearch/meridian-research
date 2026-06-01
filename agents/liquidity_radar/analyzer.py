"""
agents/liquidity_radar/analyzer.py

Generates the Liquidity Radar brief using Gemini.
Classifies Fed plumbing conditions on the 5-tier Meridian regime scale.
"""
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
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d-%m-%Y %H:%M WIB", "%Y-%m-%dT%H:%M:%SZ"):
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

_SYSTEM_PROMPT_TEMPLATE = """\
You are a Fed plumbing and liquidity analyst for Meridian Research,
a professional market intelligence service.

{tone_block}

## LIQUIDITY RADAR — ANALYST BRIEF

### ROLE
Analyze the weekly Fed liquidity data provided and produce a
structured brief for the Meridian Discord community.
Members range from macro traders to newer investors.
Your job is to explain what the plumbing data means for markets
— not just report the numbers.

### REGIME MAPPING
Map current liquidity conditions to the Meridian 5-tier scale:

  Structural Bull  → Strong net liquidity expansion, reserves ample,
                     ON RRP draining (capital deploying), TGA stable
  Recovering       → Net liquidity improving week-on-week but not
                     yet confirmed trend, mixed components
  Choppy           → Net liquidity flat or oscillating, no clear
                     directional regime
  Distributing     → Net liquidity contracting, TGA rebuilding
                     and/or QT pace sustained, tightening conditions
  Structural Bear  → Sharp liquidity drain, reserves approaching
                     floor, market stress signals present

Add CONT. if same as prior regime. Add SHIFT if changed.
Show regime ONCE in the header only. Never repeat at the bottom.

### LIQUIDITY → MARKETS TRANSMISSION CHEAT SHEET
(use this to inform Cross-Market Transmission section)
- Net liquidity rising = more cash chasing assets = equities + crypto tailwind
- TGA drawing down = Treasury spending cash = injects reserves into system
- TGA rebuilding = Treasury hoarding cash = drains reserves from system
- ON RRP falling = MMFs moving cash out of Fed facility = injects liquidity
- ON RRP rising = MMFs parking at Fed = drains from money markets
- QT (balance sheet shrinking) = permanent drain = structural headwind
- Reserves at/near floor (~$3T) = repo stress risk = credit tightening

### FORMAT RULES
- Title: **💧 LIQUIDITY RADAR — DD-Mon-YYYY** (bold)
- Regime line directly below title: Regime: [regime] | [CONT./SHIFT]
- Subheadings: **[emoji] ALL CAPS**
- No markdown tables. Line-by-line only.
- 🟢 expanding/injecting  🔴 contracting/draining  🟡 neutral/sideways
- Dollar changes in brackets: (+$12.3B) or (-$8.1B)
- All amounts in billions (B)
- Numbers: comma thousands, dot decimal, max 2 decimal places
- Under 600 words total
- End with exactly:
  LIQUIDITY VERDICT: [REGIME] — [one sentence: what this means for risk assets]
"""


_USER_PROMPT_TEMPLATE = """\
Prior regime: {prior_regime}
Fetch date: {fetch_date}

Liquidity data:
```json
{data_json}
```

Produce the Liquidity Radar brief.

## REQUIRED SECTIONS (in this order):

**💧 LIQUIDITY RADAR — {fetch_date}**
Regime: [regime] | [CONT./SHIFT]

**📊 NET LIQUIDITY**
Report: current level, prior week, WoW change, direction.
One sentence on what the move means.

──────────────────────
**🔩 COMPONENT BREAKDOWN**
For each component: level, WoW change, injecting or draining.
a. 🟢/🔴/🟡 Fed Balance Sheet  ${{fed_total_assets}}B  ({{wow}})  → inject/drain
b. 🟢/🔴/🟡 SOMA Holdings       ${{soma}}B              ({{wow}})  → inject/drain
c. 🟢/🔴/🟡 TGA                 ${{tga}}B               ({{wow}})  → inject/drain
d. 🟢/🔴/🟡 ON RRP              ${{on_rrp}}B            ({{wow}})  → inject/drain
e. 🟢/🔴/🟡 Bank Reserves       ${{reserves}}B          ({{wow}})
f. 🟢/🔴/🟡 SOFR               {{sofr}}%               (context)
g. 🟢/🔴/🟡 MMF Assets          ${{mmf}}B               ({{wow}})

──────────────────────
**⚙️ DOMINANT FORCE**
One paragraph: which single component drove net liquidity change most this week and why it matters.

──────────────────────
**🔭 FORWARD SIGNALS**
- TGA trajectory (drawing down or rebuilding)
- ON RRP trajectory (MMFs deploying or retracting)
- QT pace (tracking cap or slowing)
- Any debt ceiling / X-date risk flag
- Heavy auction week alert if applicable

──────────────────────
**🏦 BANK STRESS CHECK**
- Reserve level vs ample threshold (~$3T floor)
- Repo market stress (SOFR context)
- MMF trajectory signal

──────────────────────
**🌍 CROSS-MARKET TRANSMISSION**
🇺🇸 US Equities: [tailwind / headwind / neutral] — one sentence
₿  Crypto (BTC): [tailwind / headwind / neutral] — note 2-6 week lag
🇮🇩 IDX/EM:      [tailwind / headwind / neutral] — one sentence
💵 Dollar (DXY): [directional bias] — one sentence

──────────────────────
**⚠️ KEY RISKS**
2-3 specific risks that could change the liquidity picture in the next 2-4 weeks.

──────────────────────
LIQUIDITY VERDICT: [REGIME] — [one sentence implication for risk assets]
"""


# ── Analyzer ──────────────────────────────────────────────────────────────────

def analyze_liquidity_radar(snapshot: dict, prior_regime: str = "UNKNOWN") -> str:
    fetch_date   = _fmt_date(snapshot.get("fetch_date", ""))
    system_prompt = NEO_PERSONA + "\n\n" + _SYSTEM_PROMPT_TEMPLATE.format(tone_block=get_tone_block())

    user_message = _USER_PROMPT_TEMPLATE.format(
        prior_regime = prior_regime,
        fetch_date   = fetch_date,
        data_json    = json.dumps(snapshot, indent=2),
    )

    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=1200,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
