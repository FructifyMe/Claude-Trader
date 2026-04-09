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
load_dotenv(_env_path)


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


# ── Massive Client ─────────────────────────────────────────────

class MassiveClient:
    """REST client for Massive market data API."""

    BASE_URL = "https://api.massive.com/v1"

    def __init__(self):
        self.api_key = os.environ["MASSIVE_API_KEY"]
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15.0,
        )

    def get_bars(self, ticker: str, timeframe: str = "1Day", limit: int = 30) -> pd.DataFrame:
        resp = self._client.get(
            f"/stocks/{ticker}/bars",
            params={"timeframe": timeframe, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data.get("bars", data))
        if "t" in df.columns:
            df["timestamp"] = pd.to_datetime(df["t"])
            df.set_index("timestamp", inplace=True)
        return df

    def get_latest_quote(self, ticker: str) -> dict:
        resp = self._client.get(f"/stocks/{ticker}/quotes/latest")
        resp.raise_for_status()
        return resp.json()

    def screen_stocks(self, min_market_cap: float, min_volume: float,
                      min_price: float = 5.0, max_price: float = 500.0) -> list[dict]:
        resp = self._client.get(
            "/stocks/screener",
            params={
                "min_market_cap": int(min_market_cap),
                "min_volume": int(min_volume),
                "min_price": min_price,
                "max_price": max_price,
            },
        )
        resp.raise_for_status()
        return resp.json().get("results", resp.json())

    def close(self):
        self._client.close()


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
        try:
            resp = self._client.get("/coins/list", params={"limit": 1})
            return resp.status_code == 200
        except Exception:
            return False

    def close(self):
        self._client.close()


# ── Unified Data Service ───────────────────────────────────────

class DataService:
    """Single entry point for all data needs. Modules import this."""

    def __init__(self):
        self.settings = _load_settings()
        self.alpaca = AlpacaClient()
        self.massive = MassiveClient()
        self.lunarcrush = LunarCrushClient()

    def close(self):
        self.massive.close()
        self.lunarcrush.close()
