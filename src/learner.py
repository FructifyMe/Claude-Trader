"""Strategy learner — analyzes trade outcomes and adjusts weights/thresholds."""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime

log = logging.getLogger(__name__)


class StrategyLearner:
    def __init__(self, settings: dict):
        self.cfg = settings.get("learning", {})
        self.momentum_cfg = settings["strategy"]["momentum"]
        self.original_weights = dict(self.momentum_cfg["weights"])
        self.original_threshold = self.momentum_cfg["score_threshold"]
        self.trade_log_path = settings["logging"]["trade_log_path"]
        self.adjustments_path = self.cfg.get("adjustments_path", "data/learner_adjustments.json")
        self.history_path = self.cfg.get("history_path", "data/learner_history.json")

    # ── Data Loading ──────────────────────────────────────────

    def _load_trades(self) -> list[dict]:
        if not os.path.exists(self.trade_log_path):
            return []
        with open(self.trade_log_path) as f:
            return json.load(f)

    def _load_completed_trades(self) -> list[dict]:
        """Pair BUY/SELL entries by symbol into completed round-trip trades."""
        all_entries = self._load_trades()
        buys = {}  # symbol -> list of unmatched BUY entries
        completed = []

        for entry in all_entries:
            action = entry.get("action")
            symbol = entry.get("symbol")
            if not action or not symbol:
                continue

            if action == "BUY":
                buys.setdefault(symbol, []).append(entry)

            elif action == "SELL" and symbol in buys and buys[symbol]:
                buy = buys[symbol].pop(0)
                completed.append({
                    "symbol": symbol,
                    "entry": buy,
                    "exit": entry,
                    "entry_price": buy.get("price", 0),
                    "exit_price": entry.get("price", 0),
                    "pnl": entry.get("pnl", 0),
                    "pnl_pct": entry.get("pnl_pct", 0),
                    "hold_days": entry.get("hold_days", 0),
                    "outcome": entry.get("outcome", "loss" if entry.get("pnl", 0) <= 0 else "win"),
                    "screen_sources": buy.get("screen_sources", []),
                    "components": buy.get("components", {}),
                    "momentum_score": buy.get("momentum_score", 0),
                    "exit_reason": entry.get("reason", ""),
                })

        return completed

    # ── Analysis ──────────────────────────────────────────────

    def analyze_screen_performance(self) -> dict:
        """Win rate and avg return per Finviz screen source."""
        completed = self._load_completed_trades()
        screen_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "total_return": 0.0})

        for trade in completed:
            for screen in trade["screen_sources"]:
                stats = screen_stats[screen]
                stats["trades"] += 1
                stats["total_return"] += trade["pnl_pct"]
                if trade["outcome"] == "win":
                    stats["wins"] += 1

        result = {}
        for screen, stats in screen_stats.items():
            n = stats["trades"]
            result[screen] = {
                "trades": n,
                "wins": stats["wins"],
                "win_rate": stats["wins"] / n if n > 0 else 0,
                "avg_return": stats["total_return"] / n if n > 0 else 0,
            }
        return result

    def analyze_component_correlation(self) -> dict:
        """Compare avg component scores in wins vs losses."""
        completed = self._load_completed_trades()
        wins = [t for t in completed if t["outcome"] == "win"]
        losses = [t for t in completed if t["outcome"] == "loss"]

        if not wins or not losses:
            return {}

        # Component keys from the momentum scorer
        component_keys = [
            "sma", "rsi5", "volume_surge", "macd",
            "relative_strength", "gap", "price_momentum",
            "social_volume", "sentiment_shift",
        ]

        result = {}
        for key in component_keys:
            win_vals = [t["components"].get(key, 0) for t in wins if t["components"]]
            loss_vals = [t["components"].get(key, 0) for t in losses if t["components"]]

            win_avg = sum(win_vals) / len(win_vals) if win_vals else 0
            loss_avg = sum(loss_vals) / len(loss_vals) if loss_vals else 0

            result[key] = {
                "win_avg": round(win_avg, 1),
                "loss_avg": round(loss_avg, 1),
                "edge": round(win_avg - loss_avg, 1),
                "win_count": len(win_vals),
                "loss_count": len(loss_vals),
            }
        return result

    # ── Adjustment Decisions ──────────────────────────────────

    def should_adjust(self) -> bool:
        """Only adjust after enough data and warm-up period."""
        if not self.cfg.get("enabled", False):
            return False

        completed = self._load_completed_trades()
        if len(completed) < self.cfg.get("min_completed_trades", 10):
            return False

        # Check warm-up period
        if not completed:
            return False
        first_trade_time = completed[0]["entry"].get("timestamp", "")
        if first_trade_time:
            try:
                first_dt = datetime.fromisoformat(first_trade_time)
                days_active = (datetime.now() - first_dt).days
                if days_active < self.cfg.get("min_days_before_adjusting", 7):
                    return False
            except (ValueError, TypeError):
                return False

        return True

    def generate_adjustments(self) -> dict:
        """Produce conservative weight and threshold adjustments."""
        completed = self._load_completed_trades()
        current = self.get_active_adjustments()
        current_weights = current.get("weight_adjustments", dict(self.original_weights))
        cycle_number = current.get("cycle_number", 0) + 1

        # Safety check: revert if performance degraded
        if self._check_performance_degradation(completed):
            log.warning("Performance degradation detected — reverting to original weights")
            return self._revert_adjustments(cycle_number)

        # Component correlation analysis
        correlation = self.analyze_component_correlation()
        max_delta = self.cfg.get("max_weight_delta_per_cycle", 0.03)
        max_drift = self.cfg.get("max_weight_drift_from_original", 0.15)
        floor = self.cfg.get("weight_floor", 0.03)
        decay = self.cfg.get("drift_decay_rate", 0.10)

        # Weight map: component analysis key -> settings.yaml weight key
        weight_map = {
            "sma": "sma",
            "rsi5": "rsi",
            "volume_surge": "volume",
            "macd": "macd",
            "price_momentum": "price_momentum",
            "social_volume": "social_volume",
            "sentiment_shift": "sentiment_shift",
        }

        min_samples = self.cfg.get("min_screen_trades", 3)
        new_weights = dict(current_weights)

        for comp_key, weight_key in weight_map.items():
            if weight_key not in new_weights:
                continue
            stats = correlation.get(comp_key, {})
            if stats.get("win_count", 0) < min_samples or stats.get("loss_count", 0) < min_samples:
                continue

            edge = stats.get("edge", 0)
            normalized = edge / 100.0  # scores are 0-100
            delta = normalized * max_delta

            # Decay toward original
            original = self.original_weights.get(weight_key, 0.10)
            drift = new_weights[weight_key] - original
            drift_penalty = drift * decay

            raw = new_weights[weight_key] + delta - drift_penalty

            # Clamp to bounds
            raw = max(floor, min(original + max_drift, max(original - max_drift, raw)))
            new_weights[weight_key] = round(raw, 4)

        # Renormalize so weights sum to 0.80 (rs + gap = 0.20 hardcoded)
        total = sum(new_weights.values())
        if total > 0:
            factor = 0.80 / total
            new_weights = {k: round(v * factor, 4) for k, v in new_weights.items()}

        # Screen boosts
        screen_perf = self.analyze_screen_performance()
        max_boost = self.cfg.get("max_screen_boost", 5.0)
        min_screen_n = self.cfg.get("min_screen_trades", 3)
        screen_boosts = {}

        for screen, stats in screen_perf.items():
            if stats["trades"] < min_screen_n:
                continue
            # Boost proportional to win rate deviation from 50%
            wr_edge = stats["win_rate"] - 0.50
            boost = wr_edge * 10.0  # +10% win rate = +1 point boost
            boost = max(-max_boost, min(max_boost, boost))
            if abs(boost) > 0.5:
                screen_boosts[screen] = round(boost, 1)

        # Score threshold adjustment
        wins = [t for t in completed if t["outcome"] == "win"]
        win_rate = len(wins) / len(completed) if completed else 0
        current_threshold = current.get("score_threshold_adjustment", self.original_threshold)

        if win_rate < 0.40:
            new_threshold = min(current_threshold + 2, self.cfg.get("score_threshold_max", 85))
        elif win_rate > 0.65:
            new_threshold = max(current_threshold - 2, self.cfg.get("score_threshold_min", 60))
        else:
            new_threshold = current_threshold

        adjustments = {
            "generated_at": datetime.now().isoformat(),
            "trade_count": len(completed),
            "days_active": (datetime.now() - datetime.fromisoformat(
                completed[0]["entry"]["timestamp"])).days if completed else 0,
            "cycle_number": cycle_number,
            "win_rate": round(win_rate, 3),
            "weight_adjustments": new_weights,
            "score_threshold_adjustment": new_threshold,
            "screen_boosts": screen_boosts,
            "original_weights": self.original_weights,
            "original_threshold": self.original_threshold,
        }
        return adjustments

    def _check_performance_degradation(self, completed: list[dict]) -> bool:
        """Revert if recent rolling win rate is terrible."""
        window = self.cfg.get("revert_window_trades", 5)
        floor = self.cfg.get("revert_win_rate_floor", 0.20)

        # Only check if we have active adjustments
        current = self.get_active_adjustments()
        if not current.get("cycle_number"):
            return False

        recent = completed[-window:] if len(completed) >= window else []
        if not recent:
            return False

        wins = sum(1 for t in recent if t["outcome"] == "win")
        return (wins / len(recent)) < floor

    def _revert_adjustments(self, cycle_number: int) -> dict:
        """Reset to original settings.yaml weights."""
        return {
            "generated_at": datetime.now().isoformat(),
            "trade_count": 0,
            "days_active": 0,
            "cycle_number": cycle_number,
            "win_rate": 0,
            "weight_adjustments": dict(self.original_weights),
            "score_threshold_adjustment": self.original_threshold,
            "screen_boosts": {},
            "original_weights": self.original_weights,
            "original_threshold": self.original_threshold,
            "reverted": True,
            "reason": "Performance degradation — rolling win rate below floor",
        }

    # ── Persistence ───────────────────────────────────────────

    def apply_adjustments(self, adjustments: dict):
        """Write adjustments to JSON overlay file (not settings.yaml)."""
        os.makedirs(os.path.dirname(self.adjustments_path) or ".", exist_ok=True)
        with open(self.adjustments_path, "w") as f:
            json.dump(adjustments, f, indent=2)
        log.info(f"Learner adjustments saved (cycle {adjustments.get('cycle_number')})")

        # Append to history for audit trail
        self._append_history(adjustments)

    def _append_history(self, adjustments: dict):
        """Append-only audit log of all adjustment cycles."""
        history = []
        if os.path.exists(self.history_path):
            with open(self.history_path) as f:
                history = json.load(f)
        history.append(adjustments)
        with open(self.history_path, "w") as f:
            json.dump(history, f, indent=2)

    def get_active_adjustments(self) -> dict:
        """Read current adjustments overlay."""
        if not os.path.exists(self.adjustments_path):
            return {}
        try:
            with open(self.adjustments_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    # ── Reporting ─────────────────────────────────────────────

    def get_learning_report(self) -> dict:
        """Summary for dashboard and logging."""
        completed = self._load_completed_trades()
        current = self.get_active_adjustments()
        wins = [t for t in completed if t["outcome"] == "win"]

        first_trade_time = ""
        if completed:
            first_trade_time = completed[0]["entry"].get("timestamp", "")

        days_active = 0
        if first_trade_time:
            try:
                days_active = (datetime.now() - datetime.fromisoformat(first_trade_time)).days
            except (ValueError, TypeError):
                pass

        screen_perf = self.analyze_screen_performance()
        winning_screens = [s for s, v in screen_perf.items()
                          if v["trades"] >= 3 and v["win_rate"] > 0.55]

        return {
            "completed_trades": len(completed),
            "days_active": days_active,
            "win_rate": len(wins) / len(completed) if completed else 0,
            "avg_pnl_pct": sum(t["pnl_pct"] for t in completed) / len(completed) if completed else 0,
            "cycle_number": current.get("cycle_number", 0),
            "adjustments_active": bool(current.get("cycle_number")),
            "ready_to_adjust": self.should_adjust(),
            "screen_performance": screen_perf,
            "winning_screens": winning_screens,
            "current_adjustments": current,
        }
