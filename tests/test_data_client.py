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


class TestMassiveClient:
    @patch("src.data_client.httpx.Client")
    def test_screen_stocks(self, mock_httpx, mock_env):
        from src.data_client import MassiveClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"symbol": "AAPL", "market_cap": 3e12, "avg_volume": 50e6, "price": 180},
                {"symbol": "MSFT", "market_cap": 2.5e12, "avg_volume": 30e6, "price": 400},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.return_value.get.return_value = mock_resp

        client = MassiveClient()
        results = client.screen_stocks(min_market_cap=2e9, min_volume=1e6)

        assert len(results) == 2
        assert results[0]["symbol"] == "AAPL"

    @patch("src.data_client.httpx.Client")
    def test_get_bars(self, mock_httpx, mock_env):
        from src.data_client import MassiveClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "bars": [
                {"t": "2026-04-01T00:00:00Z", "o": 100, "h": 105, "l": 99, "c": 103, "v": 1000000},
                {"t": "2026-04-02T00:00:00Z", "o": 103, "h": 107, "l": 102, "c": 106, "v": 1200000},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.return_value.get.return_value = mock_resp

        client = MassiveClient()
        df = client.get_bars("AAPL")

        assert len(df) == 2
        assert "c" in df.columns


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
