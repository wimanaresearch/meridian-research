# market-intel-bot — Claude Code Reference

## Project Purpose
Automated Discord market intelligence system.
Fetches financial data, generates AI analysis
via Gemini, posts formatted briefs to Discord
via webhooks. Runs on GitHub Actions schedule.

---

## Stack
- Runtime: GitHub Actions (production)
  Local Python via Claude Code (development)
- LLM: Gemini API (gemini-2.0-flash)
  Library: google-generativeai
  Key: GEMINI_API_KEY
- Data sources:
  yfinance — prices, indices, ETFs, futures
  Finnhub — news, economic calendar
  CoinGecko — crypto prices, market caps
  IDX API — Indonesian stocks, announcements
  NY Fed, Treasury, FRED — macro/liquidity
  feedparser — RSS news feeds
- Storage: Supabase (snapshots between runs)
- Distribution: Discord webhooks
  via requests.POST
- Config: .env (local) / GitHub Secrets (prod)

---

## Project Structure
```
market-intel-bot/
├── CLAUDE.md                  ← you are here
├── main.py                    ← orchestrator
├── requirements.txt
├── .env                       ← never commit
├── .gitignore
├── agents/
│   ├── shared/
│   │   ├── tone.py            ← get_tone_block()
│   │   └── market_calendar.py ← holiday logic
│   ├── morning_macro/
│   │   ├── __init__.py
│   │   ├── collector.py
│   │   └── analyzer.py
│   ├── us_ta/
│   │   ├── __init__.py
│   │   ├── collector.py
│   │   └── analyzer.py
│   ├── idx_lq45/
│   │   ├── __init__.py
│   │   ├── collector.py
│   │   ├── analyzer.py
│   │   └── weekly_analyzer.py
│   ├── crypto_ta/
│   │   ├── __init__.py
│   │   ├── collector.py
│   │   └── analyzer.py
│   ├── idx_news/
│   │   ├── __init__.py
│   │   ├── collector.py
│   │   └── analyzer.py
│   ├── crypto_news/
│   │   ├── __init__.py
│   │   ├── collector.py
│   │   └── analyzer.py
│   └── weekly_recap/
│       ├── __init__.py
│       ├── collector.py
│       └── analyzer.py
├── publishers/
│   └── discord.py             ← chunk-split publisher
├── data/
│   └── snapshots/             ← never commit
│       ├── morning_macro_latest.json
│       ├── us_ta_latest.json
│       ├── idx_lq45_latest.json
│       ├── crypto_ta_latest.json
│       ├── idx_news_latest.json
│       ├── crypto_news_latest.json
│       └── weekly_recap_latest.json
└── .github/
    └── workflows/
        ├── morning-macro.yml
        ├── idx-news.yml
        ├── crypto-ta.yml
        ├── us-movement.yml
        ├── idx-movement.yml
        ├── crypto-news.yml
        └── weekly-recap.yml
```

---

## Agent Pattern
Every agent follows this exact structure:

```python
# collector.py
def collect_{agent_name}() -> dict:
    # fetch data from APIs
    # clean and structure
    # handle failures gracefully
    # add fetched_at timestamp (DD-Mon-YYYY HH:MM WIB)
    return clean_dict

# analyzer.py
from agents.shared.tone import get_tone_block
import google.generativeai as genai

def analyze_{agent_name}(data: dict, 
                          prior_regime: str = "UNKNOWN") -> str:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        system_instruction=system_prompt  # f-string with {get_tone_block()}
    )
    response = model.generate_content(user_message)
    return response.text

# main.py command block
def run_{agent_name}():
    print("[{agent}] Collecting...")
    data = collect_{agent_name}()
    
    print("[{agent}] Analyzing...")
    brief = analyze_{agent_name}(data, prior_regime)
    
    print("[{agent}] Posting to Discord...")
    publish(brief, os.getenv("DISCORD_WEBHOOK_{AGENT}"))
    
    save_snapshot("{agent}", {...})
    print("[{agent}] Done")
```

---

## main.py Commands
```
python main.py morning_macro     → #macro-radar
python main.py idx_news          → #idx-market-news
python main.py crypto_ta         → #crypto-movement
python main.py us_ta             → #us-stock-movement
python main.py idx_lq45          → #idx-movement
python main.py crypto_news       → #crypto-news
python main.py weekly_recap      → #weekly-recap
```

---

## Discord Channels & Webhooks
```
Channel                Webhook env var
───────────────────────────────────────────────
#macro-radar           DISCORD_WEBHOOK_MACRO_RADAR
#idx-market-news       DISCORD_WEBHOOK_IDX_NEWS
#crypto-movement       DISCORD_WEBHOOK_CRYPTO_MOVEMENT
#us-stock-movement     DISCORD_WEBHOOK_US_MOVEMENT
#idx-movement          DISCORD_WEBHOOK_IDX_MOVEMENT
#crypto-news           DISCORD_WEBHOOK_CRYPTO_NEWS
#weekly-recap          DISCORD_WEBHOOK_WEEKLY_RECAP
#signal-alerts         DISCORD_WEBHOOK_ALERTS
```

---

## GitHub Actions Schedule
```
WIB     UTC          Workflow          Days
──────────────────────────────────────────────────
06:30   23:30*       morning-macro     Daily
08:00   01:00        idx-news          Mon-Fri
09:00   02:00        crypto-ta         Daily
09:00   02:00†       us-movement       Tue-Sat
14:00   07:00        crypto-news       Daily
16:30   09:30        idx-movement      Mon-Fri
19:00   12:00 Sun    weekly-recap      Sunday

* = previous UTC day
† = covers Mon-Fri US sessions (posted next morning WIB)
```

---

## publishers/discord.py
```python
def split_message(text, limit=1850):
    # splits on newlines only
    # never splits mid-line
    # returns list of chunk strings

def publish(text, webhook_url):
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        requests.post(webhook_url, 
                      json={"content": chunk})
        print(f"  Chunk {i+1}/{len(chunks)} sent")
        time.sleep(0.5)
```

---

## agents/shared/tone.py
```python
def get_tone_block() -> str:
    # Returns standard tone, formatting,
    # and plain language rules
    # Injected into every analyzer prompt
    # via f-string: {get_tone_block()}
```

Key rules in tone block:
- Mixed audience: experienced + newer investors
- Lead with data, follow with plain English
- Always answer: "what does this actually mean?"
- No jargon without explanation
- Comma thousands, dot decimal
- Drop trailing zeros
- % changes always in brackets: (+1.23%)
- No markdown tables, line-by-line only

---

## agents/shared/market_calendar.py
```python
def is_us_market_open(date=None) -> bool:
    # False on weekends + US public holidays

def is_idx_market_open(date=None) -> bool:
    # False on weekends + Indonesian holidays
    # UPDATE HOLIDAY LIST ANNUALLY
    # https://idx.co.id/en/investor-relations/
    #   trading-information/trading-holiday/

def get_market_closed_message(market: str) -> str:
    # Returns formatted Discord closed message
    # market: "US" or "IDX"
```

Market check in main.py:
- us_ta: wraps in is_us_market_open()
- idx_lq45: wraps in is_idx_market_open()
- idx_news: wraps in is_idx_market_open()
- morning_macro: NO check — runs every day
- crypto_ta: NO check — crypto never closes
- crypto_news: NO check — crypto never closes
- weekly_recap: NO check — runs Sunday only

---

## Formatting Standards (all agents)
```
Title:       **{emoji} CHANNEL NAME — DD-Mon-YYYY**
Regime:      Regime: {regime} | {CONT./SHIFT}
             (shown ONCE in header only)
Subheadings: **{emoji} ALL CAPS TEXT**
Dividers:    ──────────────────────
Gainers:     🟢
Losers:      🔴
Neutral:     🟡
Tables:      NEVER — line-by-line only
% changes:   always in brackets (+1.23%)
Prices:      comma thousands, dot decimal
             stock prices = whole numbers only
             yields = always 2 decimals
             yield changes = in bps
```

---

## Regime Scale (all agents, all markets)
```
Structural Bull  → strong uptrend, risk-on
Recovering       → bouncing, not confirmed
Choppy           → range-bound, mixed signals
Distributing     → weakening, smart money selling
Structural Bear  → downtrend, risk-off
```
Always paired with: CONT. or SHIFT

---

## Key Technical Decisions
```
IDX30/KOMPAS100   → IDX API, not yfinance
                    (yfinance returns empty)

TOTAL2/TOTAL3     → calculated from snapshot delta
change %            not available from CoinGecko directly

US top movers     → filter volume_ratio > 1.5 only
                    (conviction filter)

IDX top movers    → IDX API GetSecuritiesAjax
                    (faster than scanning yfinance)

Weekend news      → expand window to 72h
                    (controlled in collector)

Snapshot storage  → data/snapshots/ locally
                    Supabase in production
                    used for prior_regime carry-forward
                    and delta calculations

Output length     → target chars per agent:
  morning_macro     3,500-4,500 chars
  us_ta             5,000-6,000 chars
  idx_lq45          4,500-5,500 chars
  crypto_ta         5,000-6,000 chars
  idx_news          4,500-5,500 chars
  crypto_news       4,500-5,500 chars
  weekly_recap      3,000-3,800 chars
```

---

## Data Sources Reference
```
Source          What it provides          Auth
──────────────────────────────────────────────────
yfinance        prices, OHLCV, MAs        none
Finnhub         news, econ calendar       FINNHUB_API_KEY
CoinGecko       crypto data, global mcap  COINGECKO_API_KEY
IDX API         Indonesian stocks, flow   none
feedparser      RSS news feeds            none
NY Fed          ON RRP data               none
Treasury        TGA balance               none
FRED            Fed balance sheet         none
Supabase        snapshot storage          SUPABASE_URL
                                          SUPABASE_KEY
```

---

## .env Keys Required
```
# LLM
GEMINI_API_KEY=

# Data
FINNHUB_API_KEY=
COINGECKO_API_KEY=

# Storage
SUPABASE_URL=
SUPABASE_KEY=

# Discord Webhooks
DISCORD_WEBHOOK_MACRO_RADAR=
DISCORD_WEBHOOK_IDX_NEWS=
DISCORD_WEBHOOK_CRYPTO_MOVEMENT=
DISCORD_WEBHOOK_US_MOVEMENT=
DISCORD_WEBHOOK_IDX_MOVEMENT=
DISCORD_WEBHOOK_CRYPTO_NEWS=
DISCORD_WEBHOOK_WEEKLY_RECAP=
DISCORD_WEBHOOK_ALERTS=
```

---

## Phase Roadmap
```
Phase 1 ✅ COMPLETE
  morning_macro, us_ta, idx_lq45,
  crypto_ta, idx_news, crypto_news,
  weekly_recap

Phase 2 — NEXT
  us_news          → #us-market-news
  us_weekly        → #weekly-deep-dives
  idx_weekly       → #weekly-deep-dives
  crypto_weekly    → #weekly-deep-dives
  signal_alerts    → #signal-alerts (regime shift trigger)

Phase 3 — FUTURE
  weekly_synthesis → orchestrator, reads all snapshots
  ask_the_bot      → interactive Q&A from snapshots
  premium_signals  → higher frequency, gated channel
```

---

## GitHub Repo Notes
- Public repo
- All secrets in GitHub Secrets only
- .env never committed (.gitignore)
- data/snapshots/ never committed
- Prompts and logic in code (acceptable for public)
- One workflow yml per agent in .github/workflows/

---

## Rules For Claude Code Sessions
- LLM is always Gemini, never Claude/Anthropic API
- Always follow existing agent pattern exactly
- Always inject get_tone_block() in analyzers
- Always use f-string for system prompts
- Check market_calendar.py for market-dependent agents
- Test collector before full pipeline test
- Never suggest new paid APIs beyond existing stack
- When fixing bugs: show only changed sections
- When building new agents: show all new files
