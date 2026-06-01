from __future__ import annotations

import json
import os
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

from agents.shared.tone import get_tone_block
from agents.shared.gemini import _gemini_generate

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

PROMPT_PATH = Path(__file__).parent / "prompts" / "liquidity_crypto.md"
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


def analyze(snapshot: dict) -> str:
    system_prompt = PROMPT_PATH.read_text()

    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=(
            f"Here is today's data snapshot:\n\n"
            f"```json\n{json.dumps(snapshot, indent=2)}\n```\n\n"
            f"Produce the daily Liquidity & Crypto report."
        ),
        config=types.GenerateContentConfig(
            system_instruction=f"{get_tone_block()}\n\n{system_prompt}",
            max_output_tokens=1200,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
