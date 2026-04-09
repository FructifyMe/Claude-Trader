# Auto-Trader Bot — Full Project Plan

> **Note for Claude Code:** You do NOT need to read this file every session.
> CLAUDE.md has the condensed version. Only reference this file if you need
> deep detail on a specific design decision, API endpoint, or research finding.

---

## Project Overview

An autonomous stock trading bot built in Python. Scans for momentum breakouts
using Massive real-time data, confirms with LunarCrush social sentiment, executes
via Alpaca. Owner reviews logs after the fact.

**Owner:** Mike Farley
**Capital:** $1,000 (paper first, then live)
**Asset class:** US Stocks (crypto expansion later)
**Strategy:** Hybrid momentum + social sentiment, swing trading (1-5 day holds)

---

## Data Flow

```
[Massive API]          [LunarCrush API]        [Alpaca API]
  Real-time OHLCV        Galaxy Score            Account info
  Historical bars        Social sentiment        Positions
  Fundamentals           Social volume           Order execution
  Volume data            AltRank                 Paper trading
       │                      │                       │
       └──────────┬───────────┘                       │
                  │                                    │
          ┌───────▼────────┐                          │
          │  data_client.py │ ← Unified data layer    │
          └───────┬────────┘                          │
                  │                                    │
          ┌───────▼────────┐                          │
          │   scanner.py    │ ← Momentum scoring      │
          └───────┬────────┘                          │
                  │                                    │
          ┌───────▼────────┐                          │
          │  sentiment.py   │ ← LunarCrush + Claude   │
          └───────┬────────┘                          │
                  │                                    │
          ┌───────▼────────┐                          │
          │ risk_manager.py │ ← Hard limits enforced  │
          └───────┬────────┘                          │
                  │                                    │
          ┌───────▼────────┐     ┌────────────────────▼─┐
          │  executor.py    │────►│    Alpaca Trading    │
          └───────┬────────┘     └──────────────────────┘
                  │
          ┌───────▼────────┐
          │   logger.py     │ ← Every decision logged
          └────────────────┘
```

---

## Detailed Module Specifications

### data_client.py

Unified interface to all three data providers. Each provider gets its own class.
A top-level `DataClient` class composes all three and provides the methods the
rest of the bot needs.

```python
class MassiveClient:
    """Handles all Massive API calls. Caches responses within scan cycle."""
    def get_bars(ticker, timeframe, limit) -> pd.DataFrame
    def get_latest_quote(ticker) -> dict
    def screen_universe(min_mcap, min_vol, min_price, max_price) -> list[str]

class LunarCrushClient:
    """Handles all LunarCrush API calls."""
    def get_galaxy_score(ticker) -> float
    def get_sentiment(ticker) -> dict  # {score, direction, social_volume}
    def get_trending() -> list[dict]

class AlpacaClient:
    """Handles account, positions, and order management."""
    def get_account() -> dict
    def get_positions() -> list[dict]
    def submit_order(ticker, qty, side, type, stop) -> dict
    def cancel_order(order_id) -> bool

class DataClient:
    """Composes all three. Single import for the rest of the bot."""
    massive: MassiveClient
    lunarcrush: LunarCrushClient
    alpaca: AlpacaClient
```

**Token optimization:** Cache Massive bars and LunarCrush scores per scan cycle.
Don't re-fetch within the same 5-minute interval.

### scanner.py

```python
def build_universe(data: DataClient, config: dict) -> list[str]:
    """Daily pre-market. Returns filtered ticker list."""

def score_momentum(ticker: str, bars: pd.DataFrame, lc_data: dict, config: dict) -> float:
    """Returns 0-100 momentum score for a single ticker."""

def scan(data: DataClient, universe: list[str], config: dict) -> list[dict]:
    """Main scan loop. Returns candidates with score > threshold."""
```

### sentiment.py

```python
def check_lunarcrush(ticker: str, data: DataClient, config: dict) -> dict:
    """Primary sentiment check. Returns {confirmed: bool, score, source}."""

def check_claude(ticker: str, headlines: list[str], config: dict) -> dict:
    """Backup. Only called when LunarCrush is mixed/unavailable."""

def confirm_sentiment(ticker: str, data: DataClient, config: dict) -> dict:
    """Orchestrator. Tries LC first, falls back to Claude."""
```

### risk_manager.py

```python
def size_position(entry: float, stop: float, portfolio_value: float, config: dict) -> int:
    """Returns number of shares to buy. Respects all risk limits."""

def check_exposure(new_position_value: float, portfolio: dict, config: dict) -> bool:
    """Returns True if the trade is within risk limits."""

def check_circuit_breakers(daily_pnl: float, weekly_pnl: float, portfolio_value: float, config: dict) -> bool:
    """Returns True if trading is still allowed."""
```

### executor.py

```python
def enter_position(ticker: str, shares: int, stop_price: float, data: DataClient) -> dict:
    """Submit limit buy + stop-loss. Returns order details."""

def exit_position(ticker: str, shares: int, reason: str, data: DataClient) -> dict:
    """Submit sell order. Logs reason."""

def check_exits(positions: list, data: DataClient, config: dict) -> list[dict]:
    """Check all open positions against exit rules. Returns actions needed."""
```

### portfolio.py

```python
def get_status(data: DataClient) -> dict:
    """Current portfolio: positions, cash, total value, daily P&L."""

def get_performance(trades: list) -> dict:
    """Win rate, avg gain/loss, Sharpe, max drawdown, R/R ratio."""
```

### logger.py

```python
def log_decision(action: str, ticker: str, reason: str, details: dict):
    """Append to trades.json. Every decision gets logged."""

def daily_summary(trades: list, portfolio: dict):
    """End-of-day CSV report."""
```

---

## Research Findings (reference only)

### Existing Projects Studied

| Project | What It Does | What We Took |
|---------|-------------|-------------|
| Claude Prophet (OpenProphet) | Claude Code + Alpaca, beat market on $100K paper | Proof that Claude + Alpaca autonomous execution works |
| Alpaca MCP Server v2 | 43 tools for natural language trading | Don't rebuild execution — use their SDK |
| TradingAgents (academic) | Multi-agent LLM framework, 23%+ returns, Sharpe 5.6 | Separate analyst/trader/risk roles into modules |
| LunarCrush + Gemini Agent | Social sentiment → trading signals pipeline | LC → AI → signal flow design |
| FinMem (cautionary) | LLM trading with memory — doesn't beat buy-and-hold | Paper trade extensively, don't trust backtests |
| StockBench (academic) | LLM trading evaluation | Risk management > signal quality |

### Key Takeaway
Most LLM trading bots struggle to beat buy-and-hold. The ones that work combine:
(1) real-time sentiment data, (2) strict risk management, (3) modular architecture,
(4) continuous adaptation. Our design incorporates all four.

---

## Cost Estimate

| Item | Cost | Frequency |
|------|------|-----------|
| Alpaca | Free | — |
| Massive | Free tier / ~$29/mo | Monthly |
| LunarCrush | Free tier / ~$30/mo | Monthly |
| Claude Haiku | ~$1-2/mo | Backup calls only |
| Capital | $1,000 | One-time |
| **Total** | **$2-60/month** | |
