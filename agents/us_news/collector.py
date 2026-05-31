from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

COMPANY_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "V", "UNH"]
SIMILARITY_THRESHOLD = 0.75


# ── Deduplication ─────────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _deduplicate(items: list[dict]) -> list[dict]:
    unique: list[dict] = []
    for item in items:
        headline = item.get("headline", "")
        matched = False
        for i, kept in enumerate(unique):
            if _similarity(headline, kept.get("headline", "")) >= SIMILARITY_THRESHOLD:
                if len(item.get("summary", "")) > len(kept.get("summary", "")):
                    unique[i] = item
                matched = True
                break
        if not matched:
            unique.append(item)
    return unique


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unix_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ── Finnhub: General Market News ──────────────────────────────────────────────

def _fetch_finnhub_general(api_key: str, since: datetime) -> tuple[list[dict], str]:
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        items = []
        for a in resp.json():
            ts = a.get("datetime", 0)
            if datetime.fromtimestamp(ts, tz=timezone.utc) < since:
                continue
            items.append({
                "headline": a.get("headline", ""),
                "summary": a.get("summary", ""),
                "source": a.get("source", ""),
                "url": a.get("url", ""),
                "published_at": _unix_to_iso(ts),
                "origin": "finnhub_general",
                "related_ticker": None,
            })
        return items, "ok"
    except Exception as exc:
        return [], f"error: {exc}"


# ── Finnhub: Company News ─────────────────────────────────────────────────────

def _fetch_finnhub_company(api_key: str, date_from: str, date_to: str) -> tuple[list[dict], str]:
    items = []
    errors = []
    for symbol in COMPANY_TICKERS:
        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": symbol, "from": date_from, "to": date_to, "token": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            for a in resp.json():
                ts = a.get("datetime", 0)
                items.append({
                    "headline": a.get("headline", ""),
                    "summary": a.get("summary", ""),
                    "source": a.get("source", ""),
                    "url": a.get("url", ""),
                    "published_at": _unix_to_iso(ts),
                    "origin": "finnhub_company",
                    "related_ticker": symbol,
                })
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
        time.sleep(0.5)

    status = "ok" if not errors else f"partial ({'; '.join(errors)})"
    return items, status


# ── NewsAPI ───────────────────────────────────────────────────────────────────

def _fetch_newsapi(api_key: str, yesterday_iso: str) -> tuple[list[dict], str]:
    items = []
    try:
        resp = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"country": "us", "category": "business", "apiKey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        for a in resp.json().get("articles", []):
            items.append({
                "headline": a.get("title", ""),
                "summary": a.get("description") or "",
                "source": a.get("source", {}).get("name", ""),
                "url": a.get("url", ""),
                "published_at": a.get("publishedAt", ""),
                "origin": "newsapi",
                "related_ticker": None,
            })

        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": "stock market OR SEC OR federal reserve OR earnings",
                "language": "en",
                "sortBy": "publishedAt",
                "from": yesterday_iso,
                "pageSize": 30,
                "apiKey": api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        for a in resp.json().get("articles", []):
            items.append({
                "headline": a.get("title", ""),
                "summary": a.get("description") or "",
                "source": a.get("source", {}).get("name", ""),
                "url": a.get("url", ""),
                "published_at": a.get("publishedAt", ""),
                "origin": "newsapi",
                "related_ticker": None,
            })

        return items, "ok"
    except Exception as exc:
        return items, f"error: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_news() -> dict:
    today = date.today()
    yesterday = today - timedelta(days=1)
    now_utc = datetime.now(timezone.utc)
    since_24h = now_utc - timedelta(hours=24)

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
    newsapi_key = os.environ.get("NEWSAPI_KEY", "")

    all_items: list[dict] = []
    sources_status: dict[str, str] = {}

    fh_general, sources_status["finnhub_general"] = _fetch_finnhub_general(finnhub_key, since_24h)
    all_items.extend(fh_general)

    fh_company, sources_status["finnhub_company"] = _fetch_finnhub_company(
        finnhub_key, yesterday.isoformat(), today.isoformat()
    )
    all_items.extend(fh_company)

    newsapi_items, sources_status["newsapi"] = _fetch_newsapi(newsapi_key, yesterday.isoformat())
    all_items.extend(newsapi_items)

    total_raw = len(all_items)

    all_items = [i for i in all_items if i.get("headline")]
    deduped = _deduplicate(all_items)
    deduped.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    snapshot = {
        "date": today.isoformat(),
        "collection_time_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "news_items": deduped,
        "total_raw_count": total_raw,
        "sources_status": sources_status,
    }

    (SNAPSHOT_DIR / f"us_news_{today.isoformat()}.json").write_text(json.dumps(snapshot, indent=2))
    return snapshot


if __name__ == "__main__":
    result = collect_news()
    print(f"Collected {len(result['news_items'])} unique items from {result['total_raw_count']} raw")
    print(f"Sources: {result['sources_status']}")
