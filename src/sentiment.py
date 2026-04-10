"""Sentiment confirmation — LunarCrush primary, Claude Haiku backup."""

import json
import logging
import re
from typing import Optional

from anthropic import Anthropic

from src.data_client import DataService

log = logging.getLogger(__name__)


class SentimentAnalyzer:
    def __init__(self, data_service: DataService, learner=None):
        self.ds = data_service
        self.cfg = data_service.settings["strategy"]["sentiment"]
        self._anthropic: Optional[Anthropic] = None
        self.learner = learner

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
        """Sentiment confirmation via Claude Haiku."""
        # Build context lines — only include data we actually have
        ctx_lines = []
        if context.get("score"):
            ctx_lines.append(f"Momentum score: {context['score']}/100 (above 70 = strong)")
        if context.get("rsi_value"):
            rsi = context["rsi_value"]
            rsi_label = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral-bullish" if rsi >= 50 else "weakening"
            ctx_lines.append(f"RSI-5: {rsi:.1f} ({rsi_label})")
        if context.get("vol_ratio"):
            vr = context["vol_ratio"]
            vol_label = "heavy unusual volume" if vr > 2 else "above-average volume" if vr > 1.3 else "normal volume"
            ctx_lines.append(f"Volume ratio: {vr:.1f}x vs 20-day avg ({vol_label})")
        if context.get("above_sma"):
            ctx_lines.append("Price is ABOVE 20-day SMA (uptrend)")
        elif context.get("above_sma") is False:
            ctx_lines.append("Price is BELOW 20-day SMA (downtrend)")
        if context.get("macd_bullish"):
            ctx_lines.append("MACD is bullish (signal line crossover)")
        if context.get("five_day_return") is not None:
            ret = context["five_day_return"]
            ctx_lines.append(f"5-day return: {ret:+.1%}")
        if context.get("relative_strength") is not None:
            rs = context["relative_strength"]
            rs_label = "outperforming" if rs > 0 else "underperforming"
            ctx_lines.append(f"Relative strength vs SPY: {rs:+.1%} ({rs_label} market)")
        if context.get("screens"):
            ctx_lines.append(f"Found on Finviz screens: {', '.join(context['screens'])}")
        # Only include LC data if we actually have it (non-zero)
        if context.get("galaxy_score", 0) > 0:
            ctx_lines.append(f"LunarCrush Galaxy Score: {context['galaxy_score']}")
        if context.get("sentiment_value", 0) > 0:
            ctx_lines.append(f"Social sentiment: {context['sentiment_value']}/5")

        context_str = "\n".join(ctx_lines) if ctx_lines else "Limited data available"

        # Add learner performance context if available
        learner_note = ""
        if self.learner:
            try:
                report = self.learner.get_learning_report()
                if report.get("completed_trades", 0) >= 5:
                    winning = report.get("winning_screens", [])
                    screens = context.get("screens", [])
                    if winning and screens:
                        overlap = [s for s in screens if s in winning]
                        if overlap:
                            learner_note = (
                                f"\nHistorical note: stocks from {', '.join(overlap)} "
                                f"screens have historically performed well in our trading. "
                                f"Overall win rate: {report['win_rate']:.0%}."
                            )
            except Exception:
                pass

        prompt = (
            f"You are a swing-trade sentiment analyst. Should a momentum trader BUY {symbol} for a 1-5 day hold?\n\n"
            f"Technical signals:\n{context_str}\n\n"
            f"This stock already passed technical screening. Your job: does the setup look strong enough to enter?\n"
            f"Be decisive — lean BULLISH when technicals are strong, not neutral.\n"
            f"{learner_note}\n"
            f'Respond with ONLY valid JSON: {{"signal": "BULLISH" or "BEARISH" or "NEUTRAL", "confidence": 1-10, "reason": "one sentence"}}'
        )

        try:
            client = self._get_anthropic()
            response = client.messages.create(
                model=self.cfg["claude_model"],
                max_tokens=self.cfg["claude_max_tokens"],
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Handle markdown code fences Haiku sometimes wraps JSON in
            match = re.search(r'\{.*\}', text, re.DOTALL)
            result = json.loads(match.group() if match else text)

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
        lc_available = self.ds.lunarcrush.is_available()

        if lc_available:
            try:
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
                    "rsi_value": components.get("rsi5_value"),
                    "vol_ratio": components.get("vol_ratio"),
                    "above_sma": components.get("sma", 0) > 50,
                    "macd_bullish": components.get("macd", 0) > 50,
                    "five_day_return": components.get("price_momentum", 0) / 1000 if components.get("price_momentum") else None,
                    "relative_strength": (components.get("relative_strength", 50) - 50) / 1000 if components.get("relative_strength") else None,
                    "screens": momentum_data.get("sources", []),
                    "galaxy_score": lc["galaxy_score"],
                    "sentiment_value": lc["sentiment_value"],
                }
            except Exception as e:
                log.warning(f"LunarCrush failed for {symbol}: {e} — using Haiku")
                lc_available = False

        if not lc_available:
            # LC down — use Haiku as primary, but ONLY for strong momentum candidates
            # to minimize token burn (only candidates with score >70 reach here)
            log.info(f"LunarCrush unavailable — using Claude Haiku for {symbol}")
            components = momentum_data.get("components", {})
            context = {
                "score": momentum_data.get("score"),
                "rsi_value": components.get("rsi5_value"),
                "vol_ratio": components.get("vol_ratio"),
                "above_sma": components.get("sma", 0) > 50,
                "macd_bullish": components.get("macd", 0) > 50,
                "five_day_return": components.get("price_momentum", 0) / 1000 if components.get("price_momentum") else None,
                "relative_strength": (components.get("relative_strength", 50) - 50) / 1000 if components.get("relative_strength") else None,
                "screens": momentum_data.get("sources", []),
            }

        haiku = self.check_haiku(symbol, context)

        return {
            "symbol": symbol,
            "confirmed": haiku["confirmed"],
            "source": "claude_haiku",
            "lunarcrush": lc if lc_available else None,
            "haiku": haiku,
            "final": True,
        }
