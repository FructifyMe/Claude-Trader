"""Universe filter and momentum scoring engine."""

import json
import logging
import os
from datetime import datetime

import pandas as pd
import ta

from src.data_client import DataService

log = logging.getLogger(__name__)


class Scanner:
    def __init__(self, data_service: DataService):
        self.ds = data_service
        self.cfg = data_service.settings["strategy"]
        self.universe_cfg = self.cfg["universe"]
        self.momentum_cfg = self.cfg["momentum"]
        self.watchlist_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "watchlist.json"
        )

    # ── Universe Filter ────────────────────────────────────────

    def build_universe(self) -> list[dict]:
        """Daily pre-market scan: filter by market cap, volume, price, Galaxy Score."""
        log.info("Building universe...")

        # Step 1: Screen by fundamentals via Massive
        candidates = self.ds.massive.screen_stocks(
            min_market_cap=self.universe_cfg["min_market_cap"],
            min_volume=self.universe_cfg["min_avg_volume"],
            min_price=self.universe_cfg["min_price"],
            max_price=self.universe_cfg["max_price"],
        )
        log.info(f"Massive screener returned {len(candidates)} stocks")

        # Step 2: Filter by LunarCrush Galaxy Score
        universe = []
        galaxy_min = self.universe_cfg["galaxy_score_min"]
        max_size = self.universe_cfg["max_universe_size"]

        for stock in candidates:
            symbol = stock.get("symbol") or stock.get("ticker", "")
            if not symbol:
                continue
            try:
                social = self.ds.lunarcrush.get_stock_social(symbol)
                galaxy = social.get("galaxy_score", 0)
                if galaxy >= galaxy_min:
                    universe.append({
                        "symbol": symbol,
                        "market_cap": stock.get("market_cap", 0),
                        "avg_volume": stock.get("avg_volume", 0),
                        "price": stock.get("price", stock.get("close", 0)),
                        "galaxy_score": galaxy,
                        "social_volume": social.get("social_volume", 0),
                    })
            except Exception as e:
                log.warning(f"LunarCrush failed for {symbol}: {e}")
                continue

            if len(universe) >= max_size:
                break

        # Sort by galaxy score descending
        universe.sort(key=lambda x: x["galaxy_score"], reverse=True)

        # Save watchlist
        self._save_watchlist(universe)
        log.info(f"Universe built: {len(universe)} stocks")
        return universe

    def _save_watchlist(self, universe: list[dict]):
        os.makedirs(os.path.dirname(self.watchlist_path), exist_ok=True)
        with open(self.watchlist_path, "w") as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "count": len(universe),
                "stocks": universe,
            }, f, indent=2)

    def load_watchlist(self) -> list[dict]:
        if not os.path.exists(self.watchlist_path):
            return []
        with open(self.watchlist_path) as f:
            data = json.load(f)
        return data.get("stocks", [])

    # ── Momentum Scoring ───────────────────────────────────────

    def score_momentum(self, symbol: str) -> dict:
        """Calculate momentum score (0-100) for a single ticker."""
        cfg = self.momentum_cfg
        weights = cfg["weights"]

        try:
            bars = self.ds.alpaca.get_bars(symbol, limit=50)
        except Exception:
            bars = self.ds.massive.get_bars(symbol, limit=50)

        if len(bars) < cfg["sma_period"]:
            return {"symbol": symbol, "score": 0, "components": {}, "reason": "insufficient data"}

        close = bars["close"]
        volume = bars["volume"]

        # SMA: price above SMA20
        sma = ta.trend.sma_indicator(close, window=cfg["sma_period"])
        sma_score = 100.0 if close.iloc[-1] > sma.iloc[-1] else 0.0

        # RSI: ideal range 50-70
        rsi = ta.momentum.rsi(close, window=cfg["rsi_period"])
        rsi_val = rsi.iloc[-1]
        if cfg["rsi_min"] <= rsi_val <= cfg["rsi_max"]:
            rsi_score = 100.0
        elif rsi_val < cfg["rsi_min"]:
            rsi_score = max(0, rsi_val / cfg["rsi_min"] * 100)
        else:
            rsi_score = max(0, 100 - (rsi_val - cfg["rsi_max"]) * 5)

        # Volume surge: current vs 20-day average
        avg_vol = volume.rolling(20).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / avg_vol if avg_vol > 0 else 0
        vol_score = min(100, (vol_ratio / cfg["volume_surge_multiplier"]) * 100)

        # MACD crossover
        macd_line = ta.trend.macd(close, window_fast=cfg["macd_fast"],
                                   window_slow=cfg["macd_slow"])
        macd_signal = ta.trend.macd_signal(close, window_fast=cfg["macd_fast"],
                                            window_slow=cfg["macd_slow"],
                                            window_sign=cfg["macd_signal"])
        macd_score = 100.0 if macd_line.iloc[-1] > macd_signal.iloc[-1] else 0.0

        # Price momentum: 5-day return
        lookback = cfg["momentum_lookback_days"]
        if len(close) > lookback:
            price_return = (close.iloc[-1] - close.iloc[-lookback - 1]) / close.iloc[-lookback - 1]
            momentum_score = min(100, max(0, price_return * 1000))  # 10% return = 100
        else:
            momentum_score = 0.0

        # Social volume surge (LunarCrush)
        try:
            social = self.ds.lunarcrush.get_stock_social(symbol)
            sv_change = social.get("social_volume_change", 0)
            social_vol_score = min(100, (sv_change / cfg["social_volume_surge_multiplier"]) * 100)
            sentiment_val = social.get("sentiment", 0)
            # Sentiment shift: positive sentiment = high score
            sentiment_score = min(100, max(0, sentiment_val * 20))  # 5 = 100
        except Exception:
            social_vol_score = 0.0
            sentiment_score = 0.0

        # Weighted total
        total = (
            sma_score * weights["sma"]
            + rsi_score * weights["rsi"]
            + vol_score * weights["volume"]
            + macd_score * weights["macd"]
            + momentum_score * weights["price_momentum"]
            + social_vol_score * weights["social_volume"]
            + sentiment_score * weights["sentiment_shift"]
        )

        components = {
            "sma": round(sma_score, 1),
            "rsi": round(rsi_score, 1),
            "rsi_value": round(rsi_val, 1),
            "volume_surge": round(vol_score, 1),
            "vol_ratio": round(vol_ratio, 2),
            "macd": round(macd_score, 1),
            "price_momentum": round(momentum_score, 1),
            "social_volume": round(social_vol_score, 1),
            "sentiment_shift": round(sentiment_score, 1),
        }

        return {
            "symbol": symbol,
            "score": round(total, 1),
            "components": components,
            "passes_threshold": bool(total >= cfg["score_threshold"]),
        }

    def scan_watchlist(self) -> list[dict]:
        """Score all watchlist stocks, return sorted by score descending."""
        watchlist = self.load_watchlist()
        if not watchlist:
            log.warning("No watchlist found — run build_universe first")
            return []

        results = []
        for stock in watchlist:
            symbol = stock["symbol"]
            result = self.score_momentum(symbol)
            results.append(result)
            if result["passes_threshold"]:
                log.info(f"SIGNAL: {symbol} score={result['score']}")

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def get_candidates(self) -> list[dict]:
        """Return only stocks passing the momentum threshold."""
        all_scored = self.scan_watchlist()
        return [r for r in all_scored if r["passes_threshold"]]
