import os
import json
import requests
from datetime import datetime, timezone
from pathlib import Path


SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_news(query: str, page_size: int = 10) -> list[dict]:
    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        raise EnvironmentError("NEWS_API_KEY is not set")

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "pageSize": page_size,
        "sortBy": "publishedAt",
        "language": "en",
        "apiKey": api_key,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    articles = resp.json().get("articles", [])
    return [
        {
            "source": a["source"]["name"],
            "title": a["title"],
            "description": a.get("description", ""),
            "url": a["url"],
            "published_at": a["publishedAt"],
        }
        for a in articles
    ]


def fetch_quote(symbol: str) -> dict:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise EnvironmentError("ALPHA_VANTAGE_API_KEY is not set")

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
        "apikey": api_key,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    quote = resp.json().get("Global Quote", {})
    return {
        "symbol": quote.get("01. symbol"),
        "price": quote.get("05. price"),
        "change": quote.get("09. change"),
        "change_percent": quote.get("10. change percent"),
        "volume": quote.get("06. volume"),
        "latest_trading_day": quote.get("07. latest trading day"),
    }


def collect(topics: list[str], symbols: list[str]) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    data = {
        "collected_at": timestamp,
        "news": {},
        "quotes": {},
    }

    for topic in topics:
        data["news"][topic] = fetch_news(topic)

    for symbol in symbols:
        data["quotes"][symbol] = fetch_quote(symbol)

    snapshot_path = SNAPSHOT_DIR / f"snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    snapshot_path.write_text(json.dumps(data, indent=2))

    return data
