from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

from agents.shared.tone import get_tone_block, KAI_PERSONA
from agents.shared.gemini import _gemini_generate

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

DAILY_PROMPT = Path(__file__).parent / "prompts" / "crypto_ta_daily.md"
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


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=120000),
        )
    return _client


def analyze_crypto_ta(snapshot: dict, prior_regime: str = "UNKNOWN") -> str:
    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=(
            f"Prior regime: {prior_regime}\n\n"
            f"EOD crypto data for {_fmt_date(snapshot.get('date', ''))} "
            f"({_fmt_date(snapshot.get('fetched_at', ''))}):\n\n"
            f"```json\n{json.dumps(snapshot, indent=2)}\n```\n\n"
            f"Produce the EOD crypto market brief."
        ),
        config=types.GenerateContentConfig(
            system_instruction=KAI_PERSONA + "\n\n" + DAILY_PROMPT.read_text().replace("<<TONE_BLOCK>>", get_tone_block()),
            max_output_tokens=1800,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
