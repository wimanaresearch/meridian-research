from __future__ import annotations

import json
import os
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

WEEKLY_PROMPT = Path(__file__).parent / "prompts" / "idx_lq45_weekly.md"
SNAPSHOT_DIR  = Path(__file__).parents[2] / "data" / "snapshots"
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


def analyze_lq45_weekly(snapshots: list[dict], prior_regime: str = "UNKNOWN") -> str:
    latest_date = snapshots[0]["date"] if snapshots else "unknown"

    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=(
            f"Prior regime: {prior_regime}\n\n"
            f"Last {len(snapshots)} daily EOD snapshots (most recent first):\n\n"
            f"```json\n{json.dumps(snapshots, indent=2)}\n```\n\n"
            f"Produce the weekly LQ45 TA synthesis for the week of {latest_date}."
        ),
        config=types.GenerateContentConfig(
            system_instruction=WEEKLY_PROMPT.read_text(),
            max_output_tokens=900,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text


def load_recent_snapshots(n: int = 5) -> list[dict]:
    """Load the n most recent idx_lq45_*.json snapshots from disk."""
    files = sorted(SNAPSHOT_DIR.glob("idx_lq45_*.json"), reverse=True)[:n]
    snapshots = []
    for f in files:
        try:
            snapshots.append(json.loads(f.read_text()))
        except Exception:
            pass
    return snapshots
