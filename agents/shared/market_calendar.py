"""
agents/shared/market_calendar.py

Market open/closed detection for US and IDX markets.
Used by main.py to gate agent runs and post closed-day
notices to Discord instead of running full analysis.
"""
from __future__ import annotations

from datetime import date, timedelta


# ── Easter calculation (Butcher's algorithm) ──────────────────────────────────

def _easter(year: int) -> date:
    """Return the date of Easter Sunday for the given year."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day   = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


# ── Floating holiday calculators ──────────────────────────────────────────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the n-th occurrence of weekday (0=Mon…6=Sun) in month/year."""
    first = date(year, month, 1)
    # day-of-week of the 1st
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday in month/year."""
    # Start from the last day of the month and walk back
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _observe(d: date) -> date:
    """Apply Saturday→Friday / Sunday→Monday observation rule."""
    if d.weekday() == 5:   # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:   # Sunday
        return d + timedelta(days=1)
    return d


# ── US market calendar ────────────────────────────────────────────────────────

def _us_holidays(year: int) -> set[date]:
    """Return the set of observed NYSE holiday dates for a given year."""
    easter_sunday = _easter(year)
    good_friday   = easter_sunday - timedelta(days=2)

    holidays = {
        # Fixed holidays (with observation rule)
        _observe(date(year, 1,  1)),   # New Year's Day
        _observe(date(year, 6, 19)),   # Juneteenth
        _observe(date(year, 7,  4)),   # Independence Day
        _observe(date(year, 12, 25)),  # Christmas

        # Good Friday (no observation rule — always a weekday by definition)
        good_friday,

        # Floating holidays
        _nth_weekday(year, 1,  0, 3),  # MLK Day  — 3rd Mon Jan
        _nth_weekday(year, 2,  0, 3),  # Presidents Day — 3rd Mon Feb
        _last_weekday(year, 5, 0),     # Memorial Day — last Mon May
        _nth_weekday(year, 9,  0, 1),  # Labor Day — 1st Mon Sep
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving — 4th Thu Nov
    }
    return holidays


def is_us_market_open(check_date: date | None = None) -> bool:
    """Return True if US (NYSE/NASDAQ) markets are open on check_date.

    Uses today's date if check_date is None.
    """
    d = check_date or date.today()
    if d.weekday() >= 5:                    # Saturday or Sunday
        return False
    if d in _us_holidays(d.year):
        return False
    return True


# ── IDX market calendar ───────────────────────────────────────────────────────

# UPDATE THIS LIST ANNUALLY
# Official IDX holiday schedule:
# https://idx.co.id/en/investor-relations/trading-information/trading-holiday/
#
# Indonesian holidays shift yearly based on the Islamic Hijri calendar and
# official government decree (PP Cuti Bersama). The dates below are approximate
# fixed anchors for 2025-2026 scheduling purposes. Verify and update each
# January for the new calendar year.

_IDX_FIXED_HOLIDAYS: dict[int, list[tuple[int, int]]] = {
    2025: [
        (1,  1),   # New Year's Day
        (1, 29),   # Chinese New Year (Imlek)
        (3, 28),   # Good Friday (Wafat Isa Almasih)
        (3, 29),   # Holy Saturday (cuti bersama)
        (3, 31),   # Eid al-Fitr eve (cuti bersama)
        (4,  1),   # Eid al-Fitr Day 1
        (4,  2),   # Eid al-Fitr Day 2
        (4,  3),   # Eid al-Fitr cuti bersama
        (4,  4),   # Eid al-Fitr cuti bersama
        (4, 18),   # Isra Miraj
        (5,  1),   # Labour Day
        (5, 12),   # Waisak (Vesak Day)
        (5, 29),   # Ascension Day (Kenaikan Yesus Kristus)
        (6,  1),   # Pancasila Day
        (6,  6),   # Eid al-Adha
        (6, 27),   # Muharram / Islamic New Year
        (8, 17),   # Independence Day
        (9,  5),   # Maulid Nabi (Prophet's Birthday)
        (12, 25),  # Christmas
        (12, 26),  # Christmas cuti bersama
    ],
    2026: [
        (1,  1),   # New Year's Day
        (1, 29),   # Chinese New Year (Imlek) — approx
        (2, 17),   # Isra Miraj — approx
        (3, 20),   # Eid al-Fitr Day 1 — approx
        (3, 21),   # Eid al-Fitr Day 2 — approx
        (3, 22),   # Eid al-Fitr cuti bersama — approx
        (3, 23),   # Eid al-Fitr cuti bersama — approx
        (3, 24),   # Eid al-Fitr cuti bersama — approx
        (4,  3),   # Good Friday — approx
        (5,  1),   # Labour Day
        (5, 14),   # Waisak (Vesak Day) — approx
        (5, 14),   # Ascension Day — approx
        (5, 27),   # Eid al-Adha — approx
        (6,  1),   # Pancasila Day
        (6, 17),   # Muharram / Islamic New Year — approx
        (8, 17),   # Independence Day
        (9, 15),   # Maulid Nabi — approx
        (12, 25),  # Christmas
        (12, 26),  # Boxing Day / cuti bersama
    ],
}


def _idx_holidays(year: int) -> set[date]:
    """Return the set of IDX holiday dates for a given year."""
    pairs = _IDX_FIXED_HOLIDAYS.get(year, [])
    if not pairs:
        # Fallback for unlisted years: use minimal fixed set
        pairs = [
            (1,  1),  # New Year's Day
            (5,  1),  # Labour Day
            (6,  1),  # Pancasila Day
            (8, 17),  # Independence Day
            (12, 25), # Christmas
            (12, 26), # Boxing Day
        ]
    return {date(year, m, d) for m, d in pairs}


def is_idx_market_open(check_date: date | None = None) -> bool:
    """Return True if IDX (Indonesia Stock Exchange) is open on check_date.

    Uses today's date if check_date is None.
    """
    d = check_date or date.today()
    if d.weekday() >= 5:                    # Saturday or Sunday
        return False
    if d in _idx_holidays(d.year):
        return False
    return True


# ── Closed-day Discord messages ───────────────────────────────────────────────

def _fmt_today() -> str:
    """Today as DD-Mon-YYYY."""
    return date.today().strftime("%d-%b-%Y")


def _next_session_phrase() -> str:
    """Return 'tomorrow' or 'Monday' depending on today's weekday."""
    today_wd = date.today().weekday()
    if today_wd == 4:   # Friday
        return "Monday"
    return "tomorrow"


def get_market_closed_message(market: str) -> str:
    """Return a formatted Discord message for a closed-market day.

    Args:
        market: "US", "IDX", or "CRYPTO"

    Returns:
        Formatted string ready to publish, or "" for CRYPTO.
    """
    dt    = _fmt_today()
    nxt   = _next_session_phrase()

    if market == "US":
        return (
            f"**🇺🇸 US MARKET MOVEMENT — {dt}**\n\n"
            f"US markets are closed today.\n"
            f"Next trading session resumes {nxt}."
        )

    if market == "IDX":
        return (
            f"**🇮🇩 IDX MARKET MOVEMENT — {dt}**\n\n"
            f"IDX is closed today.\n"
            f"Next trading session resumes {nxt}."
        )

    return ""


def get_crypto_note() -> str:
    """Crypto never closes. Returns empty string — no closed message needed."""
    return ""
