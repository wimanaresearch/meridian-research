from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

WIB = timezone(timedelta(hours=7))

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD",
    "GUSD", "FRAX", "LUSD", "CRVUSD", "PYUSD",
}

SECTORS: dict[str, list[str]] = {
    "Layer 1": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "ATOM", "NEAR"],
    "DeFi":    ["UNI", "AAVE", "MKR", "COMP", "CRV", "LDO", "SNX", "BAL", "SUSHI", "1INCH"],
    "AI":      ["FET", "RENDER", "TAO", "WLD", "AGIX", "OCEAN", "NMR", "ALT", "ARKM", "GRT"],
    "Gaming":  ["AXS", "SAND", "MANA", "IMX", "GALA", "ENJ", "BEAM", "PRIME", "YGG", "PYR"],
    "Memes":   ["DOGE", "SHIB", "PEPE", "BONK", "WIF", "FLOKI", "NEIRO", "MOG", "BRETT", "BOME"],
    "Infra":   ["LINK", "FIL", "AR", "RNDR", "LPT", "API3", "BAND", "TRB", "UMA", "ZRO"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cg_headers() -> dict:
    key = os.getenv("COINGECKO_API_KEY", "")
    h = {"Accept": "application/json"}
    if key:
        h["X-CG-Demo-API-Key"] = key
    return h


def _r(v: float | None, d: int = 2) -> float | None:
    return round(v, d) if v is not None else None


def _fmt_mcap(usd: float | None) -> str | None:
    if usd is None:
        return None
    if usd >= 1e12:
        return f"{usd / 1e12:.2f}T"
    return f"{usd / 1e9:.2f}B"


def _load_prev_snapshot() -> dict:
    """Load previous crypto_ta_latest.json for delta calculations."""
    try:
        return json.loads((SNAPSHOT_DIR / "crypto_ta_latest.json").read_text())
    except Exception:
        return {}


# ── Signal derivation ─────────────────────────────────────────────────────────

def _signal_gainer(coin: dict) -> str:
    vol   = coin.get("volume_24h") or 0
    mcap  = coin.get("market_cap") or 1
    ratio = vol / mcap
    p24   = coin.get("pct_24h") or 0
    p7    = coin.get("pct_7d") or 0
    ath   = coin.get("ath_change_pct") or -999

    if ratio > 0.40 and p24 > 5:  return "Extreme vol surge"
    if ratio > 0.20 and p24 > 3:  return "Vol breakout"
    if p24 > 5 and p7 > 15:       return "Strong momentum"
    if p24 > 3 and p7 < -10:      return "Reversal attempt"
    if ath > -5 and p24 > 2:      return "Near ATH"
    if p7 > 20 and p24 > 0:       return "Weekly continuation"
    return "—"


def _signal_loser(coin: dict) -> str:
    vol   = coin.get("volume_24h") or 0
    mcap  = coin.get("market_cap") or 1
    ratio = vol / mcap
    p24   = coin.get("pct_24h") or 0
    p7    = coin.get("pct_7d") or 0
    ath   = coin.get("ath_change_pct") or 0

    if ratio > 0.40 and p24 < -5:           return "Extreme vol dump"
    if ratio > 0.20 and p24 < -3:           return "Vol selloff"
    if p24 < -5 and p7 < -15:              return "Continuation down"
    if p24 < -3 and p7 > 5:               return "Pullback in uptrend"
    if p24 < -3 and p7 < -5 and ath < -50: return "Structural weakness"
    return "—"


# ── Section A: Global crypto market ──────────────────────────────────────────

def _fetch_global() -> dict:
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/global",
            headers=_cg_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})

        total     = data.get("total_market_cap", {}).get("usd")
        btc_dom   = data.get("market_cap_percentage", {}).get("btc")
        eth_dom   = data.get("market_cap_percentage", {}).get("eth")
        chg_24h   = data.get("market_cap_change_percentage_24h_usd")
        defi_mcap = data.get("defi_market_cap")

        total2 = (total * (1 - btc_dom / 100)) if (total and btc_dom is not None) else None
        total3 = (
            total * (1 - btc_dom / 100 - eth_dom / 100)
            if (total and btc_dom is not None and eth_dom is not None)
            else None
        )

        return {
            "total_mcap":         _fmt_mcap(total),
            "total2_mcap":        _fmt_mcap(total2),
            "total3_mcap":        _fmt_mcap(total3),
            # Raw values preserved for next-run delta calculations
            "total_raw":          round(total)  if total  else None,
            "total2_raw":         round(total2) if total2 else None,
            "total3_raw":         round(total3) if total3 else None,
            "btc_dominance":      _r(btc_dom, 1),
            "eth_dominance":      _r(eth_dom, 1),
            "total_mcap_chg_24h": _r(chg_24h, 2),
            "defi_mcap":          _fmt_mcap(defi_mcap),
        }
    except Exception as e:
        print(f"  CoinGecko /global failed: {e}")
        return {}


# ── Section B: Top 200 coins ──────────────────────────────────────────────────

def _fetch_coins() -> list[dict]:
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency":             "usd",
                "order":                   "market_cap_desc",
                "per_page":                200,
                "page":                    1,
                "sparkline":               "false",
                "price_change_percentage": "24h,7d",
                "include_platform":        "false",
            },
            headers=_cg_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return [
            {
                "id":             c.get("id"),
                "symbol":         (c.get("symbol") or "").upper(),
                "name":           c.get("name"),
                "current_price":  c.get("current_price"),
                "pct_24h":        _r(c.get("price_change_percentage_24h"), 2),
                "pct_7d":         _r(c.get("price_change_percentage_7d_in_currency"), 2),
                "volume_24h":     c.get("total_volume"),
                "market_cap":     c.get("market_cap"),
                "mcap_chg_24h":   _r(c.get("market_cap_change_percentage_24h"), 2),
                "ath":            c.get("ath"),
                "ath_change_pct": _r(c.get("ath_change_percentage"), 2),
                "rank":           c.get("market_cap_rank"),
            }
            for c in resp.json()
        ]
    except Exception as e:
        print(f"  CoinGecko /coins/markets failed: {e}")
        return []


# ── Section C: Top movers ─────────────────────────────────────────────────────

def _top_movers(coins: list[dict]) -> tuple[list[dict], list[dict]]:
    eligible = [
        c for c in coins
        if c["symbol"] not in STABLECOINS and c["pct_24h"] is not None
    ]
    gainers = sorted(eligible, key=lambda x: x["pct_24h"], reverse=True)[:5]
    losers  = sorted(eligible, key=lambda x: x["pct_24h"])[:5]

    def _slim_gainer(c: dict) -> dict:
        return {
            "symbol":        c["symbol"],
            "name":          c["name"],
            "current_price": c["current_price"],
            "pct_24h":       c["pct_24h"],
            "pct_7d":        c["pct_7d"],
            "volume_24h":    c["volume_24h"],
            "rank":          c["rank"],
            "signal":        _signal_gainer(c),
        }

    def _slim_loser(c: dict) -> dict:
        return {
            "symbol":        c["symbol"],
            "name":          c["name"],
            "current_price": c["current_price"],
            "pct_24h":       c["pct_24h"],
            "pct_7d":        c["pct_7d"],
            "volume_24h":    c["volume_24h"],
            "rank":          c["rank"],
            "signal":        _signal_loser(c),
        }

    return [_slim_gainer(c) for c in gainers], [_slim_loser(c) for c in losers]


# ── Section D: Volume surge ───────────────────────────────────────────────────

def _volume_surge(coins: list[dict]) -> list[dict]:
    results: list[dict] = []
    for c in coins:
        if c["symbol"] in STABLECOINS:
            continue
        vol  = c.get("volume_24h")
        mcap = c.get("market_cap")
        if not vol or not mcap or mcap == 0:
            continue
        ratio = vol / mcap
        if ratio > 0.20:
            results.append({
                "symbol":               c["symbol"],
                "name":                 c["name"],
                "current_price":        c["current_price"],
                "pct_24h":              c["pct_24h"],
                "volume_to_mcap_ratio": _r(ratio, 3),
                "volume_flag":          "EXTREME" if ratio > 0.40 else "HIGH",
            })

    results.sort(key=lambda x: x["volume_to_mcap_ratio"], reverse=True)
    return results[:5]


# ── Section E: Breakout candidates ───────────────────────────────────────────

def _breakout_candidates(coins: list[dict]) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []

    for c in coins:
        if c["symbol"] in STABLECOINS:
            continue
        rank    = c.get("rank")
        ath_chg = c.get("ath_change_pct")
        pct_24h = c.get("pct_24h")
        pct_7d  = c.get("pct_7d")

        # Screen 1: near ATH + positive momentum + top 100
        if (rank and rank <= 100
                and ath_chg is not None and ath_chg > -15
                and pct_24h is not None and pct_24h > 2
                and c["symbol"] not in seen):
            seen.add(c["symbol"])
            results.append({**c, "screen_type": "near_ath"})

        # Screen 2: strong momentum both timeframes + top 150
        elif (rank and rank <= 150
                and pct_24h is not None and pct_24h > 3
                and pct_7d  is not None and pct_7d  > 10
                and c["symbol"] not in seen):
            seen.add(c["symbol"])
            results.append({**c, "screen_type": "momentum"})

    results.sort(key=lambda x: x.get("pct_24h") or 0, reverse=True)
    return [
        {
            "symbol":         c["symbol"],
            "name":           c["name"],
            "current_price":  c["current_price"],
            "pct_24h":        c["pct_24h"],
            "pct_7d":         c["pct_7d"],
            "ath_change_pct": c["ath_change_pct"],
            "rank":           c["rank"],
            "screen_type":    c["screen_type"],
        }
        for c in results[:5]
    ]


# ── Section F: Sector performance ────────────────────────────────────────────

def _sector_performance(coins: list[dict]) -> list[dict]:
    by_symbol: dict[str, dict] = {c["symbol"]: c for c in coins}

    results: list[dict] = []
    for sector_name, symbols in SECTORS.items():
        matched = [by_symbol[s] for s in symbols if s in by_symbol]
        pcts = [c["pct_24h"] for c in matched if c["pct_24h"] is not None]
        avg  = _r(sum(pcts) / len(pcts), 2) if pcts else None

        results.append({
            "sector":      sector_name,
            "avg_pct_24h": avg,
            "green":       sum(1 for p in pcts if p > 0),
            "red":         sum(1 for p in pcts if p < 0),
            "coins_found": len(matched),
        })

    results.sort(key=lambda x: (x["avg_pct_24h"] or -999), reverse=True)
    return results


# ── Section G: BTC & ETH moving averages (yfinance) ──────────────────────────

def _ma_detail(ticker: str, coin: dict) -> dict:
    base = {
        "current_price": coin.get("current_price"),
        "pct_24h":       coin.get("pct_24h"),
        "ma_7d":         None,
        "ma_30d":        None,
        "vs_ma7d_pct":   None,
        "vs_ma30d_pct":  None,
    }
    try:
        hist   = yf.Ticker(ticker).history(period="30d")
        if hist.empty:
            return base
        closes = hist["Close"].dropna()
        price  = float(closes.iloc[-1])
        ma7    = float(closes.iloc[-7:].mean()) if len(closes) >= 7  else None
        ma30   = float(closes.mean())            if len(closes) >= 20 else None
        return {
            **base,
            "ma_7d":        _r(ma7,  2),
            "ma_30d":       _r(ma30, 2),
            "vs_ma7d_pct":  _r((price - ma7)  / ma7  * 100, 2) if ma7  else None,
            "vs_ma30d_pct": _r((price - ma30) / ma30 * 100, 2) if ma30 else None,
        }
    except Exception:
        return base


def _fetch_ma_details(coins: list[dict]) -> tuple[dict, dict]:
    by_symbol = {c["symbol"]: c for c in coins}
    btc_detail = _ma_detail("BTC-USD", by_symbol.get("BTC", {}))
    eth_detail = _ma_detail("ETH-USD", by_symbol.get("ETH", {}))
    return btc_detail, eth_detail


# ── Section H: Stablecoin liquidity ──────────────────────────────────────────

def _stablecoin_liquidity(
    coins: list[dict],
    total_mcap_raw: float | None,
    prev_usdt_d: float | None,
) -> dict:
    by_symbol = {c["symbol"]: c for c in coins}
    usdt = by_symbol.get("USDT", {})
    usdc = by_symbol.get("USDC", {})

    usdt_mcap = usdt.get("market_cap")
    usdc_mcap = usdc.get("market_cap")
    combined  = (usdt_mcap + usdc_mcap) if (usdt_mcap and usdc_mcap) else None

    usdt_chg = usdt.get("mcap_chg_24h") or 0
    usdc_chg = usdc.get("mcap_chg_24h") or 0
    avg_chg  = (usdt_chg + usdc_chg) / 2 if (usdt or usdc) else 0

    trend = "STABLE"
    if avg_chg > 0.5:
        trend = "EXPANDING"
    elif avg_chg < -0.5:
        trend = "CONTRACTING"

    # USDT.D = USDT market cap as % of total crypto market cap
    usdt_d        = None
    usdt_d_change = None
    if usdt_mcap and total_mcap_raw:
        usdt_d = _r(usdt_mcap / total_mcap_raw * 100, 2)
        if prev_usdt_d is not None:
            usdt_d_change = _r(usdt_d - prev_usdt_d, 2)

    return {
        "usdt_mcap":      _fmt_mcap(usdt_mcap),
        "usdt_chg_24h":   usdt.get("mcap_chg_24h"),
        "usdc_mcap":      _fmt_mcap(usdc_mcap),
        "usdc_chg_24h":   usdc.get("mcap_chg_24h"),
        "combined_mcap":  _fmt_mcap(combined),
        "trend":          trend,
        "usdt_dominance": usdt_d,
        "usdt_d_change":  usdt_d_change,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_crypto_ta() -> dict:
    now_utc    = datetime.now(timezone.utc)
    now_wib    = now_utc.astimezone(WIB)
    today_str  = now_wib.strftime("%Y-%m-%d")
    fetched_at = now_wib.strftime("%d-%m-%Y %H:%M WIB")

    # Load previous snapshot for delta calculations (first run returns {})
    prev        = _load_prev_snapshot()
    prev_global = prev.get("global", {})
    prev_stable = prev.get("stablecoin_liquidity", {})

    print("  [A] Global crypto market (CoinGecko)...")
    global_data = _fetch_global()

    time.sleep(1.5)

    print("  [B] Top 200 coins (CoinGecko)...")
    coins = _fetch_coins()
    print(f"      Fetched {len(coins)} coins")

    # ── Delta calculations ────────────────────────────────────────────────────

    total_raw   = global_data.get("total_raw")
    total2_raw  = global_data.get("total2_raw")
    total3_raw  = global_data.get("total3_raw")
    prev_total2 = prev_global.get("total2_raw")
    prev_total3 = prev_global.get("total3_raw")

    global_data["total2_chg_pct"] = (
        _r((total2_raw - prev_total2) / prev_total2 * 100, 2)
        if (total2_raw and prev_total2) else None
    )
    global_data["total3_chg_pct"] = (
        _r((total3_raw - prev_total3) / prev_total3 * 100, 2)
        if (total3_raw and prev_total3) else None
    )

    # OTHERS = total market cap minus top-10 coins by rank
    top10_mcap = sum(
        c.get("market_cap") or 0
        for c in sorted(coins, key=lambda x: x.get("rank") or 9999)[:10]
    )
    others_raw  = (total_raw - top10_mcap) if total_raw else None
    prev_others = prev_global.get("others_raw")
    global_data["others_mcap"]    = _fmt_mcap(others_raw)
    global_data["others_raw"]     = round(others_raw) if others_raw else None
    global_data["others_chg_pct"] = (
        _r((others_raw - prev_others) / prev_others * 100, 2)
        if (others_raw and prev_others) else None
    )

    print("  [C] Top movers...")
    gainers, losers = _top_movers(coins)

    print("  [D] Volume surge...")
    vol_surge = _volume_surge(coins)

    print("  [E] Breakout candidates...")
    breakouts = _breakout_candidates(coins)

    print("  [F] Sector performance...")
    sectors = _sector_performance(coins)

    print("  [G] BTC & ETH MAs (yfinance)...")
    btc_detail, eth_detail = _fetch_ma_details(coins)

    print("  [H] Stablecoin liquidity...")
    prev_usdt_d = prev_stable.get("usdt_dominance")
    stablecoin  = _stablecoin_liquidity(coins, total_raw, prev_usdt_d)

    snapshot = {
        "fetched_at":           fetched_at,
        "date":                 today_str,
        "global":               global_data,
        "top_gainers":          gainers,
        "top_losers":           losers,
        "volume_surge":         vol_surge,
        "breakout_candidates":  breakouts,
        "sectors":              sectors,
        "btc_detail":           btc_detail,
        "eth_detail":           eth_detail,
        "stablecoin_liquidity": stablecoin,
    }

    payload = json.dumps(snapshot, indent=2)
    out = SNAPSHOT_DIR / f"crypto_ta_{today_str}.json"
    out.write_text(payload)
    (SNAPSHOT_DIR / "crypto_ta_latest.json").write_text(payload)
    print(f"  Saved → {out.name}")

    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_crypto_ta()
    pprint.pprint({k: v for k, v in result.items()
                   if k not in ("top_gainers", "top_losers", "sectors")})
    print(f"\nGainers: {len(result['top_gainers'])}  "
          f"Losers: {len(result['top_losers'])}  "
          f"Breakouts: {len(result['breakout_candidates'])}  "
          f"Vol surge: {len(result['volume_surge'])}")
