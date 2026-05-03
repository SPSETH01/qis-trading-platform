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
    Core/Satellite Strategy — Tier 2 Enhanced
    ──────────────────────────────────────────
    Structure:
      60% CORE    → QQQ (always held, never traded out in bull)
      40% SATELLITE → Top thematic ETFs (only if beating QQQ)

    Regimes:
      BULL     (SPY > EMA200, VIX < 20): Core QQQ + Satellite thematic
      CAUTION  (VIX 20-25):              Core QQQ + reduced satellite
      BEAR     (SPY < EMA200, VIX > 25): TLT/GLD/BND + SH hedge
      CRISIS   (VIX > 35):              Max defensive
      RECOVERY (was BEAR, VIX < 22):    Exit SH, re-enter QQQ aggressively

    Satellite filter:
      Only include thematic ETF if beating QQQ on 3M relative momentum
      Otherwise park satellite allocation in QQQ (full core mode)
    """

    def __init__(self, ibkr_client):
        self.client = ibkr_client

        self.CORE              = "QQQ"
        self.CORE_ALLOCATION   = 0.60

        self.SATELLITE_UNIVERSE = [
            "BOTZ", "ROBO", "CIBR", "SOXX", "QTUM",
            "NUKZ", "ICLN", "TAN",
            "ITA",  "XAR",
            "ARKG", "XBI", "IBB",
            "INDA", "EEM",
            "XLK",  "XLV", "XLE",
        ]
        self.SATELLITE_ALLOCATION = 0.40
        self.SATELLITE_TOP_N      = 3

        self.DEFENSIVE          = ["TLT", "GLD", "BND"]
        self.BEAR_HEDGE         = "SH"
        self.BEAR_DEFENSIVE_PCT = 0.70
        self.BEAR_HEDGE_PCT     = 0.20

        self.MOMENTUM_DAYS_3M   = 63
        self.MOMENTUM_DAYS_6M   = 126
        self.MOMENTUM_DAYS_1M   = 21
        self.MAX_DRAWDOWN       = 0.15
        self.REBALANCE_DAYS     = 30
        self.MAX_CORRELATION    = 0.75
        self.RSI_OVERSOLD       = 30
        self.RSI_OVERBOUGHT     = 80

        self.VIX_BULL           = 20
        self.VIX_BEAR           = 25
        self.VIX_CRISIS         = 35
        self.VIX_RECOVERY       = 22
        self.RELATIVE_MOM_MIN   = -5.0

        self.last_rebalance     = None
        self.last_top_etfs      = []
        self.last_regime        = None
        self.last_vix           = None

    # ─── REGIME DETECTION ─────────────────────────────────────

    def detect_regime(self):
        """
        Returns (regime, vix):
          BULL, CAUTION, BEAR, CRISIS, RECOVERY
        """
        try:
            vix_data = self.client.get_historical_data("VIX", period="3M", bar="1d")
            vix_df   = self._to_dataframe(vix_data)
            vix      = vix_df["close"].iloc[-1] if vix_df is not None and not vix_df.empty else 20.0

            spy_data         = self.client.get_historical_data("SPY", period="1Y", bar="1d")
            spy_df           = self._to_dataframe(spy_data)
            spy_above_ema200 = True

            if spy_df is not None and not spy_df.empty and len(spy_df) >= 50:
                ema200           = EMAIndicator(spy_df["close"], window=min(200, len(spy_df))).ema_indicator()
                spy_above_ema200 = spy_df["close"].iloc[-1] > ema200.iloc[-1]

            logger.info(f"VIX={vix:.1f} | SPY>EMA200={spy_above_ema200}")

            if (self.last_regime in ["BEAR", "CRISIS"] and
                    vix < self.VIX_RECOVERY and spy_above_ema200):
                regime = "RECOVERY"
            elif vix > self.VIX_CRISIS:
                regime = "CRISIS"
            elif not spy_above_ema200 or vix > self.VIX_BEAR:
                regime = "BEAR"
            elif vix > self.VIX_BULL:
                regime = "CAUTION"
            else:
                regime = "BULL"

            logger.info(f"Regime: {regime}")
            self.last_vix = vix
            return regime, vix

        except Exception as e:
            logger.error(f"Regime detection error: {e}")
            return "BULL", 20.0

    # ─── SCORING ──────────────────────────────────────────────

    def _momentum(self, close, days):
        if len(close) < days:
            return None
        past = close.iloc[-days]
        return (close.iloc[-1] - past) / past * 100 if past != 0 else None

    def get_qqq_benchmark_momentum(self):
        try:
            data = self.client.get_historical_data("QQQ", period="6M", bar="1d")
            df   = self._to_dataframe(data)
            if df is None or df.empty:
                return 0.0
            return self._momentum(df["close"], self.MOMENTUM_DAYS_3M) or 0.0
        except Exception:
            return 0.0

    def score_vs_benchmark(self, symbol, data, benchmark_mom_3m):
        """Multi-factor score with relative momentum vs QQQ"""
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return None
        try:
            close  = df["close"]
            mom_3m = self._momentum(close, self.MOMENTUM_DAYS_3M)
            mom_6m = self._momentum(close, self.MOMENTUM_DAYS_6M)
            mom_1m = self._momentum(close, self.MOMENTUM_DAYS_1M)

            if None in (mom_3m, mom_6m, mom_1m):
                return None

            relative_mom = mom_3m - benchmark_mom_3m
            if relative_mom < self.RELATIVE_MOM_MIN:
                logger.info(f"{symbol}: rel_mom={relative_mom:.1f}% below threshold — skip")
                return None

            returns    = close.pct_change().dropna()
            volatility = returns.std() * np.sqrt(252) * 100

            rsi_penalty = 0
            if len(close) >= 14:
                rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
                if rsi < self.RSI_OVERSOLD:
                    return None
                rsi_penalty = max(0, rsi - self.RSI_OVERBOUGHT) * 0.5

            score = (
                0.35 * mom_3m +
                0.25 * mom_6m +
                0.15 * mom_1m +
                0.15 * relative_mom -
                0.10 * volatility -
                rsi_penalty
            )

            logger.info(f"{symbol}: score={score:.2f} (3M={mom_3m:.1f}% rel={relative_mom:+.1f}% vol={volatility:.1f}%)")
            return score

        except Exception as e:
            logger.error(f"Score error for {symbol}: {e}")
            return None

    def rank_satellite_etfs(self, benchmark_mom_3m):
        logger.info(f"=== Ranking satellites (QQQ 3M={benchmark_mom_3m:.1f}%) ===")
        scores = []
        for symbol in self.SATELLITE_UNIVERSE:
            data  = self.client.get_historical_data(symbol, period="9M", bar="1d")
            if not data:
                continue
            score = self.score_vs_benchmark(symbol, data, benchmark_mom_3m)
            if score is not None and score > 0:
                scores.append({"symbol": symbol, "score": score, "data": data})
        scores.sort(key=lambda x: x["score"], reverse=True)
        logger.info(f"Top satellites: {[(s['symbol'], round(s['score'],2)) for s in scores[:5]]}")
        return scores

    def build_uncorrelated_portfolio(self, ranked_etfs):
        if not ranked_etfs:
            return []
        selected = [ranked_etfs[0]]
        for candidate in ranked_etfs[1:]:
            if len(selected) >= self.SATELLITE_TOP_N:
                break
            cdf = self._to_dataframe(candidate["data"])
            if cdf is None:
                continue
            c_ret    = cdf["close"].pct_change().dropna()
            too_corr = False
            for held in selected:
                hdf = self._to_dataframe(held["data"])
                if hdf is None:
                    continue
                h_ret = hdf["close"].pct_change().dropna()
                n     = min(len(c_ret), len(h_ret))
                if n < 20:
                    continue
                if c_ret.iloc[-n:].corr(h_ret.iloc[-n:]) > self.MAX_CORRELATION:
                    too_corr = True
                    break
            if not too_corr:
                selected.append(candidate)
        logger.info(f"Uncorrelated satellites: {[s['symbol'] for s in selected]}")
        return selected

    def should_rebalance(self, new_top_etfs=None, new_regime=None):
        if self.last_rebalance is None:
            return True
        days_since = (datetime.now() - self.last_rebalance).days
        if days_since >= self.REBALANCE_DAYS:
            return True
        if new_top_etfs and self.last_top_etfs:
            if len(set(new_top_etfs) & set(self.last_top_etfs)) < self.SATELLITE_TOP_N:
                logger.info("Rebalance: satellite rankings shifted")
                return True
        if new_regime and self.last_regime and new_regime != self.last_regime:
            logger.info(f"Rebalance: regime {self.last_regime} → {new_regime}")
            return True
        logger.info(f"No rebalance — {days_since}/{self.REBALANCE_DAYS} days")
        return False

    # ─── POSITION SIZING ──────────────────────────────────────

    def calculate_position_size(self, symbol, allocation, data):
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return 0
        try:
            atr           = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
            stop_distance = atr.iloc[-1] * 2
            risk_amount   = allocation * float(os.getenv("MAX_RISK_PER_TRADE", 0.02))
            price         = df["close"].iloc[-1]
            if stop_distance <= 0 or price <= 0:
                return 0
            shares = int(min(int(risk_amount / stop_distance) * price, allocation) / price)
            logger.info(f"{symbol}: {shares} shares @ ${price:.2f}")
            return shares
        except Exception as e:
            logger.error(f"Position sizing error for {symbol}: {e}")
            return 0

    def check_drawdown(self, symbol, data):
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return True
        try:
            dd = (df["close"] - df["close"].expanding().max()) / df["close"].expanding().max()
            if dd.iloc[-1] < -self.MAX_DRAWDOWN:
                logger.warning(f"{symbol}: drawdown {dd.iloc[-1]:.1%}")
                return False
            return True
        except Exception:
            return True

    def check_liquidity(self, symbol, data):
        df = self._to_dataframe(data)
        if df is None or df.empty or "volume" not in df.columns:
            return True
        return df["volume"].iloc[-1] >= df["volume"].iloc[-20:].mean() * 0.8

    # ─── MAIN EXECUTION ───────────────────────────────────────

    def run(self, portfolio_value):
        """Core/Satellite execution across all regimes"""
        logger.info("=== Core/Satellite Strategy (Tier 2) Running ===")
        results = []

        regime, vix     = self.detect_regime()
        positions       = self.client.get_positions()
        current_symbols = [p.get("ticker") for p in positions]
        pending_symbols = self.client.get_open_order_symbols()
        active_symbols  = set(current_symbols) | pending_symbols

        logger.info(f"Regime={regime} VIX={vix:.1f} Positions={current_symbols}")

        # ── BEAR / CRISIS ─────────────────────────────────────
        if regime in ["BEAR", "CRISIS"]:
            logger.info("BEAR — rotating defensive + SH hedge")

            for symbol in current_symbols:
                if symbol in [self.CORE] + self.SATELLITE_UNIVERSE:
                    self.client.close_position(symbol)
                    results.append({"symbol": symbol, "action": "CLOSE",
                                    "reason": f"bear exit (VIX={vix:.1f})"})

            defensive_alloc = portfolio_value * self.BEAR_DEFENSIVE_PCT
            per_def         = defensive_alloc / len(self.DEFENSIVE)
            for symbol in self.DEFENSIVE:
                if symbol not in active_symbols:
                    data   = self.client.get_historical_data(symbol, period="3M", bar="1d")
                    shares = self.calculate_position_size(symbol, per_def, data)
                    if shares > 0:
                        self.client.place_order(symbol, "BUY", shares,
                                                strategy="thematic_rotation",
                                                reason=f"defensive ({regime})")
                        results.append({"symbol": symbol, "action": "BUY",
                                        "reason": f"defensive ({regime})"})

            if self.BEAR_HEDGE not in active_symbols:
                sh_alloc  = portfolio_value * self.BEAR_HEDGE_PCT
                sh_data   = self.client.get_historical_data(self.BEAR_HEDGE, period="3M", bar="1d")
                sh_shares = self.calculate_position_size(self.BEAR_HEDGE, sh_alloc, sh_data)
                if sh_shares > 0:
                    self.client.place_order(self.BEAR_HEDGE, "BUY", sh_shares,
                                            strategy="thematic_rotation",
                                            reason=f"SH hedge ({regime}, VIX={vix:.1f})")
                    results.append({"symbol": self.BEAR_HEDGE, "action": "BUY",
                                    "reason": f"SH bear hedge ({regime})"})

        # ── RECOVERY ──────────────────────────────────────────
        elif regime == "RECOVERY":
            logger.info("RECOVERY — exit hedge, re-enter QQQ")

            for symbol in [self.BEAR_HEDGE] + self.DEFENSIVE:
                if symbol in current_symbols:
                    self.client.close_position(symbol)
                    results.append({"symbol": symbol, "action": "CLOSE",
                                    "reason": "recovery exit"})

            if self.CORE not in active_symbols:
                core_alloc  = portfolio_value * self.CORE_ALLOCATION
                core_data   = self.client.get_historical_data(self.CORE, period="6M", bar="1d")
                core_shares = self.calculate_position_size(self.CORE, core_alloc, core_data)
                if core_shares > 0:
                    self.client.place_order(self.CORE, "BUY", core_shares,
                                            strategy="thematic_rotation",
                                            reason=f"recovery re-entry (VIX={vix:.1f})")
                    results.append({"symbol": self.CORE, "action": "BUY",
                                    "reason": "recovery re-entry QQQ"})

        # ── BULL / CAUTION ────────────────────────────────────
        else:
            logger.info(f"{regime} — core QQQ + satellite rotation")

            # Core QQQ
            if self.CORE not in active_symbols:
                core_alloc  = portfolio_value * self.CORE_ALLOCATION
                core_data   = self.client.get_historical_data(self.CORE, period="6M", bar="1d")
                core_shares = self.calculate_position_size(self.CORE, core_alloc, core_data)
                if core_shares > 0:
                    self.client.place_order(self.CORE, "BUY", core_shares,
                                            strategy="thematic_rotation",
                                            reason=f"core QQQ ({regime})")
                    results.append({"symbol": self.CORE, "action": "BUY",
                                    "reason": f"core ({regime})"})
            else:
                logger.info(f"Core {self.CORE} already held")

            # Satellite rotation
            benchmark_mom = self.get_qqq_benchmark_momentum()
            ranked        = self.rank_satellite_etfs(benchmark_mom)
            selected      = self.build_uncorrelated_portfolio(ranked)
            new_top       = [s["symbol"] for s in selected]

            if not selected:
                logger.info("No satellite beats QQQ — satellite parked in QQQ")
            elif self.should_rebalance(new_top, regime):
                for symbol in current_symbols:
                    if symbol in self.SATELLITE_UNIVERSE and symbol not in new_top:
                        self.client.close_position(symbol)
                        results.append({"symbol": symbol, "action": "CLOSE",
                                        "reason": "satellite rotation out"})

                sat_alloc = portfolio_value * self.SATELLITE_ALLOCATION
                per_sat   = sat_alloc / self.SATELLITE_TOP_N
                for item in selected:
                    symbol = item["symbol"]
                    data   = item["data"]
                    if not self.check_drawdown(symbol, data):
                        continue
                    if not self.check_liquidity(symbol, data):
                        continue
                    if symbol not in active_symbols:
                        shares = self.calculate_position_size(symbol, per_sat, data)
                        if shares > 0:
                            self.client.place_order(
                                symbol, "BUY", shares,
                                strategy="thematic_rotation",
                                reason=f"satellite in score={item['score']:.2f} ({regime})"
                            )
                            results.append({"symbol": symbol, "action": "BUY",
                                            "score": item["score"],
                                            "reason": f"satellite ({regime})"})

            self.last_top_etfs = new_top

        self.last_regime    = regime
        self.last_rebalance = datetime.now()
        logger.info(f"Core/Satellite complete — {len(results)} actions | {regime}")
        return results

    # ─── HELPERS ──────────────────────────────────────────────

    def _to_dataframe(self, data):
        if not data:
            return None
        try:
            df         = pd.DataFrame(data)
            df.columns = [c.lower() for c in df.columns]
            df         = df.rename(columns={"o": "open", "h": "high",
                                             "l": "low",  "c": "close", "v": "volume"})
            for col in ["close", "high", "low"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
            return df.dropna(subset=["close"])
        except Exception as e:
            logger.error(f"DataFrame conversion error: {e}")
            return None