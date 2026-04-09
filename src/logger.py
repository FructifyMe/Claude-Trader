"""Trade logging — JSON trade log, daily summary CSV, decision audit."""

import csv
import json
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)


class TradeLogger:
    def __init__(self, settings: dict):
        self.log_cfg = settings["logging"]
        self.trade_log_path = self.log_cfg["trade_log_path"]
        self.summary_path = self.log_cfg["daily_summary_path"]
        self.log_decisions = self.log_cfg.get("log_decisions", True)

        os.makedirs(os.path.dirname(self.trade_log_path) or ".", exist_ok=True)
        os.makedirs(self.summary_path, exist_ok=True)

    # ── Trade Log (JSON, append-only) ──────────────────────────

    def log_trade(self, trade: dict):
        """Append a trade entry to the JSON log."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            **trade,
        }

        trades = self._load_trades()
        trades.append(entry)
        self._save_trades(trades)

        if self.log_decisions:
            action = trade.get("action", "UNKNOWN")
            symbol = trade.get("symbol", "???")
            log.info(f"TRADE LOG: {action} {symbol} — {trade.get('reason', trade.get('detail', ''))}")

    def log_decision(self, symbol: str, decision: str, reason: str, data: dict = None):
        """Log a skip/pass/halt decision for audit trail."""
        if not self.log_decisions:
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "decision",
            "symbol": symbol,
            "decision": decision,
            "reason": reason,
        }
        if data:
            entry["data"] = data

        trades = self._load_trades()
        trades.append(entry)
        self._save_trades(trades)

        log.info(f"DECISION: {decision} {symbol} — {reason}")

    def _load_trades(self) -> list:
        if not os.path.exists(self.trade_log_path):
            return []
        with open(self.trade_log_path) as f:
            return json.load(f)

    def _save_trades(self, trades: list):
        with open(self.trade_log_path, "w") as f:
            json.dump(trades, f, indent=2)

    # ── Daily Summary (CSV) ────────────────────────────────────

    def write_daily_summary(self, summary: dict):
        """Write daily summary to a CSV file."""
        date = summary.get("date", datetime.now().date().isoformat())
        filepath = os.path.join(self.summary_path, f"{date}.csv")

        rows = []

        # Portfolio row
        portfolio = summary.get("portfolio", {})
        rows.append({
            "type": "portfolio",
            "equity": portfolio.get("equity"),
            "cash": portfolio.get("cash"),
            "open_positions": portfolio.get("open_positions"),
            "unrealized_pl": portfolio.get("total_unrealized_pl"),
        })

        # Metrics row
        metrics = summary.get("metrics", {})
        rows.append({
            "type": "metrics",
            "total_trades": metrics.get("total_trades"),
            "win_rate": metrics.get("win_rate"),
            "total_pnl": metrics.get("total_pnl"),
            "sharpe": metrics.get("sharpe_ratio"),
            "max_drawdown": metrics.get("max_drawdown_pct"),
        })

        # Summary row
        rows.append({
            "type": "summary",
            "trades_today": summary.get("trades_today"),
            "pnl_today": summary.get("pnl_today"),
        })

        if rows:
            fieldnames = sorted(set().union(*(r.keys() for r in rows)))
            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            log.info(f"Daily summary written: {filepath}")

    # ── Query ──────────────────────────────────────────────────

    def get_trades(self, symbol: str = None, action: str = None) -> list[dict]:
        """Query trade log with optional filters."""
        trades = self._load_trades()
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]
        if action:
            trades = [t for t in trades if t.get("action") == action]
        return trades

    def get_todays_trades(self) -> list[dict]:
        today = datetime.now().date().isoformat()
        return [t for t in self._load_trades() if t.get("timestamp", "").startswith(today)]
