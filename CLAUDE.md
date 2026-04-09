# Auto-Trader Bot — Claude Code Project Instructions

## What This Project Is

An autonomous stock trading bot. It scans for momentum breakouts using Massive real-time data, confirms signals with LunarCrush social sentiment, and executes trades via Alpaca. Fully autonomous — owner (Mike) reviews logs after the fact.

**Stack:** Python 3.11+ | Alpaca (execution) | Massive (market data) | LunarCrush (sentiment) | Claude Haiku (edge-case analysis)
**Capital:** $1,000 — paper trading first, then live
**Strategy:** Hybrid momentum + social sentiment, swing trading (1-5 day holds)

---

## CRITICAL: Token Optimization Rules

This project runs on Claude Code. Every token costs money. Follow these rules strictly:

### DO
- Read files before editing — never rewrite what you haven't read
- Use targeted edits (Edit tool) — never rewrite entire files to change a few lines
- Keep responses concise — no summaries of what you just did unless asked
- Cache API responses — never call the same endpoint twice in one cycle
- Batch operations — if checking 5 tickers, make one call with a list, not 5 separate calls
- Use .env for all secrets — never hardcode, never log API keys
- Write modular code — small focused functions, one responsibility each
- Run tests with `pytest -x` (stop on first failure) — don't waste tokens on full suite when something's broken

### DON'T
- Don't read the entire project plan every session — it's in PLAN.md if you need it, but the summary below is enough for most tasks
- Don't explain code you're writing unless asked — just write it
- Don't generate boilerplate comments in code — only comment non-obvious logic
- Don't reinstall dependencies if requirements.txt hasn't changed
- Don't re-read files you've already read in this session
- Don't use MCP tools for simple operations that a direct API call handles — MCP has overhead
- Don't ask what to do next — check the Phase Tracker below and pick up the next incomplete task

---

## Architecture (memorize this — don't re-read PLAN.md for it)

```
Scheduler (APScheduler, every 5 min market hours)
  → Scanner (Massive data → technical indicators → momentum score)
    → Sentiment (LunarCrush Galaxy Score primary, Claude Haiku backup)
      → Risk Manager (position sizing, exposure limits, circuit breakers)
        → Executor (Alpaca limit orders + stops)
          → Logger (JSON trade log + daily summary)
```

### File Layout
```
auto-trader/
├── config/
│   ├── settings.yaml      # ALL tunable params — strategy, risk, schedule
│   └── .env               # API keys (gitignored)
├── src/
│   ├── main.py            # Entry point + scheduler
│   ├── data_client.py     # Unified data layer (Massive + LunarCrush + Alpaca)
│   ├── scanner.py         # Universe filter + momentum scoring
│   ├── sentiment.py       # LunarCrush primary + Claude Haiku backup
│   ├── risk_manager.py    # Position sizing, exposure, circuit breakers
│   ├── executor.py        # Alpaca orders + stop management
│   ├── portfolio.py       # Position tracking, P&L, metrics
│   └── logger.py          # Trade log, daily summaries, decision audit
├── data/
│   ├── watchlist.json     # Daily universe (regenerated each morning)
│   ├── trades.json        # Append-only trade log
│   └── daily_summary/     # Daily P&L CSVs
├── tests/
│   ├── test_scanner.py
│   ├── test_sentiment.py
│   ├── test_risk_manager.py
│   ├── test_executor.py
│   └── test_data_client.py
├── CLAUDE.md              # THIS FILE — project brain
├── PLAN.md                # Full project plan (reference only)
├── requirements.txt
└── .gitignore
```

---

## Strategy Summary

### Universe Filter (daily 9:25 AM ET)
- Market cap > $2B, avg volume > 1M, price $5-$500
- LunarCrush Galaxy Score > 50
- Exclude: earnings within 2 days, halted stocks

### Momentum Score (every 5 min, 9:45 AM - 3:45 PM ET)
Score 0-100 based on: SMA20 (20%), RSI 50-70 (15%), volume surge 1.5x (20%), MACD crossover (10%), 5-day momentum (10%), LC social volume spike 2x (15%), LC sentiment shift (10%).
**Score > 70 → sentiment check.**

### Sentiment Confirmation
- Primary: LunarCrush Galaxy Score > 60, sentiment bullish, social volume rising
- Backup: Claude Haiku BULLISH with confidence >= 7
- **Confirmed → BUY**

### Exit Rules
- Stop-loss: -5% from entry → sell
- Trailing stop: -3% from high → sell
- Profit target: +10% → sell half, trail rest
- Sentiment reversal: LC flips bearish → sell
- Time: > 5 days → re-evaluate, sell if not bullish

### Risk Rules (HARD-CODED, NEVER OVERRIDE)
- Max position: 20% of portfolio
- Max open positions: 5
- Max daily loss: 3% → halt trading for the day
- Max weekly loss: 7% → halt until Monday
- Max per-trade risk: 1%
- No trading 9:30-9:45 or 3:45-4:00
- No earnings plays (2-day buffer)
- If LunarCrush API down → halt all new entries

---

## Phase Tracker

This is the build roadmap. Pick up the next incomplete task. Mark done by changing [ ] to [x].

### Phase 1: Foundation
- [x] Scaffold project structure (dirs, files, configs)
- [x] Install dependencies: `pip install -r requirements.txt`
- [x] Build `data_client.py` — Massive client (OHLCV, fundamentals), LunarCrush client (Galaxy Score, sentiment, social volume), Alpaca client (account, positions, orders)
- [x] Build `scanner.py` — universe filter using Massive + LunarCrush, momentum scoring engine
- [x] Test: `pytest tests/test_scanner.py` — scanner outputs ranked candidates

### Phase 2: Intelligence
- [x] Build `sentiment.py` — LunarCrush sentiment check (primary), Claude Haiku backup
- [x] Build `risk_manager.py` — position sizing, exposure checks, all circuit breakers
- [x] Wire pipeline: scanner → sentiment → risk → signal output
- [x] Test: `pytest tests/test_sentiment.py tests/test_risk_manager.py`

### Phase 3: Execution
- [x] Build `executor.py` — Alpaca limit orders, stop-loss placement, order cancellation
- [x] Build `portfolio.py` — open positions, unrealized P&L, performance metrics
- [x] Build exit logic — stops, trailing, profit target, sentiment reversal, time exit
- [x] Build `logger.py` — JSON trade log, daily summary CSV, decision rationale
- [x] Test: `pytest tests/test_executor.py` — paper trades execute end-to-end

### Phase 4: Automation
- [x] Build `main.py` — APScheduler loop, market hours detection, pre-market universe refresh
- [x] Add circuit breakers to main loop (daily/weekly loss halt, API-down halt)
- [x] Add error handling, retry logic, graceful degradation for all 3 APIs
- [x] Test: bot runs autonomously through a full paper trading day

### Phase 5: Paper Validation (2-4 weeks)
- [ ] Run on paper for 10+ trading days
- [ ] Analyze: win rate, avg gain/loss, reward/risk ratio, Sharpe, max drawdown
- [ ] Tune settings.yaml based on results
- [ ] Go-live criteria: win rate >50%, R/R >1.5, max DD <10%, Sharpe >1.0

### Phase 6: Go Live
- [ ] Switch Alpaca to live ($1K)
- [ ] Reduce max position to 10% initially
- [ ] Monitor daily for first week
- [ ] Scale to full risk params after validation

---

## API Reference (quick lookup — don't web search for these)

### Alpaca (alpaca-py)
```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

client = TradingClient(api_key, secret_key, paper=True)
```

### Massive (REST API)
```
Base URL: https://api.massive.com/v1
Headers: {"Authorization": "Bearer {MASSIVE_API_KEY}"}
GET /stocks/{ticker}/bars?timeframe=1Day&limit=30
GET /stocks/{ticker}/quotes/latest
GET /stocks/screener?min_market_cap=2000000000&min_volume=1000000
```

### LunarCrush (REST API)
```
Base URL: https://lunarcrush.com/api4/public
Headers: {"Authorization": "Bearer {LUNARCRUSH_API_KEY}"}
GET /coins/list — all tracked assets
GET /coins/{symbol}/time-series/v2 — historical social + price
GET /coins/{symbol}/v1 — current Galaxy Score, AltRank, sentiment
GET /category/stocks — stock-specific social data
```

### Anthropic (Claude Haiku — backup sentiment only)
```python
from anthropic import Anthropic
client = Anthropic()
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
    messages=[{"role": "user", "content": prompt}]
)
```

---

## Best Practices (stolen from research)

1. **From Claude Prophet:** Let the bot run. Don't over-engineer the decision loop. Simple rules + good risk management beats complex AI.
2. **From TradingAgents:** Separate concerns — scanner, analyst, trader, risk manager should be independent modules, not one monolith.
3. **From FinMem (cautionary):** Backtests lie. Paper trade extensively. The only metric that matters is live performance.
4. **From StockBench:** Risk management matters MORE than signal quality. The stops and sizing keep you alive. The signals just find the opportunities.
5. **From Alpaca MCP Server:** Don't rebuild what exists. Alpaca's SDK handles order execution, position tracking, and account management well. Use it.

---

## Git Workflow
- Commit after each completed Phase task
- Commit messages: `phase-N: description` (e.g., `phase-1: build scanner with momentum scoring`)
- Never commit .env or API keys
- Tag each phase completion: `git tag phase-1-complete`
