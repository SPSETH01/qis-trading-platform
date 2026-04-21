import os
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from loguru import logger

class MacroRegimeStrategy:
    """
    Macro Regime Switcher
    Rotates between risk-on ETFs, defensive ETFs and inverse ETFs
    based on VIX level, EMA trend and RSI momentum
    """

    def __init__(self, ibkr_client):
        self.client = ibkr_client
        self.vix_bear = float(os.getenv("VIX_BEAR_THRESHOLD", 20))
        self.vix_extreme = float(os.getenv("VIX_EXTREME_THRESHOLD", 35))

        # ETF universes per regime
        self.STRONG_BULL  = ["QQQ", "BOTZ", "BLOK"]
        self.MILD_BULL    = ["SPY", "QQQ"]
        self.NEUTRAL      = ["SPY", "GLD"]
        self.MILD_BEAR    = ["GLD", "TLT", "SH"]
        self.STRONG_BEAR  = ["SDS", "GLD", "TLT"]
        self.EXTREME_FEAR = ["GLD"]

    # ─── REGIME DETECTION ─────────────────────────────────────

    def get_bear_score(self, vix, spy_data):
        """
        Score 0-7 bear signals
        Higher score = more bearish
        """
        score = 0
        df = self._to_dataframe(spy_data)
        if df is None or df.empty:
            return 0

        # Signal 1 — VIX level
        if vix and vix > self.vix_bear:
            score += 1
            logger.info(f"Bear signal: VIX {vix:.1f} > {self.vix_bear}")

        # Signal 2 — VIX extreme
        if vix and vix > self.vix_extreme:
            score += 1
            logger.info(f"Bear signal: VIX extreme {vix:.1f} > {self.vix_extreme}")

        # Signal 3 — Price below 200 EMA
        ema200 = EMAIndicator(df["close"], window=200).ema_indicator()
        if len(ema200.dropna()) > 0:
            if df["close"].iloc[-1] < ema200.iloc[-1]:
                score += 1
                logger.info("Bear signal: Price below 200 EMA")

        # Signal 4 — Death cross (50 EMA below 200 EMA)
        ema50  = EMAIndicator(df["close"], window=50).ema_indicator()
        if len(ema50.dropna()) > 0 and len(ema200.dropna()) > 0:
            if ema50.iloc[-1] < ema200.iloc[-1]:
                score += 1
                logger.info("Bear signal: Death cross detected")

        # Signal 5 — RSI below 40
        rsi = RSIIndicator(df["close"], window=14).rsi()
        if len(rsi.dropna()) > 0:
            if rsi.iloc[-1] < 40:
                score += 1
                logger.info(f"Bear signal: RSI {rsi.iloc[-1]:.1f} < 40")

        # Signal 6 — Volume declining
        if "volume" in df.columns and len(df) > 20:
            recent_vol = df["volume"].iloc[-5:].mean()
            avg_vol    = df["volume"].iloc[-20:].mean()
            if recent_vol < avg_vol * 0.8:
                score += 1
                logger.info("Bear signal: Volume declining")

        logger.info(f"Bear score: {score}/6")
        return score

    def get_regime(self, vix, spy_data):
        """Determine current market regime"""
        score = self.get_bear_score(vix, spy_data)

        if score <= 1:
            regime = "STRONG_BULL"
        elif score == 2:
            regime = "MILD_BULL"
        elif score == 3:
            regime = "NEUTRAL"
        elif score == 4:
            regime = "MILD_BEAR"
        elif score == 5:
            regime = "STRONG_BEAR"
        else:
            regime = "EXTREME_FEAR"

        logger.info(f"Market regime: {regime}")
        return regime

    def get_target_etfs(self, regime):
        """Get target ETF basket for regime"""
        mapping = {
            "STRONG_BULL":  self.STRONG_BULL,
            "MILD_BULL":    self.MILD_BULL,
            "NEUTRAL":      self.NEUTRAL,
            "MILD_BEAR":    self.MILD_BEAR,
            "STRONG_BEAR":  self.STRONG_BEAR,
            "EXTREME_FEAR": self.EXTREME_FEAR
        }
        return mapping.get(regime, self.NEUTRAL)

    # ─── LIQUIDITY CHECK ──────────────────────────────────────

    def check_liquidity(self, symbol, data):
        """Verify sufficient liquidity before trading"""
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return False
        if "volume" not in df.columns:
            return True  # assume liquid if no volume data
        avg_volume = df["volume"].iloc[-20:].mean()
        recent_volume = df["volume"].iloc[-1]
        liquid = recent_volume >= avg_volume * 0.8
        if not liquid:
            logger.warning(f"{symbol} liquidity check failed")
        return liquid

    # ─── POSITION SIZING ──────────────────────────────────────

    def calculate_position_size(self, symbol, portfolio_value, data):
        """ATR-based position sizing — risk 2% per trade"""
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return 0
        try:
            atr = AverageTrueRange(
                df["high"], df["low"], df["close"], window=14
            ).average_true_range()
            atr_value = atr.iloc[-1]
            stop_distance = atr_value * 2
            risk_amount = portfolio_value * float(
                os.getenv("MAX_RISK_PER_TRADE", 0.02)
            )
            price = df["close"].iloc[-1]
            shares = risk_amount / stop_distance
            position_value = shares * price
            # Cap at 30% of portfolio per position
            max_position = portfolio_value * 0.30
            position_value = min(position_value, max_position)
            shares = int(position_value / price)
            logger.info(f"{symbol} position size: {shares} shares (${position_value:.2f})")
            return shares
        except Exception as e:
            logger.error(f"Position sizing error for {symbol}: {e}")
            return 0

    # ─── MAIN SIGNAL ──────────────────────────────────────────

    def run(self, portfolio_value):
        """Main strategy execution"""
        logger.info("=== Macro Regime Strategy Running ===")
        try:
            # Get VIX
            vix = self.client.get_price("VIX")

            # Get SPY data for regime detection
            spy_data = self.client.get_historical_data("SPY", period="1Y", bar="1d")

            # Determine regime
            regime = self.get_regime(vix, spy_data)
            target_etfs = self.get_target_etfs(regime)

            # Get current positions
            positions = self.client.get_positions()
            current_symbols = [p.get("ticker") for p in positions]

            # Close positions not in target
            for symbol in current_symbols:
                if symbol not in target_etfs:
                    logger.info(f"Closing {symbol} — not in {regime} basket")
                    self.client.close_position(symbol)

            # Open positions in target basket
            allocation = portfolio_value * 0.40  # 40% of portfolio
            per_etf = allocation / len(target_etfs)

            for symbol in target_etfs:
                if symbol not in current_symbols:
                    data = self.client.get_historical_data(symbol, period="3M", bar="1d")
                    if not self.check_liquidity(symbol, data):
                        continue
                    shares = self.calculate_position_size(symbol, per_etf, data)
                    if shares > 0:
                        self.client.place_order(symbol, "BUY", shares)

            return {
                "strategy": "macro_regime",
                "regime": regime,
                "target_etfs": target_etfs,
                "vix": vix
            }

        except Exception as e:
            logger.error(f"Macro regime strategy error: {e}")
            return None

    # ─── HELPERS ──────────────────────────────────────────────

    def _to_dataframe(self, data):
        """Convert IBKR historical data to DataFrame"""
        if not data:
            return None
        try:
            df = pd.DataFrame(data)
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={"o": "open", "h": "high",
                                     "l": "low",  "c": "close", "v": "volume"})
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["high"]  = pd.to_numeric(df["high"],  errors="coerce")
            df["low"]   = pd.to_numeric(df["low"],   errors="coerce")
            df = df.dropna(subset=["close"])
            return df
        except Exception as e:
            logger.error(f"DataFrame conversion error: {e}")
            return None