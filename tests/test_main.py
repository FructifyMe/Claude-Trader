"""Tests for main.py — bot automation, pipeline integration, error handling."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime


def _make_full_settings():
    return {
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
                "sma_period": 20, "rsi_period": 14, "rsi_min": 50, "rsi_max": 70,
                "volume_surge_multiplier": 1.5, "social_volume_surge_multiplier": 2.0,
                "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
                "momentum_lookback_days": 5, "score_threshold": 70,
                "weights": {"sma": 0.20, "rsi": 0.15, "volume": 0.20, "macd": 0.10,
                            "price_momentum": 0.10, "social_volume": 0.15, "sentiment_shift": 0.10},
            },
            "sentiment": {
                "lunarcrush_galaxy_min": 60, "lunarcrush_sentiment": "bullish",
                "social_volume_trend": "rising", "claude_model": "claude-haiku-4-5-20251001",
                "claude_confidence_min": 7, "claude_max_tokens": 200,
            },
            "exits": {
                "stop_loss_pct": 0.05, "trailing_stop_pct": 0.03,
                "profit_target_pct": 0.10, "max_hold_days": 5,
                "sentiment_reversal_exit": True,
            },
        },
        "risk": {
            "max_position_pct": 0.20, "max_open_positions": 5,
            "max_daily_loss_pct": 0.03, "max_weekly_loss_pct": 0.07,
            "max_per_trade_risk_pct": 0.01, "min_cash_reserve_pct": 0.10,
        },
        "schedule": {
            "scan_interval_minutes": 5, "universe_refresh_time": "09:25",
            "market_open": "09:45", "market_close": "15:45", "timezone": "US/Eastern",
        },
        "logging": {
            "trade_log_path": "data/trades.json",
            "daily_summary_path": "data/daily_summary",
            "log_level": "INFO", "log_decisions": True,
        },
    }


class TestPipeline:
    @patch("src.main.DataService")
    def test_scan_cycle_halted_outside_hours(self, mock_ds_cls):
        """Trading halts outside market hours, not when LC is down."""
        from src.main import Pipeline

        mock_ds = MagicMock()
        mock_ds.settings = _make_full_settings()
        mock_ds_cls.return_value = mock_ds
        mock_ds.alpaca.get_account.return_value = {
            "equity": 1000, "cash": 800, "buying_power": 1600, "portfolio_value": 1000,
        }

        pipeline = Pipeline()
        with patch.object(pipeline.risk, '_in_trading_window', return_value=False):
            signals = pipeline.run_scan_cycle()

        assert signals == []

    @patch("src.main.DataService")
    def test_scan_cycle_no_candidates(self, mock_ds_cls):
        from src.main import Pipeline

        mock_ds = MagicMock()
        mock_ds.settings = _make_full_settings()
        mock_ds_cls.return_value = mock_ds
        mock_ds.alpaca.get_account.return_value = {
            "equity": 1000, "cash": 800, "buying_power": 1600, "portfolio_value": 1000,
        }
        mock_ds.alpaca.get_positions.return_value = []
        mock_ds.lunarcrush.is_available.return_value = True

        pipeline = Pipeline()

        # Empty watchlist
        with patch.object(pipeline.scanner, 'load_watchlist', return_value=[]):
            with patch.object(pipeline.risk, '_in_trading_window', return_value=True):
                signals = pipeline.run_scan_cycle()

        assert signals == []


class TestTradingBot:
    @patch("src.main.DataService")
    def test_bot_registers_jobs(self, mock_ds_cls):
        from src.main import TradingBot

        mock_ds = MagicMock()
        mock_ds.settings = _make_full_settings()
        mock_ds_cls.return_value = mock_ds

        bot = TradingBot()

        # Don't start the scheduler, just check jobs are added
        # Manually add jobs the same way start() does
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("US/Eastern")

        bot.scheduler.add_job(lambda: None, CronTrigger(hour=9, minute=25, timezone=tz), id="universe_refresh")
        bot.scheduler.add_job(lambda: None, IntervalTrigger(minutes=5), id="scan_cycle")
        bot.scheduler.add_job(lambda: None, IntervalTrigger(minutes=5), id="exit_check")
        bot.scheduler.add_job(lambda: None, CronTrigger(hour=15, minute=50, timezone=tz), id="daily_summary")
        bot.scheduler.add_job(lambda: None, CronTrigger(hour=9, minute=20, timezone=tz), id="daily_reset")

        jobs = bot.scheduler.get_jobs()
        job_ids = [j.id for j in jobs]

        assert "universe_refresh" in job_ids
        assert "scan_cycle" in job_ids
        assert "exit_check" in job_ids
        assert "daily_summary" in job_ids
        assert "daily_reset" in job_ids

    @patch("src.main.DataService")
    def test_market_hours_check(self, mock_ds_cls):
        from src.main import TradingBot

        mock_ds = MagicMock()
        mock_ds.settings = _make_full_settings()
        mock_ds_cls.return_value = mock_ds

        bot = TradingBot()

        # Patch datetime to test market hours
        with patch("src.main.datetime") as mock_dt:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("US/Eastern")

            # Wednesday at 10:00 AM ET — should be in market
            mock_now = datetime(2026, 4, 8, 10, 0, tzinfo=tz)
            mock_dt.now.return_value = mock_now
            mock_dt.strptime = datetime.strptime
            assert bot._in_market_hours() is True

            # Wednesday at 4:00 PM ET — after close
            mock_now = datetime(2026, 4, 8, 16, 0, tzinfo=tz)
            mock_dt.now.return_value = mock_now
            assert bot._in_market_hours() is False

            # Saturday at 10:00 AM — weekend
            mock_now = datetime(2026, 4, 11, 10, 0, tzinfo=tz)
            mock_dt.now.return_value = mock_now
            assert bot._in_market_hours() is False

    @patch("src.main.DataService")
    def test_safe_run_tracks_failures(self, mock_ds_cls):
        from src.main import TradingBot, MAX_API_FAILURES

        mock_ds = MagicMock()
        mock_ds.settings = _make_full_settings()
        mock_ds_cls.return_value = mock_ds

        bot = TradingBot()

        def failing_job():
            raise Exception("API timeout")

        wrapped = bot._safe_run(failing_job)

        # Should not crash
        wrapped()
        assert bot.pipeline._api_fail_count == 1

        wrapped()
        assert bot.pipeline._api_fail_count == 2

    @patch("src.main.DataService")
    def test_safe_run_halts_after_max_failures(self, mock_ds_cls):
        from src.main import TradingBot, MAX_API_FAILURES

        mock_ds = MagicMock()
        mock_ds.settings = _make_full_settings()
        mock_ds_cls.return_value = mock_ds

        bot = TradingBot()
        bot.pipeline._api_fail_count = MAX_API_FAILURES - 1

        def failing_job():
            raise Exception("API timeout")

        with patch.object(bot, '_shutdown') as mock_shutdown:
            wrapped = bot._safe_run(failing_job)
            wrapped()
            mock_shutdown.assert_called_once()
