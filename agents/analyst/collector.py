from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_snapshot(target_date: date) -> dict | None:
    path = SNAPSHOT_DIR / f"{target_date.isoformat()}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _delta(current, previous):
    if current is None or previous is None:
        return None
    return round(current - previous, 6)


def _delta_pct(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 4)


# ── FRED (Fed / Liquidity) ────────────────────────────────────────────────────

def _fred_series(series_id: str) -> float | None:
    try:
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            return None
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={api_key}"
            "&file_type=json&sort_order=desc&limit=5"
        )
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        for obs in resp.json().get("observations", []):
            if obs.get("value", ".") != ".":
                return float(obs["value"])
        return None
    except Exception:
        return None


def _fetch_liquidity(prev1d: dict | None, prev7d: dict | None) -> dict:
    # Units: WALCL → millions USD, WTREGEN → millions USD, RRPONTSYD → billions USD
    walcl_raw = _fred_series("WALCL")
    tga_raw = _fred_series("WTREGEN")
    rrp_raw = _fred_series("RRPONTSYD")

    fed_bs = walcl_raw * 1e6 if walcl_raw is not None else None
    tga = tga_raw * 1e6 if tga_raw is not None else None
    rrp = rrp_raw * 1e9 if rrp_raw is not None else None

    net = (
        (fed_bs - tga - rrp)
        if all(v is not None for v in [fed_bs, tga, rrp])
        else None
    )

    p1_net = (prev1d or {}).get("liquidity", {}).get("net_liquidity")
    p7_net = (prev7d or {}).get("liquidity", {}).get("net_liquidity")

    return {
        "fed_balance_sheet": fed_bs,
        "tga": tga,
        "on_rrp": rrp,
        "net_liquidity": net,
        "net_liquidity_delta_1d": _delta(net, p1_net),
        "net_liquidity_delta_7d": _delta(net, p7_net),
    }


# ── yfinance (Macro) ──────────────────────────────────────────────────────────

def _yf_close(symbol: str) -> float | None:
    try:
        hist = yf.Ticker(symbol).history(period="5d")
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception:
        return None


def _fetch_macro(prev1d: dict | None) -> dict:
    p = (prev1d or {}).get("macro", {})

    dxy = _yf_close("DX-Y.NYB")
    us10y = _yf_close("^TNX")
    us2y = _yf_close("^IRX")   # 13-week T-bill used as 2Y proxy per spec
    vix = _yf_close("^VIX")
    oil = _yf_close("CL=F")
    gold = _yf_close("GC=F")
    sp500 = _yf_close("^GSPC")

    return {
        "dxy": dxy,
        "dxy_delta_1d": _delta(dxy, p.get("dxy")),
        "us10y": us10y,
        "us10y_delta_1d": _delta(us10y, p.get("us10y")),
        "us2y": us2y,
        "vix": vix,
        "oil_wti": oil,
        "gold": gold,
        "sp500": sp500,
    }


# ── CoinGecko (Crypto) ────────────────────────────────────────────────────────

def _cg_get(url: str) -> dict | None:
    try:
        resp = requests.get(url, timeout=15, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _fetch_crypto_cg(prev7d: dict | None) -> tuple[dict, float | None]:
    p7 = (prev7d or {}).get("crypto", {})

    btc_data = _cg_get(
        "https://api.coingecko.com/api/v3/coins/bitcoin"
        "?localization=false&tickers=false&community_data=false&developer_data=false"
    )
    time.sleep(1.5)

    btc_price, btc_vol, btc_change_1d = None, None, None
    if btc_data:
        md = btc_data.get("market_data", {})
        btc_price = md.get("current_price", {}).get("usd")
        btc_vol = md.get("total_volume", {}).get("usd")
        btc_change_1d = md.get("price_change_percentage_24h")

    global_data = _cg_get("https://api.coingecko.com/api/v3/global")
    time.sleep(1.5)

    btc_dominance = None
    if global_data:
        btc_dominance = global_data.get("data", {}).get("market_cap_percentage", {}).get("btc")

    usdt_data = _cg_get(
        "https://api.coingecko.com/api/v3/coins/tether"
        "?localization=false&tickers=false&community_data=false&developer_data=false"
    )
    time.sleep(1.5)

    usdt_mcap = None
    if usdt_data:
        usdt_mcap = usdt_data.get("market_data", {}).get("market_cap", {}).get("usd")

    usdc_data = _cg_get(
        "https://api.coingecko.com/api/v3/coins/usd-coin"
        "?localization=false&tickers=false&community_data=false&developer_data=false"
    )

    usdc_mcap = None
    if usdc_data:
        usdc_mcap = usdc_data.get("market_data", {}).get("market_cap", {}).get("usd")

    stablecoin_total = (
        (usdt_mcap + usdc_mcap)
        if usdt_mcap is not None and usdc_mcap is not None
        else None
    )

    result = {
        "btc_price": btc_price,
        "btc_price_delta_1d_pct": btc_change_1d,
        "btc_volume_24h": btc_vol,
        "btc_dominance": btc_dominance,
        "usdt_mcap": usdt_mcap,
        "usdc_mcap": usdc_mcap,
        "stablecoin_total_mcap": stablecoin_total,
        "stablecoin_mcap_delta_7d": _delta(stablecoin_total, p7.get("stablecoin_total_mcap")),
    }
    return result, btc_price


# ── Binance (BTC Derivatives) ─────────────────────────────────────────────────

def _fetch_btc_derivatives(btc_price: float | None, prev1d: dict | None) -> dict:
    funding_rate = None
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            funding_rate = float(data[0]["fundingRate"])
    except Exception:
        pass

    oi_usd = None
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT",
            timeout=15,
        )
        resp.raise_for_status()
        oi_btc = float(resp.json()["openInterest"])
        if btc_price is not None:
            oi_usd = oi_btc * btc_price
    except Exception:
        pass

    prev_oi = (prev1d or {}).get("crypto", {}).get("btc_oi")

    return {
        "btc_oi": oi_usd,
        "btc_oi_delta_1d_pct": _delta_pct(oi_usd, prev_oi),
        "btc_funding_rate": funding_rate,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_data() -> dict:
    today = date.today()
    prev1d = _load_snapshot(today - timedelta(days=1))
    prev7d = _load_snapshot(today - timedelta(days=7))

    liquidity = _fetch_liquidity(prev1d, prev7d)
    macro = _fetch_macro(prev1d)
    crypto_cg, btc_price = _fetch_crypto_cg(prev7d)
    derivatives = _fetch_btc_derivatives(btc_price, prev1d)

    crypto = {
        "btc_price": crypto_cg["btc_price"],
        "btc_price_delta_1d_pct": crypto_cg["btc_price_delta_1d_pct"],
        "btc_volume_24h": crypto_cg["btc_volume_24h"],
        "btc_dominance": crypto_cg["btc_dominance"],
        "btc_oi": derivatives["btc_oi"],
        "btc_oi_delta_1d_pct": derivatives["btc_oi_delta_1d_pct"],
        "btc_funding_rate": derivatives["btc_funding_rate"],
        "usdt_mcap": crypto_cg["usdt_mcap"],
        "usdc_mcap": crypto_cg["usdc_mcap"],
        "stablecoin_total_mcap": crypto_cg["stablecoin_total_mcap"],
        "stablecoin_mcap_delta_7d": crypto_cg["stablecoin_mcap_delta_7d"],
    }

    snapshot = {
        "date": today.isoformat(),
        "liquidity": liquidity,
        "macro": macro,
        "crypto": crypto,
    }

    payload = json.dumps(snapshot, indent=2)
    (SNAPSHOT_DIR / f"{today.isoformat()}.json").write_text(payload)
    (SNAPSHOT_DIR / "analyst_latest.json").write_text(payload)
    return snapshot


if __name__ == "__main__":
    import pprint
    pprint.pprint(collect_data())
