from __future__ import annotations

import os
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

PROMPT_PATH = Path(__file__).parent / "prompts" / "us_news.md"
MODEL = "gemini-2.5-flash"
MAX_ITEMS = 50  # 50 most recent is plenty for Claude to pick top 5-8

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=120000),
        )
    return _client


def _build_news_text(items: list[dict]) -> str:
    """50 most recent items, compact line format: headline | source | published_at.
    Snapshot is pre-sorted by published_at desc in the collector."""
    lines = []
    for i, item in enumerate(items[:MAX_ITEMS], 1):
        ticker = f"[{item['related_ticker']}] " if item.get("related_ticker") else ""
        headline = item.get("headline", "").strip()
        source = item.get("source", "").strip()
        published_at = item.get("published_at", "").strip()
        parts = [f"{i}. {ticker}{headline}"]
        if source:
            parts.append(source)
        if published_at:
            parts.append(published_at)
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def analyze_news(snapshot: dict) -> str:
    system_prompt = PROMPT_PATH.read_text()
    news_text = _build_news_text(snapshot["news_items"])
    print(f"Payload to Gemini: {len(news_text)} chars, ~{len(news_text) // 4} tokens")

    response = _gemini_generate(_get_client(),
        model=MODEL,
        contents=(
            f"Here are today's raw news items "
            f"({snapshot['total_raw_count']} collected, {len(news_text.splitlines())} shown after dedup and cap):\n\n"
            f"{news_text}\n\n"
            f"Produce the US Stock News digest for {snapshot['date']}."
        ),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=800,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text
