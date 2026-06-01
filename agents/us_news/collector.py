from __future__ import annotations

import calendar as cal_mod
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import feedparser
import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

UTC = timezone.utc

SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLV":  "Healthcare",
    "XLI":  "Industrials",
    "XLY":  "Consumer Disc",
    "XLP":  "Consumer Staples",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
    "XLC":  "Communications",
}

RSS_FEEDS = {
    "reuters":     "https://feeds.reuters.com/reuters/businessNews",
    "cnbc":        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
}

SIMILARITY_THRESHOLD = 0.72


# ── Generic helpers ───────────────────────────────────────────────────────────

def _r(v, d: int = 2):
    try:
        return round(float(v), d) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct(curr, prev):
    try:
        c, p = float(curr), float(prev)
        if p != 0:
            return _r((c - p) / p * 100, 2)
    except (TypeError, ValueError):
        pass
    return None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _deduplicate(items: list[dict]) -> list[dict]:
    unique: list[dict] = []
    for item in items:
        hl = item.get("headline", "")
        replaced = False
        for i, kept in enumerate(unique):
            if _similarity(hl, kept.get("headline", "")) >= SIMILARITY_THRESHOLD:
                if len(item.get("summary", "")) > len(kept.get("summary", "")):
                    unique[i] = item
                replaced = True
                break
        if not replaced:
            unique.append(item)
    return unique


# ── yfinance helpers (mirrored from us_ta collector) ─────────────────────────

def _get_sp500_tickers() -> list[str]:
    try:
        req = urllib.request.Request(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read()
        tables = pd.read_html(html)
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        print(f"  Loaded {len(tickers)} S&P 500 tickers from Wikipedia")
        return tickers
    except Exception as e:
        print(f"  Wikipedia fetch failed ({e}), skipping movers screen")
        return []


def _extract(data: pd.DataFrame, ticker: str) -> tuple[pd.Series, pd.Series]:
    try:
        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"][ticker].dropna()
            vol = (
                data["Volume"][ticker].dropna()
                if "Volume" in data.columns.get_level_values(0)
                else pd.Series(dtype=float)
            )
        else:
            close = data["Close"].dropna()
            vol = data["Volume"].dropna() if "Volume" in data.columns else pd.Series(dtype=float)
        return close, vol
    except Exception:
        return pd.Series(dtype=float), pd.Series(dtype=float)


def _metrics(close: pd.Series, vol: pd.Series) -> dict | None:
    if len(close) < 2:
        return None
    c, c_prev = float(close.iloc[-1]), float(close.iloc[-2])
    day_chg = round((c - c_prev) / c_prev * 100, 2)

    v_today = float(vol.iloc[-1]) if len(vol) > 0 else None
    v_20d   = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else None
    vol_ratio = round(v_today / v_20d, 2) if (v_today and v_20d and v_20d > 0) else None

    # 5-day change
    chg_5d = round((c - float(close.iloc[-6])) / float(close.iloc[-6]) * 100, 2) if len(close) >= 6 else None

    return {
        "close":       round(c, 2),
        "day_chg_pct": day_chg,
        "chg_5d_pct":  chg_5d,
        "vol_ratio":   vol_ratio,
    }


def _download_bulk(tickers: list[str]) -> pd.DataFrame:
    try:
        return yf.download(
            tickers=tickers,
            period="1mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception:
        return pd.DataFrame()


# ── Section A: Indices + VIX + DXY ───────────────────────────────────────────

def fetch_section_a() -> dict:
    print("  [A] Indices + VIX + DXY...")
    INDEX_MAP = {
        "^GSPC":  "sp500",
        "^IXIC":  "nasdaq",
        "^DJI":   "dow",
        "^VIX":   "vix",
        "DX-Y.NYB": "dxy",
    }
    tickers = list(INDEX_MAP.keys())
    result: dict = {}

    try:
        data = yf.download(tickers, period="10d", interval="1d",
                           auto_adjust=True, progress=False)
        for yf_tk, key in INDEX_MAP.items():
            try:
                close, _ = _extract(data, yf_tk)
                if len(close) < 2:
                    continue
                c, p = float(close.iloc[-1]), float(close.iloc[-2])
                entry: dict = {
                    "price":      _r(c, 2),
                    "chg_1d_pct": _pct(c, p),
                }
                if len(close) >= 6:
                    entry["chg_5d_pct"] = _pct(c, float(close.iloc[-6]))
                result[key] = entry
            except Exception:
                pass
    except Exception as e:
        print(f"    Index download failed: {e}")

    # Fear & Greed proxy from VIX
    vix_level = (result.get("vix") or {}).get("price")
    if vix_level is not None:
        if vix_level > 25:
            fg = "Fear"
        elif vix_level >= 18:
            fg = "Neutral"
        else:
            fg = "Greed"
        result["fear_greed_proxy"] = {"label": fg, "vix_basis": _r(vix_level, 2)}

    return result


# ── Section B: Sector performance ────────────────────────────────────────────

def fetch_section_b() -> dict:
    print("  [B] Sector ETFs...")
    tickers = list(SECTOR_ETFS.keys())
    sectors: list[dict] = []

    try:
        data = yf.download(tickers, period="5d", interval="1d",
                           auto_adjust=True, progress=False)
        for etf, name in SECTOR_ETFS.items():
            try:
                close, _ = _extract(data, etf)
                if len(close) < 2:
                    continue
                c, p = float(close.iloc[-1]), float(close.iloc[-2])
                sectors.append({
                    "ticker":      etf,
                    "name":        name,
                    "chg_1d_pct":  _pct(c, p),
                })
            except Exception:
                pass
    except Exception as e:
        print(f"    Sector download failed: {e}")

    sectors.sort(key=lambda x: (x.get("chg_1d_pct") or 0), reverse=True)
    return {
        "all":    sectors,
        "top3":   sectors[:3],
        "bottom3": sectors[-3:][::-1] if len(sectors) >= 3 else [],
    }


# ── Section C: Top movers (S&P 500) ──────────────────────────────────────────

def fetch_section_c() -> dict:
    print("  [C] S&P 500 top movers (screening ~500 tickers)...")
    sp500 = _get_sp500_tickers()
    if not sp500:
        return {"gainers": [], "losers": []}

    print(f"    Downloading {len(sp500)} tickers (1mo)... this takes ~60s")
    data = _download_bulk(sp500)

    metrics: dict[str, dict] = {}
    for ticker in sp500:
        m = _metrics(*_extract(data, ticker))
        if m and m.get("vol_ratio") is not None:
            metrics[ticker] = m

    # Filter for conviction moves (vol_ratio >= 1.5)
    conviction = [(t, m) for t, m in metrics.items() if (m.get("vol_ratio") or 0) >= 1.5]
    gainers = sorted(conviction, key=lambda x: x[1]["day_chg_pct"], reverse=True)[:10]
    losers  = sorted(conviction, key=lambda x: x[1]["day_chg_pct"])[:10]

    def _fmt(t, m):
        return {"ticker": t, "close": m["close"], "day_chg_pct": m["day_chg_pct"],
                "vol_ratio": m["vol_ratio"]}

    return {
        "gainers": [_fmt(t, m) for t, m in gainers],
        "losers":  [_fmt(t, m) for t, m in losers],
    }


# ── Section D: Earnings highlights (Finnhub) ─────────────────────────────────

def fetch_section_d() -> list[dict]:
    print("  [D] Earnings highlights (Finnhub)...")
    token = os.getenv("FINNHUB_API_KEY", "")
    if not token:
        return []

    now_utc   = datetime.now(UTC)
    today_str = now_utc.strftime("%Y-%m-%d")
    yest_str  = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": yest_str, "to": today_str, "token": token},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("earningsCalendar", [])

        results = []
        for e in raw:
            eps_actual  = e.get("epsActual")
            eps_est     = e.get("epsEstimate")
            rev_actual  = e.get("revenueActual")
            rev_est     = e.get("revenueEstimate")

            # Only include if actual data is present
            if eps_actual is None and rev_actual is None:
                continue

            beat_miss = "inline"
            if eps_actual is not None and eps_est is not None:
                if eps_actual > eps_est * 1.02:
                    beat_miss = "beat"
                elif eps_actual < eps_est * 0.98:
                    beat_miss = "miss"

            results.append({
                "ticker":       e.get("symbol", ""),
                "company":      e.get("company", ""),
                "date":         e.get("date", today_str),
                "eps_actual":   _r(eps_actual, 2),
                "eps_estimate": _r(eps_est, 2),
                "rev_actual":   _r(rev_actual, 0) if rev_actual else None,
                "rev_estimate": _r(rev_est, 0) if rev_est else None,
                "beat_miss":    beat_miss,
            })

        print(f"    {len(results)} earnings reports with actual data")
        return results[:15]
    except Exception as e:
        print(f"    Earnings fetch failed: {e}")
        return []


# ── Section E: Market news (Finnhub + RSS) ───────────────────────────────────

def _fetch_finnhub_news(token: str, cutoff: datetime) -> list[dict]:
    items: list[dict] = []
    if not token:
        return items
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": token},
            timeout=15,
        )
        resp.raise_for_status()
        for a in resp.json():
            ts  = a.get("datetime", 0)
            pub = datetime.fromtimestamp(ts, tz=UTC) if ts else None
            if pub and pub < cutoff:
                continue
            items.append({
                "headline":   a.get("headline", ""),
                "summary":    (a.get("summary") or "")[:300],
                "source":     a.get("source", "Finnhub"),
                "published_ts": ts,
                "origin":     "finnhub",
            })
    except Exception as e:
        print(f"    Finnhub news failed: {e}")
    return items


def _fetch_rss_news(cutoff: datetime) -> list[dict]:
    items: list[dict] = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url, request_headers=headers)
            if not feed.entries or feed.bozo:
                continue
            for entry in feed.entries[:30]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                pub     = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        ts  = cal_mod.timegm(entry.published_parsed)
                        pub = datetime.fromtimestamp(ts, tz=UTC)
                    except Exception:
                        pass
                if pub and pub < cutoff:
                    continue
                items.append({
                    "headline":     title,
                    "summary":      summary[:300],
                    "source":       source,
                    "published_ts": pub.timestamp() if pub else 0,
                    "origin":       "rss",
                })
        except Exception as e:
            print(f"    RSS {source} failed: {e}")
    return items


def fetch_section_e() -> list[dict]:
    print("  [E] Market news (Finnhub + RSS)...")
    token  = os.getenv("FINNHUB_API_KEY", "")
    cutoff = datetime.now(UTC) - timedelta(hours=24)

    all_items = _fetch_finnhub_news(token, cutoff) + _fetch_rss_news(cutoff)
    all_items = [i for i in all_items if i.get("headline")]
    deduped   = _deduplicate(all_items)
    deduped.sort(key=lambda x: x.get("published_ts", 0), reverse=True)
    result = deduped[:8]
    print(f"    {len(result)} news items after dedup")
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_us_news() -> dict:
    now_utc    = datetime.now(UTC)
    fetched_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    today_str  = now_utc.strftime("%Y-%m-%d")

    section_a = fetch_section_a()
    section_b = fetch_section_b()
    section_c = fetch_section_c()
    section_d = fetch_section_d()
    section_e = fetch_section_e()

    snapshot = {
        "fetched_at":   fetched_at,
        "date":         today_str,
        "indices":      section_a,
        "sectors":      section_b,
        "top_movers":   section_c,
        "earnings":     section_d,
        "news":         section_e,
    }

    payload = json.dumps(snapshot, indent=2)
    (SNAPSHOT_DIR / "us_news_latest.json").write_text(payload)
    print("  Saved → us_news_latest.json")
    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_us_news()
    pprint.pprint({k: v for k, v in result.items() if k not in ("news", "top_movers")})
    print(f"\nTop movers: {len(result['top_movers'].get('gainers', []))} gainers, "
          f"{len(result['top_movers'].get('losers', []))} losers")
    print(f"Earnings: {len(result['earnings'])}")
    print(f"News: {len(result['news'])}")
