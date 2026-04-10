"""Tests for executor, portfolio, and logger modules."""
import pytest
import json
import os
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


def _make_settings():
    return {
        "strategy": {
            "exits": {
                "stop_loss_pct": 0.05,
                "trailing_stop_pct": 0.03,
                "profit_target_pct": 0.10,
                "max_hold_days": 5,
                "sentiment_reversal_exit": True,
            }
        },
        "logging": {
            "trade_log_path": "",  # set per test
            "daily_summary_path": "",
            "log_level": "INFO",
            "log_decisions": True,
        },
    }


@pytest.fixture
def mock_ds():
    ds = MagicMock()
    ds.settings = _make_settings()
    ds.alpaca.submit_limit_order.return_value = "order-123"
    ds.alpaca.submit_stop_order.return_value = "stop-456"
    ds.alpaca.get_latest_quote.return_value = {"bid": 99.0, "ask": 101.0, "mid": 100.0}
    ds.alpaca.get_positions.return_value = []
    ds.alpaca.cancel_order.return_value = None
    return ds


# ── Executor Tests ─────────────────────────────────────────────

class TestExecuteBuy:
    def test_successful_buy(self, mock_ds):
        from src.executor import Executor

        # Mock bracket order via trading.submit_order
        mock_order = MagicMock()
        mock_order.id = "order-123"
        mock_ds.alpaca.trading.submit_order.return_value = mock_order

        ex = Executor(mock_ds)
        signal = {"symbol": "AAPL", "shares": 2, "entry_price": 100.0, "stop_price": 95.0}

        result = ex.execute_buy(signal)

        assert result["success"] is True
        assert result["order_id"] == "order-123"
        assert result["stop_order_id"] == "bracket"
        assert ex._entry_prices["AAPL"] == 100.0
        mock_ds.alpaca.trading.submit_order.assert_called_once()

    def test_buy_failure(self, mock_ds):
        from src.executor import Executor

        mock_ds.alpaca.trading.submit_order.side_effect = Exception("API error")

        ex = Executor(mock_ds)
        signal = {"symbol": "FAIL", "shares": 1, "entry_price": 50.0, "stop_price": 47.5}

        result = ex.execute_buy(signal)

        assert result["success"] is False
        assert "error" in result


class TestExitLogic:
    def test_trailing_stop_triggers(self, mock_ds):
        from src.executor import Executor

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "AAPL", "current_price": 97.0, "qty": 2, "avg_entry": 100.0,
             "unrealized_pl": -6, "unrealized_plpc": -0.03}
        ]

        ex = Executor(mock_ds)
        ex._high_water["AAPL"] = 100.0  # 3% drop from 100 to 97
        ex._entry_prices["AAPL"] = 100.0
        ex._entry_times["AAPL"] = datetime.now()

        actions = ex.check_exits()

        assert len(actions) == 1
        assert actions[0]["reason"] == "trailing_stop"

    def test_profit_target_sells_half(self, mock_ds):
        from src.executor import Executor

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "WIN", "current_price": 111.0, "qty": 10, "avg_entry": 100.0,
             "unrealized_pl": 110, "unrealized_plpc": 0.11}
        ]

        ex = Executor(mock_ds)
        ex._high_water["WIN"] = 111.0
        ex._entry_prices["WIN"] = 100.0
        ex._entry_times["WIN"] = datetime.now()
        ex._half_sold["WIN"] = False

        actions = ex.check_exits()

        assert len(actions) == 1
        assert actions[0]["reason"] == "profit_target"
        assert actions[0]["shares"] == 5  # half of 10
        assert ex._half_sold["WIN"] is True

    def test_profit_target_doesnt_fire_twice(self, mock_ds):
        from src.executor import Executor

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "WIN", "current_price": 112.0, "qty": 5, "avg_entry": 100.0,
             "unrealized_pl": 60, "unrealized_plpc": 0.12}
        ]

        ex = Executor(mock_ds)
        ex._high_water["WIN"] = 112.0
        ex._entry_prices["WIN"] = 100.0
        ex._entry_times["WIN"] = datetime.now()
        ex._half_sold["WIN"] = True  # Already sold half

        # No trailing stop triggered (only 0% from high)
        # Sentiment check would need to fail too
        mock_ds.lunarcrush.get_stock_social.return_value = {"sentiment": 4.5}

        actions = ex.check_exits()
        # Should not trigger profit target again
        profit_actions = [a for a in actions if a.get("reason") == "profit_target"]
        assert len(profit_actions) == 0

    def test_sentiment_reversal_exit(self, mock_ds):
        from src.executor import Executor

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "BAD", "current_price": 101.0, "qty": 3, "avg_entry": 100.0,
             "unrealized_pl": 3, "unrealized_plpc": 0.01}
        ]
        mock_ds.lunarcrush.get_stock_social.return_value = {"sentiment": 1.5}

        ex = Executor(mock_ds)
        ex._high_water["BAD"] = 101.0
        ex._entry_prices["BAD"] = 100.0
        ex._entry_times["BAD"] = datetime.now()

        actions = ex.check_exits()

        assert len(actions) == 1
        assert actions[0]["reason"] == "sentiment_reversal"

    def test_time_exit_after_max_days(self, mock_ds):
        from src.executor import Executor

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "OLD", "current_price": 102.0, "qty": 2, "avg_entry": 100.0,
             "unrealized_pl": 4, "unrealized_plpc": 0.02}
        ]
        # Neutral sentiment — should trigger time exit
        mock_ds.lunarcrush.get_stock_social.return_value = {"sentiment": 3.0}

        ex = Executor(mock_ds)
        ex._high_water["OLD"] = 102.0
        ex._entry_prices["OLD"] = 100.0
        ex._entry_times["OLD"] = datetime.now() - timedelta(days=6)

        actions = ex.check_exits()

        assert len(actions) == 1
        assert actions[0]["reason"] == "time_exit"


class TestTrailingStopUpdate:
    def test_raises_stop(self, mock_ds):
        from src.executor import Executor

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "AAPL", "current_price": 110.0, "qty": 2, "avg_entry": 100.0,
             "unrealized_pl": 20, "unrealized_plpc": 0.1}
        ]

        ex = Executor(mock_ds)
        ex._high_water["AAPL"] = 105.0
        ex._entry_prices["AAPL"] = 100.0
        ex._stop_orders["AAPL"] = "old-stop"

        mock_ds.alpaca.submit_stop_order.return_value = "new-stop"
        ex.update_trailing_stops()

        # High water should update to 110
        assert ex._high_water["AAPL"] == 110.0
        mock_ds.alpaca.cancel_order.assert_called_with("old-stop")
        mock_ds.alpaca.submit_stop_order.assert_called_once()
        assert ex._stop_orders["AAPL"] == "new-stop"


# ── Portfolio Tests ────────────────────────────────────────────

class TestPortfolio:
    def test_snapshot(self, mock_ds):
        from src.portfolio import Portfolio

        mock_ds.alpaca.get_account.return_value = {
            "equity": 1050, "cash": 500, "buying_power": 1000, "portfolio_value": 1050,
        }
        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "AAPL", "qty": 2, "avg_entry": 100, "current_price": 110,
             "unrealized_pl": 20, "unrealized_plpc": 0.1},
        ]

        p = Portfolio(mock_ds)
        snap = p.get_snapshot()

        assert snap["equity"] == 1050
        assert snap["open_positions"] == 1
        assert snap["total_unrealized_pl"] == 20

    def test_performance_metrics(self, mock_ds):
        from src.portfolio import Portfolio

        p = Portfolio(mock_ds)
        p.record_closed_trade({"symbol": "A", "pnl": 15})
        p.record_closed_trade({"symbol": "B", "pnl": -5})
        p.record_closed_trade({"symbol": "C", "pnl": 20})

        m = p.get_performance_metrics()

        assert m["total_trades"] == 3
        assert m["wins"] == 2
        assert m["losses"] == 1
        assert m["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert m["total_pnl"] == 30

    def test_max_drawdown(self, mock_ds):
        from src.portfolio import Portfolio

        p = Portfolio(mock_ds)
        # Peak at 20, drop to 5 = 75% drawdown
        dd = p._calc_max_drawdown([10, 10, -15, 5])

        assert dd > 0
        assert dd <= 1.0

    def test_empty_metrics(self, mock_ds):
        from src.portfolio import Portfolio

        p = Portfolio(mock_ds)
        m = p.get_performance_metrics()

        assert m["total_trades"] == 0
        assert m["win_rate"] == 0


# ── Logger Tests ───────────────────────────────────────────────

class TestTradeLogger:
    def test_log_and_query_trade(self, tmp_path):
        from src.logger import TradeLogger

        settings = _make_settings()
        settings["logging"]["trade_log_path"] = str(tmp_path / "trades.json")
        settings["logging"]["daily_summary_path"] = str(tmp_path / "summaries")

        tl = TradeLogger(settings)
        tl.log_trade({"action": "BUY", "symbol": "AAPL", "shares": 2, "price": 100})
        tl.log_trade({"action": "SELL", "symbol": "AAPL", "shares": 2, "price": 110, "reason": "profit"})
        tl.log_trade({"action": "BUY", "symbol": "MSFT", "shares": 3, "price": 400})

        all_trades = tl.get_trades()
        assert len(all_trades) == 3

        aapl_trades = tl.get_trades(symbol="AAPL")
        assert len(aapl_trades) == 2

        sells = tl.get_trades(action="SELL")
        assert len(sells) == 1

    def test_log_decision(self, tmp_path):
        from src.logger import TradeLogger

        settings = _make_settings()
        settings["logging"]["trade_log_path"] = str(tmp_path / "trades.json")
        settings["logging"]["daily_summary_path"] = str(tmp_path / "summaries")

        tl = TradeLogger(settings)
        tl.log_decision("AAPL", "SKIP", "Sentiment not confirmed")

        trades = tl.get_trades()
        assert len(trades) == 1
        assert trades[0]["decision"] == "SKIP"

    def test_daily_summary_csv(self, tmp_path):
        from src.logger import TradeLogger

        settings = _make_settings()
        settings["logging"]["trade_log_path"] = str(tmp_path / "trades.json")
        settings["logging"]["daily_summary_path"] = str(tmp_path / "summaries")

        tl = TradeLogger(settings)
        tl.write_daily_summary({
            "date": "2026-04-09",
            "portfolio": {"equity": 1050, "cash": 500, "open_positions": 2, "total_unrealized_pl": 50},
            "metrics": {"total_trades": 5, "win_rate": 60, "total_pnl": 30, "sharpe_ratio": 1.2, "max_drawdown_pct": 5},
            "trades_today": 3,
            "pnl_today": 15,
        })

        csv_path = tmp_path / "summaries" / "2026-04-09.csv"
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "portfolio" in content
        assert "metrics" in content
