import os
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from ta.volume import VolumeWeightedAveragePrice
from loguru import logger

class CryptoTrendStrategy:
    """
    Crypto Trend Following Strategy
    Trades BTC, ETH, SOL using EMA crossover + RSI + volume confirmation
    Inverse ETF (BITI) on bearish signal
    """

    def __init__(self, ibkr_client):
        self.client = ibkr_client

        # Crypto universe
        self.CRYPTO = ["BTC", "ETH", "SOL"]
        self.INVERSE = "BITI"  # inverse BTC ETF for bear market

        # Signal thresholds
        self.RSI_OVERSOLD    = 35
        self.RSI_OVERBOUGHT  = 70
        self.RSI_NEUTRAL     = 50
        self.VOLUME_CONFIRM  = 1.5   # volume must be 1.5x average
        self.EMA_FAST        = 50
        self.EMA_SLOW        = 200

    # ─── TREND SIGNALS ────────────────────────────────────────

    def get_trend_signal(self, symbol, data):
        """
        Returns: BULL, BEAR or NEUTRAL
        Requires EMA cross + RSI + volume confirmation
        """
        df = self._to_dataframe(data)
        if df is None or df.empty or len(df) < self.EMA_SLOW:
            logger.warning(f"{symbol}: insufficient data for signal")
            return "NEUTRAL"

        try:
            # EMA crossover
            ema_fast = EMAIndicator(df["close"], window=self.EMA_FAST).ema_indicator()
            ema_slow = EMAIndicator(df["close"], window=self.EMA_SLOW).ema_indicator()

            ema_bullish = ema_fast.iloc[-1] > ema_slow.iloc[-1]
            ema_bearish = ema_fast.iloc[-1] < ema_slow.iloc[-1]

            # RSI
            rsi = RSIIndicator(df["close"], window=14).rsi()
            rsi_value = rsi.iloc[-1]
            rsi_bullish = self.RSI_NEUTRAL < rsi_value < self.RSI_OVERBOUGHT
            rsi_bearish = rsi_value < self.RSI_NEUTRAL

            # Volume confirmation
            if "volume" in df.columns:
                avg_volume    = df["volume"].iloc[-20:].mean()
                recent_volume = df["volume"].iloc[-1]
                volume_confirmed = recent_volume >= avg_volume * self.VOLUME_CONFIRM
            else:
                volume_confirmed = True  # assume confirmed if no data

            # Higher highs confirmation
            recent_highs = df["high"].iloc[-5:]
            higher_highs = recent_highs.iloc[-1] > recent_highs.iloc[0]

            # ── Bull signal — all conditions must be met
            if ema_bullish and rsi_bullish and volume_confirmed and higher_highs:
                logger.info(f"{symbol}: BULL signal — EMA✅ RSI:{rsi_value:.1f}✅ Vol✅ HH✅")
                return "BULL"

            # ── Bear signal
            if ema_bearish and rsi_bearish and volume_confirmed:
                logger.info(f"{symbol}: BEAR signal — EMA✅ RSI:{rsi_value:.1f}✅ Vol✅")
                return "BEAR"

            logger.info(f"{symbol}: NEUTRAL — EMA_bull:{ema_bullish} RSI:{rsi_value:.1f} Vol:{volume_confirmed}")
            return "NEUTRAL"

        except Exception as e:
            logger.error(f"Trend signal error for {symbol}: {e}")
            return "NEUTRAL"

    # ─── LIQUIDITY CHECK ──────────────────────────────────────

    def check_liquidity(self, symbol, data):
        """Check 24hr volume is sufficient"""
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return False
        if "volume" not in df.columns:
            return True
        avg_volume    = df["volume"].iloc[-7:].mean()
        recent_volume = df["volume"].iloc[-1]
        liquid = recent_volume >= avg_volume * 0.5
        if not liquid:
            logger.warning(f"{symbol}: liquidity check failed")
        return liquid

    # ─── POSITION SIZING ──────────────────────────────────────

    def calculate_position_size(self, symbol, portfolio_value, data):
        """
        ATR-based position sizing
        Crypto uses 3x ATR stop — more volatile than ETFs
        """
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return 0
        try:
            atr = AverageTrueRange(
                df["high"], df["low"], df["close"], window=14
            ).average_true_range()
            atr_value     = atr.iloc[-1]
            stop_distance = atr_value * 3  # wider stop for crypto
            risk_amount   = portfolio_value * float(
                os.getenv("MAX_RISK_PER_TRADE", 0.02)
            )
            price          = df["close"].iloc[-1]
            quantity       = risk_amount / stop_distance
            position_value = quantity * price

            # Cap at 15% of portfolio per crypto position
            max_position   = portfolio_value * 0.15
            position_value = min(position_value, max_position)
            quantity       = round(position_value / price, 6)  # crypto uses decimals

            logger.info(f"{symbol} position: {quantity} units (${position_value:.2f})")
            return quantity

        except Exception as e:
            logger.error(f"Position sizing error for {symbol}: {e}")
            return 0

    # ─── TRAILING STOP ────────────────────────────────────────

    def calculate_trailing_stop(self, entry_price, current_price, atr_value):
        """Calculate trailing stop level"""
        highest_price  = max(entry_price, current_price)
        trailing_stop  = highest_price - (atr_value * 2)
        return trailing_stop

    # ─── MAIN SIGNAL ──────────────────────────────────────────

    def run(self, portfolio_value):
        """Main strategy execution"""
        logger.info("=== Crypto Trend Strategy Running ===")
        results = []

        # 35% of portfolio allocated to crypto
        allocation  = portfolio_value * 0.35
        per_coin    = allocation / len(self.CRYPTO)

        # Get current positions AND pending orders
        positions       = self.client.get_positions()
        current_symbols = [p.get("ticker") for p in positions]
        pending_symbols = self.client.get_open_order_symbols()
        active_symbols  = set(current_symbols) | pending_symbols
        logger.info(f"Crypto positions: {current_symbols}, pending: {pending_symbols}")

        bear_count = 0  # track how many coins are bearish

        for symbol in self.CRYPTO:
            try:
                # Get historical data
                data = self.client.get_historical_data(
                    symbol, period="1Y", bar="1d"
                )
                if not data:
                    logger.warning(f"{symbol}: no data available")
                    continue

                # Get trend signal
                signal = self.get_trend_signal(symbol, data)

                # Check liquidity
                if not self.check_liquidity(symbol, data):
                    continue

                # ── Execute based on signal
                if signal == "BULL":
                    if symbol not in active_symbols:
                        quantity = self.calculate_position_size(
                            symbol, per_coin, data
                        )
                        if quantity > 0:
                            self.client.place_order(symbol, "BUY", quantity)
                            results.append({
                                "symbol": symbol,
                                "action": "BUY",
                                "quantity": quantity,
                                "signal": signal
                            })

                elif signal == "BEAR":
                    bear_count += 1
                    # Close long if open
                    if symbol in current_symbols:
                        self.client.close_position(symbol)
                        results.append({
                            "symbol": symbol,
                            "action": "CLOSE",
                            "quantity": 0,
                            "signal": signal
                        })

                else:  # NEUTRAL
                    logger.info(f"{symbol}: no action — neutral signal")

            except Exception as e:
                logger.error(f"Crypto trend error for {symbol}: {e}")
                continue

        # ── If majority coins bearish → buy BITI
        if bear_count >= 2:
            logger.info("Majority crypto bearish → buying BITI inverse ETF")
            biti_data = self.client.get_historical_data(
                self.INVERSE, period="3M", bar="1d"
            )
            if biti_data:
                biti_allocation = allocation * 0.5
                biti_quantity   = self.calculate_position_size(
                    self.INVERSE, biti_allocation, biti_data
                )
                if biti_quantity > 0 and self.INVERSE not in active_symbols:
                    self.client.place_order(self.INVERSE, "BUY", biti_quantity)
                    results.append({
                        "symbol": self.INVERSE,
                        "action": "BUY",
                        "quantity": biti_quantity,
                        "signal": "BEAR_HEDGE"
                    })

        logger.info(f"Crypto trend complete — {len(results)} actions taken")
        return results

    # ─── HELPERS ──────────────────────────────────────────────

    def _to_dataframe(self, data):
        """Convert IBKR historical data to DataFrame"""
        if not data:
            return None
        try:
            df = pd.DataFrame(data)
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={
                "o": "open",  "h": "high",
                "l": "low",   "c": "close", "v": "volume"
            })
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
            df["high"]   = pd.to_numeric(df["high"],   errors="coerce")
            df["low"]    = pd.to_numeric(df["low"],    errors="coerce")
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
            df = df.dropna(subset=["close"])
            return df
        except Exception as e:
            logger.error(f"DataFrame conversion error: {e}")
            return None