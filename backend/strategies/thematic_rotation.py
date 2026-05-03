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
    Tactical QQQ Strategy — Tier 3
    ────────────────────────────────
    Backtested results (10yr):
      CAGR: 13.43% | Sharpe: 0.64 | MaxDD: -20.48%
      vs SPY: 15.3% CAGR — within 1.9% with better risk-adjusted returns

    Regime-based allocation:
      BULL     (SPY>EMA200, VIX<20):  100% QQQ
      CAUTION  (VIX 20-25):           80% QQQ + 20% SGOV
      BEAR     (SPY<EMA200, VIX>25):  40% SGOV + 30% GLD + 30% SH
      CRISIS   (VIX>35):              30% SGOV + 40% GLD + 30% SH
      RECOVERY (was BEAR, VIX<22):    100% QQQ aggressively

    Satellite overlay:
      Only added when ETF beats QQQ by >10% on 3M relative momentum
      AND passes quality filter (not >25% below 52w high)
      When triggered: 80% QQQ + 20% best satellite
    """

    def __init__(self, ibkr_client):
        self.client = ibkr_client

        # ── Core
        self.CORE = "QQQ"

        # ── Satellite universe (bank compliant — ETFs only)
        self.SATELLITE_UNIVERSE = [
            "SOXX",  # Semiconductors
            "CIBR",  # Cybersecurity
            "ITA",   # Defence
            "INDA",  # India
            "NUKZ",  # Nuclear Energy
            "BOTZ",  # AI + Robotics
            "XLK",   # Technology
            "XLV",   # Healthcare
            "XLE",   # Energy
        ]

        # ── Bear regime holdings
        self.BEAR_HEDGE  = "SH"     # 1x inverse SPY
        self.SAFE_HAVEN  = "SGOV"   # short-term treasury (rate-hike safe)
        self.GOLD        = "GLD"    # gold hedge

        # ── Regime thresholds
        self.VIX_BULL       = 20
        self.VIX_CAUTION    = 25
        self.VIX_CRISIS     = 35
        self.VIX_RECOVERY   = 22

        # ── Satellite filter
        self.SATELLITE_MIN_OUTPERFORMANCE = 10.0  # must beat QQQ by 10% on 3M
        self.QUALITY_FILTER_MAX_DD        = 0.25  # skip if >25% below 52w high

        # ── Rebalance
        self.REBALANCE_DAYS = 30
        self.MOMENTUM_DAYS  = 63   # 3M

        # ── State
        self.last_rebalance = None
        self.last_regime    = None
        self.last_vix       = None

    # ─── REGIME DETECTION ─────────────────────────────────────

    def detect_regime(self):
        """
        Multi-signal regime detection:
        1. VIX level
        2. SPY vs EMA200
        3. Early bear (SPY drops >5% in 10 days)
        4. Recovery (was BEAR/CRISIS, VIX now dropping)
        """
        try:
            # VIX
            vix_data = self.client.get_historical_data("VIX", period="3M", bar="1d")
            vix_df   = self._to_dataframe(vix_data)
            vix      = float(vix_df["close"].iloc[-1]) if vix_df is not None and not vix_df.empty else 20.0

            # SPY vs EMA200
            spy_data         = self.client.get_historical_data("SPY", period="1Y", bar="1d")
            spy_df           = self._to_dataframe(spy_data)
            spy_above_ema200 = True
            early_bear       = False

            if spy_df is not None and not spy_df.empty:
                close = spy_df["close"]
                if len(close) >= 50:
                    ema200           = EMAIndicator(close, window=min(200, len(close))).ema_indicator()
                    spy_above_ema200 = float(close.iloc[-1]) > float(ema200.iloc[-1])
                # Early bear: SPY drops >5% in 10 days
                if len(close) >= 10:
                    drop = (float(close.iloc[-1]) - float(close.iloc[-10])) / float(close.iloc[-10])
                    early_bear = drop < -0.05

            logger.info(f"VIX={vix:.1f} | SPY>EMA200={spy_above_ema200} | EarlyBear={early_bear}")

            # Regime logic
            if (self.last_regime in ["BEAR", "CRISIS"] and
                    vix < self.VIX_RECOVERY and spy_above_ema200 and not early_bear):
                regime = "RECOVERY"
            elif vix > self.VIX_CRISIS:
                regime = "CRISIS"
            elif not spy_above_ema200 or vix > self.VIX_CAUTION:
                regime = "BEAR"
            elif early_bear or vix > self.VIX_BULL:
                regime = "CAUTION"
            else:
                regime = "BULL"

            logger.info(f"Regime: {regime}")
            self.last_vix = vix
            return regime, vix

        except Exception as e:
            logger.error(f"Regime detection error: {e}")
            return "BULL", 20.0

    # ─── SATELLITE SELECTION ──────────────────────────────────

    def get_qqq_momentum(self):
        """Get QQQ 3M momentum as benchmark"""
        try:
            data = self.client.get_historical_data("QQQ", period="6M", bar="1d")
            df   = self._to_dataframe(data)
            if df is None or df.empty or len(df["close"]) < self.MOMENTUM_DAYS:
                return 0.0
            close = df["close"]
            return float((close.iloc[-1] - close.iloc[-self.MOMENTUM_DAYS]) /
                          close.iloc[-self.MOMENTUM_DAYS] * 100)
        except Exception:
            return 0.0

    def find_best_satellite(self, qqq_mom_3m):
        """
        Find best satellite ETF that beats QQQ by >10% on 3M basis
        AND passes quality filter (not >25% below 52w high)
        Returns (symbol, relative_momentum) or (None, 0)
        """
        best_sym = None
        best_rel = self.SATELLITE_MIN_OUTPERFORMANCE  # minimum threshold

        for symbol in self.SATELLITE_UNIVERSE:
            try:
                data = self.client.get_historical_data(symbol, period="9M", bar="1d")
                df   = self._to_dataframe(data)
                if df is None or df.empty:
                    continue

                close = df["close"]
                if len(close) < self.MOMENTUM_DAYS:
                    continue

                # Quality filter — skip if >25% below 52w high
                lookback = min(252, len(close))
                peak     = float(close.iloc[-lookback:].max())
                current  = float(close.iloc[-1])
                dd       = (current - peak) / peak
                if dd < -self.QUALITY_FILTER_MAX_DD:
                    logger.info(f"{symbol}: quality filter {dd:.1%} from 52w high")
                    continue

                # RSI filter — skip falling knives
                if len(close) >= 14:
                    rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
                    if rsi < 30:
                        logger.info(f"{symbol}: RSI {rsi:.1f} — falling knife")
                        continue

                # 3M momentum vs QQQ
                mom_3m   = float((close.iloc[-1] - close.iloc[-self.MOMENTUM_DAYS]) /
                                  close.iloc[-self.MOMENTUM_DAYS] * 100)
                rel_mom  = mom_3m - qqq_mom_3m

                logger.info(f"{symbol}: 3M={mom_3m:.1f}% rel={rel_mom:+.1f}% vs QQQ")

                if rel_mom > best_rel:
                    best_rel = rel_mom
                    best_sym = symbol

            except Exception as e:
                logger.error(f"Satellite error {symbol}: {e}")
                continue

        if best_sym:
            logger.info(f"Best satellite: {best_sym} (+{best_rel:.1f}% vs QQQ)")
        else:
            logger.info("No satellite beats QQQ — staying 100% QQQ")

        return best_sym, best_rel

    # ─── REBALANCE CHECK ──────────────────────────────────────

    def should_rebalance(self, new_regime):
        if self.last_rebalance is None:
            return True
        days_since = (datetime.now() - self.last_rebalance).days
        if days_since >= self.REBALANCE_DAYS:
            logger.info(f"Rebalance: {days_since} days elapsed")
            return True
        if new_regime != self.last_regime:
            logger.info(f"Rebalance: regime changed {self.last_regime} → {new_regime}")
            return True
        logger.info(f"No rebalance — {days_since}/{self.REBALANCE_DAYS} days")
        return False

    # ─── POSITION SIZING ──────────────────────────────────────

    def calculate_position_size(self, symbol, allocation, data):
        """ATR-based sizing capped at allocation"""
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return 0
        try:
            if len(df) < 14:
                price  = float(df["close"].iloc[-1])
                shares = int(allocation / price) if price > 0 else 0
                return shares
            atr           = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
            stop_distance = float(atr.iloc[-1]) * 2
            risk_amount   = allocation * float(os.getenv("MAX_RISK_PER_TRADE", 0.02))
            price         = float(df["close"].iloc[-1])
            if stop_distance <= 0 or price <= 0:
                return 0
            shares = int(min(int(risk_amount / stop_distance) * price, allocation) / price)
            logger.info(f"{symbol}: {shares} shares @ ${price:.2f} = ${shares*price:,.0f}")
            return shares
        except Exception as e:
            logger.error(f"Position sizing error {symbol}: {e}")
            return 0

    # ─── EXECUTE TRADES ───────────────────────────────────────

    def _buy(self, symbol, allocation, period, results, reason):
        """Helper to buy a position"""
        data   = self.client.get_historical_data(symbol, period=period, bar="1d")
        shares = self.calculate_position_size(symbol, allocation, data)
        if shares > 0:
            self.client.place_order(
                symbol, "BUY", shares,
                strategy="thematic_rotation",
                reason=reason
            )
            results.append({"symbol": symbol, "action": "BUY",
                             "shares": shares, "reason": reason})
            logger.info(f"BUY {shares} {symbol} — {reason}")

    def _close(self, symbol, results, reason):
        """Helper to close a position"""
        self.client.close_position(symbol)
        results.append({"symbol": symbol, "action": "CLOSE", "reason": reason})
        logger.info(f"CLOSE {symbol} — {reason}")

    # ─── MAIN EXECUTION ───────────────────────────────────────

    def run(self, portfolio_value):
        """
        Tactical QQQ execution:

        BULL/RECOVERY:
          Default: 100% QQQ
          If satellite beats QQQ by >10%: 80% QQQ + 20% satellite

        CAUTION:
          80% QQQ + 20% SGOV

        BEAR:
          40% SGOV + 30% GLD + 30% SH

        CRISIS:
          30% SGOV + 40% GLD + 30% SH
        """
        logger.info("=== Tactical QQQ Strategy (Tier 3) Running ===")
        results = []

        # ── Detect regime
        regime, vix = self.detect_regime()

        # ── Get current positions
        positions       = self.client.get_positions()
        current_symbols = [p.get("ticker") for p in positions]
        pending_symbols = self.client.get_open_order_symbols()
        active_symbols  = set(current_symbols) | pending_symbols

        logger.info(f"Regime={regime} VIX={vix:.1f} Positions={current_symbols}")

        # ── Check if rebalance needed
        if not self.should_rebalance(regime):
            return results

        # ── Define target portfolio by regime
        if regime in ["BULL", "RECOVERY"]:
            # Check for satellite opportunity
            qqq_mom    = self.get_qqq_momentum()
            best_sat, rel_mom = self.find_best_satellite(qqq_mom)

            if best_sat:
                targets = {
                    self.CORE: 0.80,
                    best_sat:  0.20,
                }
            else:
                targets = {self.CORE: 1.00}

        elif regime == "CAUTION":
            targets = {
                self.CORE:       0.80,
                self.SAFE_HAVEN: 0.20,
            }

        elif regime == "BEAR":
            targets = {
                self.SAFE_HAVEN: 0.40,
                self.GOLD:       0.30,
                self.BEAR_HEDGE: 0.30,
            }

        else:  # CRISIS
            targets = {
                self.SAFE_HAVEN: 0.30,
                self.GOLD:       0.40,
                self.BEAR_HEDGE: 0.30,
            }

        logger.info(f"Target portfolio: {targets}")

        # ── Close positions not in targets
        all_managed = (
            [self.CORE, self.BEAR_HEDGE, self.SAFE_HAVEN, self.GOLD] +
            self.SATELLITE_UNIVERSE
        )
        for symbol in current_symbols:
            if symbol in all_managed and symbol not in targets:
                self._close(symbol, results, f"exit — {regime} regime")

        # ── Open target positions
        for symbol, weight in targets.items():
            if symbol not in active_symbols:
                allocation = portfolio_value * weight
                period     = "6M" if symbol in [self.CORE] + self.SATELLITE_UNIVERSE else "3M"
                self._buy(
                    symbol, allocation, period, results,
                    reason=f"{regime} regime weight={weight:.0%} VIX={vix:.1f}"
                )

        # ── Update state
        self.last_regime    = regime
        self.last_rebalance = datetime.now()
        logger.info(f"Tactical QQQ complete — {len(results)} actions | {regime}")
        return results

    # ─── HELPERS ──────────────────────────────────────────────

    def _to_dataframe(self, data):
        if not data:
            return None
        try:
            df         = pd.DataFrame(data)
            df.columns = [c.lower() for c in df.columns]
            df         = df.rename(columns={
                "o": "open", "h": "high",
                "l": "low",  "c": "close", "v": "volume"
            })
            for col in ["close", "high", "low"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
            return df.dropna(subset=["close"])
        except Exception as e:
            logger.error(f"DataFrame conversion error: {e}")
            return None
