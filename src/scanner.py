"""Universe filter and momentum scoring engine.

Scans the ENTIRE market via Finviz + Alpaca, then scores candidates using
technical momentum (RSI-5, MACD, volume surge, relative strength vs SPY,
gap detection) plus social/alternative signals.
"""

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
        self._spy_bars: pd.DataFrame | None = None  # cached for relative strength

    # ── Universe Filter ────────────────────────────────────────

    def build_universe(self) -> list[dict]:
        """Pre-market scan: Finviz screens + Alpaca validation + optional LC filter."""
        log.info("Building universe via full-market screen...")

        # MarketScreener runs 9 Finviz screens + Alpaca validation
        candidates = self.ds.massive.screen_stocks(
            min_market_cap=0,  # Finviz handles cap filtering per-screen
            min_volume=self.universe_cfg["min_avg_volume"],
            min_price=self.universe_cfg["min_price"],
            max_price=self.universe_cfg["max_price"],
        )
        log.info(f"Full-market screener returned {len(candidates)} stocks")

        # Prioritize: stocks found by multiple screens rank higher
        candidates.sort(key=lambda x: (x.get("source_count", 0), x.get("volume", 0)),
                        reverse=True)

        # Optional: LC Galaxy Score filter on top candidates only (saves API calls)
        lc_available = self.ds.lunarcrush.is_available()
        if lc_available:
            log.info("LunarCrush available — enriching top candidates with social data")
            galaxy_min = self.universe_cfg["galaxy_score_min"]
            enriched = 0
            for stock in candidates[:30]:  # only check top 30 to stay within rate limits
                try:
                    social = self.ds.lunarcrush.get_stock_social(stock["symbol"])
                    stock["galaxy_score"] = social.get("galaxy_score", 0)
                    stock["social_volume"] = social.get("social_volume", 0)
                    stock["sentiment"] = social.get("sentiment", 0)
                    enriched += 1
                except Exception as e:
                    log.debug(f"LC skip {stock['symbol']}: {e}")
            log.info(f"Enriched {enriched} stocks with LunarCrush data")
        else:
            log.warning("LunarCrush unavailable — using technical signals only")

        # Cap universe size
        max_size = self.universe_cfg.get("max_universe_size", 100)
        universe = candidates[:max_size]

        self._save_watchlist(universe)
        log.info(f"Universe built: {len(universe)} stocks from {len(set().union(*[set(s.get('sources', [])) for s in universe]))} screens")
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

    # ── SPY Benchmark (for relative strength) ──────────────────

    def _get_spy_bars(self) -> pd.DataFrame:
        """Get SPY bars, cached for the session."""
        if self._spy_bars is None or len(self._spy_bars) == 0:
            try:
                self._spy_bars = self.ds.alpaca.get_bars("SPY", limit=50)
            except Exception as e:
                log.warning(f"Failed to get SPY bars: {e}")
                self._spy_bars = pd.DataFrame()
        return self._spy_bars

    # ── Momentum Scoring ───────────────────────────────────────

    def score_momentum(self, symbol: str) -> dict:
        """Calculate momentum score (0-100) using enhanced signals.

        Signals:
        - SMA20: price above 20-day SMA (trend confirmation)
        - RSI-5: short-term RSI for swing timing (better than RSI-14)
        - Volume surge: current volume vs 20-day avg
        - MACD crossover: trend momentum
        - Relative strength vs SPY: outperforming the market
        - Gap detection: gap-up as catalyst
        - Price momentum: 5-day return
        - Social signals: LC sentiment or neutral if unavailable
        """
        cfg = self.momentum_cfg
        adjustments = self._load_learner_adjustments()
        weights = adjustments.get("weight_adjustments", cfg["weights"])

        try:
            bars = self.ds.alpaca.get_bars(symbol, limit=50)
        except Exception as e:
            log.warning(f"Failed to get bars for {symbol}: {e}")
            return {"symbol": symbol, "score": 0, "components": {}, "passes_threshold": False, "reason": "data unavailable"}

        if len(bars) < 20:
            return {"symbol": symbol, "score": 0, "components": {}, "passes_threshold": False, "reason": "insufficient data"}

        close = bars["close"]
        volume = bars["volume"]
        high = bars["high"]
        low = bars["low"]
        open_price = bars["open"]

        # 1. SMA20: price above SMA20
        sma = ta.trend.sma_indicator(close, window=cfg["sma_period"])
        sma_score = 100.0 if close.iloc[-1] > sma.iloc[-1] else 0.0

        # 2. RSI-5 (short-term, better for swing trading than RSI-14)
        rsi_period = cfg.get("rsi_period", 14)
        rsi5 = ta.momentum.rsi(close, window=5)
        rsi14 = ta.momentum.rsi(close, window=rsi_period)
        rsi5_val = rsi5.iloc[-1] if len(rsi5) > 0 and pd.notna(rsi5.iloc[-1]) else 50
        rsi14_val = rsi14.iloc[-1] if len(rsi14) > 0 and pd.notna(rsi14.iloc[-1]) else 50

        # Ideal: RSI-5 between 40-70 (not overbought, some momentum)
        if 40 <= rsi5_val <= 70:
            rsi_score = 100.0
        elif rsi5_val < 40:
            rsi_score = max(0, (rsi5_val / 40) * 80)  # oversold = partial credit
        else:
            rsi_score = max(0, 100 - (rsi5_val - 70) * 5)  # overbought penalty
        # Bonus: RSI-5 crossing above 50 (momentum shift)
        if len(rsi5) > 1 and rsi5.iloc[-2] < 50 <= rsi5.iloc[-1]:
            rsi_score = min(100, rsi_score + 20)

        # 3. Volume surge: current vs 20-day average
        avg_vol = volume.rolling(20).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / avg_vol if avg_vol > 0 else 0
        vol_score = min(100, (vol_ratio / cfg["volume_surge_multiplier"]) * 100)

        # 4. MACD crossover
        macd_line = ta.trend.macd(close, window_fast=cfg["macd_fast"],
                                   window_slow=cfg["macd_slow"])
        macd_signal = ta.trend.macd_signal(close, window_fast=cfg["macd_fast"],
                                            window_slow=cfg["macd_slow"],
                                            window_sign=cfg["macd_signal"])
        macd_score = 100.0 if macd_line.iloc[-1] > macd_signal.iloc[-1] else 0.0
        # Bonus: fresh crossover (crossed within last 2 bars)
        if len(macd_line) > 2 and len(macd_signal) > 2:
            if (macd_line.iloc[-2] <= macd_signal.iloc[-2] and
                    macd_line.iloc[-1] > macd_signal.iloc[-1]):
                macd_score = 100.0  # fresh cross is maximum signal

        # 5. Relative strength vs SPY (outperformance = good)
        spy_bars = self._get_spy_bars()
        rs_score = 50.0  # neutral default
        if len(spy_bars) >= 5 and len(close) >= 5:
            stock_ret_5d = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]
            spy_close = spy_bars["close"]
            spy_ret_5d = (spy_close.iloc[-1] - spy_close.iloc[-5]) / spy_close.iloc[-5]
            relative_strength = stock_ret_5d - spy_ret_5d
            # +5% outperformance = 100, -5% = 0
            rs_score = min(100, max(0, 50 + relative_strength * 1000))

        # 6. Gap detection (gap-up from previous close = catalyst)
        gap_score = 0.0
        if len(open_price) >= 2 and len(close) >= 2:
            gap_pct = (open_price.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            if gap_pct > 0.02:  # 2%+ gap up
                gap_score = min(100, gap_pct * 2000)  # 5% gap = 100

        # 7. Price momentum: 5-day return
        lookback = cfg["momentum_lookback_days"]
        if len(close) > lookback:
            price_return = (close.iloc[-1] - close.iloc[-lookback - 1]) / close.iloc[-lookback - 1]
            momentum_score = min(100, max(0, price_return * 1000))
        else:
            momentum_score = 0.0

        # 8. Social signals (LC or redistribute weights to technicals)
        lc_available = self.ds.lunarcrush.is_available()
        if lc_available:
            try:
                social = self.ds.lunarcrush.get_stock_social(symbol)
                sv_change = social.get("social_volume_change", 0)
                social_vol_score = min(100, (sv_change / cfg["social_volume_surge_multiplier"]) * 100)
                sentiment_val = social.get("sentiment", 0)
                sentiment_score = min(100, max(0, sentiment_val * 20))
            except Exception:
                lc_available = False
                social_vol_score = 0.0
                sentiment_score = 0.0
        else:
            social_vol_score = 0.0
            sentiment_score = 0.0

        # Weighted total
        w = weights
        if lc_available:
            # Full weights including social signals
            total = (
                sma_score * w.get("sma", 0.20)
                + rsi_score * w.get("rsi", 0.15)
                + vol_score * w.get("volume", 0.20)
                + macd_score * w.get("macd", 0.10)
                + momentum_score * w.get("price_momentum", 0.05)
                + social_vol_score * w.get("social_volume", 0.05)
                + sentiment_score * w.get("sentiment_shift", 0.05)
                + rs_score * 0.10
                + gap_score * 0.10
            )
        else:
            # LC unavailable — redistribute social weight to technicals
            # so scores aren't dragged down by missing data
            total = (
                sma_score * 0.22
                + rsi_score * 0.18
                + vol_score * 0.22
                + macd_score * 0.13
                + momentum_score * 0.10
                + rs_score * 0.10
                + gap_score * 0.05
            )

        components = {
            "sma": round(sma_score, 1),
            "rsi5": round(rsi_score, 1),
            "rsi5_value": round(rsi5_val, 1),
            "rsi14_value": round(rsi14_val, 1),
            "volume_surge": round(vol_score, 1),
            "vol_ratio": round(vol_ratio, 2),
            "macd": round(macd_score, 1),
            "relative_strength": round(rs_score, 1),
            "gap": round(gap_score, 1),
            "price_momentum": round(momentum_score, 1),
            "social_volume": round(social_vol_score, 1),
            "sentiment_shift": round(sentiment_score, 1),
        }

        threshold = adjustments.get("score_threshold_adjustment", cfg["score_threshold"])

        return {
            "symbol": symbol,
            "score": round(total, 1),
            "components": components,
            "passes_threshold": bool(total >= threshold),
        }

    def scan_watchlist(self) -> list[dict]:
        """Score all watchlist stocks, return sorted by score descending."""
        watchlist = self.load_watchlist()
        if not watchlist:
            log.warning("No watchlist found — run build_universe first")
            return []

        adjustments = self._load_learner_adjustments()
        screen_boosts = adjustments.get("screen_boosts", {})

        results = []
        for stock in watchlist:
            symbol = stock["symbol"]
            result = self.score_momentum(symbol)
            # Carry forward Finviz screen sources for sentiment context
            result["sources"] = stock.get("sources", [])

            # Apply learned screen boosts (capped at +5)
            if screen_boosts and result["sources"]:
                boost = sum(screen_boosts.get(s, 0) for s in result["sources"])
                boost = max(-5.0, min(5.0, boost))
                result["score"] = round(result["score"] + boost, 1)
                result["passes_threshold"] = bool(result["score"] >= adjustments.get(
                    "score_threshold_adjustment", self.momentum_cfg["score_threshold"]))

            results.append(result)
            if result["passes_threshold"]:
                log.info(f"SIGNAL: {symbol} score={result['score']} "
                         f"sources={stock.get('sources', [])}")

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def get_candidates(self) -> list[dict]:
        """Return only stocks passing the momentum threshold."""
        all_scored = self.scan_watchlist()
        return [r for r in all_scored if r["passes_threshold"]]

    def _load_learner_adjustments(self) -> dict:
        """Load learner weight/threshold adjustments if they exist."""
        path = os.path.join(os.path.dirname(__file__), "..", "data", "learner_adjustments.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
