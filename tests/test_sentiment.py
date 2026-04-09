"""Tests for sentiment analysis module."""
import pytest
from unittest.mock import MagicMock, patch


def _make_settings():
    return {
        "strategy": {
            "sentiment": {
                "lunarcrush_galaxy_min": 60,
                "lunarcrush_sentiment": "bullish",
                "social_volume_trend": "rising",
                "claude_model": "claude-haiku-4-5-20251001",
                "claude_confidence_min": 7,
                "claude_max_tokens": 200,
            }
        }
    }


@pytest.fixture
def mock_ds():
    ds = MagicMock()
    ds.settings = _make_settings()
    ds.lunarcrush.is_available.return_value = True
    return ds


class TestLunarCrushCheck:
    def test_bullish_confirmed(self, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_ds.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 75,
            "sentiment": 4.5,
            "social_volume": 5000,
            "social_volume_change": 2.0,
        }

        sa = SentimentAnalyzer(mock_ds)
        result = sa.check_lunarcrush("AAPL")

        assert result["confirmed"] is True
        assert result["galaxy_pass"] is True
        assert result["sentiment"] == "bullish"
        assert result["sv_pass"] is True

    def test_bearish_rejected(self, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_ds.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 70,
            "sentiment": 1.5,
            "social_volume": 3000,
            "social_volume_change": 1.0,
        }

        sa = SentimentAnalyzer(mock_ds)
        result = sa.check_lunarcrush("BAD")

        assert result["confirmed"] is False
        assert result["sentiment"] == "bearish"

    def test_low_galaxy_rejected(self, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_ds.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 40,
            "sentiment": 4.5,
            "social_volume": 5000,
            "social_volume_change": 2.0,
        }

        sa = SentimentAnalyzer(mock_ds)
        result = sa.check_lunarcrush("LOW")

        assert result["confirmed"] is False
        assert result["galaxy_pass"] is False

    def test_flat_social_volume_rejected(self, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_ds.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 70,
            "sentiment": 4.2,
            "social_volume": 5000,
            "social_volume_change": 0.05,
        }

        sa = SentimentAnalyzer(mock_ds)
        result = sa.check_lunarcrush("FLAT")

        assert result["confirmed"] is False
        assert result["sv_pass"] is False


class TestHaikuCheck:
    @patch("src.sentiment.Anthropic")
    def test_bullish_high_confidence(self, mock_anthropic_cls, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"signal": "BULLISH", "confidence": 8, "reason": "Strong momentum"}')]
        mock_anthropic_cls.return_value.messages.create.return_value = mock_response

        sa = SentimentAnalyzer(mock_ds)
        result = sa.check_haiku("AAPL", {"score": 75, "rsi_value": 60, "vol_ratio": 2.0, "galaxy_score": 65, "sentiment_value": 3.5})

        assert result["confirmed"] is True
        assert result["signal"] == "BULLISH"
        assert result["confidence"] == 8

    @patch("src.sentiment.Anthropic")
    def test_bullish_low_confidence_rejected(self, mock_anthropic_cls, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"signal": "BULLISH", "confidence": 4, "reason": "Weak signal"}')]
        mock_anthropic_cls.return_value.messages.create.return_value = mock_response

        sa = SentimentAnalyzer(mock_ds)
        result = sa.check_haiku("WEAK", {"score": 72})

        assert result["confirmed"] is False
        assert result["confidence"] == 4

    @patch("src.sentiment.Anthropic")
    def test_api_error_returns_false(self, mock_anthropic_cls, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_anthropic_cls.return_value.messages.create.side_effect = Exception("API error")

        sa = SentimentAnalyzer(mock_ds)
        result = sa.check_haiku("ERR", {"score": 75})

        assert result["confirmed"] is False
        assert result["signal"] == "ERROR"


class TestConfirmSentiment:
    def test_lc_unavailable_halts(self, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_ds.lunarcrush.is_available.return_value = False

        sa = SentimentAnalyzer(mock_ds)
        result = sa.confirm_sentiment("AAPL", {"score": 80})

        assert result["confirmed"] is False
        assert "unavailable" in result["reason"].lower()

    def test_clear_bullish_uses_lc(self, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_ds.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 75,
            "sentiment": 4.5,
            "social_volume": 5000,
            "social_volume_change": 2.0,
        }

        sa = SentimentAnalyzer(mock_ds)
        result = sa.confirm_sentiment("AAPL", {"score": 80})

        assert result["confirmed"] is True
        assert result["source"] == "lunarcrush"

    def test_clear_bearish_rejected_without_haiku(self, mock_ds):
        from src.sentiment import SentimentAnalyzer

        mock_ds.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 30,
            "sentiment": 1.5,
            "social_volume": 500,
            "social_volume_change": -1.0,
        }

        sa = SentimentAnalyzer(mock_ds)
        result = sa.confirm_sentiment("BAD", {"score": 75})

        assert result["confirmed"] is False

    @patch("src.sentiment.Anthropic")
    def test_mixed_signals_falls_back_to_haiku(self, mock_anthropic_cls, mock_ds):
        from src.sentiment import SentimentAnalyzer

        # Neutral LC — galaxy OK but sentiment neutral, volume rising
        mock_ds.lunarcrush.get_stock_social.return_value = {
            "galaxy_score": 65,
            "sentiment": 3.0,
            "social_volume": 5000,
            "social_volume_change": 1.5,
        }

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"signal": "BULLISH", "confidence": 8, "reason": "Good momentum"}')]
        mock_anthropic_cls.return_value.messages.create.return_value = mock_response

        sa = SentimentAnalyzer(mock_ds)
        result = sa.confirm_sentiment("MIX", {"score": 75, "components": {"rsi_value": 60, "vol_ratio": 1.8}})

        assert result["confirmed"] is True
        assert result["source"] == "claude_haiku"
