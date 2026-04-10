"""Unified data layer — Alpaca, Massive, LunarCrush clients."""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pandas as pd
import yaml
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    StopLimitOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

log = logging.getLogger(__name__)

# Load env from config/.env if it exists, else root .env
_env_path = os.path.join(os.path.dirname(__file__), "..", "config", ".env")
if not os.path.exists(_env_path):
    _env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_env_path, override=True)


def _load_settings() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


# ── Alpaca Client ──────────────────────────────────────────────

class AlpacaClient:
    """Wraps alpaca-py for trading and market data."""

    def __init__(self):
        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]
        base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        paper = "paper" in base_url

        self.trading = TradingClient(api_key, secret_key, paper=paper)
        self.data = StockHistoricalDataClient(api_key, secret_key)

    def get_account(self) -> dict:
        acct = self.trading.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
        }

    def get_positions(self) -> list[dict]:
        positions = self.trading.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    def get_bars(self, symbol: str, timeframe: TimeFrame = TimeFrame.Day,
                 limit: int = 30) -> pd.DataFrame:
        start = datetime.now() - timedelta(days=limit * 2)
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            limit=limit,
        )
        bars = self.data.get_stock_bars(request)
        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel("symbol")
        return df

    def get_latest_quote(self, symbol: str) -> dict:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self.data.get_stock_latest_quote(request)
        q = quotes[symbol]
        return {
            "bid": float(q.bid_price),
            "ask": float(q.ask_price),
            "mid": (float(q.bid_price) + float(q.ask_price)) / 2,
        }

    def submit_limit_order(self, symbol: str, qty: float, side: OrderSide,
                           limit_price: float, time_in_force: TimeInForce = TimeInForce.DAY) -> str:
        request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            type="limit",
            time_in_force=time_in_force,
            limit_price=limit_price,
        )
        order = self.trading.submit_order(request)
        log.info(f"Order submitted: {side.value} {qty} {symbol} @ {limit_price} → {order.id}")
        return str(order.id)

    def submit_stop_order(self, symbol: str, qty: float, stop_price: float,
                          limit_price: float) -> str:
        request = StopLimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            type="stop_limit",
            time_in_force=TimeInForce.GTC,
            stop_price=stop_price,
            limit_price=limit_price,
        )
        order = self.trading.submit_order(request)
        log.info(f"Stop order: SELL {qty} {symbol} stop@{stop_price} limit@{limit_price} → {order.id}")
        return str(order.id)

    def cancel_order(self, order_id: str):
        self.trading.cancel_order_by_id(order_id)
        log.info(f"Cancelled order {order_id}")

    def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = self.trading.get_orders(request)
        result = []
        for o in orders:
            if symbol and o.symbol != symbol:
                continue
            result.append({
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "type": o.type.value,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "stop_price": float(o.stop_price) if o.stop_price else None,
                "status": o.status.value,
            })
        return result


# ── Full-Market Screener (Finviz + Alpaca) ───────────────────

class MarketScreener:
    """Multi-source screener that finds opportunities across the ENTIRE market.

    Runs multiple Finviz screens (unusual volume, momentum breakouts, gap ups,
    insider buying, small-cap movers) and validates with Alpaca quote data.
    Replaces the old 60-stock hardcoded universe.
    """

    def __init__(self, alpaca_client: 'AlpacaClient'):
        self.alpaca = alpaca_client
        self._finviz_cache: dict = {}       # screen_name → results
        self._cache_time: datetime = datetime.min

    def screen_stocks(self, min_market_cap: float = 0, min_volume: float = 100_000,
                      min_price: float = 5.0, max_price: float = 500.0) -> list[dict]:
        """Run all screens, merge, deduplicate, validate with Alpaca."""
        all_tickers: dict[str, dict] = {}   # symbol → {source, ...}

        # Run Finviz screens (cached 30 min to avoid hammering)
        now = datetime.now()
        cache_stale = (now - self._cache_time).total_seconds() > 1800
        if cache_stale:
            self._run_finviz_screens(min_price, max_price, min_volume)
            self._cache_time = now

        # Merge all Finviz results
        for screen_name, stocks in self._finviz_cache.items():
            for stock in stocks:
                ticker = stock.get("Ticker", "")
                if not ticker or len(ticker) > 5 or "-" in ticker:
                    continue  # skip tickers with dashes (BF-A etc) — Alpaca rejects them
                if ticker not in all_tickers:
                    all_tickers[ticker] = {
                        "symbol": ticker,
                        "sources": [],
                        "finviz_change": stock.get("Change", 0),
                        "finviz_volume": stock.get("Volume", 0),
                        "market_cap": stock.get("Market Cap", 0),
                        "sector": stock.get("Sector", ""),
                        "company": stock.get("Company", ""),
                    }
                all_tickers[ticker]["sources"].append(screen_name)

        log.info(f"Finviz screens found {len(all_tickers)} unique tickers")

        # Validate with Alpaca: get real-time quotes in batches of 500
        validated = self._validate_with_alpaca(
            list(all_tickers.keys()), all_tickers, min_price, max_price, min_volume
        )

        validated.sort(key=lambda x: x.get("volume", 0), reverse=True)
        log.info(f"MarketScreener: {len(validated)} stocks pass all filters")
        return validated

    def _run_finviz_screens(self, min_price: float, max_price: float, min_volume: float):
        """Run multiple Finviz screens to cast a wide net."""
        from finvizfinance.screener.overview import Overview

        screens = {
            "unusual_volume": {
                "Relative Volume": "Over 2",
                "Average Volume": "Over 200K",
                "Price": f"Over ${int(min_price)}",
            },
            "momentum_breakout": {
                "Relative Volume": "Over 1.5",
                "Average Volume": "Over 100K",
                "Price": f"Over ${int(min_price)}",
                "Change": "Up",
                "20-Day Simple Moving Average": "Price above SMA20",
            },
            "small_cap_momentum": {
                "Market Cap.": "Small ($300mln to $2bln)",
                "Relative Volume": "Over 1.5",
                "Average Volume": "Over 100K",
                "Price": f"Over ${int(min_price)}",
                "Change": "Up",
                "20-Day Simple Moving Average": "Price above SMA20",
            },
            "mid_cap_breakout": {
                "Market Cap.": "Mid ($2bln to $10bln)",
                "Average Volume": "Over 200K",
                "Relative Volume": "Over 1",
                "Performance": "Week Up",
                "20-Day Simple Moving Average": "Price above SMA20",
                "50-Day Simple Moving Average": "Price above SMA50",
                "Change": "Up",
            },
            "top_gainers": {
                "Change": "Up 5%",
                "Average Volume": "Over 200K",
                "Price": f"Over ${int(min_price)}",
            },
            "high_short_interest": {
                "Float Short": "Over 15%",
                "Relative Volume": "Over 1",
                "Average Volume": "Over 200K",
                "Price": f"Over ${int(min_price)}",
                "Change": "Up",
            },
            "insider_buying": {
                "InsiderTransactions": "Very Positive (>20%)",
                "Average Volume": "Over 100K",
                "Price": f"Over ${int(min_price)}",
            },
            "gap_up": {
                "Gap": "Up 2%",
                "Average Volume": "Over 200K",
                "Price": f"Over ${int(min_price)}",
            },
            "oversold_bounce": {
                "RSI (14)": "Oversold (30)",
                "Average Volume": "Over 200K",
                "Price": f"Over ${int(min_price)}",
                "Change": "Up",
            },
        }

        self._finviz_cache = {}
        for name, filters in screens.items():
            try:
                filt = Overview()
                filt.set_filter(filters_dict=filters)
                df = filt.screener_view()
                if df is not None and len(df) > 0:
                    self._finviz_cache[name] = df.to_dict("records")
                    log.info(f"Finviz [{name}]: {len(df)} stocks")
                else:
                    self._finviz_cache[name] = []
                    log.debug(f"Finviz [{name}]: 0 stocks")
            except Exception as e:
                log.warning(f"Finviz [{name}] failed: {e}")
                self._finviz_cache[name] = []

    def _validate_with_alpaca(self, tickers: list[str], meta: dict,
                               min_price: float, max_price: float,
                               min_volume: float) -> list[dict]:
        """Batch-validate tickers with Alpaca quotes."""
        if not tickers:
            return []

        validated = []
        batch_size = 500
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            try:
                req = StockLatestQuoteRequest(symbol_or_symbols=batch)
                quotes = self.alpaca.data.get_stock_latest_quote(req)
                for symbol, q in quotes.items():
                    mid = (float(q.bid_price) + float(q.ask_price)) / 2
                    if mid <= 0 or not (min_price <= mid <= max_price):
                        continue
                    # Skip extremely wide spreads (>5% = illiquid)
                    spread_pct = (float(q.ask_price) - float(q.bid_price)) / mid if mid > 0 else 1
                    if spread_pct > 0.05:
                        continue
                    info = meta.get(symbol, {})
                    validated.append({
                        "symbol": symbol,
                        "price": round(mid, 2),
                        "bid": float(q.bid_price),
                        "ask": float(q.ask_price),
                        "spread_pct": round(spread_pct * 100, 2),
                        "volume": info.get("finviz_volume", 0),
                        "avg_volume": info.get("finviz_volume", 0),
                        "market_cap": info.get("market_cap", 0),
                        "sector": info.get("sector", ""),
                        "company": info.get("company", ""),
                        "sources": info.get("sources", []),
                        "source_count": len(info.get("sources", [])),
                    })
            except Exception as e:
                log.warning(f"Alpaca quote batch failed: {e}")

        return validated

    def close(self):
        pass


# ── LunarCrush Client ─────────────────────────────────────────

class LunarCrushClient:
    """REST client for LunarCrush social sentiment API."""

    BASE_URL = "https://lunarcrush.com/api4/public"

    def __init__(self):
        self.api_key = os.environ["LUNARCRUSH_API_KEY"]
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15.0,
        )
        self._available_cache: bool | None = None
        self._available_checked = datetime.min

    def get_stock_social(self, symbol: str) -> dict:
        resp = self._client.get(f"/coins/{symbol}/v1")
        resp.raise_for_status()
        data = resp.json().get("data", resp.json())
        if isinstance(data, list) and data:
            data = data[0]
        return {
            "galaxy_score": data.get("galaxy_score", 0),
            "alt_rank": data.get("alt_rank"),
            "sentiment": data.get("sentiment", 0),
            "social_volume": data.get("social_volume", 0),
            "social_volume_change": data.get("social_volume_global_change", 0),
            "news_articles": data.get("news", 0),
        }

    def get_time_series(self, symbol: str, interval: str = "1w") -> list[dict]:
        resp = self._client.get(
            f"/coins/{symbol}/time-series/v2",
            params={"interval": interval},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_stock_list(self) -> list[dict]:
        resp = self._client.get("/category/stocks")
        resp.raise_for_status()
        return resp.json().get("data", resp.json())

    def is_available(self) -> bool:
        """Check if LC API is reachable. Cached for 5 minutes."""
        now = datetime.now()
        if (self._available_cache is not None
                and (now - self._available_checked).total_seconds() < 300):
            return self._available_cache
        try:
            resp = self._client.get("/coins/list", params={"limit": 1})
            self._available_cache = resp.status_code == 200
        except Exception:
            self._available_cache = False
        self._available_checked = now
        return self._available_cache

    def close(self):
        self._client.close()


# ── Unified Data Service ───────────────────────────────────────

class DataService:
    """Single entry point for all data needs. Modules import this."""

    def __init__(self):
        self.settings = _load_settings()
        self.alpaca = AlpacaClient()
        self.massive = MarketScreener(self.alpaca)  # Full-market Finviz + Alpaca
        self.lunarcrush = LunarCrushClient()

    def close(self):
        self.massive.close()
        self.lunarcrush.close()
