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
                "min_market_cap": 0,
                "min_avg_volume": 100_000,
                "min_price": 5.0,
                "max_price": 500.0,
                "galaxy_score_min": 50,
                "earnings_exclusion_days": 2,
                "max_universe_size": 100,
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
                    "price_momentum": 0.05,
                    "social_volume": 0.05,
                    "sentiment_shift": 0.05,
                },
            },
        }
    }
    # Default: LC unavailable, SPY bars available
    ds.lunarcrush.is_available.return_value = False
    ds.alpaca.get_bars.return_value = _make_bars(50, "up")
    return ds


class TestUniverseFilter:
    def test_build_universe_from_market_screener(self, mock_data_service, tmp_path):
        from src.scanner import Scanner

        mock_data_service.massive.screen_stocks.return_value = [
            {"symbol": "AAPL", "price": 180, "volume": 50e6,
             "sources": ["unusual_volume", "top_gainers"], "source_count": 2},
            {"symbol": "MSFT", "price": 400, "volume": 30e6,
             "sources": ["momentum_breakout"], "source_count": 1},
        ]

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(tmp_path / "watchlist.json")

        universe = scanner.build_universe()
        symbols = [s["symbol"] for s in universe]
        assert "AAPL" in symbols
        assert "MSFT" in symbols

    def test_build_universe_with_lc_enrichment(self, mock_data_service, tmp_path):
        from src.scanner import Scanner

        mock_data_service.massive.screen_stocks.return_value = [
            {"symbol": "AAPL", "price": 180, "volume": 50e6,
             "sources": ["unusual_volume"], "source_count": 1},
        ]
        mock_data_service.lunarcrush.is_available.return_value = True
        mock_data_service.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 85, "social_volume": 5000, "sentiment": 4.2
        }

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(tmp_path / "watchlist.json")

        universe = scanner.build_universe()
        assert universe[0].get("galaxy_score") == 85

    def test_universe_respects_max_size(self, mock_data_service, tmp_path):
        from src.scanner import Scanner

        mock_data_service.massive.screen_stocks.return_value = [
            {"symbol": f"S{i}", "price": 100, "volume": 1e6,
             "sources": ["test"], "source_count": 1}
            for i in range(200)
        ]

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(tmp_path / "watchlist.json")

        universe = scanner.build_universe()
        assert len(universe) <= 100


class TestMomentumScoring:
    def test_uptrend_scores_positive(self, mock_data_service):
        from src.scanner import Scanner

        scanner = Scanner(mock_data_service)
        result = scanner.score_momentum("AAPL")

        assert result["symbol"] == "AAPL"
        assert result["score"] > 0
        assert "components" in result
        assert "sma" in result["components"]
        assert "rsi5" in result["components"]
        assert "relative_strength" in result["components"]
        assert "gap" in result["components"]

    def test_insufficient_data_returns_zero(self, mock_data_service):
        from src.scanner import Scanner

        mock_data_service.alpaca.get_bars.return_value = _make_bars(5, "up")

        scanner = Scanner(mock_data_service)
        result = scanner.score_momentum("TINY")

        assert result["score"] == 0
        assert "insufficient" in result.get("reason", "")

    def test_score_bounded_0_to_100(self, mock_data_service):
        from src.scanner import Scanner

        scanner = Scanner(mock_data_service)
        result = scanner.score_momentum("TEST")

        assert 0 <= result["score"] <= 100

    def test_data_unavailable_returns_zero(self, mock_data_service):
        from src.scanner import Scanner

        mock_data_service.alpaca.get_bars.side_effect = Exception("API error")

        scanner = Scanner(mock_data_service)
        result = scanner.score_momentum("FAIL")

        assert result["score"] == 0
        assert "unavailable" in result.get("reason", "")

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

        # Flat bars — unlikely to score above 70
        mock_data_service.alpaca.get_bars.return_value = _make_bars(50, "flat")

        scanner = Scanner(mock_data_service)
        scanner.watchlist_path = str(wl_path)

        candidates = scanner.get_candidates()
        for c in candidates:
            assert c["passes_threshold"] is True
