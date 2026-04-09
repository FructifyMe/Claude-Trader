"""Entry point, trading pipeline, and APScheduler automation."""

import logging
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.data_client import DataService
from src.scanner import Scanner
from src.sentiment import SentimentAnalyzer
from src.risk_manager import RiskManager
from src.executor import Executor
from src.portfolio import Portfolio
from src.logger import TradeLogger

log = logging.getLogger(__name__)

# Max consecutive API failures before halting
MAX_API_FAILURES = 3


class Pipeline:
    """Wires scanner -> sentiment -> risk -> signal output."""

    def __init__(self):
        self.ds = DataService()
        self.scanner = Scanner(self.ds)
        self.sentiment = SentimentAnalyzer(self.ds)
        self.risk = RiskManager(self.ds)
        self.executor = Executor(self.ds)
        self.portfolio = Portfolio(self.ds)
        self.logger = TradeLogger(self.ds.settings)
        self._api_fail_count = 0

    def run_scan_cycle(self) -> list[dict]:
        """
        Full scan cycle:
        1. Score watchlist for momentum
        2. Filter candidates above threshold
        3. Confirm sentiment for each candidate
        4. Run risk approval for confirmed signals
        Returns list of approved trade signals.
        """
        cb = self.risk.check_circuit_breakers()
        if cb["halted"]:
            log.warning(f"Trading halted: {'; '.join(cb['reasons'])}")
            return []

        candidates = self.scanner.get_candidates()
        if not candidates:
            log.info("No momentum candidates this cycle")
            return []

        log.info(f"Momentum candidates: {[c['symbol'] for c in candidates]}")

        signals = []
        for candidate in candidates:
            symbol = candidate["symbol"]

            sentiment = self.sentiment.confirm_sentiment(symbol, candidate)
            if not sentiment.get("confirmed"):
                self.logger.log_decision(symbol, "SKIP", "Sentiment not confirmed",
                                         {"source": sentiment.get("source")})
                continue

            try:
                quote = self.ds.alpaca.get_latest_quote(symbol)
                entry_price = quote["ask"]
                stop_price = entry_price * (1 - self.ds.settings["strategy"]["exits"]["stop_loss_pct"])
            except Exception as e:
                log.error(f"Quote failed for {symbol}: {e}")
                continue

            approval = self.risk.approve_trade(symbol, entry_price, stop_price)
            if not approval["approved"]:
                self.logger.log_decision(symbol, "SKIP", f"Risk rejected: {approval['reason']}")
                continue

            signal = {
                "symbol": symbol,
                "action": "BUY",
                "entry_price": entry_price,
                "stop_price": stop_price,
                "shares": approval["sizing"]["shares"],
                "position_value": approval["sizing"]["position_value"],
                "risk_dollars": approval["sizing"]["risk_dollars"],
                "momentum_score": candidate["score"],
                "sentiment": sentiment,
            }
            signals.append(signal)
            log.info(f"SIGNAL: BUY {signal['shares']} {symbol} @ {entry_price:.2f} (stop {stop_price:.2f})")

        return signals

    def close(self):
        self.ds.close()


class TradingBot:
    """Autonomous trading bot with APScheduler."""

    def __init__(self):
        self.pipeline = Pipeline()
        self.settings = self.pipeline.ds.settings
        self.schedule_cfg = self.settings["schedule"]
        self.tz = ZoneInfo(self.schedule_cfg["timezone"])
        self.scheduler = BlockingScheduler(timezone=self.tz)
        self._running = False

    def start(self):
        """Start the bot with scheduled jobs."""
        self._setup_logging()
        log.info("=== Auto-Trader Bot starting ===")

        # Pre-market universe refresh
        refresh_time = self.schedule_cfg["universe_refresh_time"]
        h, m = refresh_time.split(":")
        self.scheduler.add_job(
            self._safe_run(self._refresh_universe),
            CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri", timezone=self.tz),
            id="universe_refresh",
            name="Pre-market universe refresh",
        )

        # Intraday scan cycle
        self.scheduler.add_job(
            self._safe_run(self._scan_and_execute),
            IntervalTrigger(minutes=self.schedule_cfg["scan_interval_minutes"]),
            id="scan_cycle",
            name="Scan and execute cycle",
        )

        # Exit check (runs with scan but also independently)
        self.scheduler.add_job(
            self._safe_run(self._check_exits),
            IntervalTrigger(minutes=self.schedule_cfg["scan_interval_minutes"]),
            id="exit_check",
            name="Exit condition check",
        )

        # End-of-day summary
        close_time = self.schedule_cfg["market_close"]
        h, m = close_time.split(":")
        self.scheduler.add_job(
            self._safe_run(self._daily_summary),
            CronTrigger(hour=int(h), minute=int(m) + 5, day_of_week="mon-fri", timezone=self.tz),
            id="daily_summary",
            name="End-of-day summary",
        )

        # Daily reset
        self.scheduler.add_job(
            self._safe_run(self._daily_reset),
            CronTrigger(hour=9, minute=20, day_of_week="mon-fri", timezone=self.tz),
            id="daily_reset",
            name="Daily P&L reset",
        )

        # Weekly reset on Monday
        self.scheduler.add_job(
            self._safe_run(self._weekly_reset),
            CronTrigger(hour=9, minute=20, day_of_week="mon", timezone=self.tz),
            id="weekly_reset",
            name="Weekly P&L reset",
        )

        # Graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        self._running = True
        log.info("Scheduler started. Jobs registered:")
        for job in self.scheduler.get_jobs():
            log.info(f"  - {job.name} (next: {job.next_run_time})")

        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            self._shutdown()

    def _shutdown(self, *args):
        log.info("=== Shutting down ===")
        self._running = False
        self.scheduler.shutdown(wait=False)
        self.pipeline.close()
        log.info("Bot stopped.")

    # ── Scheduled Jobs ─────────────────────────────────────────

    def _refresh_universe(self):
        """Pre-market: rebuild the watchlist."""
        log.info("--- Pre-market universe refresh ---")
        universe = self.pipeline.scanner.build_universe()
        log.info(f"Watchlist: {len(universe)} stocks")

    def _scan_and_execute(self):
        """Core loop: scan for signals, execute trades."""
        if not self._in_market_hours():
            return

        log.info("--- Scan cycle ---")
        signals = self.pipeline.run_scan_cycle()

        for sig in signals:
            result = self.pipeline.executor.execute_buy(sig)
            if result["success"]:
                self.pipeline.logger.log_trade({
                    "action": "BUY",
                    "symbol": sig["symbol"],
                    "shares": sig["shares"],
                    "price": sig["entry_price"],
                    "stop_price": sig["stop_price"],
                    "momentum_score": sig["momentum_score"],
                })
                self.pipeline._api_fail_count = 0
            else:
                log.error(f"Execution failed: {result}")

        # Update trailing stops after new entries
        self.pipeline.executor.update_trailing_stops()

    def _check_exits(self):
        """Check all positions for exit conditions."""
        if not self._in_market_hours():
            return

        actions = self.pipeline.executor.check_exits()
        for action in actions:
            if action["success"]:
                # Calculate realized P&L
                entry = self.pipeline.executor._entry_prices.get(action["symbol"], 0)
                pnl = (action["price"] - entry) * action["shares"] if entry > 0 else 0

                self.pipeline.risk.update_pnl(pnl)
                self.pipeline.portfolio.record_closed_trade({
                    "symbol": action["symbol"],
                    "shares": action["shares"],
                    "exit_price": action["price"],
                    "reason": action["reason"],
                    "pnl": round(pnl, 2),
                })
                self.pipeline.logger.log_trade({
                    "action": "SELL",
                    "symbol": action["symbol"],
                    "shares": action["shares"],
                    "price": action["price"],
                    "reason": action["reason"],
                    "detail": action.get("detail", ""),
                    "pnl": round(pnl, 2),
                })

    def _daily_summary(self):
        """End-of-day summary."""
        log.info("--- Daily summary ---")
        summary = self.pipeline.portfolio.get_daily_summary()
        self.pipeline.logger.write_daily_summary(summary)

        metrics = summary.get("metrics", {})
        log.info(
            f"Day complete: trades={summary.get('trades_today', 0)}, "
            f"pnl=${summary.get('pnl_today', 0):.2f}, "
            f"win_rate={metrics.get('win_rate', 0)}%, "
            f"total_pnl=${metrics.get('total_pnl', 0):.2f}"
        )

    def _daily_reset(self):
        """Reset daily P&L tracker."""
        self.pipeline.risk.reset_daily()
        log.info("Daily P&L reset")

    def _weekly_reset(self):
        """Reset weekly P&L tracker."""
        self.pipeline.risk.reset_weekly()
        log.info("Weekly P&L reset")

    # ── Helpers ────────────────────────────────────────────────

    def _in_market_hours(self) -> bool:
        now = datetime.now(self.tz)
        if now.weekday() >= 5:
            return False
        open_time = datetime.strptime(self.schedule_cfg["market_open"], "%H:%M").time()
        close_time = datetime.strptime(self.schedule_cfg["market_close"], "%H:%M").time()
        return open_time <= now.time() <= close_time

    def _safe_run(self, func):
        """Wrap a job function with error handling and retry tracking."""
        def wrapper():
            try:
                func()
            except Exception as e:
                self.pipeline._api_fail_count += 1
                log.error(f"Job {func.__name__} failed ({self.pipeline._api_fail_count}/{MAX_API_FAILURES}): {e}")
                if self.pipeline._api_fail_count >= MAX_API_FAILURES:
                    log.critical(f"Too many failures — halting bot")
                    self._shutdown()
        return wrapper

    def _setup_logging(self):
        log_level = self.settings["logging"].get("log_level", "INFO")
        logging.basicConfig(
            level=getattr(logging, log_level),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler("data/bot.log", mode="a"),
            ],
        )


# ── Entry Point ────────────────────────────────────────────────

def main():
    bot = TradingBot()
    bot.start()


if __name__ == "__main__":
    main()
