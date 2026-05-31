from __future__ import annotations

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env", override=True)


def split_message(text: str, limit: int = 1850) -> list[str]:
    chunks: list[str] = []
    current = ""

    for line in text.split("\n"):
        # +1 for the newline character
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current.strip())
            current = line + "\n"
        else:
            current += line + "\n"

    if current.strip():
        chunks.append(current.strip())

    return chunks


def publish(text: str, webhook_url: str | None = None) -> list:
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK_LIQUIDITY_RADAR")
    if not url:
        raise EnvironmentError("No webhook URL provided")

    chunks = split_message(text)
    print(f"  Total output: {len(text)} chars")
    print(f"  Split into {len(chunks)} chunks")

    responses = []
    for i, chunk in enumerate(chunks):
        response = requests.post(url, json={"content": chunk})
        responses.append(response)
        print(f"  Chunk {i+1}/{len(chunks)} sent ({len(chunk)} chars)")
        if i < len(chunks) - 1:
            time.sleep(0.5)

    print(f"[Discord] Sent {len(chunks)} chunk(s) ({len(text)} chars total)")
    return responses
