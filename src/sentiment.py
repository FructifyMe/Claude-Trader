"""Sentiment confirmation — LunarCrush primary, Claude Haiku backup."""

import json
import logging
from typing import Optional

from anthropic import Anthropic

from src.data_client import DataService

log = logging.getLogger(__name__)


class SentimentAnalyzer:
    def __init__(self, data_service: DataService):
        self.ds = data_service
        self.cfg = data_service.settings["strategy"]["sentiment"]
        self._anthropic: Optional[Anthropic] = None

    # ── Primary: LunarCrush ────────────────────────────────────

    def check_lunarcrush(self, symbol: str) -> dict:
        """Check LunarCrush Galaxy Score, sentiment, and social volume trend."""
        social = self.ds.lunarcrush.get_stock_social(symbol)
        galaxy = social.get("galaxy_score", 0)
        sentiment_val = social.get("sentiment", 0)
        sv_change = social.get("social_volume_change", 0)

        if sentiment_val >= 4:
            direction = "bullish"
        elif sentiment_val <= 2:
            direction = "bearish"
        else:
            direction = "neutral"

        if sv_change > 0.1:
            sv_trend = "rising"
        elif sv_change < -0.1:
            sv_trend = "falling"
        else:
            sv_trend = "flat"

        galaxy_pass = galaxy >= self.cfg["lunarcrush_galaxy_min"]
        sentiment_pass = direction == self.cfg["lunarcrush_sentiment"]
        sv_pass = sv_trend == self.cfg["social_volume_trend"]
        confirmed = galaxy_pass and sentiment_pass and sv_pass

        return {
            "source": "lunarcrush",
            "symbol": symbol,
            "confirmed": confirmed,
            "galaxy_score": galaxy,
            "galaxy_pass": galaxy_pass,
            "sentiment": direction,
            "sentiment_value": sentiment_val,
            "sentiment_pass": sentiment_pass,
            "social_volume_trend": sv_trend,
            "social_volume_change": sv_change,
            "sv_pass": sv_pass,
        }

    # ── Backup: Claude Haiku ───────────────────────────────────

    def _get_anthropic(self) -> Anthropic:
        if self._anthropic is None:
            self._anthropic = Anthropic()
        return self._anthropic

    def check_haiku(self, symbol: str, context: dict) -> dict:
        """Backup sentiment via Claude Haiku. Only called when LC signals are mixed."""
        prompt = (
            f"You are a stock sentiment analyst. Analyze {symbol} and respond with ONLY valid JSON.\n"
            f"Context: momentum score {context.get('score', 'N/A')}, "
            f"RSI {context.get('rsi_value', 'N/A')}, "
            f"volume ratio {context.get('vol_ratio', 'N/A')}x, "
            f"Galaxy Score {context.get('galaxy_score', 'N/A')}, "
            f"social sentiment {context.get('sentiment_value', 'N/A')}/5.\n"
            f'Respond: {{"signal": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 1-10, "reason": "one sentence"}}'
        )

        try:
            client = self._get_anthropic()
            response = client.messages.create(
                model=self.cfg["claude_model"],
                max_tokens=self.cfg["claude_max_tokens"],
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            result = json.loads(text)

            signal = result.get("signal", "NEUTRAL").upper()
            confidence = int(result.get("confidence", 0))
            confirmed = signal == "BULLISH" and confidence >= self.cfg["claude_confidence_min"]

            return {
                "source": "claude_haiku",
                "symbol": symbol,
                "confirmed": confirmed,
                "signal": signal,
                "confidence": confidence,
                "reason": result.get("reason", ""),
            }
        except Exception as e:
            log.error(f"Haiku sentiment failed for {symbol}: {e}")
            return {
                "source": "claude_haiku",
                "symbol": symbol,
                "confirmed": False,
                "signal": "ERROR",
                "confidence": 0,
                "reason": str(e),
            }

    # ── Combined Check ─────────────────────────────────────────

    def confirm_sentiment(self, symbol: str, momentum_data: dict) -> dict:
        """
        1. Check LunarCrush (primary)
        2. If clearly bullish or bearish → use that
        3. If mixed → fall back to Claude Haiku
        """
        if not self.ds.lunarcrush.is_available():
            log.warning("LunarCrush API unavailable — halting new entries")
            return {
                "symbol": symbol,
                "confirmed": False,
                "source": "none",
                "reason": "LunarCrush API unavailable",
            }

        lc = self.check_lunarcrush(symbol)

        if lc["confirmed"]:
            log.info(f"CONFIRMED {symbol} via LunarCrush (galaxy={lc['galaxy_score']}, sentiment={lc['sentiment']})")
            return {**lc, "final": True}

        if lc["sentiment"] == "bearish" or lc["galaxy_score"] < 40:
            log.info(f"REJECTED {symbol} via LunarCrush (galaxy={lc['galaxy_score']}, sentiment={lc['sentiment']})")
            return {**lc, "final": True}

        # Mixed signals — use Haiku backup
        log.info(f"Mixed LC signals for {symbol} — falling back to Claude Haiku")
        components = momentum_data.get("components", {})
        context = {
            "score": momentum_data.get("score"),
            "rsi_value": components.get("rsi_value"),
            "vol_ratio": components.get("vol_ratio"),
            "galaxy_score": lc["galaxy_score"],
            "sentiment_value": lc["sentiment_value"],
        }
        haiku = self.check_haiku(symbol, context)

        return {
            "symbol": symbol,
            "confirmed": haiku["confirmed"],
            "source": "claude_haiku",
            "lunarcrush": lc,
            "haiku": haiku,
            "final": True,
        }
