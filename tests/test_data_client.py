"""Tests for data_client module — uses mocks, no real API calls."""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from datetime import datetime


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("MASSIVE_API_KEY", "test_massive")
    monkeypatch.setenv("LUNARCRUSH_API_KEY", "test_lunar")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_anthropic")


class TestMarketScreener:
    def test_screen_stocks_finviz_plus_alpaca(self, mock_env):
        from src.data_client import MarketScreener

        mock_alpaca = MagicMock()

        # Mock Alpaca quote validation
        mock_quote_aapl = MagicMock()
        mock_quote_aapl.bid_price = 179.5
        mock_quote_aapl.ask_price = 180.5
        mock_quote_msft = MagicMock()
        mock_quote_msft.bid_price = 399.0
        mock_quote_msft.ask_price = 401.0
        mock_alpaca.data.get_stock_latest_quote.return_value = {
            "AAPL": mock_quote_aapl,
            "MSFT": mock_quote_msft,
        }

        screener = MarketScreener(mock_alpaca)
        # Pre-fill the Finviz cache to avoid real web calls
        screener._finviz_cache = {
            "unusual_volume": [
                {"Ticker": "AAPL", "Company": "Apple", "Sector": "Technology",
                 "Market Cap": 3e12, "Volume": 50e6, "Change": 0.02},
                {"Ticker": "MSFT", "Company": "Microsoft", "Sector": "Technology",
                 "Market Cap": 2.5e12, "Volume": 30e6, "Change": 0.01},
            ],
            "top_gainers": [
                {"Ticker": "AAPL", "Company": "Apple", "Sector": "Technology",
                 "Market Cap": 3e12, "Volume": 50e6, "Change": 0.05},
            ],
        }
        screener._cache_time = datetime.now()  # mark cache as fresh

        results = screener.screen_stocks()
        symbols = [r["symbol"] for r in results]
        assert "AAPL" in symbols
        assert "MSFT" in symbols
        # AAPL appears in 2 screens, should have source_count=2
        aapl = next(r for r in results if r["symbol"] == "AAPL")
        assert aapl["source_count"] == 2

    def test_filters_wide_spreads(self, mock_env):
        from src.data_client import MarketScreener

        mock_alpaca = MagicMock()
        # Wide spread (>5%)
        mock_q = MagicMock()
        mock_q.bid_price = 10.0
        mock_q.ask_price = 11.0  # 10% spread
        mock_alpaca.data.get_stock_latest_quote.return_value = {"WIDE": mock_q}

        screener = MarketScreener(mock_alpaca)
        screener._finviz_cache = {
            "test": [{"Ticker": "WIDE", "Company": "Wide Inc", "Sector": "Tech",
                       "Market Cap": 1e9, "Volume": 1e6, "Change": 0.01}]
        }
        screener._cache_time = datetime.now()

        results = screener.screen_stocks()
        assert len(results) == 0  # filtered out due to wide spread

    def test_filters_dash_tickers(self, mock_env):
        from src.data_client import MarketScreener

        mock_alpaca = MagicMock()
        mock_alpaca.data.get_stock_latest_quote.return_value = {}

        screener = MarketScreener(mock_alpaca)
        screener._finviz_cache = {
            "test": [{"Ticker": "BF-A", "Company": "Brown Forman", "Sector": "Consumer",
                       "Market Cap": 1e9, "Volume": 1e6, "Change": 0.01}]
        }
        screener._cache_time = datetime.now()

        results = screener.screen_stocks()
        assert len(results) == 0  # BF-A filtered out (dash in ticker)


class TestLunarCrushClient:
    @patch("src.data_client.httpx.Client")
    def test_get_stock_social(self, mock_httpx, mock_env):
        from src.data_client import LunarCrushClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{
                "galaxy_score": 72,
                "alt_rank": 15,
                "sentiment": 4.2,
                "social_volume": 5000,
                "social_volume_global_change": 2.5,
                "news": 12,
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.return_value.get.return_value = mock_resp

        client = LunarCrushClient()
        social = client.get_stock_social("AAPL")

        assert social["galaxy_score"] == 72
        assert social["sentiment"] == 4.2
        assert social["social_volume"] == 5000

    @patch("src.data_client.httpx.Client")
    def test_is_available_success(self, mock_httpx, mock_env):
        from src.data_client import LunarCrushClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx.return_value.get.return_value = mock_resp

        client = LunarCrushClient()
        assert client.is_available() is True

    @patch("src.data_client.httpx.Client")
    def test_is_available_failure(self, mock_httpx, mock_env):
        from src.data_client import LunarCrushClient

        mock_httpx.return_value.get.side_effect = Exception("API down")

        client = LunarCrushClient()
        assert client.is_available() is False
