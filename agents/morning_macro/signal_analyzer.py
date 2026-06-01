from __future__ import annotations

import json
import os
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

PROMPT_PATH = Path(__file__).parent / "prompts" / "morning_signal.md"
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


def analyze_morning_signal(
    snapshot: dict,
    prior_us_regime: str = "UNKNOWN",
    prior_idx_regime: str = "UNKNOWN",
) -> str:
    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=(
            f"Prior US regime: {prior_us_regime}\n"
            f"Prior IDX regime: {prior_idx_regime}\n\n"
            f"Morning macro data ({snapshot.get('fetched_at', '')}):\n\n"
            f"```json\n{json.dumps(snapshot, indent=2)}\n```\n\n"
            f"Produce the condensed morning signal."
        ),
        config=types.GenerateContentConfig(
            system_instruction=PROMPT_PATH.read_text(),
            max_output_tokens=350,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
