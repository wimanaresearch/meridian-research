"""
agents/liquidity_radar/collector.py

Collects Fed plumbing and liquidity data via FRED and NY Fed.

FRED series used:
  WALCL   — Fed Total Assets (millions)
  WSOMA   — SOMA Holdings / Securities Held Outright (millions)
  WTREGEN — Treasury General Account (billions)
  RRPONTSYD — ON Reverse Repo (billions)
  WRESBAL — Bank Reserves (millions)
  WRMFSL  — MMF Retail Total Assets (billions)

SOFR from NY Fed reference rates API.

Net liquidity = Fed Total Assets - TGA - ON RRP
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import time

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

SNAPSHOT_DIR = Path(__file__).parents[2] / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOT_FILE = SNAPSHOT_DIR / "liquidity_radar_latest.json"

WIB        = timezone(timedelta(hours=7))
FRED_BASE  = "https://api.stlouisfed.org/fred/series/observations"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _r(v, d: int = 2):
    try:
        return round(float(v), d) if v is not None else None
    except (TypeError, ValueError):
        return None


def _wow(current, prior):
    if current is None or prior is None:
        return None
    return _r(current - prior, 2)


def _fred(series_id: str, api_key: str, divisor: float = 1.0) -> tuple[float | None, float | None]:
    """
    Fetch the two most recent non-null observations from FRED.
    Returns (latest_value, prior_value) divided by `divisor`.
    Divisor = 1000 converts millions → billions.
    """
    try:
        resp = requests.get(
            FRED_BASE,
            params={
                "series_id":         series_id,
                "api_key":           api_key,
                "file_type":         "json",
                "sort_order":        "desc",
                "limit":             "10",
                "observation_start": "2020-01-01",
            },
            timeout=20,
        )
        resp.raise_for_status()
        obs = [
            o for o in resp.json().get("observations", [])
            if o.get("value", ".") not in (".", "")
        ]
        if not obs:
            return None, None
        latest = _r(float(obs[0]["value"]) / divisor)
        prior  = _r(float(obs[1]["value"]) / divisor) if len(obs) >= 2 else None
        return latest, prior
    except Exception as e:
        print(f"  FRED {series_id} error: {e}")
        return None, None


def _fetch_sofr() -> float | None:
    """
    Fetch latest SOFR rate from NY Fed reference rates API.
    Endpoint: https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json
    """
    urls = [
        "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json",
        "https://markets.newyorkfed.org/api/rates/all/last/1.json",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()
            # Try refRates array first
            rates = data.get("refRates", [])
            if not rates:
                # Try nested structure
                rates = data.get("refRates", {}).get("refRate", [])
            for r in rates:
                name = r.get("type", r.get("rateName", "")).lower()
                if "sofr" in name:
                    val = _r(float(r.get("percentRate", r.get("rate", 0))), 4)
                    print(f"  SOFR: {val}%")
                    return val
            # If single-rate endpoint, grab first
            if rates:
                val = _r(float(rates[0].get("percentRate", rates[0].get("rate", 0))), 4)
                print(f"  SOFR: {val}%")
                return val
        except Exception as e:
            print(f"  SOFR ({url}): {e}")
            continue
    return None


def _load_prior_snapshot() -> dict:
    try:
        if SNAPSHOT_FILE.exists():
            data = json.loads(SNAPSHOT_FILE.read_text())
            print(f"  Prior snapshot: {data.get('fetch_date', 'unknown')}")
            return data
    except Exception as e:
        print(f"  Prior snapshot error: {e}")
    return {}


# ── Entry point ───────────────────────────────────────────────────────────────

def collect_liquidity_radar() -> dict:
    now_wib    = datetime.now(timezone.utc).astimezone(WIB)
    fetch_date = now_wib.strftime("%d-%b-%Y")

    fred_key = os.getenv("FRED_API_KEY", "")
    if not fred_key:
        print("  WARNING: FRED_API_KEY not set")

    prior = _load_prior_snapshot()

    # ── [1] Fed Total Assets — WALCL (millions → billions) ───────────────────
    print("  [1] Fed Total Assets (FRED: WALCL)...")
    fed_cur, fed_prior = _fred("WALCL", fred_key, divisor=1000)
    print(f"      current={fed_cur}B  prior={fed_prior}B")
    time.sleep(0.4)

    # ── [2] SOMA Holdings — WSECOUT (millions → billions) ────────────────────
    # WSECOUT: Total securities held outright (Fed H.4.1, weekly)
    #   Value ~$6.4T = Treasuries (~$4.4T) + MBS (~$2.0T) + residual agency debt
    #   Verified 2026-06: TREAST($4,462B) + WSHOMCB($1,965B) ≈ WSECOUT($6,437B) ✓
    # Fallback: TREAST (Treasury securities only, ~$4.4T) if WSECOUT fails
    # Note: WSECFA, WSHOSA, WSOMA all return 400 — discontinued/invalid series IDs
    print("  [2] SOMA Holdings (FRED: WSECOUT)...")
    soma_cur, soma_prior = _fred("WSECOUT", fred_key, divisor=1000)
    if soma_cur is None:
        print("      WSECOUT unavailable, trying TREAST (sleep 2s first)...")
        time.sleep(2)
        soma_cur, soma_prior = _fred("TREAST", fred_key, divisor=1000)
    print(f"      current={soma_cur}B  prior={soma_prior}B")
    time.sleep(0.4)

    # ── [3] Treasury General Account — WTREGEN (millions → billions) ─────────
    print("  [3] TGA (FRED: WTREGEN)...")
    tga_cur, tga_prior = _fred("WTREGEN", fred_key, divisor=1000)
    print(f"      current={tga_cur}B  prior={tga_prior}B")
    time.sleep(0.4)

    # ── [4] ON Reverse Repo — RRPONTSYD (billions) ───────────────────────────
    print("  [4] ON RRP (FRED: RRPONTSYD)...")
    rrp_cur, rrp_prior = _fred("RRPONTSYD", fred_key, divisor=1.0)
    print(f"      current={rrp_cur}B  prior={rrp_prior}B")
    time.sleep(0.4)

    # ── [5] Bank Reserves — WRESBAL (millions → billions) ────────────────────
    print("  [5] Bank Reserves (FRED: WRESBAL)...")
    res_cur, res_prior = _fred("WRESBAL", fred_key, divisor=1000)
    print(f"      current={res_cur}B  prior={res_prior}B")
    time.sleep(0.4)

    # ── [6] SOFR — NY Fed ─────────────────────────────────────────────────────
    print("  [6] SOFR (NY Fed)...")
    sofr = _fetch_sofr()

    # ── [7] MMF Total Assets — WRMFSL (billions, retail subset) ──────────────
    # WRMFSL = retail MMF assets (~$1T). For institutional: WRMFNS.
    # Combined institutional + retail ≈ total MMF system (~$6-7T).
    print("  [7] MMF Assets (FRED: WRMFSL retail + WRMFNS institutional)...")
    time.sleep(0.4)
    mmf_ret_cur,  mmf_ret_prior  = _fred("WRMFSL", fred_key, divisor=1.0)
    time.sleep(0.4)
    mmf_inst_cur, mmf_inst_prior = _fred("WRMFNS", fred_key, divisor=1.0)
    # Sum retail + institutional for total MMF
    mmf_cur   = _r(mmf_ret_cur  + mmf_inst_cur)  if (mmf_ret_cur  and mmf_inst_cur)  else (mmf_ret_cur  or mmf_inst_cur)
    mmf_prior = _r(mmf_ret_prior + mmf_inst_prior) if (mmf_ret_prior and mmf_inst_prior) else (mmf_ret_prior or mmf_inst_prior)
    print(f"      retail={mmf_ret_cur}B  institutional={mmf_inst_cur}B  total={mmf_cur}B")

    # ── Net liquidity: Fed Total Assets - TGA - ON RRP ────────────────────────
    net_liq = None
    if fed_cur is not None and tga_cur is not None and rrp_cur is not None:
        net_liq = _r(fed_cur - tga_cur - rrp_cur)

    net_liq_prior = None
    if fed_prior is not None and tga_prior is not None and rrp_prior is not None:
        net_liq_prior = _r(fed_prior - tga_prior - rrp_prior)
    elif prior:
        net_liq_prior = prior.get("net_liquidity")

    net_liq_wow = _wow(net_liq, net_liq_prior)

    snapshot = {
        "fetch_date": fetch_date,

        "fed_total_assets": {
            "current":    fed_cur,
            "prior_week": fed_prior,
            "wow_change": _wow(fed_cur, fed_prior),
        },
        "soma_holdings": {
            "current":    soma_cur,
            "prior_week": soma_prior,
            "wow_change": _wow(soma_cur, soma_prior),
        },
        "tga": {
            "current":    tga_cur,
            "prior_week": tga_prior,
            "wow_change": _wow(tga_cur, tga_prior),
        },
        "on_rrp": {
            "current":    rrp_cur,
            "prior_week": rrp_prior,
            "wow_change": _wow(rrp_cur, rrp_prior),
        },
        "bank_reserves": {
            "current":    res_cur,
            "prior_week": res_prior,
            "wow_change": _wow(res_cur, res_prior),
        },
        "sofr_rate": sofr,
        "mmf_total_assets": {
            "current":    mmf_cur,
            "prior_week": mmf_prior,
            "wow_change": _wow(mmf_cur, mmf_prior),
        },

        "net_liquidity":       net_liq,
        "net_liquidity_prior": net_liq_prior,
        "net_liquidity_wow":   net_liq_wow,

        "prior_snapshot": {
            "fetch_date":       prior.get("fetch_date"),
            "net_liquidity":    prior.get("net_liquidity"),
            "fed_total_assets": prior.get("fed_total_assets", {}).get("current") if prior else None,
            "tga":              prior.get("tga", {}).get("current") if prior else None,
            "on_rrp":           prior.get("on_rrp", {}).get("current") if prior else None,
            "regime":           prior.get("regime"),
        },
    }

    SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2))
    print(f"  Saved → liquidity_radar_latest.json")
    print(f"  Net liquidity: {net_liq}B  (WoW: {net_liq_wow}B)")

    return snapshot


if __name__ == "__main__":
    import pprint
    result = collect_liquidity_radar()
    pprint.pprint({k: v for k, v in result.items() if k != "prior_snapshot"})
