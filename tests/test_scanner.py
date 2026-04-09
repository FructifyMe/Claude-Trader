"""Tests for scanner — universe filter and momentum scoring."""
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def _make_bars(n=50, trend="up"):
    """Generate fake OHLCV bars for testing indicators."""
    dates = pd.date_range(end=datetime.now(), periods=n, freq="D")
    if trend == "up":
        close = 100 + np.cumsum(np.random.uniform(0, 1, n))
    elif trend == "down":
        close = 150 - np.cumsum(np.random.uniform(0, 1, n))
    else:
        close = 100 + np.random.uniform(-0.5, 0.5, n).cumsum()

    volume_base = 1_000_000
    volume = np.random.uniform(0.8, 1.2, n) * volume_base
    # Make last bar volume high for surge detection
    volume[-1] = volume_base * 2.0

    return pd.DataFrame({
        "open": close - np.random.uniform(0, 1, n),
        "high": close + np.random.uniform(0, 2, n),
        "low": close - np.random.uniform(0, 2, n),
        "close": close,
        "volume": volume,
    }, index=dates)


@pytest.fixture
def mock_data_service():
    ds = MagicMock()
    ds.settings = {
        "strategy": {
            "universe": {
                "min_market_cap": 2_000_000_000,
                "min_avg_volume": 1_000_000,
                "min_price": 5.0,
                "max_price": 500.0,
                "galaxy_score_min": 50,
                "earnings_exclusion_days": 2,
                "max_universe_size": 50,
            },
            "momentum": {
                "sma_period": 20,
                "rsi_period": 14,
                "rsi_min": 50,
                "rsi_max": 70,
                "volume_surge_multiplier": 1.5,
                "social_volume_surge_multiplier": 2.0,
                "macd_fast": 12,
                "macd_slow": 26,
                "macd_signal": 9,
                "momentum_lookback_days": 5,
                "score_threshold": 70,
                "weights": {
                    "sma": 0.20,
                    "rsi": 0.15,
                    "volume": 0.20,
                    "macd": 0.10,
                    "price_momentum": 0.10,
                    "social_volume": 0.15,
                    "sentiment_shift": 0.10,
                },
            },
        }
    }
    return ds


class TestUniverseFilter:
    def test_build_universe_filters_by_galaxy_score(self, mock_data_service, tmp_path):
        from src.scanner import Scanner

        mock_data_service.massive.screen_stocks.return_value = [
            {"symbol": "AAPL", "market_cap": 3e12, "avg_volume": 50e6, "price": 180},
            {"symbol": "MSFT", "market_cap": 2.5e12, "avg_volume": 30e6, "price": 400},
            {"symbol": "WEAK", "market_cap": 5e9, "avg_volume": 2e6, "price": 50},
        ]

        def mock_social(symbol):
            scores = {"AAPL": 72, "MSFT": 65, "WEAK": 30}
            return {
                "galaxy_score": scores.get(symbol, 0),
                "social_volume": 1000,
                "social_volume_change": 1.0,
            }

        mock_data_service.lunarcrush.get_stock_social.side_effect = mock_social

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(tmp_path / "watchlist.json")

        universe = scanner.build_universe()

        # WEAK should be filtered out (galaxy_score 30 < 50)
        symbols = [s["symbol"] for s in universe]
        assert "AAPL" in symbols
        assert "MSFT" in symbols
        assert "WEAK" not in symbols

    def test_build_universe_sorts_by_galaxy_score(self, mock_data_service, tmp_path):
        from src.scanner import Scanner

        mock_data_service.massive.screen_stocks.return_value = [
            {"symbol": "LOW", "market_cap": 3e12, "avg_volume": 50e6, "price": 100},
            {"symbol": "HIGH", "market_cap": 3e12, "avg_volume": 50e6, "price": 100},
        ]

        def mock_social(symbol):
            scores = {"LOW": 55, "HIGH": 85}
            return {"galaxy_score": scores[symbol], "social_volume": 1000, "social_volume_change": 1.0}

        mock_data_service.lunarcrush.get_stock_social.side_effect = mock_social

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(tmp_path / "watchlist.json")

        universe = scanner.build_universe()
        assert universe[0]["symbol"] == "HIGH"
        assert universe[1]["symbol"] == "LOW"

    def test_universe_respects_max_size(self, mock_data_service, tmp_path):
        from src.scanner import Scanner

        mock_data_service.massive.screen_stocks.return_value = [
            {"symbol": f"S{i}", "market_cap": 3e12, "avg_volume": 50e6, "price": 100}
            for i in range(100)
        ]
        mock_data_service.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 80, "social_volume": 1000, "social_volume_change": 1.0
        }

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(tmp_path / "watchlist.json")

        universe = scanner.build_universe()
        assert len(universe) <= 50


class TestMomentumScoring:
    def test_uptrend_scores_high(self, mock_data_service):
        from src.scanner import Scanner

        bars = _make_bars(50, "up")
        mock_data_service.alpaca.get_bars.return_value = bars
        mock_data_service.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 75,
            "social_volume": 5000,
            "social_volume_change": 3.0,
            "sentiment": 4.5,
        }

        scanner = Scanner(mock_data_service)
        result = scanner.score_momentum("AAPL")

        assert result["symbol"] == "AAPL"
        assert result["score"] > 0
        assert "components" in result
        assert "sma" in result["components"]

    def test_insufficient_data_returns_zero(self, mock_data_service):
        from src.scanner import Scanner

        bars = _make_bars(5, "up")
        mock_data_service.alpaca.get_bars.return_value = bars

        scanner = Scanner(mock_data_service)
        result = scanner.score_momentum("TINY")

        assert result["score"] == 0
        assert "insufficient" in result.get("reason", "")

    def test_score_components_are_bounded(self, mock_data_service):
        from src.scanner import Scanner

        bars = _make_bars(50, "up")
        mock_data_service.alpaca.get_bars.return_value = bars
        mock_data_service.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 90,
            "social_volume": 10000,
            "social_volume_change": 5.0,
            "sentiment": 5.0,
        }

        scanner = Scanner(mock_data_service)
        result = scanner.score_momentum("TEST")

        assert 0 <= result["score"] <= 100
        for key, val in result["components"].items():
            if key not in ("rsi_value", "vol_ratio"):
                assert 0 <= val <= 100, f"{key} out of bounds: {val}"

    def test_scan_watchlist_returns_sorted(self, mock_data_service, tmp_path):
        from src.scanner import Scanner
        import json

        watchlist = {
            "generated_at": datetime.now().isoformat(),
            "count": 2,
            "stocks": [
                {"symbol": "LOW_SCORE"},
                {"symbol": "HIGH_SCORE"},
            ],
        }
        wl_path = tmp_path / "watchlist.json"
        wl_path.write_text(json.dumps(watchlist))

        def mock_bars(symbol, **kwargs):
            return _make_bars(50, "up")

        mock_data_service.alpaca.get_bars.side_effect = mock_bars
        mock_data_service.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 70, "social_volume": 3000,
            "social_volume_change": 2.0, "sentiment": 4.0,
        }

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(wl_path)

        results = scanner.scan_watchlist()

        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]

    def test_get_candidates_filters_threshold(self, mock_data_service, tmp_path):
        from src.scanner import Scanner
        import json

        watchlist = {
            "generated_at": datetime.now().isoformat(),
            "count": 1,
            "stocks": [{"symbol": "TEST"}],
        }
        wl_path = tmp_path / "watchlist.json"
        wl_path.write_text(json.dumps(watchlist))

        # Return flat bars that won't score high
        bars = _make_bars(50, "flat")
        mock_data_service.alpaca.get_bars.return_value = bars
        mock_data_service.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 40, "social_volume": 500,
            "social_volume_change": 0.5, "sentiment": 2.5,
        }

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(wl_path)

        candidates = scanner.get_candidates()
        # Flat trend with low social should not pass threshold 70
        for c in candidates:
            assert c["passes_threshold"] is True
