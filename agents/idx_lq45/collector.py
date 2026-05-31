from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from curl_cffi import requests as cf_requests
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

# ── Tickers ───────────────────────────────────────────────────────────────────
# IHSG and LQ45 only — IDX30 and KOMPAS100 are fetched from the IDX API below

INDEX_TICKERS = {
    "^JKSE":   "ihsg",
    "^JKLQ45": "lq45",
}

LQ45_TICKERS = [
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BBTN.JK",
    "TLKM.JK", "GOTO.JK", "EMTK.JK",
    "ASII.JK", "UNTR.JK",
    "ADRO.JK", "PTBA.JK", "ITMG.JK", "INCO.JK", "ANTM.JK", "MDKA.JK",
    "UNVR.JK", "ICBP.JK", "MYOR.JK", "SIDO.JK",
    "SMGR.JK", "WIKA.JK", "WSKT.JK",
    "EXCL.JK", "ISAT.JK",
]

SECTOR_MAP: dict[str, list[str]] = {
    "Financials":  ["BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BBTN.JK"],
    "Telco":       ["TLKM.JK", "EXCL.JK", "ISAT.JK"],
    "Tech":        ["GOTO.JK", "EMTK.JK"],
    "Industrial":  ["ASII.JK", "UNTR.JK", "SMGR.JK", "WIKA.JK", "WSKT.JK"],
    "Commodities": ["ADRO.JK", "PTBA.JK", "ITMG.JK", "INCO.JK", "ANTM.JK", "MDKA.JK"],
    "Consumer":    ["UNVR.JK", "ICBP.JK", "MYOR.JK", "SIDO.JK"],
}

LQ45_NAMES: dict[str, str] = {
    "BBCA.JK": "BCA",             "BBRI.JK": "BRI",
    "BMRI.JK": "Mandiri",         "BBNI.JK": "BNI",
    "BBTN.JK": "BTN",             "TLKM.JK": "Telkom",
    "GOTO.JK": "GoTo",            "EMTK.JK": "Elang Mahkota",
    "ASII.JK": "Astra",           "UNTR.JK": "United Tractors",
    "ADRO.JK": "Adaro",           "PTBA.JK": "Bukit Asam",
    "ITMG.JK": "Indo Tambangraya","INCO.JK": "Vale Indonesia",
    "ANTM.JK": "Antam",           "MDKA.JK": "Merdeka Copper",
    "UNVR.JK": "Unilever",        "ICBP.JK": "Indofood CBP",
    "MYOR.JK": "Mayora",          "SIDO.JK": "Sido Muncul",
    "SMGR.JK": "Semen Indonesia", "WIKA.JK": "Wijaya Karya",
    "WSKT.JK": "Waskita Karya",   "EXCL.JK": "XL Axiata",
    "ISAT.JK": "Indosat",
}

# ── Number formatting ─────────────────────────────────────────────────────────

def format_number(value: float | int | None, decimals: int = 2) -> str | None:
    """Comma thousands separator, dot decimal.
    decimals=0 → whole number  e.g. 7850.0  → '7,850'
    decimals=2 → 2dp           e.g. 6127.38 → '6,127.38'
    """
    if value is None:
        return None
    if decimals == 0:
        return f"{int(round(float(value))):,}"
    return f"{float(value):,.{decimals}f}"


def _fmt_pct(value: float | None) -> str | None:
    """Format a percentage with explicit sign. e.g. 1.23 → '+1.23'  -0.45 → '-0.45'"""
    if value is None:
        return None
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}"


def _apply_formatting(snapshot: dict) -> None:
    """Format all display-facing numeric fields in-place.

    Stock prices (.JK): 0 decimals — whole Rupiah, comma thousands.
    Index levels, IDR/USD rate: 2 decimals, comma thousands.
    pct_change fields: raw float kept, companion *_fmt string added.
    All sorting is done before this is called — safe to convert in place.
    """
    # Indices — 2 decimal places
    for idx in snapshot.get("indices", {}).values():
        idx["close"] = format_number(idx.get("close"), decimals=2)
        idx["day_change_pct_fmt"] = _fmt_pct(idx.get("day_change_pct"))

    # IDR/USD — 2 decimal places
    fx = snapshot.get("idr_usd")
    if fx:
        fx["rate"] = format_number(fx.get("rate"), decimals=2)
        fx["day_change_pct_fmt"] = _fmt_pct(fx.get("day_change_pct"))

    # LQ45 stocks — close is whole Rupiah (0 decimals)
    for stock in snapshot.get("stocks", {}).values():
        stock["close"] = format_number(stock.get("close"), decimals=0)
        stock["volume_ratio"] = format_number(stock.get("volume_ratio"), decimals=2)
        stock["day_change_pct_fmt"] = _fmt_pct(stock.get("day_change_pct"))

    # LQ45 top movers
    for direction in ("gainers", "losers"):
        for m in snapshot.get("top_movers", {}).get(direction, []):
            m["volume_ratio"] = format_number(m.get("volume_ratio"), decimals=2)
            m["day_change_pct_fmt"] = _fmt_pct(m.get("day_change_pct"))

    # Setup candidates — key_level and close are stock prices (0 decimals)
    for s in snapshot.get("setup_candidates", []):
        s["close"] = format_number(s.get("close"), decimals=0)
        s["key_level"] = format_number(s.get("key_level"), decimals=0)
        s["volume_ratio"] = format_number(s.get("volume_ratio"), decimals=2)

    # IDX full-market movers — stock prices (0 decimals)
    for direction in ("idx_top_gainers", "idx_top_losers"):
        for m in snapshot.get(direction, []):
            m["close"] = format_number(m.get("close"), decimals=0)
            m["change"] = format_number(m.get("change"), decimals=0)
            m["pct_change_fmt"] = _fmt_pct(m.get("pct_change"))


# ── IDX API ───────────────────────────────────────────────────────────────────

_IDX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://idx.co.id/en/market-data/stocks-data/indices-data/",
    "Accept":     "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


_INDEX_SUMMARY_URL = (
    "https://idx.co.id/primary/TradingSummary/GetIndexSummary"
    "?start=0&length=100"
)
_index_summary_cache: dict | None = None   # fetched once per run, shared across calls


def _get_index_summary() -> list[dict]:
    """Fetch and cache all IDX index summaries for the current run."""
    global _index_summary_cache
    if _index_summary_cache is not None:
        return _index_summary_cache
    try:
        resp = cf_requests.get(
            _INDEX_SUMMARY_URL,
            impersonate="chrome124",
            timeout=15,
        )
        resp.raise_for_status()
        _index_summary_cache = resp.json().get("data", [])
        print(f"  IDX index summary: {len(_index_summary_cache)} indices loaded")
    except Exception as e:
        print(f"  IDX index summary fetch failed: {e}")
        _index_summary_cache = []
    return _index_summary_cache


def fetch_idx_index(index_code: str) -> dict | None:
    """
    Return EOD data for a single IDX index from the GetIndexSummary API.
    Fields: Close, Previous → computes day_change_pct.
    Returns {"close": float, "day_change_pct": float} or None on failure.
    """
    try:
        records = _get_index_summary()
        row = next((r for r in records if r.get("IndexCode") == index_code), None)
        if not row:
            print(f"  IDX {index_code}: not found in summary")
            return None

        close = float(row.get("Close") or 0)
        prev  = float(row.get("Previous") or 0)
        if close <= 0 or prev <= 0:
            print(f"  IDX {index_code}: invalid values (close={close}, prev={prev})")
            return None

        day_chg = round((close - prev) / prev * 100, 2)
        print(f"  IDX {index_code}: close={close:.3f}  chg={day_chg:+.2f}%")
        return {"close": round(close, 3), "day_change_pct": day_chg}

    except Exception as e:
        print(f"  IDX {index_code} lookup failed: {e}")
        return None


def fetch_idx_top_movers() -> dict:
    """
    Fetch all regular-board stocks from the IDX TradingSummary API.
    Uses curl_cffi to pass Cloudflare's browser fingerprint check.
    Returns top 5 gainers and top 5 losers by day % change.
    """
    url = (
        "https://idx.co.id/primary/TradingSummary/GetStockSummary"
        "?start=0&length=9999&code=&sector=&board=reguler"
    )
    try:
        resp = cf_requests.get(url, impersonate="chrome124", timeout=15)
        resp.raise_for_status()
        stocks = resp.json().get("data", [])

        filtered = []
        for s in stocks:
            close = s.get("Close") or 0
            prev  = s.get("Previous") or 0
            if close <= 0 or prev <= 0:
                continue
            pct = round((close - prev) / prev * 100, 2)
            if pct == 0:
                continue
            filtered.append({
                "code":       s.get("StockCode", ""),
                "name":       s.get("StockName", ""),
                "close":      close,
                "pct_change": pct,
                "change":     s.get("Change", 0),
            })

        filtered.sort(key=lambda s: s["pct_change"], reverse=True)
        gainers = filtered[:5]
        losers  = filtered[-5:][::-1]   # worst loser first

        print(f"  IDX API: {len(filtered)} active stocks → top 5 gainers, top 5 losers")
        return {"top_gainers": gainers, "top_losers": losers, "error": None}

    except Exception as e:
        print(f"  IDX movers API failed: {e}")
        return {"top_gainers": [], "top_losers": [], "error": str(e)}


# ── yfinance helpers ──────────────────────────────────────────────────────────

def _download(tickers: list[str]) -> pd.DataFrame:
    try:
        return yf.download(
            tickers=tickers,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"  Download failed: {e}")
        return pd.DataFrame()


def _extract(data: pd.DataFrame, ticker: str) -> tuple[pd.Series, pd.Series]:
    """Pull Close + Volume Series from a single- or multi-ticker DataFrame."""
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
    """Compute EOD technical metrics from price and volume series."""
    if len(close) < 2:
        return None

    c      = float(close.iloc[-1])
    c_prev = float(close.iloc[-2])
    day_chg = round((c - c_prev) / c_prev * 100, 2)

    v_today = float(vol.iloc[-1]) if len(vol) > 0 else None
    v_20d   = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else None
    vol_ratio = round(v_today / v_20d, 2) if (v_today and v_20d and v_20d > 0) else None

    n = len(close)
    ma50  = round(float(close.iloc[-50:].mean()), 2) if n >= 50 else None
    ma200 = round(float(close.iloc[-200:].mean()), 2) if n >= 200 else None
    vs_50d = round((c - ma50) / ma50 * 100, 2) if ma50 else None

    h52w = round(float(close.iloc[-252:].max()), 2) if n >= 20 else None
    dist_52w = round((c - h52w) / h52w * 100, 2) if h52w else None

    crossed_50d = False
    if n >= 51 and ma50:
        ma50_yd = float(close.iloc[-51:-1].mean())
        crossed_50d = (c_prev < ma50_yd) and (c > ma50)

    return {
        "close":                round(c, 2),
        "prev_close":           round(c_prev, 2),
        "day_change_pct":       day_chg,
        "volume":               int(v_today) if v_today else None,
        "volume_20d_avg":       int(v_20d) if v_20d else None,
        "volume_ratio":         vol_ratio,
        "ma50":                 ma50,
        "price_vs_50d_ma_pct":  vs_50d,
        "high_52w":             h52w,
        "dist_to_52w_high_pct": dist_52w,
        "crossed_above_50d":    crossed_50d,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_idx_lq45() -> dict:
    today = date.today()
    now   = datetime.now(timezone.utc)

    snapshot: dict = {
        "date":             today.isoformat(),
        "fetched_at":       now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "indices":          {},
        "idr_usd":          None,
        "idx_top_gainers":  [],
        "idx_top_losers":   [],
        "stocks":           {},
        "top_movers":       {"gainers": [], "losers": []},
        "setup_candidates": [],
        "sectors_ranked":   [],
    }

    # ── IDX top movers (full market) ──────────────────────────────────────────
    print("Fetching IDX top movers from IDX API...")
    idx_movers = fetch_idx_top_movers()
    snapshot["idx_top_gainers"] = idx_movers["top_gainers"]
    snapshot["idx_top_losers"]  = idx_movers["top_losers"]

    # ── IDX30 and KOMPAS100 from IDX Trade History API ────────────────────────
    print("Fetching IDX30 and KOMPAS100 from IDX API...")
    for index_code, key in [("IDX30", "idx30"), ("KOMPAS100", "kompas100")]:
        result = fetch_idx_index(index_code)
        if result:
            snapshot["indices"][key] = result

    # ── IHSG and LQ45 via yfinance ────────────────────────────────────────────
    yf_tickers = list(INDEX_TICKERS.keys())
    print(f"Fetching {len(yf_tickers)} yfinance indices + IDR/USD...")
    idx_data = _download(yf_tickers + ["IDR=X"])

    for yf_ticker, key in INDEX_TICKERS.items():
        m = _metrics(*_extract(idx_data, yf_ticker))
        if m:
            snapshot["indices"][key] = {
                "close":          m["close"],
                "day_change_pct": m["day_change_pct"],
            }

    # IDR/USD
    m_fx = _metrics(*_extract(idx_data, "IDR=X"))
    if m_fx:
        snapshot["idr_usd"] = {
            "rate":           m_fx["close"],
            "day_change_pct": m_fx["day_change_pct"],
        }

    # ── LQ45 stocks ───────────────────────────────────────────────────────────
    print(f"Fetching {len(LQ45_TICKERS)} LQ45 stocks...")
    stock_data = _download(LQ45_TICKERS)

    stock_metrics: dict[str, dict] = {}
    for ticker in LQ45_TICKERS:
        m = _metrics(*_extract(stock_data, ticker))
        if m:
            stock_metrics[ticker] = m
            snapshot["stocks"][ticker] = {
                "name":                 LQ45_NAMES.get(ticker, ticker),
                "close":                m["close"],
                "day_change_pct":       m["day_change_pct"],
                "volume":               m["volume"],
                "volume_20d_avg":       m["volume_20d_avg"],
                "volume_ratio":         m["volume_ratio"],
                "price_vs_50d_ma_pct":  m["price_vs_50d_ma_pct"],
                "high_52w":             m["high_52w"],
                "dist_to_52w_high_pct": m["dist_to_52w_high_pct"],
            }

    # ── LQ45 top movers (vol_ratio > 1.3 filter) ─────────────────────────────
    conviction = [
        (t, m) for t, m in stock_metrics.items()
        if (m.get("volume_ratio") or 0) > 1.3
    ]
    gainers = sorted(conviction, key=lambda x: x[1]["day_change_pct"], reverse=True)[:3]
    losers  = sorted(conviction, key=lambda x: x[1]["day_change_pct"])[:3]

    snapshot["top_movers"]["gainers"] = [
        {
            "ticker":         t,
            "name":           LQ45_NAMES.get(t, t),
            "day_change_pct": m["day_change_pct"],
            "volume_ratio":   m["volume_ratio"],
        }
        for t, m in gainers
    ]
    snapshot["top_movers"]["losers"] = [
        {
            "ticker":         t,
            "name":           LQ45_NAMES.get(t, t),
            "day_change_pct": m["day_change_pct"],
            "volume_ratio":   m["volume_ratio"],
        }
        for t, m in losers
    ]

    # ── Setup candidates ──────────────────────────────────────────────────────
    setups = []
    for ticker, m in stock_metrics.items():
        if (m.get("volume_ratio") or 0) < 1.3:
            continue
        setup_type = None
        key_level  = None
        if m.get("dist_to_52w_high_pct") is not None and m["dist_to_52w_high_pct"] >= -3.0:
            setup_type = "near_52w_high"
            key_level  = m["high_52w"]
        elif m.get("crossed_above_50d"):
            setup_type = "crossed_50d_ma"
            key_level  = m["ma50"]
        if setup_type:
            setups.append({
                "ticker":       ticker,
                "name":         LQ45_NAMES.get(ticker, ticker),
                "close":        m["close"],
                "setup_type":   setup_type,
                "key_level":    key_level,
                "volume_ratio": m["volume_ratio"],
            })

    setups.sort(key=lambda x: x["volume_ratio"], reverse=True)
    snapshot["setup_candidates"] = setups[:3]

    # ── Sector rotation ───────────────────────────────────────────────────────
    sector_chg: dict[str, float] = {}
    for sector, tickers in SECTOR_MAP.items():
        chgs = [
            stock_metrics[t]["day_change_pct"]
            for t in tickers
            if t in stock_metrics
        ]
        if chgs:
            sector_chg[sector] = round(sum(chgs) / len(chgs), 2)

    snapshot["sectors_ranked"] = [
        {"sector": s, "avg_day_change_pct": v}
        for s, v in sorted(sector_chg.items(), key=lambda x: x[1], reverse=True)
    ]

    # ── Format all display-facing numbers ─────────────────────────────────────
    _apply_formatting(snapshot)

    # ── Save snapshot ─────────────────────────────────────────────────────────
    payload = json.dumps(snapshot, indent=2)
    out = SNAPSHOT_DIR / f"idx_lq45_{today.isoformat()}.json"
    out.write_text(payload)
    (SNAPSHOT_DIR / "idx_lq45_latest.json").write_text(payload)
    print(f"Saved → {out.name}")

    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_idx_lq45()
    pprint.pprint({k: v for k, v in result.items() if k != "stocks"})
    print(f"\nStocks collected: {len(result['stocks'])}")
    print(f"Setup candidates: {len(result['setup_candidates'])}")
