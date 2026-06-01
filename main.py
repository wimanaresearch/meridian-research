from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from agents.shared.market_calendar import (
    is_us_market_open,
    is_idx_market_open,
    get_market_closed_message,
)

load_dotenv(Path(__file__).parent / ".env", override=True)

_STATE_DIR = Path(__file__).parent / "data" / "snapshots"


# ── Regime state helpers ──────────────────────────────────────────────────────

def save_snapshot(agent: str, data: dict) -> None:
    """Persist agent state (regime + date) to data/snapshots/{agent}_state.json."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    (_STATE_DIR / f"{agent}_state.json").write_text(json.dumps(data, indent=2))


def load_snapshot(agent: str) -> dict:
    """Load persisted agent state. Returns {} if no state file exists yet."""
    try:
        return json.loads((_STATE_DIR / f"{agent}_state.json").read_text())
    except Exception:
        return {}


def _extract_regime(report: str) -> str:
    """Parse the closing regime tag from a formatted report string.

    Handles both formats used across agents:
      Regime: Choppy                    (IDX)
      `Regime: RISK-ON | CONT.`        (US TA — with backticks)
    Returns just the regime token before any ' | ' suffix.
    """
    for line in reversed(report.strip().splitlines()):
        line = line.strip().strip("`").strip()
        if line.lower().startswith("regime:"):
            tag = line[len("regime:"):].strip()
            tag = tag.split("|")[0].strip()   # drop "| CONT." / "| SHIFT"
            if tag:
                return tag
    return "UNKNOWN"


def _extract_morning_regimes(report: str) -> tuple[str, str]:
    """Parse dual-regime tag from morning_macro report.

    Looks for closing line: Regime → US: {tag} | IDX: {tag}
    Returns (us_regime, idx_regime).
    """
    for line in reversed(report.strip().splitlines()):
        line = line.strip().strip("`").strip()
        low = line.lower()
        if low.startswith("regime") and "us:" in low and "idx:" in low:
            # e.g. "Regime → US: CAUTIOUS | IDX: Choppy"
            us_regime = "UNKNOWN"
            idx_regime = "UNKNOWN"
            parts = line.split("|")
            for part in parts:
                part = part.strip()
                if "us:" in part.lower():
                    us_regime = part.split(":", 1)[-1].strip()
                if "idx:" in part.lower():
                    idx_regime = part.split(":", 1)[-1].strip()
            if us_regime != "UNKNOWN" or idx_regime != "UNKNOWN":
                return us_regime, idx_regime
    return "UNKNOWN", "UNKNOWN"


# ── Agents ────────────────────────────────────────────────────────────────────

def run_morning_macro() -> None:
    from agents.morning_macro.collector import collect_morning_macro
    from agents.morning_macro.analyzer import analyze_morning_macro
    from publishers.discord import publish

    prior_us = load_snapshot("us_ta").get("regime") or os.getenv("PRIOR_REGIME_US_TA", "UNKNOWN")
    prior_idx = load_snapshot("idx_lq45").get("regime") or os.getenv("PRIOR_REGIME_IDX_LQ45", "UNKNOWN")

    print("[morning_macro] Collecting data...")
    snapshot = collect_morning_macro()

    print("[morning_macro] Generating macro radar brief...")
    full_brief = analyze_morning_macro(snapshot, prior_us_regime=prior_us, prior_idx_regime=prior_idx)
    responses = publish(full_brief, os.getenv("DISCORD_WEBHOOK_MACRO_RADAR"))
    print(f"[morning_macro] Posted to #macro-radar — {len(responses)} chunk(s)")

    us_regime, idx_regime = _extract_morning_regimes(full_brief)

    # Persist regimes so downstream agents pick them up
    if us_regime != "UNKNOWN":
        save_snapshot("us_ta", {"regime": us_regime, "date": date.today().isoformat()})
    if idx_regime != "UNKNOWN":
        save_snapshot("idx_lq45", {"regime": idx_regime, "date": date.today().isoformat()})

    # Save morning_macro snapshot with key metrics
    dollar_vol  = snapshot.get("dollar_vol", {})
    rates_bonds = snapshot.get("rates_bonds", {})
    crypto      = snapshot.get("crypto", {})
    save_snapshot("morning_macro", {
        "fetched_at":  snapshot.get("fetched_at"),
        "us_regime":   us_regime,
        "idx_regime":  idx_regime,
        "btc_price":   crypto.get("btc", {}).get("price"),
        "dxy_level":   dollar_vol.get("dxy", {}).get("level"),
        "us10y_level": rates_bonds.get("us10y", {}).get("level"),
        "vix_level":   dollar_vol.get("vix", {}).get("level"),
    })

    print(f"[morning_macro] Done  |  US: {us_regime}  |  IDX: {idx_regime}")


def run_crypto_ta() -> None:
    from agents.crypto_ta.collector import collect_crypto_ta
    from agents.crypto_ta.analyzer import analyze_crypto_ta
    from publishers.discord import publish

    prior = load_snapshot("crypto_ta")
    prior_regime = prior.get("regime") or "UNKNOWN"

    print("[crypto_ta] Collecting data...")
    snapshot = collect_crypto_ta()

    print("[crypto_ta] Analyzing...")
    report = analyze_crypto_ta(snapshot, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_CRYPTO_MOVEMENT"))
    print("[crypto_ta] Posted to #crypto-movement")

    regime = _extract_regime(report)
    save_snapshot("crypto_ta", {
        "fetched_at":    snapshot.get("fetched_at"),
        "regime":        regime,
        "btc_price":     snapshot.get("btc_detail", {}).get("current_price"),
        "btc_pct_24h":   snapshot.get("btc_detail", {}).get("pct_24h"),
        "total_mcap":    snapshot.get("global", {}).get("total_mcap"),
        "btc_dominance": snapshot.get("global", {}).get("btc_dominance"),
    })
    print(f"[crypto_ta] Done  |  Regime: {regime}")


def run_weekly_recap() -> None:
    from agents.weekly_recap.collector import collect_weekly_recap
    from agents.weekly_recap.analyzer import analyze_weekly_recap
    from publishers.discord import publish

    print("[weekly_recap] Collecting weekly data...")
    snapshot = collect_weekly_recap()

    print("[weekly_recap] Analyzing...")
    report = analyze_weekly_recap(snapshot)
    publish(report, os.getenv("DISCORD_WEBHOOK_WEEKLY_RECAP"))
    print("[weekly_recap] Posted to #weekly-recap")

    us_regime     = _extract_regime(report) if "US Equities" not in report else "UNKNOWN"
    idx_regime    = snapshot.get("snapshots", {}).get("idx_lq45", {}).get("regime", "UNKNOWN")
    crypto_regime = snapshot.get("snapshots", {}).get("crypto_ta", {}).get("regime", "UNKNOWN")

    sp500_chg  = snapshot.get("us_weekly", {}).get("indices", {}).get("sp500", {}).get("weekly_chg_pct")
    ihsg_chg   = snapshot.get("idx_weekly", {}).get("indices", {}).get("ihsg", {}).get("weekly_chg_pct")
    btc_chg    = snapshot.get("crypto_weekly", {}).get("btc", {}).get("pct_7d")

    save_snapshot("weekly_recap", {
        "fetched_at":       snapshot.get("fetched_at"),
        "week_ending":      snapshot.get("week_ending"),
        "us_regime":        us_regime,
        "idx_regime":       idx_regime,
        "crypto_regime":    crypto_regime,
        "sp500_weekly_chg": sp500_chg,
        "ihsg_weekly_chg":  ihsg_chg,
        "btc_weekly_chg":   btc_chg,
    })
    print("[weekly_recap] Done")


def run_crypto_news() -> None:
    from agents.crypto_news.collector import collect_crypto_news
    from agents.crypto_news.analyzer import analyze_crypto_news
    from publishers.discord import publish

    prior = load_snapshot("crypto_news")
    prior_regime = prior.get("regime") or "UNKNOWN"

    print("[crypto_news] Collecting data...")
    snapshot = collect_crypto_news()

    print("[crypto_news] Analyzing...")
    report = analyze_crypto_news(snapshot, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_CRYPTO_NEWS"))
    print("[crypto_news] Posted to #crypto-news")

    regime = _extract_regime(report)
    save_snapshot("crypto_news", {
        "fetched_at":           snapshot.get("fetched_at"),
        "regime":               regime,
        "notable_movers_count": len(snapshot.get("notable_movers", [])),
        "articles_fetched":     len(snapshot.get("rss_news", [])) + len(snapshot.get("finnhub_news", [])),
    })
    print(f"[crypto_news] Done  |  Regime: {regime}")


def run_liquidity() -> None:
    from agents.analyst.collector import collect_data
    from agents.analyst.analyzer import analyze
    from publishers.discord import publish

    snapshot = collect_data()
    report = analyze(snapshot)
    publish(report, os.getenv("DISCORD_WEBHOOK_LIQUIDITY_RADAR"))
    print("[Liquidity] Published")


def run_liquidity_radar() -> None:
    from agents.liquidity_radar.collector import collect_liquidity_radar
    from agents.liquidity_radar.analyzer import analyze_liquidity_radar
    from publishers.discord import publish

    prior = load_snapshot("liquidity_radar")
    prior_regime = prior.get("regime") or "UNKNOWN"

    print("[liquidity_radar] Collecting data...")
    snapshot = collect_liquidity_radar()

    print("[liquidity_radar] Analyzing...")
    report = analyze_liquidity_radar(snapshot, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_LIQUIDITY_RADAR"))
    print("[liquidity_radar] Posted to #liquidity-radar")

    regime = _extract_regime(report)
    save_snapshot("liquidity_radar", {
        "fetch_date":       snapshot.get("fetch_date"),
        "regime":           regime,
        "net_liquidity":    snapshot.get("net_liquidity"),
        "fed_total_assets": snapshot.get("fed_total_assets", {}).get("current"),
        "tga":              snapshot.get("tga", {}).get("current"),
        "on_rrp":           snapshot.get("on_rrp", {}).get("current"),
        "bank_reserves":    snapshot.get("bank_reserves", {}).get("current"),
    })
    print(f"[liquidity_radar] Done  |  Regime: {regime}")


def run_us_news() -> None:
    from agents.us_news.collector import collect_news
    from agents.us_news.analyzer import analyze_news
    from publishers.discord import publish

    snapshot = collect_news()
    report = analyze_news(snapshot)
    publish(report, os.getenv("DISCORD_WEBHOOK_US_NEWS"))
    print("[US News] Published")


def run_us_ta() -> None:
    from agents.us_ta.collector import collect_ta
    from agents.us_ta.analyzer import analyze_ta
    from publishers.discord import publish

    if not is_us_market_open():
        msg = get_market_closed_message("US")
        publish(msg, os.getenv("DISCORD_WEBHOOK_US_MOVEMENT"))
        print("[us_ta] US market closed today — posted notice")
        return

    prior = load_snapshot("us_ta")
    prior_regime = prior.get("regime") or os.getenv("PRIOR_REGIME_US_TA", "UNKNOWN")

    print("[US TA] Collecting...")
    snapshot = collect_ta()
    print("[US TA] Analyzing...")
    report = analyze_ta(snapshot, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_US_MOVEMENT"))

    regime = _extract_regime(report)
    save_snapshot("us_ta", {"regime": regime, "date": date.today().isoformat()})
    print(f"[US TA] Done  |  Regime: {regime}")


def run_idx_news() -> None:
    from agents.idx_news.collector import collect_idx_news
    from agents.idx_news.analyzer import analyze_idx_news
    from publishers.discord import publish

    # idx_news is a NEWS agent — always runs regardless of market hours.
    # The collector handles weekend/weekday news window (24h vs 72h) internally.

    prior = load_snapshot("idx_news")
    prior_regime = prior.get("regime") or "UNKNOWN"

    print("[idx_news] Collecting data...")
    snapshot = collect_idx_news()

    print("[idx_news] Analyzing...")
    report = analyze_idx_news(snapshot, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_IDX_NEWS"))
    print("[idx_news] Posted to #idx-market-news")

    regime = _extract_regime(report)
    save_snapshot("idx_news", {
        "fetched_at":   snapshot.get("fetched_at"),
        "regime":       regime,
        "idr_rate":     snapshot.get("idr", {}).get("rate"),
        "coal_price":   snapshot.get("commodities", {}).get("coal", {}).get("price"),
        "nickel_price": snapshot.get("commodities", {}).get("nickel", {}).get("price"),
        "cpo_price":    snapshot.get("commodities", {}).get("cpo", {}).get("price"),
    })
    print(f"[idx_news] Done  |  Regime: {regime}")


def run_idx_lq45() -> None:
    from agents.idx_lq45.collector import collect_idx_lq45
    from agents.idx_lq45.analyzer import analyze_lq45
    from publishers.discord import publish

    if not is_idx_market_open():
        msg = get_market_closed_message("IDX")
        publish(msg, os.getenv("DISCORD_WEBHOOK_IDX_MOVEMENT"))
        print("[idx_lq45] IDX closed today — posted notice")
        return

    prior = load_snapshot("idx_lq45")
    prior_regime = prior.get("regime") or os.getenv("PRIOR_REGIME_IDX_LQ45", "UNKNOWN")

    print("[IDX LQ45] Collecting...")
    snapshot = collect_idx_lq45()
    print("[IDX LQ45] Analyzing...")
    report = analyze_lq45(snapshot, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_IDX_MOVEMENT"))

    regime = _extract_regime(report)
    save_snapshot("idx_lq45", {"regime": regime, "date": date.today().isoformat()})
    print(f"[IDX LQ45] Done — 1 post sent  |  Regime: {regime}")


def run_idx_lq45_weekly() -> None:
    from agents.idx_lq45.collector import collect_idx_lq45
    from agents.idx_lq45.weekly_analyzer import analyze_lq45_weekly, load_recent_snapshots
    from publishers.discord import publish

    prior = load_snapshot("idx_lq45")
    prior_regime = prior.get("regime") or os.getenv("PRIOR_REGIME_IDX_LQ45", "UNKNOWN")

    print("[IDX LQ45 Weekly] Loading recent snapshots...")
    snapshots = load_recent_snapshots(n=5)

    if not snapshots:
        print("[IDX LQ45 Weekly] No snapshots found — running fresh collect...")
        snapshots = [collect_idx_lq45()]

    print(f"[IDX LQ45 Weekly] Analyzing {len(snapshots)} snapshots...")
    report = analyze_lq45_weekly(snapshots, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_IDX_MOVEMENT"))

    regime = _extract_regime(report)
    save_snapshot("idx_lq45", {"regime": regime, "date": date.today().isoformat()})
    print(f"[IDX LQ45 Weekly] Done  |  Regime: {regime}")


def run_us_ta_weekly() -> None:
    from agents.us_ta.analyzer import analyze_ta_weekly, load_recent_snapshots
    from agents.us_ta.collector import collect_ta
    from publishers.discord import publish

    prior = load_snapshot("us_ta")
    prior_regime = prior.get("regime") or os.getenv("PRIOR_REGIME_US_TA", "UNKNOWN")

    print("[US TA Weekly] Loading recent snapshots...")
    snapshots = load_recent_snapshots(n=5)

    if not snapshots:
        print("[US TA Weekly] No snapshots found — running fresh collect...")
        snapshots = [collect_ta()]

    print(f"[US TA Weekly] Analyzing {len(snapshots)} snapshots...")
    report = analyze_ta_weekly(snapshots, prior_regime=prior_regime)
    publish(report, os.getenv("DISCORD_WEBHOOK_US_MOVEMENT"))

    regime = _extract_regime(report)
    save_snapshot("us_ta", {"regime": regime, "date": date.today().isoformat()})
    print(f"[US TA Weekly] Done  |  Regime: {regime}")


# ── Dispatch ──────────────────────────────────────────────────────────────────

# Daily agents — included in `all`
AGENTS = {
    "morning_macro":    run_morning_macro,
    "liquidity":        run_liquidity,
    "liquidity_radar":  run_liquidity_radar,
    "us_news":          run_us_news,
    "us_ta":            run_us_ta,
    "idx_lq45":         run_idx_lq45,
    "idx_news":         run_idx_news,
    "crypto_ta":        run_crypto_ta,
    "crypto_news":      run_crypto_news,
}

# Weekly agents — not included in `all` (Saturday only)
WEEKLY_AGENTS = {
    "us_ta_weekly":    run_us_ta_weekly,
    "idx_lq45_weekly": run_idx_lq45_weekly,
    "weekly_recap":    run_weekly_recap,
}

ALL_AGENTS = {**AGENTS, **WEEKLY_AGENTS}

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target == "all":
        for fn in AGENTS.values():
            fn()
    elif target in ALL_AGENTS:
        ALL_AGENTS[target]()
    else:
        daily_keys = "|".join(AGENTS)
        weekly_keys = "|".join(WEEKLY_AGENTS)
        print(f"Unknown agent: {target}")
        print(f"Usage: python main.py [{daily_keys}|all]")
        print(f"       python main.py [{weekly_keys}]  (Saturday)")
        sys.exit(1)
