from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

WIB = timezone(timedelta(hours=7))

RSS_FEEDS = {
    "cointelegraph":  "https://cointelegraph.com/rss",
    "coindesk":       "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "decrypt":        "https://decrypt.co/feed",
    "bitcoinmagazine": "https://bitcoinmagazine.com/.rss/full/",
}

_RSS_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

MACRO_KEYWORDS = {
    "fed", "rate", "inflation", "sec", "etf", "regulation", "ban", "approval",
    "hack", "exploit", "institutional", "blackrock", "fidelity", "grayscale",
    "binance", "coinbase", "whale", "liquidation", "stablecoin", "tether",
    "usdc", "cbdc",
}
STRUCTURE_KEYWORDS = {
    "crypto", "bitcoin", "ethereum", "defi", "nft", "layer 2", "blockchain",
    "web3", "altcoin", "token", "protocol", "dao",
}
QUALITY_SKIP = [
    "price prediction", "will reach", "could hit", "might go",
    "according to experts", "top 10 picks", "best crypto to buy",
]


def _r(v, d: int = 2):
    try:
        return round(float(v), d) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Section A: Top 200 coins ──────────────────────────────────────────────────

def load_top200() -> tuple[list[dict], set[str], set[str]]:
    snap_path = SNAPSHOT_DIR / "crypto_ta_latest.json"
    coins: list[dict] = []

    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text())
            # Build from gainers + losers + known majors
            seen: set[str] = set()
            for entry in (snap.get("top_gainers", []) + snap.get("top_losers", [])):
                sym = (entry.get("symbol") or "").lower()
                name = (entry.get("name") or "").lower()
                if sym and sym not in seen:
                    coins.append({"symbol": sym, "name": name})
                    seen.add(sym)
            # Always include top coins by name
            for sym, name in [("btc", "bitcoin"), ("eth", "ethereum"), ("sol", "solana"),
                               ("bnb", "bnb"), ("xrp", "xrp"), ("usdt", "tether"),
                               ("usdc", "usd coin"), ("ada", "cardano"), ("avax", "avalanche"),
                               ("doge", "dogecoin"), ("trx", "tron"), ("link", "chainlink"),
                               ("dot", "polkadot"), ("matic", "polygon"), ("ltc", "litecoin")]:
                if sym not in seen:
                    coins.append({"symbol": sym, "name": name})
                    seen.add(sym)
        except Exception:
            pass

    if not coins:
        print("  [A] Fetching top 200 from CoinGecko...")
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={"vs_currency": "usd", "order": "market_cap_desc",
                        "per_page": 200, "page": 1, "sparkline": "false"},
                timeout=15,
            )
            resp.raise_for_status()
            for c in resp.json():
                coins.append({
                    "symbol": (c.get("symbol") or "").lower(),
                    "name":   (c.get("name") or "").lower(),
                })
        except Exception as e:
            print(f"    CoinGecko top200 failed: {e}")

    top200_symbols = {c["symbol"] for c in coins if c["symbol"]}
    top200_names   = {c["name"]   for c in coins if c["name"]}
    return coins, top200_symbols, top200_names


# ── Section C: Market snapshot ────────────────────────────────────────────────

def load_market_snapshot() -> dict:
    snap_path = SNAPSHOT_DIR / "crypto_ta_latest.json"
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text())
            g   = snap.get("global", {})
            btc = snap.get("btc_detail", {})
            return {
                "btc_price":           btc.get("current_price"),
                "btc_pct_24h":         btc.get("pct_24h"),
                "total_mcap":          g.get("total_mcap"),
                "total_mcap_chg_24h":  g.get("total_mcap_chg_24h"),
                "btc_dominance":       g.get("btc_dominance"),
                "top_gainers":         snap.get("top_gainers", [])[:3],
                "top_losers":          snap.get("top_losers", [])[:3],
                "regime":              snap.get("regime"),
                "snapshot_at":         snap.get("fetched_at"),
            }
        except Exception:
            pass

    print("  [C] Fetching global snapshot from CoinGecko...")
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        mcap = data.get("total_market_cap", {}).get("usd")
        return {
            "btc_price":           None,
            "btc_pct_24h":         None,
            "total_mcap":          f"${mcap/1e12:.2f}T" if mcap else None,
            "total_mcap_chg_24h":  _r(data.get("market_cap_change_percentage_24h_usd")),
            "btc_dominance":       _r(data.get("market_cap_percentage", {}).get("btc")),
            "top_gainers":         [],
            "top_losers":          [],
            "regime":              None,
            "snapshot_at":         None,
        }
    except Exception as e:
        print(f"    Global snapshot failed: {e}")
        return {}


# ── Section D: RSS news ───────────────────────────────────────────────────────

def _is_relevant(title: str, summary: str, top200_symbols: set, top200_names: set) -> bool:
    text = (title + " " + summary).lower()
    if any(kw in text for kw in MACRO_KEYWORDS):
        return True
    if any(kw in text for kw in STRUCTURE_KEYWORDS):
        return True
    if any(sym in text for sym in top200_symbols):
        return True
    if any(name in text for name in top200_names if len(name) > 3):
        return True
    return False


def _quality_ok(title: str) -> bool:
    t = title.lower()
    return not any(skip in t for skip in QUALITY_SKIP)


def _dedup(articles: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for art in articles:
        words = art["title"].lower().split()
        duplicate = False
        for k in kept:
            k_words = k["title"].lower().split()
            # Check for 5+ consecutive shared words
            for i in range(len(words) - 4):
                chunk = " ".join(words[i:i+5])
                if chunk in " ".join(k_words):
                    # Keep the one with longer summary
                    if len(art.get("summary", "")) > len(k.get("summary", "")):
                        kept.remove(k)
                        kept.append(art)
                    duplicate = True
                    break
            if duplicate:
                break
        if not duplicate:
            kept.append(art)
    return kept


def fetch_crypto_rss(news_window_hours: int, top200_symbols: set, top200_names: set) -> list[dict]:
    print("  [D] Crypto RSS news...")
    import calendar
    cutoff   = datetime.now(WIB) - timedelta(hours=news_window_hours)
    combined: list[dict] = []

    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url, request_headers=_RSS_HEADERS)
            if not feed.entries or feed.bozo:
                continue
            count = 0
            for entry in feed.entries:
                if count >= 8:
                    break
                title   = entry.get("title", "")
                summary = (entry.get("summary") or entry.get("description") or "")[:300]
                if not _quality_ok(title):
                    continue
                if not _is_relevant(title, summary, top200_symbols, top200_names):
                    continue
                pub = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        ts  = calendar.timegm(entry.published_parsed)
                        pub = datetime.fromtimestamp(ts, tz=WIB)
                    except Exception:
                        pass
                if pub and pub < cutoff:
                    continue
                combined.append({
                    "source":       source_name,
                    "title":        title,
                    "link":         entry.get("link", ""),
                    "published":    pub.strftime("%d-%m-%Y %H:%M WIB") if pub else "",
                    "published_ts": pub.timestamp() if pub else 0,
                    "summary":      summary,
                })
                count += 1
        except Exception as e:
            print(f"    RSS {source_name} failed: {e}")

    combined.sort(key=lambda x: x["published_ts"], reverse=True)
    combined = _dedup(combined)
    result   = combined[:20]
    print(f"    {len(result)} RSS articles")
    return result


# ── Section E: Finnhub crypto news ───────────────────────────────────────────

def fetch_finnhub_news(news_window_hours: int) -> list[dict]:
    print("  [E] Finnhub crypto news...")
    token = os.getenv("FINNHUB_API_KEY", "")
    if not token:
        print("    No FINNHUB_API_KEY — skipping")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=news_window_hours)
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "crypto", "token": token},
            timeout=15,
        )
        resp.raise_for_status()
        results: list[dict] = []
        for item in resp.json():
            ts  = item.get("datetime", 0)
            pub = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            if pub and pub < cutoff:
                continue
            title = item.get("headline", "")
            if not _quality_ok(title):
                continue
            results.append({
                "headline": title,
                "source":   item.get("source", ""),
                "datetime": ts,
                "summary":  (item.get("summary") or "")[:300],
                "url":      item.get("url", ""),
            })
        results.sort(key=lambda x: x["datetime"], reverse=True)
        print(f"    {len(results[:10])} Finnhub articles")
        return results[:10]
    except Exception as e:
        print(f"    Finnhub failed: {e}")
        return []


# ── Section F: Notable movers ─────────────────────────────────────────────────

def load_notable_movers() -> list[dict]:
    snap_path = SNAPSHOT_DIR / "crypto_ta_latest.json"
    movers: list[dict] = []
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text())
            for entry in snap.get("top_gainers", []) + snap.get("top_losers", []):
                pct = entry.get("pct_24h")
                if pct is not None and abs(float(pct)) > 10:
                    movers.append({
                        "symbol":          entry.get("symbol"),
                        "name":            entry.get("name"),
                        "pct_24h":         _r(pct),
                        "market_cap_rank": entry.get("rank"),
                        "price":           entry.get("current_price"),
                    })
        except Exception:
            pass
    movers.sort(key=lambda x: abs(x.get("pct_24h") or 0), reverse=True)
    return movers[:10]


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_crypto_news() -> dict:
    now_wib    = datetime.now(WIB)
    fetched_at = now_wib.strftime("%d-%m-%Y %H:%M WIB")

    is_weekend        = now_wib.weekday() >= 5
    news_window_hours = 72 if is_weekend else 24

    print("  [A] Loading top 200 coins...")
    coins, top200_symbols, top200_names = load_top200()

    print("  [C] Loading market snapshot...")
    market_snapshot = load_market_snapshot()

    rss_news     = fetch_crypto_rss(news_window_hours, top200_symbols, top200_names)
    finnhub_news = fetch_finnhub_news(news_window_hours)
    notable_movers = load_notable_movers()

    snapshot = {
        "fetched_at":        fetched_at,
        "is_weekend":        is_weekend,
        "news_window_hours": news_window_hours,
        "market_snapshot":   market_snapshot,
        "top200_symbols":    sorted(top200_symbols),
        "notable_movers":    notable_movers,
        "rss_news":          rss_news,
        "finnhub_news":      finnhub_news,
    }

    payload = json.dumps(snapshot, indent=2)
    (SNAPSHOT_DIR / "crypto_news_latest.json").write_text(payload)
    print("  Saved → crypto_news_latest.json")

    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_crypto_news()
    pprint.pprint({k: v for k, v in result.items()
                   if k not in ("rss_news", "finnhub_news", "top200_symbols")})
    print(f"\nRSS articles:   {len(result['rss_news'])}")
    print(f"Finnhub:        {len(result['finnhub_news'])}")
    print(f"Notable movers: {len(result['notable_movers'])}")
