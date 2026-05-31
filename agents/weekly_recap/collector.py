from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

WIB = timezone(timedelta(hours=7))

# ── Tickers ───────────────────────────────────────────────────────────────────

US_INDICES = {
    "SPY":     "sp500_etf",
    "QQQ":     "nasdaq",
    "IWM":     "russell2000",
    "DIA":     "dow",
    "^GSPC":   "sp500",
}

US_MACRO = {
    "DX-Y.NYB": "dxy",
    "^TNX":     "us10y",
    "^VIX":     "vix",
    "GC=F":     "gold",
    "CL=F":     "wti",
}

US_SECTORS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLV":  "Health Care",
    "XLI":  "Industrials",
    "XLC":  "Comm Services",
    "XLY":  "Consumer Disc",
    "XLP":  "Consumer Staples",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLB":  "Materials",
}

IDX_INDICES = {
    "^JKSE":   "ihsg",
    "^JKLQ45": "lq45",
    "IDR=X":   "idr",
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

LQ45_NAMES = {
    "BBCA.JK": "BCA",              "BBRI.JK": "BRI",
    "BMRI.JK": "Mandiri",          "BBNI.JK": "BNI",
    "BBTN.JK": "BTN",              "TLKM.JK": "Telkom",
    "GOTO.JK": "GoTo",             "EMTK.JK": "Elang Mahkota",
    "ASII.JK": "Astra",            "UNTR.JK": "United Tractors",
    "ADRO.JK": "Adaro",            "PTBA.JK": "Bukit Asam",
    "ITMG.JK": "Indo Tambangraya", "INCO.JK": "Vale Indonesia",
    "ANTM.JK": "Antam",            "MDKA.JK": "Merdeka Copper",
    "UNVR.JK": "Unilever",         "ICBP.JK": "Indofood CBP",
    "MYOR.JK": "Mayora",           "SIDO.JK": "Sido Muncul",
    "SMGR.JK": "Semen Indonesia",  "WIKA.JK": "Wijaya Karya",
    "WSKT.JK": "Waskita Karya",    "EXCL.JK": "XL Axiata",
    "ISAT.JK": "Indosat",
}

IDX_SECTOR_MAP = {
    "Financials":  ["BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BBTN.JK"],
    "Telco":       ["TLKM.JK", "EXCL.JK", "ISAT.JK"],
    "Tech":        ["GOTO.JK", "EMTK.JK"],
    "Industrial":  ["ASII.JK", "UNTR.JK", "SMGR.JK", "WIKA.JK", "WSKT.JK"],
    "Commodities": ["ADRO.JK", "PTBA.JK", "ITMG.JK", "INCO.JK", "ANTM.JK", "MDKA.JK"],
    "Consumer":    ["UNVR.JK", "ICBP.JK", "MYOR.JK", "SIDO.JK"],
}

CRYPTO_SECTORS = {
    "Layer 1": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "ATOM", "NEAR"],
    "DeFi":    ["UNI", "AAVE", "MKR", "COMP", "CRV", "LDO", "SNX", "BAL", "SUSHI", "1INCH"],
    "AI":      ["FET", "RENDER", "TAO", "WLD", "AGIX", "OCEAN", "NMR", "ALT", "ARKM", "GRT"],
    "Gaming":  ["AXS", "SAND", "MANA", "IMX", "GALA", "ENJ", "BEAM", "PRIME", "YGG", "PYR"],
    "Memes":   ["DOGE", "SHIB", "PEPE", "BONK", "WIF", "FLOKI", "NEIRO", "MOG", "BRETT", "BOME"],
    "Infra":   ["LINK", "FIL", "AR", "RNDR", "LPT", "API3", "BAND", "TRB", "UMA", "ZRO"],
}

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD",
    "GUSD", "FRAX", "LUSD", "CRVUSD", "PYUSD",
}

ECON_COUNTRIES = {"US", "EU", "UK", "JP", "CN"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _r(v, d: int = 2):
    try:
        return round(float(v), d) if v is not None else None
    except (TypeError, ValueError):
        return None


def _weekly_chg(data: pd.DataFrame, ticker: str) -> dict | None:
    """Return weekly open→close change and OHLC for a ticker."""
    try:
        if isinstance(data.columns, pd.MultiIndex):
            closes = data["Close"][ticker].dropna()
        else:
            closes = data["Close"].dropna()
        if len(closes) < 2:
            return None
        start = float(closes.iloc[0])
        end   = float(closes.iloc[-1])
        chg   = _r((end - start) / start * 100)

        result: dict = {"close": _r(end), "weekly_chg_pct": chg}

        # High/low for the week
        try:
            if isinstance(data.columns, pd.MultiIndex):
                highs = data["High"][ticker].dropna()
                lows  = data["Low"][ticker].dropna()
            else:
                highs = data["High"].dropna()
                lows  = data["Low"].dropna()
            if not highs.empty:
                result["weekly_high"] = _r(float(highs.max()))
                result["weekly_low"]  = _r(float(lows.min()))
        except Exception:
            pass

        return result
    except Exception:
        return None


def _download_weekly(tickers: list[str]) -> pd.DataFrame:
    try:
        return yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"    yfinance download failed: {e}")
        return pd.DataFrame()


# ── Section A: Load snapshots ─────────────────────────────────────────────────

def load_snapshots() -> dict:
    print("  [A] Loading agent snapshots...")
    snap_files = {
        "us_ta":        "us_ta_latest.json",
        "idx_lq45":     "idx_lq45_latest.json",
        "crypto_ta":    "crypto_ta_latest.json",
        "morning_macro": "morning_macro_latest.json",
        "crypto_news":  "crypto_news_latest.json",
        "idx_news":     "idx_news_latest.json",
    }
    result: dict = {}
    for key, filename in snap_files.items():
        path = SNAPSHOT_DIR / filename
        if path.exists():
            try:
                data = json.loads(path.read_text())
                result[key] = {
                    "regime":     data.get("regime") or data.get("us_regime") or data.get("idx_regime"),
                    "fetched_at": data.get("fetched_at"),
                    "raw":        data,
                }
                print(f"    Loaded {filename}")
            except Exception as e:
                print(f"    Failed to load {filename}: {e}")
    return result


# ── Section B: US weekly performance ──────────────────────────────────────────

def fetch_us_weekly() -> dict:
    print("  [B] US weekly performance...")
    result: dict = {"indices": {}, "macro": {}, "sectors": [], "top_gainers": [], "top_losers": []}

    # Indices + macro
    all_tickers = list(US_INDICES.keys()) + list(US_MACRO.keys())
    data = _download_weekly(all_tickers)

    for ticker, key in US_INDICES.items():
        m = _weekly_chg(data, ticker)
        if m:
            result["indices"][key] = m

    for ticker, key in US_MACRO.items():
        m = _weekly_chg(data, ticker)
        if m:
            if key == "us10y":
                # Express as bps change
                start_close = None
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        closes = data["Close"][ticker].dropna()
                    else:
                        closes = data["Close"].dropna()
                    if len(closes) >= 2:
                        start_close = _r(float(closes.iloc[0]))
                except Exception:
                    pass
                m["start_yield"] = start_close
                m["bps_change"]  = _r((m["close"] - start_close) * 100) if start_close else None
            result["macro"][key] = m

    # Sectors
    print("    Fetching sector ETFs...")
    sector_data = _download_weekly(list(US_SECTORS.keys()))
    sectors = []
    for etf, name in US_SECTORS.items():
        m = _weekly_chg(sector_data, etf)
        if m:
            sectors.append({"etf": etf, "sector": name, "weekly_chg_pct": m["weekly_chg_pct"]})
    sectors.sort(key=lambda x: x["weekly_chg_pct"] or -999, reverse=True)
    result["sectors"] = sectors

    # S&P 500 top movers
    print("    Fetching S&P 500 movers (Wikipedia)...")
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            storage_options={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        sp500_df = tables[0]
        tickers = sp500_df["Symbol"].str.replace(".", "-", regex=False).tolist()[:200]
        names   = dict(zip(sp500_df["Symbol"].str.replace(".", "-", regex=False),
                           sp500_df["Security"]))
        sectors_map = dict(zip(sp500_df["Symbol"].str.replace(".", "-", regex=False),
                               sp500_df["GICS Sector"]))

        batch_data = _download_weekly(tickers)
        movers = []
        for t in tickers:
            m = _weekly_chg(batch_data, t)
            if m and m.get("weekly_chg_pct") is not None:
                movers.append({
                    "symbol":         t,
                    "name":           names.get(t, t)[:30],
                    "weekly_chg_pct": m["weekly_chg_pct"],
                    "sector":         sectors_map.get(t, ""),
                })
        movers.sort(key=lambda x: x["weekly_chg_pct"], reverse=True)
        result["top_gainers"] = movers[:5]
        result["top_losers"]  = movers[-5:][::-1]
        print(f"    S&P 500 movers: {len(movers)} stocks")
    except Exception as e:
        print(f"    S&P 500 movers failed: {e}")

    return result


# ── Section C: IDX weekly performance ─────────────────────────────────────────

def fetch_idx_weekly() -> dict:
    print("  [C] IDX weekly performance...")
    result: dict = {"indices": {}, "idr": {}, "sectors": [], "top_gainers": [], "top_losers": []}

    # Indices + IDR
    idx_tickers = list(IDX_INDICES.keys())
    data = _download_weekly(idx_tickers)

    for ticker, key in IDX_INDICES.items():
        m = _weekly_chg(data, ticker)
        if m:
            if key == "idr":
                result["idr"] = m
            else:
                result["indices"][key] = m

    # LQ45 stocks weekly
    print("    Fetching LQ45 weekly...")
    lq45_data = _download_weekly(LQ45_TICKERS)
    stock_chgs: dict[str, float] = {}

    for ticker in LQ45_TICKERS:
        m = _weekly_chg(lq45_data, ticker)
        if m and m.get("weekly_chg_pct") is not None:
            stock_chgs[ticker] = m["weekly_chg_pct"]

    # LQ45 movers
    sorted_stocks = sorted(stock_chgs.items(), key=lambda x: x[1], reverse=True)
    result["top_gainers"] = [
        {"ticker": t.replace(".JK", ""), "name": LQ45_NAMES.get(t, t), "weekly_chg_pct": chg}
        for t, chg in sorted_stocks[:3]
    ]
    result["top_losers"] = [
        {"ticker": t.replace(".JK", ""), "name": LQ45_NAMES.get(t, t), "weekly_chg_pct": chg}
        for t, chg in sorted_stocks[-3:][::-1]
    ]

    # IDX sector weekly
    sectors = []
    for sector, tickers in IDX_SECTOR_MAP.items():
        chgs = [stock_chgs[t] for t in tickers if t in stock_chgs]
        if chgs:
            avg = _r(sum(chgs) / len(chgs))
            best = max(tickers, key=lambda t: stock_chgs.get(t, -999))
            sectors.append({
                "sector":          sector,
                "avg_weekly_chg":  avg,
                "top_stock":       LQ45_NAMES.get(best, best.replace(".JK", "")),
            })
    sectors.sort(key=lambda x: x["avg_weekly_chg"] or -999, reverse=True)
    result["sectors"] = sectors

    return result


# ── Section D: Crypto weekly ───────────────────────────────────────────────────

def fetch_crypto_weekly() -> dict:
    print("  [D] Crypto weekly (CoinGecko)...")
    result: dict = {"btc": {}, "eth": {}, "market": {}, "sectors": [], "top_gainers": [], "top_losers": []}

    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 200,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "7d",
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        coins = resp.json()

        coin_map = {c["symbol"].upper(): c for c in coins if c.get("symbol")}

        btc = coin_map.get("BTC", {})
        eth = coin_map.get("ETH", {})

        result["btc"] = {
            "price":     _r(btc.get("current_price")),
            "pct_7d":    _r(btc.get("price_change_percentage_7d_in_currency")),
            "market_cap": btc.get("market_cap"),
        }
        result["eth"] = {
            "price":  _r(eth.get("current_price")),
            "pct_7d": _r(eth.get("price_change_percentage_7d_in_currency")),
        }

        # Top movers (excl stablecoins)
        eligible = [
            {
                "symbol":   c["symbol"].upper(),
                "name":     c["name"],
                "pct_7d":   _r(c.get("price_change_percentage_7d_in_currency")),
                "rank":     c.get("market_cap_rank"),
            }
            for c in coins
            if c.get("symbol", "").upper() not in STABLECOINS
            and c.get("price_change_percentage_7d_in_currency") is not None
        ]
        eligible.sort(key=lambda x: x["pct_7d"], reverse=True)
        result["top_gainers"] = eligible[:5]
        result["top_losers"]  = eligible[-5:][::-1]

        # Sector performance
        symbol_pct = {c["symbol"].upper(): c.get("price_change_percentage_7d_in_currency")
                      for c in coins}
        sector_rows = []
        for sector, syms in CRYPTO_SECTORS.items():
            chgs = [symbol_pct[s] for s in syms if symbol_pct.get(s) is not None]
            if chgs:
                sector_rows.append({
                    "sector":       sector,
                    "avg_pct_7d":   _r(sum(chgs) / len(chgs)),
                })
        sector_rows.sort(key=lambda x: x["avg_pct_7d"] or -999, reverse=True)
        result["sectors"] = sector_rows

        print(f"    {len(coins)} coins fetched")
    except Exception as e:
        print(f"    CoinGecko weekly failed: {e}")

    # Global market cap
    try:
        g = requests.get("https://api.coingecko.com/api/v3/global",
                         headers={"Accept": "application/json"}, timeout=10).json()["data"]
        mcap = g.get("total_market_cap", {}).get("usd")
        result["market"] = {
            "total_mcap":          f"${mcap/1e12:.2f}T" if mcap else None,
            "total_mcap_chg_24h":  _r(g.get("market_cap_change_percentage_24h_usd")),
            "btc_dominance":       _r(g.get("market_cap_percentage", {}).get("btc")),
            "eth_dominance":       _r(g.get("market_cap_percentage", {}).get("eth")),
        }
    except Exception as e:
        print(f"    CoinGecko global failed: {e}")

    return result


# ── Section E: Economic events this week ──────────────────────────────────────

def fetch_economic_events(from_date: str, to_date: str, label: str) -> list[dict]:
    print(f"  Finnhub economic calendar ({label})...")
    token = os.getenv("FINNHUB_API_KEY", "")
    if not token:
        print("    No FINNHUB_API_KEY — skipping")
        return []
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": from_date, "to": to_date, "token": token},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json().get("economicCalendar", [])
        results = []
        for e in events:
            if e.get("impact", "").upper() != "HIGH":
                continue
            country = (e.get("country") or "").upper()
            if country not in ECON_COUNTRIES:
                continue
            actual   = e.get("actual")
            forecast = e.get("estimate")
            prev     = e.get("prev")
            # Beat/Miss/In-Line vs forecast
            verdict = None
            if actual is not None and forecast is not None:
                try:
                    diff = float(actual) - float(forecast)
                    if abs(diff) < 0.05:
                        verdict = "IN-LINE"
                    elif diff > 0:
                        verdict = "BEAT"
                    else:
                        verdict = "MISS"
                except (TypeError, ValueError):
                    pass
            results.append({
                "event":    e.get("event", ""),
                "country":  country,
                "date":     e.get("time", "")[:10],
                "time":     e.get("time", ""),
                "actual":   actual,
                "forecast": forecast,
                "previous": prev,
                "verdict":  verdict,
            })
        results.sort(key=lambda x: x["date"])
        print(f"    {len(results)} high-impact events")
        return results[:10]
    except Exception as e:
        print(f"    Economic calendar failed: {e}")
        return []


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_weekly_recap() -> dict:
    now_wib    = datetime.now(WIB)
    fetched_at = now_wib.strftime("%d-%m-%Y %H:%M WIB")
    today      = now_wib.date()

    # Week boundaries: last Monday → today
    days_since_monday = today.weekday()  # 0=Mon … 6=Sun
    last_monday = today - timedelta(days=days_since_monday if days_since_monday > 0 else 7)
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    next_friday = next_monday + timedelta(days=4)

    week_ending  = today.strftime("%d-%m-%Y")
    from_str     = last_monday.isoformat()
    to_str       = today.isoformat()
    fwd_from_str = next_monday.isoformat()
    fwd_to_str   = next_friday.isoformat()

    snapshots    = load_snapshots()
    us_weekly    = fetch_us_weekly()
    idx_weekly   = fetch_idx_weekly()
    crypto_weekly = fetch_crypto_weekly()
    econ_events  = fetch_economic_events(from_str, to_str, "this week")
    fwd_calendar = fetch_economic_events(fwd_from_str, fwd_to_str, "next week")

    snapshot = {
        "fetched_at":           fetched_at,
        "week_ending":          week_ending,
        "snapshots":            snapshots,
        "us_weekly":            us_weekly,
        "idx_weekly":           idx_weekly,
        "crypto_weekly":        crypto_weekly,
        "economic_events_week": econ_events,
        "forward_calendar":     fwd_calendar,
    }

    (SNAPSHOT_DIR / "weekly_recap_latest.json").write_text(json.dumps(snapshot, indent=2))
    print("  Saved → weekly_recap_latest.json")

    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_weekly_recap()
    print(f"\nUS indices:      {list(result['us_weekly']['indices'].keys())}")
    print(f"US sectors:      {len(result['us_weekly']['sectors'])}")
    print(f"US top gainers:  {len(result['us_weekly']['top_gainers'])}")
    print(f"IDX indices:     {list(result['idx_weekly']['indices'].keys())}")
    print(f"IDX sectors:     {len(result['idx_weekly']['sectors'])}")
    print(f"Crypto gainers:  {len(result['crypto_weekly']['top_gainers'])}")
    print(f"Econ events:     {len(result['economic_events_week'])}")
    print(f"Forward cal:     {len(result['forward_calendar'])}")
