from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import feedparser
import pandas as pd
import requests as std_requests
import yfinance as yf
from curl_cffi import requests as cf_requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

WIB = timezone(timedelta(hours=7))

COAL_STOCKS   = ["ADRO", "ITMG", "PTBA", "BUMI"]
CPO_STOCKS    = ["AALI", "SSMS", "LSIP"]
NICKEL_STOCKS = ["INCO", "MDKA"]

TIER1_TICKERS = ["BBCA", "BBRI", "BMRI", "BBNI", "BBTN", "TLKM", "GOTO", "ASII", "ADRO", "MDKA", "INCO"]
TIER2_TICKERS = ["PTBA", "UNVR", "ICBP"]

RSS_FEEDS = {
    "antara_economy": "https://www.antaranews.com/rss/ekonomi.xml",
    "cnbc_indonesia":  "https://www.cnbcindonesia.com/rss",
    "detik_finance":   "https://finance.detik.com/feed",
    "investing_id":    "https://id.investing.com/rss/news.rss",
}
RSS_KEYWORDS = [
    "saham", "idx", "ihsg", "bursa", "emiten",
    "laba", "dividen", "rights", "ipo", "ojk",
    "bank indonesia", "rupiah", "coalindo",
    "nikel", "sawit", "bbca", "bbri", "bmri",
    "tlkm", "goto", "asii", "adro", "mdka",
]

ANNOUNCEMENT_KEYWORDS = [
    "dividen", "rights", "laba", "keuangan", "akuisisi",
    "merger", "suspensi",
]
MACRO_KEYWORDS = [
    "indonesia", "idx", "ihsg", "rupiah", "idr", "bank indonesia", "ojk",
    "bi rate", "jakarta", "jokowi", "prabowo", "bkpm", "coal", "nickel",
    "palm oil", "cpo",
]

_IDX_HEADERS = {
    "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":           "https://idx.co.id",
    "Accept":            "application/json",
    "X-Requested-With":  "XMLHttpRequest",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _r(v, d: int = 2):
    try:
        return round(float(v), d) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct_chg(curr, prev):
    try:
        if curr and prev and float(prev) != 0:
            return _r((float(curr) - float(prev)) / float(prev) * 100, 2)
    except (TypeError, ValueError):
        pass
    return None


def _last_two_closes(ticker: str, fallbacks: list[str] | None = None) -> tuple[float | None, float | None]:
    """Return (current_close, prev_close) for a yfinance ticker. Tries fallbacks on failure."""
    for t in [ticker] + (fallbacks or []):
        try:
            data = yf.download(t, period="5d", interval="1d",
                               auto_adjust=True, progress=False)
            if data.empty:
                continue
            closes = (
                data["Close"][t].dropna()
                if isinstance(data.columns, pd.MultiIndex)
                else data["Close"].dropna()
            )
            if len(closes) >= 2:
                return float(closes.iloc[-1]), float(closes.iloc[-2])
        except Exception:
            continue
    return None, None


# ── Section A: IDR + Commodities (yfinance) ───────────────────────────────────

def fetch_section_a() -> dict:
    print("  [A] IDR + commodities (yfinance)...")

    def _commodity(ticker: str, fallbacks: list[str] | None = None,
                   unit: str = "", impacts: list[str] | None = None) -> dict:
        curr, prev = _last_two_closes(ticker, fallbacks)
        return {
            "price":   _r(curr, 2),
            "chg_pct": _pct_chg(curr, prev),
            "unit":    unit,
            "impacts": impacts or [],
        }

    idr_curr, idr_prev = _last_two_closes("IDR=X")

    return {
        "idr": {
            "rate":    _r(idr_curr, 2),
            "chg_pct": _pct_chg(idr_curr, idr_prev),
        },
        "commodities": {
            "coal":    _commodity("COAL",    [],         "USD/shr (ETF)", COAL_STOCKS),
            "cpo":     _commodity("AALI.JK", [],         "IDR (proxy)",   CPO_STOCKS),
            "nickel":  _commodity("INCO.JK", [],         "IDR (proxy)",   NICKEL_STOCKS),
            "copper":  _commodity("HG=F",    [],         "USD/lb",        []),
        },
    }


# ── Section B: IDX Session Summary ───────────────────────────────────────────

def fetch_section_b() -> dict:
    print("  [B] IDX session summary...")
    result: dict = {
        "jkse":               None,
        "lq45":               None,
        "prior_regime":       "UNKNOWN",
        "top_gainers":        [],
        "top_losers":         [],
        "sector_performance": [],
        "snapshot_at":        None,
    }

    # Load latest idx_lq45 snapshot for regime / movers / sectors
    snap_path = SNAPSHOT_DIR / "idx_lq45_latest.json"
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text())
            result["prior_regime"]       = snap.get("regime", "UNKNOWN")
            result["top_gainers"]        = snap.get("idx_top_gainers", [])[:5]
            result["top_losers"]         = snap.get("idx_top_losers",  [])[:5]
            result["sector_performance"] = snap.get("sectors_ranked",  [])
            result["snapshot_at"]        = snap.get("fetched_at")
        except Exception as e:
            print(f"    Snapshot load failed: {e}")

    # Quick yfinance refresh for JKSE / LQ45 closes
    try:
        data = yf.download(["^JKSE", "^JKLQ45"], period="5d", interval="1d",
                           auto_adjust=True, progress=False)
        if not data.empty:
            for ticker, key in [("^JKSE", "jkse"), ("^JKLQ45", "lq45")]:
                try:
                    closes = (
                        data["Close"][ticker].dropna()
                        if isinstance(data.columns, pd.MultiIndex)
                        else data["Close"].dropna()
                    )
                    if len(closes) >= 2:
                        curr = float(closes.iloc[-1])
                        prev = float(closes.iloc[-2])
                        result[key] = {
                            "close":   _r(curr, 2),
                            "chg_pct": _pct_chg(curr, prev),
                        }
                except Exception:
                    pass
    except Exception as e:
        print(f"    yfinance indices failed: {e}")

    return result


# ── Section C: Foreign Flow (IDX API) ────────────────────────────────────────

def fetch_section_c() -> dict | None:
    print("  [C] Foreign flow (IDX API)...")
    endpoints = [
        "https://idx.co.id/primary/TradingSummary/GetForeignFlow?start=0&length=1",
        "https://idx.co.id/umbraco/Surface/StockData/GetForeignFlow",
    ]
    for url in endpoints:
        try:
            resp = cf_requests.get(url, impersonate="chrome124",
                                   headers=_IDX_HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            data    = resp.json()
            records = data.get("data", data) if isinstance(data, dict) else data
            first   = records[0] if isinstance(records, list) and records else records
            if isinstance(first, dict):
                net = (first.get("NetBuyValue")
                       or first.get("NetForeign")
                       or first.get("net"))
                if net is not None:
                    net_val = float(net)
                    return {
                        "net_value_rp_bn": _r(net_val / 1e9, 2),
                        "direction":       "NET_BUY" if net_val >= 0 else "NET_SELL",
                    }
        except Exception:
            continue

    print("    Foreign flow unavailable — skipping")
    return None


# ── Section D: IDX Corporate Announcements ────────────────────────────────────

def fetch_section_d(date_from: str | None = None) -> list[dict]:
    print("  [D] IDX corporate announcements...")
    today_str = date.today().strftime("%Y-%m-%d")
    url = "https://idx.co.id/umbraco/Surface/Announcement/GetAnnouncement"
    params = {
        "start":            0,
        "length":           20,
        "indexFrom":        "",
        "indexTo":          "",
        "aannouncementType": "",
        "entryDateFrom":    date_from or today_str,
        "entryDateTo":      today_str,
    }
    try:
        resp = cf_requests.get(url, params=params, headers=_IDX_HEADERS,
                               impersonate="chrome124", timeout=15)
        resp.raise_for_status()
        records = resp.json().get("data", [])

        results = []
        for r in records:
            title    = r.get("Title") or r.get("AnnouncementTitle") or ""
            ann_type = r.get("AnnouncementType") or r.get("Type") or ""
            text     = (title + " " + ann_type).lower()
            if not any(kw in text for kw in ANNOUNCEMENT_KEYWORDS):
                continue
            results.append({
                "company_code":      r.get("Emiten") or r.get("StockCode", ""),
                "company_name":      r.get("EmitenName") or r.get("CompanyName", ""),
                "announcement_type": ann_type,
                "title":             title,
                "date":              r.get("EntryDate", today_str),
            })

        print(f"    {len(results)} relevant announcements")
        return results[:10]

    except Exception as e:
        print(f"    Announcements failed: {e}")
        return []


# ── Section E: Stock News (Finnhub) ──────────────────────────────────────────

def fetch_section_e(news_window_hours: int = 24) -> list[dict]:
    print("  [E] Stock news (Finnhub)...")
    token = os.getenv("FINNHUB_API_KEY", "")
    if not token:
        return []

    now_utc   = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    yest_str  = (now_utc - timedelta(hours=news_window_hours)).strftime("%Y-%m-%d")
    cutoff    = now_utc - timedelta(hours=news_window_hours)

    articles: list[dict] = []
    for ticker in TIER1_TICKERS + TIER2_TICKERS:
        try:
            resp = std_requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": f"{ticker}.JK", "from": yest_str,
                        "to": today_str, "token": token},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            count = 0
            for item in resp.json():
                if count >= 2:
                    break
                ts  = item.get("datetime", 0)
                pub = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
                if pub and pub < cutoff:
                    continue
                articles.append({
                    "ticker":   ticker,
                    "headline": item.get("headline", ""),
                    "source":   item.get("source", ""),
                    "datetime": ts,
                    "summary":  (item.get("summary") or "")[:300],
                    "url":      item.get("url", ""),
                })
                count += 1
        except Exception:
            pass
        time.sleep(0.2)

    articles.sort(key=lambda x: x["datetime"], reverse=True)
    print(f"    {len(articles)} stock news articles")
    return articles[:15]


# ── Section F: Macro News (Finnhub) ──────────────────────────────────────────

def fetch_section_f(news_window_hours: int = 24) -> list[dict]:
    print("  [F] Macro news (Finnhub)...")
    token = os.getenv("FINNHUB_API_KEY", "")
    if not token:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=news_window_hours)
    try:
        resp = std_requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": token},
            timeout=15,
        )
        resp.raise_for_status()
        results: list[dict] = []
        for item in resp.json():
            ts  = item.get("datetime", 0)
            pub = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            if pub and pub < cutoff:
                continue
            text = ((item.get("headline") or "") + " " +
                    (item.get("summary") or "")).lower()
            if not any(kw in text for kw in MACRO_KEYWORDS):
                continue
            results.append({
                "headline": item.get("headline", ""),
                "source":   item.get("source", ""),
                "datetime": ts,
                "summary":  (item.get("summary") or "")[:300],
                "url":      item.get("url", ""),
            })
        results.sort(key=lambda x: x["datetime"], reverse=True)
        print(f"    {len(results)} macro news articles (filtered)")
        return results[:8]
    except Exception as e:
        print(f"    Macro news failed: {e}")
        return []


# ── Section G: RSS News ───────────────────────────────────────────────────────

_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def fetch_rss_news(news_window_hours: int = 24, max_per_feed: int = 8) -> list[dict]:
    print("  [G] RSS news (Antara, CNBC ID, Detik, Investing)...")
    cutoff = datetime.now(WIB) - timedelta(hours=news_window_hours)
    combined: list[dict] = []

    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url, request_headers=_RSS_HEADERS)
            if not feed.entries or feed.bozo:
                continue
            count = 0
            for entry in feed.entries:
                if count >= max_per_feed:
                    break
                title   = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                text    = (title + " " + summary).lower()
                if not any(kw in text for kw in RSS_KEYWORDS):
                    continue
                # Parse published date
                pub = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        import calendar
                        ts  = calendar.timegm(entry.published_parsed)
                        pub = datetime.fromtimestamp(ts, tz=WIB)
                    except Exception:
                        pass
                if pub and pub < cutoff:
                    continue
                combined.append({
                    "source":    source_name,
                    "title":     title,
                    "link":      entry.get("link", ""),
                    "published": pub.strftime("%d-%m-%Y %H:%M WIB") if pub else "",
                    "published_ts": pub.timestamp() if pub else 0,
                    "summary":   summary[:300],
                })
                count += 1
        except Exception as e:
            print(f"    RSS {source_name} failed: {e}")

    combined.sort(key=lambda x: x["published_ts"], reverse=True)
    result = combined[:15]
    print(f"    {len(result)} RSS articles")
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_idx_news() -> dict:
    now_wib    = datetime.now(WIB)
    fetched_at = now_wib.strftime("%d-%m-%Y %H:%M WIB")
    today_str  = now_wib.strftime("%Y-%m-%d")

    is_weekend = now_wib.weekday() >= 5
    if is_weekend:
        news_window_hours = 72
        date_from = (now_wib - timedelta(days=3)).strftime("%Y-%m-%d")
    else:
        news_window_hours = 24
        date_from = (now_wib - timedelta(days=1)).strftime("%Y-%m-%d")

    section_a = fetch_section_a()
    section_b = fetch_section_b()
    section_c = fetch_section_c()
    section_d = fetch_section_d(date_from=date_from)
    section_e = fetch_section_e(news_window_hours=news_window_hours)
    section_f = fetch_section_f(news_window_hours=news_window_hours)
    section_g = fetch_rss_news(news_window_hours=news_window_hours)

    snapshot = {
        "fetched_at":          fetched_at,
        "date":                today_str,
        "is_weekend":          is_weekend,
        "news_window_hours":   news_window_hours,
        "idr":                 section_a["idr"],
        "commodities":         section_a["commodities"],
        "idx_session_summary": section_b,
        "foreign_flow":        section_c,
        "announcements":       section_d,
        "stock_news":          section_e,
        "macro_news":          section_f,
        "rss_news":            section_g,
    }

    payload = json.dumps(snapshot, indent=2)
    (SNAPSHOT_DIR / "idx_news_latest.json").write_text(payload)
    print("  Saved → idx_news_latest.json")

    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_idx_news()
    pprint.pprint({k: v for k, v in result.items()
                   if k not in ("stock_news", "macro_news", "announcements")})
    print(f"\nStock news: {len(result['stock_news'])}")
    print(f"Macro news: {len(result['macro_news'])}")
    print(f"Announcements: {len(result['announcements'])}")
