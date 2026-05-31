from __future__ import annotations

import json
import os
from datetime import datetime


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

from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

from agents.shared.tone import get_tone_block

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

DAILY_PROMPT = Path(__file__).parent / "prompts" / "idx_lq45_daily.md"
MODEL = "gemini-2.5-flash"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=120000),
        )
    return _client


def analyze_lq45(snapshot: dict, prior_regime: str = "UNKNOWN") -> str:
    response = _get_client().models.generate_content(
        model=MODEL,
        contents=(
            f"Prior regime: {prior_regime}\n\n"
            f"EOD data for {_fmt_date(snapshot['date'])}:\n\n"
            f"```json\n{json.dumps(snapshot, indent=2)}\n```\n\n"
            f"Produce the EOD LQ45 Market Structure brief."
        ),
        config=types.GenerateContentConfig(
            system_instruction=f"{get_tone_block()}\n\n{DAILY_PROMPT.read_text()}",
            max_output_tokens=800,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
