from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

INDICES = ["SPY", "QQQ", "IWM"]
SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE"]
MACRO_TICKERS = {"^VIX": "vix", "DX-Y.NYB": "dxy", "^TNX": "us10y"}
SECTOR_NAMES = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
    "XLV": "Healthcare", "XLI": "Industrials", "XLC": "Comm Svcs",
    "XLY": "Cons Disc", "XLP": "Cons Staples", "XLU": "Utilities",
    "XLRE": "Real Estate",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_sp500_tickers() -> list[str]:
    try:
        import urllib.request
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
        print(f"  Wikipedia fetch failed ({e}), skipping S&P 500 screen")
        return []


def _extract(data: pd.DataFrame, ticker: str) -> tuple[pd.Series, pd.Series]:
    """Pull close + volume Series from a multi-ticker download DataFrame."""
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
    v_20d = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else None
    vol_ratio = round(v_today / v_20d, 2) if (v_today and v_20d and v_20d > 0) else None

    n = len(close)
    ma50 = round(float(close.iloc[-50:].mean()), 2) if n >= 50 else None
    ma200 = round(float(close.iloc[-200:].mean()), 2) if n >= 200 else None
    vs_50d = round((c - ma50) / ma50 * 100, 2) if ma50 else None
    vs_200d = round((c - ma200) / ma200 * 100, 2) if ma200 else None

    h52w = round(float(close.iloc[-252:].max()), 2) if n >= 20 else None
    dist_52w = round((c - h52w) / h52w * 100, 2) if h52w else None

    crossed_50d = False
    if n >= 51 and ma50:
        ma50_yd = float(close.iloc[-51:-1].mean())
        crossed_50d = c_prev < ma50_yd and c > ma50

    return {
        "close": round(c, 2),
        "prev_close": round(c_prev, 2),
        "day_chg_pct": day_chg,
        "volume": int(v_today) if v_today else None,
        "avg_vol_20d": int(v_20d) if v_20d else None,
        "vol_ratio": vol_ratio,
        "ma50": ma50,
        "ma200": ma200,
        "vs_50d_pct": vs_50d,
        "vs_200d_pct": vs_200d,
        "high_52w": h52w,
        "dist_52w_high_pct": dist_52w,
        "crossed_above_50d": crossed_50d,
    }


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


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_ta() -> dict:
    today = date.today()
    now = datetime.now(timezone.utc)

    snapshot: dict = {
        "date": today.isoformat(),
        "fetch_timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "indices": {},
        "macro": {},
        "sectors": {},
        "sectors_ranked": [],
        "top_movers": {"gainers": [], "losers": []},
        "breakout_candidates": [],
    }

    # ── Core: indices + sectors + macro ──────────────────────────────────────
    core = INDICES + SECTORS + list(MACRO_TICKERS.keys())
    print(f"Fetching {len(core)} core tickers...")
    core_data = _download(core)

    for ticker in INDICES:
        m = _metrics(*_extract(core_data, ticker))
        if m:
            snapshot["indices"][ticker] = {
                "close": m["close"],
                "day_chg_pct": m["day_chg_pct"],
                "volume": m["volume"],
                "avg_vol_20d": m["avg_vol_20d"],
                "vol_ratio": m["vol_ratio"],
                "vs_50d_pct": m["vs_50d_pct"],
                "vs_200d_pct": m["vs_200d_pct"],
                "ma50": m["ma50"],
                "ma200": m["ma200"],
            }

    for ticker, key in MACRO_TICKERS.items():
        m = _metrics(*_extract(core_data, ticker))
        if m:
            snapshot["macro"][key] = {
                "level": m["close"],
                "day_chg": round(m["close"] - m["prev_close"], 3),
                "day_chg_pct": m["day_chg_pct"],
            }

    sector_chg: dict[str, float] = {}
    for ticker in SECTORS:
        m = _metrics(*_extract(core_data, ticker))
        if m:
            sector_chg[ticker] = m["day_chg_pct"]

    snapshot["sectors"] = sector_chg
    snapshot["sectors_ranked"] = [
        {"ticker": t, "name": SECTOR_NAMES.get(t, t), "day_chg_pct": v}
        for t, v in sorted(sector_chg.items(), key=lambda x: x[1], reverse=True)
    ]

    # ── S&P 500: movers + breakouts ───────────────────────────────────────────
    sp500 = _get_sp500_tickers()
    if sp500:
        print(f"Downloading {len(sp500)} S&P 500 components (1y)... this takes ~60s")
        sp_data = _download(sp500)

        sp_metrics: dict[str, dict] = {}
        for ticker in sp500:
            m = _metrics(*_extract(sp_data, ticker))
            if m and m["vol_ratio"] is not None:
                sp_metrics[ticker] = m

        # Movers: vol_ratio >= 1.5 only
        conviction = [(t, m) for t, m in sp_metrics.items() if (m["vol_ratio"] or 0) >= 1.5]
        gainers = sorted(conviction, key=lambda x: x[1]["day_chg_pct"], reverse=True)[:5]
        losers = sorted(conviction, key=lambda x: x[1]["day_chg_pct"])[:5]

        snapshot["top_movers"]["gainers"] = [
            {"ticker": t, "close": m["close"], "day_chg_pct": m["day_chg_pct"], "vol_ratio": m["vol_ratio"]}
            for t, m in gainers
        ]
        snapshot["top_movers"]["losers"] = [
            {"ticker": t, "close": m["close"], "day_chg_pct": m["day_chg_pct"], "vol_ratio": m["vol_ratio"]}
            for t, m in losers
        ]

        # Breakouts: near 52w high OR crossed 50d MA, with vol_ratio >= 1.5
        breakouts = []
        for ticker, m in sp_metrics.items():
            if (m.get("vol_ratio") or 0) < 1.5:
                continue
            setup = None
            if m.get("dist_52w_high_pct") is not None and m["dist_52w_high_pct"] >= -2.0:
                setup = "near_52w_high"
            elif m.get("crossed_above_50d"):
                setup = "crossed_50d_ma"
            if setup:
                breakouts.append({
                    "ticker": ticker,
                    "close": m["close"],
                    "high_52w": m["high_52w"],
                    "dist_52w_high_pct": m["dist_52w_high_pct"],
                    "ma50": m["ma50"],
                    "vol_ratio": m["vol_ratio"],
                    "setup_type": setup,
                })

        breakouts.sort(key=lambda x: x["vol_ratio"], reverse=True)
        snapshot["breakout_candidates"] = breakouts[:5]

    # ── Save ──────────────────────────────────────────────────────────────────
    payload = json.dumps(snapshot, indent=2)
    out = SNAPSHOT_DIR / f"us_ta_{today.isoformat()}.json"
    out.write_text(payload)
    (SNAPSHOT_DIR / "us_ta_latest.json").write_text(payload)
    print(f"Saved → {out.name}")

    return snapshot


# Alias for external callers
collect_us_ta = collect_ta


if __name__ == "__main__":
    import pprint
    result = collect_ta()
    pprint.pprint({k: v for k, v in result.items() if k != "breakout_candidates"})
    print(f"\nBreakout candidates: {len(result['breakout_candidates'])}")
