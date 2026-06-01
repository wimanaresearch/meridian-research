from __future__ import annotations

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env", override=True)


def split_message(text: str, limit: int = 1950) -> list[str]:
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
        n = i + 1
        total = len(chunks)
        print(f"  Posting chunk {n}/{total} — chars: {len(chunk)}")

        for attempt in range(2):
            response = requests.post(url, json={"content": chunk})

            if response.status_code == 429:
                try:
                    retry_after = float(response.json().get("retry_after", 2.0))
                except Exception:
                    retry_after = 2.0
                print(f"  429 RATE LIMITED on chunk {n}/{total} — retry_after: {retry_after}s")
                print(f"  Retrying chunk {n}/{total} after {retry_after}s")
                time.sleep(retry_after + 0.1)
                continue
            break

        responses.append(response)

        if response.status_code in (200, 204):
            print(f"  Chunk {n}/{total} posted successfully (HTTP {response.status_code})")
        else:
            print(f"  ERROR chunk {n}/{total}: HTTP {response.status_code}")
            print(f"  Response body: {response.text}")

        time.sleep(2.0)

    print(f"[Discord] Sent {len(chunks)} chunk(s) ({len(text)} chars total)")
    return responses
