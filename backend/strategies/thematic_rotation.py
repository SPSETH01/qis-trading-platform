import os
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from loguru import logger
from datetime import datetime, timedelta

class ThematicRotationStrategy:
    """
    Thematic ETF Momentum Rotation Strategy
    Ranks ETFs by 3-month momentum
    Holds top 3 — rebalances monthly
    Rotates to defensive/inverse on bear signals
    """

    def __init__(self, ibkr_client):
        self.client = ibkr_client

        # Thematic ETF universe
        self.UNIVERSE = [
            "BOTZ",  # AI + Robotics
            "BLOK",  # Blockchain
            "ARKG",  # Genomics
            "ITA",   # Defence
            "ROBO",  # Robotics
            "NUKZ",  # Nuclear Energy
            "CIBR",  # Cybersecurity
            "ICLN",  # Clean Energy
        ]

        # Defensive rotation
        self.DEFENSIVE = ["GLD", "TLT"]
        self.INVERSE   = ["SH", "SDS"]

        # Strategy settings
        self.TOP_N           = 3     # hold top 3 ETFs
        self.MOMENTUM_DAYS   = 63    # 3 month momentum (~63 trading days)
        self.MAX_DRAWDOWN    = 0.15  # exit if drawdown > 15%
        self.REBALANCE_DAYS  = 30    # rebalance every 30 days

        # Track last rebalance
        self.last_rebalance = None

    # ─── MOMENTUM SCORING ─────────────────────────────────────

    def calculate_momentum(self, symbol, data):
        """
        Calculate 3-month price momentum score
        Returns momentum as % return over period
        """
        df = self._to_dataframe(data)
        if df is None or df.empty or len(df) < self.MOMENTUM_DAYS:
            logger.warning(f"{symbol}: insufficient data for momentum")
            return None
        try:
            current_price = df["close"].iloc[-1]
            past_price    = df["close"].iloc[-self.MOMENTUM_DAYS]
            momentum      = (current_price - past_price) / past_price * 100
            logger.info(f"{symbol} momentum: {momentum:.2f}%")
            return momentum
        except Exception as e:
            logger.error(f"Momentum calculation error for {symbol}: {e}")
            return None

    def rank_etfs(self):
        """
        Score and rank all ETFs by momentum
        Returns sorted list — highest momentum first
        """
        logger.info("Ranking ETFs by momentum...")
        scores = []

        for symbol in self.UNIVERSE:
            data = self.client.get_historical_data(
                symbol, period="6M", bar="1d"
            )
            if not data:
                continue

            momentum = self.calculate_momentum(symbol, data)
            if momentum is None:
                continue

            # Only include positive momentum
            if momentum > 0:
                scores.append({
                    "symbol":   symbol,
                    "momentum": momentum,
                    "data":     data
                })

        # Sort by momentum descending
        scores.sort(key=lambda x: x["momentum"], reverse=True)

        logger.info("ETF Rankings:")
        for i, s in enumerate(scores):
            logger.info(f"  {i+1}. {s['symbol']}: {s['momentum']:.2f}%")

        return scores

    # ─── DRAWDOWN CHECK ───────────────────────────────────────

    def check_drawdown(self, symbol, data):
        """
        Check if ETF is in excessive drawdown
        Returns True if safe to hold, False if should exit
        """
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return True
        try:
            rolling_max = df["close"].expanding().max()
            drawdown    = (df["close"] - rolling_max) / rolling_max
            current_dd  = drawdown.iloc[-1]
            if current_dd < -self.MAX_DRAWDOWN:
                logger.warning(
                    f"{symbol}: drawdown {current_dd:.1%} exceeds limit"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"Drawdown check error for {symbol}: {e}")
            return True

    # ─── LIQUIDITY CHECK ──────────────────────────────────────

    def check_liquidity(self, symbol, data):
        """Verify volume is sufficient"""
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return False
        if "volume" not in df.columns:
            return True
        avg_volume    = df["volume"].iloc[-20:].mean()
        recent_volume = df["volume"].iloc[-1]
        liquid        = recent_volume >= avg_volume * 0.8
        if not liquid:
            logger.warning(f"{symbol}: liquidity check failed")
        return liquid

    # ─── POSITION SIZING ──────────────────────────────────────

    def calculate_position_size(self, symbol, portfolio_value, data):
        """Equal weight across top N ETFs with ATR stop"""
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return 0
        try:
            atr = AverageTrueRange(
                df["high"], df["low"], df["close"], window=14
            ).average_true_range()
            atr_value     = atr.iloc[-1]
            stop_distance = atr_value * 2
            risk_amount   = portfolio_value * float(
                os.getenv("MAX_RISK_PER_TRADE", 0.02)
            )
            price          = df["close"].iloc[-1]
            shares         = int(risk_amount / stop_distance)
            position_value = shares * price

            # Cap at equal weight allocation
            max_position   = portfolio_value / self.TOP_N
            position_value = min(position_value, max_position)
            shares         = int(position_value / price)

            logger.info(
                f"{symbol}: {shares} shares (${position_value:.2f})"
            )
            return shares
        except Exception as e:
            logger.error(f"Position sizing error for {symbol}: {e}")
            return 0

    # ─── REBALANCE CHECK ──────────────────────────────────────

    def should_rebalance(self):
        """Check if 30 days have passed since last rebalance"""
        if self.last_rebalance is None:
            return True
        days_since = (datetime.now() - self.last_rebalance).days
        should     = days_since >= self.REBALANCE_DAYS
        if should:
            logger.info(f"Rebalance due — {days_since} days since last")
        else:
            logger.info(
                f"No rebalance — {days_since}/{self.REBALANCE_DAYS} days"
            )
        return should

    # ─── BEAR DETECTION ───────────────────────────────────────

    def detect_broad_bear(self):
        """
        Check if broad market is bearish
        If so — rotate to defensive/inverse ETFs
        """
        spy_data = self.client.get_historical_data(
            "SPY", period="1Y", bar="1d"
        )
        if not spy_data:
            return False
        df = self._to_dataframe(spy_data)
        if df is None or df.empty:
            return False
        try:
            ema200   = EMAIndicator(df["close"], window=200).ema_indicator()
            rsi      = RSIIndicator(df["close"], window=14).rsi()
            bearish  = (
                df["close"].iloc[-1] < ema200.iloc[-1] and
                rsi.iloc[-1] < 45
            )
            if bearish:
                logger.warning("Broad market bear detected — rotating defensive")
            return bearish
        except Exception as e:
            logger.error(f"Bear detection error: {e}")
            return False

    # ─── MAIN SIGNAL ──────────────────────────────────────────

    def run(self, portfolio_value):
        """Main strategy execution"""
        logger.info("=== Thematic Rotation Strategy Running ===")
        results = []

        # Check if rebalance is due
        if not self.should_rebalance():
            logger.info("Thematic rotation: no rebalance due")
            return results

        # 25% of portfolio allocated to thematic
        allocation = portfolio_value * 0.25

        # Get current positions AND pending orders
        positions       = self.client.get_positions()
        current_symbols = [p.get("ticker") for p in positions]
        pending_symbols = self.client.get_open_order_symbols()
        active_symbols  = set(current_symbols) | pending_symbols
        logger.info(f"Thematic positions: {current_symbols}, pending: {pending_symbols}")

        # Check broad market regime
        broad_bear = self.detect_broad_bear()

        if broad_bear:
            # ── Defensive rotation
            logger.info("Rotating to defensive ETFs")
            for symbol in current_symbols:
                if symbol in self.UNIVERSE:
                    self.client.close_position(symbol)
                    results.append({
                        "symbol": symbol,
                        "action": "CLOSE",
                        "reason": "broad bear rotation"
                    })
            # Buy defensive
            per_etf = allocation / len(self.DEFENSIVE)
            for symbol in self.DEFENSIVE:
                if symbol not in active_symbols:
                    data   = self.client.get_historical_data(
                        symbol, period="3M", bar="1d"
                    )
                    shares = self.calculate_position_size(
                        symbol, per_etf, data
                    )
                    if shares > 0:
                        self.client.place_order(symbol, "BUY", shares)
                        results.append({
                            "symbol": symbol,
                            "action": "BUY",
                            "reason": "defensive rotation"
                        })
        else:
            # ── Momentum rotation
            ranked = self.rank_etfs()
            top_etfs = [r["symbol"] for r in ranked[:self.TOP_N]]
            logger.info(f"Top {self.TOP_N} ETFs: {top_etfs}")

            # Close positions not in top N
            for symbol in current_symbols:
                if symbol in self.UNIVERSE and symbol not in top_etfs:
                    logger.info(f"Closing {symbol} — dropped out of top {self.TOP_N}")
                    self.client.close_position(symbol)
                    results.append({
                        "symbol": symbol,
                        "action": "CLOSE",
                        "reason": "momentum rotation out"
                    })

            # Open top N positions
            per_etf = allocation / self.TOP_N
            for item in ranked[:self.TOP_N]:
                symbol = item["symbol"]
                data   = item["data"]

                # Drawdown check
                if not self.check_drawdown(symbol, data):
                    logger.warning(f"Skipping {symbol} — excessive drawdown")
                    continue

                # Liquidity check
                if not self.check_liquidity(symbol, data):
                    continue

                if symbol not in active_symbols:
                    shares = self.calculate_position_size(
                        symbol, per_etf, data
                    )
                    if shares > 0:
                        self.client.place_order(symbol, "BUY", shares)
                        results.append({
                            "symbol": symbol,
                            "action": "BUY",
                            "momentum": item["momentum"],
                            "reason": "momentum rotation in"
                        })

        # Update last rebalance timestamp
        self.last_rebalance = datetime.now()
        logger.info(
            f"Thematic rotation complete — {len(results)} actions"
        )
        return results

    # ─── HELPERS ──────────────────────────────────────────────

    def _to_dataframe(self, data):
        """Convert IBKR historical data to DataFrame"""
        if not data:
            return None
        try:
            df          = pd.DataFrame(data)
            df.columns  = [c.lower() for c in df.columns]
            df          = df.rename(columns={
                "o": "open",  "h": "high",
                "l": "low",   "c": "close", "v": "volume"
            })
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["high"]  = pd.to_numeric(df["high"],  errors="coerce")
            df["low"]   = pd.to_numeric(df["low"],   errors="coerce")
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(
                    df["volume"], errors="coerce"
                )
            df = df.dropna(subset=["close"])
            return df
        except Exception as e:
            logger.error(f"DataFrame conversion error: {e}")
            return None