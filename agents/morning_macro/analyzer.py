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

PROMPT_PATH = Path(__file__).parent / "prompts" / "macro_radar.md"
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


def analyze_morning_macro(
    snapshot: dict,
    prior_us_regime: str = "UNKNOWN",
    prior_idx_regime: str = "UNKNOWN",
) -> str:
    ctx = snapshot.get("market_data_context", {})
    data_label_block = (
        f"MARKET DATA LABELS (use these exact phrases — never say 'yesterday' "
        f"if yesterday was a weekend):\n"
        f"  Run day       : {ctx.get('run_day', 'unknown')}\n"
        f"  IDX data      : as of {ctx.get('idx_data_as_of', 'prior close')}\n"
        f"  Asia data     : as of {ctx.get('asia_data_as_of', 'prior close')}\n"
        f"  US futures    : {ctx.get('us_futures_as_of', 'prior session')}\n"
    ) if ctx else ""

    response = _get_client().models.generate_content(
        model=MODEL,
        contents=(
            f"Prior US regime: {prior_us_regime}\n"
            f"Prior IDX regime: {prior_idx_regime}\n\n"
            f"{data_label_block}\n"
            f"Morning macro data ({_fmt_date(snapshot.get('fetched_at', ''))}):\n\n"
            f"```json\n{json.dumps(snapshot, indent=2)}\n```\n\n"
            f"Produce the macro radar brief."
        ),
        config=types.GenerateContentConfig(
            system_instruction=f"{get_tone_block()}\n\n{PROMPT_PATH.read_text()}",
            max_output_tokens=900,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
