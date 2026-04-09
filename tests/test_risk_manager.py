"""Tests for risk manager module."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


def _make_settings():
    return {
        "risk": {
            "max_position_pct": 0.20,
            "max_open_positions": 5,
            "max_daily_loss_pct": 0.03,
            "max_weekly_loss_pct": 0.07,
            "max_per_trade_risk_pct": 0.01,
            "min_cash_reserve_pct": 0.10,
        },
        "schedule": {
            "market_open": "09:45",
            "market_close": "15:45",
            "timezone": "US/Eastern",
        },
    }


@pytest.fixture
def mock_ds():
    ds = MagicMock()
    ds.settings = _make_settings()
    ds.alpaca.get_account.return_value = {
        "equity": 1000,
        "cash": 800,
        "buying_power": 1600,
        "portfolio_value": 1000,
    }
    ds.alpaca.get_positions.return_value = []
    ds.lunarcrush.is_available.return_value = True
    return ds


class TestPositionSizing:
    def test_basic_sizing(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        result = rm.calculate_position_size("AAPL", entry_price=100, stop_price=95)

        assert result["approved"] is True
        assert result["shares"] > 0
        # Risk per share = $5, max risk = 1% of $1000 = $10, so max 2 shares by risk
        assert result["shares"] <= 2
        assert result["risk_dollars"] <= 10

    def test_respects_max_position_pct(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        # With $1K portfolio, max position = 20% = $200. At $10/share = 20 shares max
        result = rm.calculate_position_size("CHEAP", entry_price=10, stop_price=9.9)

        assert result["approved"] is True
        assert result["position_value"] <= 200

    def test_respects_cash_reserve(self, mock_ds):
        from src.risk_manager import RiskManager

        mock_ds.alpaca.get_account.return_value = {
            "equity": 1000, "cash": 110, "buying_power": 110, "portfolio_value": 1000,
        }

        rm = RiskManager(mock_ds)
        # Cash=$110, reserve=10%=$100, available=$10
        result = rm.calculate_position_size("AAPL", entry_price=100, stop_price=95)

        assert result["approved"] is False
        assert "cash" in result["reason"].lower() or result["shares"] == 0 if result.get("shares") else True

    def test_zero_risk_rejected(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        result = rm.calculate_position_size("AAPL", entry_price=100, stop_price=100)

        assert result["approved"] is False
        assert "risk" in result["reason"].lower()


class TestExposure:
    def test_no_positions(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        result = rm.check_exposure()

        assert result["open_positions"] == 0
        assert result["can_open_new"] is True

    def test_max_positions_reached(self, mock_ds):
        from src.risk_manager import RiskManager

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": f"S{i}", "qty": 1, "current_price": 100, "avg_entry": 100, "unrealized_pl": 0, "unrealized_plpc": 0}
            for i in range(5)
        ]

        rm = RiskManager(mock_ds)
        result = rm.check_exposure()

        assert result["open_positions"] == 5
        assert result["can_open_new"] is False


class TestCircuitBreakers:
    def test_daily_loss_halt(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        # Lose $31 on $1000 = 3.1% > 3% limit
        rm.update_pnl(-31)

        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.check_circuit_breakers()

        assert result["halted"] is True
        assert result["daily_halt"] is True

    def test_weekly_loss_halt(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        rm.update_pnl(-71)  # 7.1% > 7% limit

        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.check_circuit_breakers()

        assert result["halted"] is True
        assert result["weekly_halt"] is True

    def test_lc_unavailable_halts(self, mock_ds):
        from src.risk_manager import RiskManager

        mock_ds.lunarcrush.is_available.return_value = False

        rm = RiskManager(mock_ds)
        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.check_circuit_breakers()

        assert result["halted"] is True
        assert result["lc_available"] is False

    def test_outside_trading_window_halts(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        with patch.object(rm, '_in_trading_window', return_value=False):
            result = rm.check_circuit_breakers()

        assert result["halted"] is True
        assert result["in_trading_window"] is False

    def test_no_halt_when_healthy(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.check_circuit_breakers()

        assert result["halted"] is False

    def test_daily_reset(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        rm.update_pnl(-31)

        with patch.object(rm, '_in_trading_window', return_value=True):
            assert rm.check_circuit_breakers()["daily_halt"] is True

        rm.reset_daily()

        with patch.object(rm, '_in_trading_window', return_value=True):
            assert rm.check_circuit_breakers()["daily_halt"] is False


class TestTradeApproval:
    def test_approved_trade(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.approve_trade("AAPL", entry_price=100, stop_price=95)

        assert result["approved"] is True
        assert result["sizing"]["shares"] > 0

    def test_rejected_when_halted(self, mock_ds):
        from src.risk_manager import RiskManager

        rm = RiskManager(mock_ds)
        rm.update_pnl(-31)

        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.approve_trade("AAPL", entry_price=100, stop_price=95)

        assert result["approved"] is False
        assert "halted" in result["reason"].lower()

    def test_rejected_when_max_positions(self, mock_ds):
        from src.risk_manager import RiskManager

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": f"S{i}", "qty": 1, "current_price": 100, "avg_entry": 100, "unrealized_pl": 0, "unrealized_plpc": 0}
            for i in range(5)
        ]

        rm = RiskManager(mock_ds)
        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.approve_trade("NEW", entry_price=100, stop_price=95)

        assert result["approved"] is False
        assert "max positions" in result["reason"].lower()

    def test_rejected_when_already_holding(self, mock_ds):
        from src.risk_manager import RiskManager

        mock_ds.alpaca.get_positions.return_value = [
            {"symbol": "AAPL", "qty": 5, "current_price": 180, "avg_entry": 170, "unrealized_pl": 50, "unrealized_plpc": 0.06}
        ]

        rm = RiskManager(mock_ds)
        with patch.object(rm, '_in_trading_window', return_value=True):
            result = rm.approve_trade("AAPL", entry_price=180, stop_price=171)

        assert result["approved"] is False
        assert "already holding" in result["reason"].lower()
