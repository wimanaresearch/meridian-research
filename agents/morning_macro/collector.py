from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

WIB = timezone(timedelta(hours=7))

# Single batch download — all market tickers
BATCH_TICKERS = [
    # A: Rates / yields
    "^IRX", "^FVX", "^TNX", "^TYX",
    # A: Bond ETFs
    "TLT", "HYG", "LQD",
    # B: Dollar & vol
    "DX-Y.NYB", "^VIX", "^VXN",
    # C: Commodities
    "GC=F", "CL=F", "BZ=F", "NG=F", "HG=F", "SI=F",
    # D: US futures
    "ES=F", "NQ=F", "YM=F", "RTY=F",
    # E: Crypto (yfinance leg)
    "BTC-USD", "ETH-USD",
    # F: Asia session
    "^N225", "^HSI", "^STI", "^KS11", "^AXJO", "^JKSE", "^TWII", "000001.SS",
    # F: FX
    "IDR=X", "CNY=X", "JPY=X",
]

ASIA_LABELS = {
    "^N225":  "🇯🇵 Nikkei",
    "^HSI":   "🇭🇰 HSI",
    "^STI":   "🇸🇬 STI",
    "^KS11":  "🇰🇷 KOSPI",
    "^AXJO":  "🇦🇺 ASX",
    "^JKSE":  "🇮🇩 IHSG",
    "^TWII":  "🇹🇼 Taiwan",
    "000001.SS": "🇨🇳 Shanghai",
}


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _download_batch(tickers: list[str]) -> pd.DataFrame:
    try:
        return yf.download(
            tickers=tickers,
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"  Batch download error: {e}")
        return pd.DataFrame()


def _cp(data: pd.DataFrame, ticker: str) -> tuple[float | None, float | None]:
    """Return (current_close, prev_close) from batch DataFrame."""
    try:
        if data.empty:
            return None, None
        if isinstance(data.columns, pd.MultiIndex):
            closes = data["Close"][ticker].dropna()
        else:
            closes = data["Close"].dropna()
        if len(closes) == 0:
            return None, None
        c = float(closes.iloc[-1])
        p = float(closes.iloc[-2]) if len(closes) >= 2 else None
        return c, p
    except Exception:
        return None, None


def _chg_pct(c: float | None, p: float | None) -> float | None:
    if c is None or p is None or p == 0:
        return None
    return round((c - p) / abs(p) * 100, 2)


def _chg_bps(c: float | None, p: float | None) -> float | None:
    """Yield level change in basis points (yield stored as % e.g. 4.32)."""
    if c is None or p is None:
        return None
    return round((c - p) * 100, 1)


def _r(v: float | None, d: int = 2) -> float | None:
    return round(v, d) if v is not None else None


def _fmt_mcap(usd: float | None) -> str | None:
    if usd is None:
        return None
    if usd >= 1e12:
        return f"${usd / 1e12:.2f}T"
    return f"${usd / 1e9:.2f}B"


# ── Section A: Rates & Bonds ──────────────────────────────────────────────────

def _build_rates_bonds(data: pd.DataFrame) -> tuple[dict, dict]:
    rates: dict = {}
    for ticker, key in [
        ("^IRX", "us3m"), ("^FVX", "us5y"),
        ("^TNX", "us10y"), ("^TYX", "us30y"),
    ]:
        c, p = _cp(data, ticker)
        rates[key] = {"level": _r(c, 3), "chg_bps": _chg_bps(c, p)}

    etfs: dict = {}
    for ticker, key in [("TLT", "tlt"), ("HYG", "hyg"), ("LQD", "lqd")]:
        c, p = _cp(data, ticker)
        etfs[key] = {"price": _r(c, 2), "chg_pct": _chg_pct(c, p)}

    # Yield curve spreads (^IRX used as 2Y/3M proxy)
    us3m  = rates["us3m"]["level"]
    us10y = rates["us10y"]["level"]

    def _spread(short: float | None, long: float | None) -> tuple[float | None, str | None]:
        if short is None or long is None:
            return None, None
        diff = long - short
        return round(diff * 100, 1), ("NORMAL" if diff >= 0 else "INVERTED")

    s2y10y_bps, s2y10y_label = _spread(us3m, us10y)
    s3m10y_bps, s3m10y_label = _spread(us3m, us10y)  # same proxy

    yield_curve = {
        "spread_2y10y": {"bps": s2y10y_bps, "label": s2y10y_label},
        "spread_3m10y": {"bps": s3m10y_bps, "label": s3m10y_label},
    }

    return {**rates, **etfs}, yield_curve


# ── Section B: Dollar & Volatility ───────────────────────────────────────────

def _build_dollar_vol(data: pd.DataFrame) -> dict:
    result: dict = {}
    for ticker, key in [
        ("DX-Y.NYB", "dxy"), ("^VIX", "vix"), ("^VXN", "vxn"),
    ]:
        c, p = _cp(data, ticker)
        result[key] = {"level": _r(c, 2), "chg_pct": _chg_pct(c, p)}

    # ^MOVE often absent on yfinance — fetch separately, fail gracefully
    try:
        hist = yf.Ticker("^MOVE").history(period="2d")
        if not hist.empty:
            closes = hist["Close"].dropna()
            c = float(closes.iloc[-1]) if len(closes) >= 1 else None
            p = float(closes.iloc[-2]) if len(closes) >= 2 else None
            result["move"] = {"level": _r(c, 2), "chg_pct": _chg_pct(c, p)}
        else:
            result["move"] = {"level": None, "chg_pct": None}
    except Exception:
        result["move"] = {"level": None, "chg_pct": None}

    return result


# ── Section C: Commodities ────────────────────────────────────────────────────

def _build_commodities(data: pd.DataFrame) -> dict:
    result: dict = {}
    for ticker, key in [
        ("GC=F", "gold"),   ("CL=F", "wti"),    ("BZ=F", "brent"),
        ("NG=F", "natgas"), ("HG=F", "copper"),  ("SI=F", "silver"),
    ]:
        c, p = _cp(data, ticker)
        result[key] = {"price": _r(c, 2), "chg_pct": _chg_pct(c, p)}
    return result


# ── Section D: US Futures ─────────────────────────────────────────────────────

def _build_us_futures(data: pd.DataFrame) -> dict:
    result: dict = {}
    for ticker, key in [
        ("ES=F", "es"), ("NQ=F", "nq"), ("YM=F", "ym"), ("RTY=F", "rty"),
    ]:
        c, p = _cp(data, ticker)
        result[key] = {"price": _r(c, 2), "chg_pct": _chg_pct(c, p)}
    return result


# ── Section E: Crypto ─────────────────────────────────────────────────────────

def _build_crypto(data: pd.DataFrame) -> dict:
    btc_c, btc_p = _cp(data, "BTC-USD")
    eth_c, eth_p = _cp(data, "ETH-USD")

    result: dict = {
        "btc": {"price": _r(btc_c, 0), "chg_pct": _chg_pct(btc_c, btc_p)},
        "eth": {"price": _r(eth_c, 0), "chg_pct": _chg_pct(eth_c, eth_p)},
        "total_mcap":       None,
        "total2_mcap":      None,
        "total3_mcap":      None,
        "btc_dominance":    None,
        "eth_dominance":    None,
        "mcap_chg_24h_pct": None,
    }

    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=15,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        gd = resp.json().get("data", {})

        total    = gd.get("total_market_cap", {}).get("usd")
        btc_dom  = gd.get("market_cap_percentage", {}).get("btc")
        eth_dom  = gd.get("market_cap_percentage", {}).get("eth")
        mcap_chg = gd.get("market_cap_change_percentage_24h_usd")

        total2 = (total * (1 - btc_dom / 100)) if (total and btc_dom is not None) else None
        total3 = (
            total * (1 - btc_dom / 100 - eth_dom / 100)
            if (total and btc_dom is not None and eth_dom is not None)
            else None
        )

        result.update({
            "total_mcap":       _fmt_mcap(total),
            "total2_mcap":      _fmt_mcap(total2),
            "total3_mcap":      _fmt_mcap(total3),
            "btc_dominance":    _r(btc_dom, 1),
            "eth_dominance":    _r(eth_dom, 1),
            "mcap_chg_24h_pct": _r(mcap_chg, 2),
        })
    except Exception as e:
        print(f"  CoinGecko failed: {e}")

    return result


# ── Section F: Asia Session + FX ─────────────────────────────────────────────

def _build_asia(data: pd.DataFrame) -> dict:
    result: dict = {}
    for ticker, label in ASIA_LABELS.items():
        c, p = _cp(data, ticker)
        result[ticker] = {
            "label":    label,
            "close":    _r(c, 2),
            "chg_pct":  _chg_pct(c, p),
            "status":   "PREV_CLOSE",
        }
    return result


def _build_fx(data: pd.DataFrame) -> dict:
    result: dict = {}
    for ticker, key in [("IDR=X", "idr_usd"), ("CNY=X", "cny_usd"), ("JPY=X", "jpy_usd")]:
        c, p = _cp(data, ticker)
        result[key] = {"rate": _r(c, 2), "chg_pct": _chg_pct(c, p)}
    return result


# ── Section G: Economic Calendar ──────────────────────────────────────────────

_CALENDAR_COUNTRIES = {"us", "eu", "uk", "jp", "cn", "au", "ca", "de", "fr", "ch", "nz"}


def _fetch_economic_calendar(today_str: str) -> list[dict]:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return []
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": today_str, "to": today_str, "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json().get("economicCalendar", [])

        # Filter to macro-relevant countries only
        relevant = [
            e for e in events
            if str(e.get("country", "")).lower() in _CALENDAR_COUNTRIES
        ]

        high = [e for e in relevant if str(e.get("impact", "")).lower() in ("high", "3")]
        if high:
            return high[:8]

        medium = [e for e in relevant if str(e.get("impact", "")).lower() in ("medium", "2")]
        return medium[:5]
    except Exception:
        return []


# ── Section H: News ───────────────────────────────────────────────────────────

def _fetch_news_category(
    category: str,
    cutoff_ts: float,
    limit: int,
    api_key: str,
) -> list[dict]:
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": category, "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        articles = resp.json()
        recent = [
            {
                "headline": a.get("headline", ""),
                "source":   a.get("source", ""),
                "datetime": a.get("datetime", 0),
                "summary":  a.get("summary", ""),
            }
            for a in articles
            if a.get("datetime", 0) >= cutoff_ts and a.get("headline")
        ]
        return recent[:limit]
    except Exception:
        return []


def _fetch_news(cutoff_ts: float) -> list[dict]:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return []

    general = _fetch_news_category("general", cutoff_ts, 12, api_key)
    time.sleep(0.5)
    forex   = _fetch_news_category("forex",   cutoff_ts,  5, api_key)

    seen: set[str] = set()
    merged: list[dict] = []
    for item in general + forex:
        key = item["headline"].strip().lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)

    merged.sort(key=lambda x: x["datetime"], reverse=True)
    return merged


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_morning_macro() -> dict:
    now_utc  = datetime.now(timezone.utc)
    now_wib  = now_utc.astimezone(WIB)
    today_str = now_wib.strftime("%Y-%m-%d")
    fetched_at = now_wib.strftime("%d-%m-%Y %H:%M WIB")
    cutoff_ts  = (now_utc - timedelta(hours=12)).timestamp()

    print(f"  Downloading {len(BATCH_TICKERS)} market tickers...")
    data = _download_batch(BATCH_TICKERS)

    print("  [A] Rates & bonds...")
    rates_bonds, yield_curve = _build_rates_bonds(data)

    print("  [B] Dollar & volatility...")
    dollar_vol = _build_dollar_vol(data)

    print("  [C] Commodities...")
    commodities = _build_commodities(data)

    print("  [D] US futures...")
    us_futures = _build_us_futures(data)

    print("  [E] Crypto + CoinGecko global...")
    crypto = _build_crypto(data)

    print("  [F] Asia session + FX...")
    asia = _build_asia(data)
    fx   = _build_fx(data)

    print("  [G] Economic calendar...")
    economic_calendar = _fetch_economic_calendar(today_str)

    print("  [H] News headlines...")
    news = _fetch_news(cutoff_ts)

    snapshot = {
        "fetched_at":        fetched_at,
        "date":              today_str,
        "rates_bonds":       rates_bonds,
        "yield_curve":       yield_curve,
        "dollar_vol":        dollar_vol,
        "commodities":       commodities,
        "us_futures":        us_futures,
        "crypto":            crypto,
        "asia":              asia,
        "fx":                fx,
        "economic_calendar": economic_calendar,
        "news":              news,
    }

    payload = json.dumps(snapshot, indent=2)
    (SNAPSHOT_DIR / "morning_macro_latest.json").write_text(payload)
    print("  Saved → morning_macro_latest.json")

    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_morning_macro()
    pprint.pprint({
        k: v for k, v in result.items()
        if k not in ("news", "economic_calendar")
    })
    print(f"\nCalendar events : {len(result['economic_calendar'])}")
    print(f"News articles   : {len(result['news'])}")
