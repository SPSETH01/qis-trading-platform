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
    Thematic ETF Momentum Rotation Strategy — Tier 1 Enhanced
    1. Expanded ETF universe — 20 ETFs across themes (bank compliant, ETFs only)
    2. Multi-factor scoring — momentum (3M, 6M, 1M) + volatility penalty + RSI filter
    3. VIX regime filter — reduces allocation in elevated/crisis vol regimes
    4. Correlation-aware portfolio construction
    5. Adaptive rebalance — triggers on regime change or rank shift
    """

    def __init__(self, ibkr_client):
        self.client = ibkr_client

        self.UNIVERSE = [
            "BOTZ", "ROBO", "CIBR", "SOXX", "QTUM",
            "NUKZ", "ICLN", "TAN", "MLPA",
            "ITA", "XAR",
            "ARKG", "XBI", "IBB",
            "INDA", "EEM",
            "QQQ", "XLK", "XLV", "XLE",
        ]

        self.DEFENSIVE       = ["GLD", "TLT", "BND"]
        self.TOP_N           = 3
        self.MOMENTUM_DAYS_3M = 63
        self.MOMENTUM_DAYS_6M = 126
        self.MOMENTUM_DAYS_1M = 21
        self.MAX_DRAWDOWN    = 0.15
        self.REBALANCE_DAYS  = 30
        self.MAX_CORRELATION = 0.75
        self.RSI_OVERSOLD    = 30
        self.RSI_OVERBOUGHT  = 80
        self.VIX_LOW         = 15
        self.VIX_NORMAL      = 20
        self.VIX_ELEVATED    = 30
        self.VIX_CRISIS      = 40
        self.last_rebalance  = None
        self.last_top_etfs   = []
        self.last_vix_regime = None

    def score_etf(self, symbol, data):
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return None
        try:
            close  = df["close"]
            mom_3m = self._momentum(close, self.MOMENTUM_DAYS_3M)
            mom_6m = self._momentum(close, self.MOMENTUM_DAYS_6M)
            mom_1m = self._momentum(close, self.MOMENTUM_DAYS_1M)
            if mom_3m is None or mom_6m is None or mom_1m is None:
                return None
            returns    = close.pct_change().dropna()
            volatility = returns.std() * np.sqrt(252) * 100
            if len(close) >= 14:
                rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
                if rsi < self.RSI_OVERSOLD:
                    logger.warning(f"{symbol}: RSI {rsi:.1f} falling knife, skipping")
                    return None
                rsi_penalty = max(0, rsi - self.RSI_OVERBOUGHT) * 0.5
            else:
                rsi_penalty = 0
            score = (0.40 * mom_3m + 0.30 * mom_6m + 0.20 * mom_1m
                     - 0.10 * volatility - rsi_penalty)
            logger.info(f"{symbol}: score={score:.2f} (3M={mom_3m:.1f}% 6M={mom_6m:.1f}% 1M={mom_1m:.1f}% vol={volatility:.1f}%)")
            return score
        except Exception as e:
            logger.error(f"Score error for {symbol}: {e}")
            return None

    def _momentum(self, close, days):
        if len(close) < days:
            return None
        past = close.iloc[-days]
        current = close.iloc[-1]
        if past == 0:
            return None
        return (current - past) / past * 100

    def rank_etfs(self):
        logger.info("=== Ranking ETFs (multi-factor) ===")
        scores = []
        for symbol in self.UNIVERSE:
            data = self.client.get_historical_data(symbol, period="9M", bar="1d")
            if not data:
                continue
            score = self.score_etf(symbol, data)
            if score is None or score <= 0:
                continue
            scores.append({"symbol": symbol, "score": score, "data": data})
        scores.sort(key=lambda x: x["score"], reverse=True)
        logger.info("ETF Rankings:")
        for i, s in enumerate(scores[:10]):
            logger.info(f"  {i+1}. {s['symbol']}: {s['score']:.2f}")
        return scores

    def get_vix_regime(self):
        try:
            vix_data = self.client.get_historical_data("VIX", period="1M", bar="1d")
            if not vix_data:
                return "NORMAL", 1.0
            df = self._to_dataframe(vix_data)
            if df is None or df.empty:
                return "NORMAL", 1.0
            vix = df["close"].iloc[-1]
            logger.info(f"Current VIX: {vix:.1f}")
            if vix < self.VIX_LOW:
                return "LOW_VOL", 1.0
            elif vix < self.VIX_NORMAL:
                return "NORMAL", 1.0
            elif vix < self.VIX_ELEVATED:
                return "ELEVATED", 0.60
            else:
                return "CRISIS", 0.30
        except Exception as e:
            logger.error(f"VIX regime error: {e}")
            return "NORMAL", 1.0

    def build_uncorrelated_portfolio(self, ranked_etfs):
        if not ranked_etfs:
            return []
        selected = [ranked_etfs[0]]
        for candidate in ranked_etfs[1:]:
            if len(selected) >= self.TOP_N:
                break
            candidate_df = self._to_dataframe(candidate["data"])
            if candidate_df is None:
                continue
            candidate_returns = candidate_df["close"].pct_change().dropna()
            too_correlated = False
            for held in selected:
                held_df = self._to_dataframe(held["data"])
                if held_df is None:
                    continue
                held_returns = held_df["close"].pct_change().dropna()
                min_len = min(len(candidate_returns), len(held_returns))
                if min_len < 20:
                    continue
                corr = candidate_returns.iloc[-min_len:].corr(held_returns.iloc[-min_len:])
                logger.info(f"Correlation {candidate['symbol']} vs {held['symbol']}: {corr:.2f}")
                if corr > self.MAX_CORRELATION:
                    logger.warning(f"Skipping {candidate['symbol']} — too correlated ({corr:.2f})")
                    too_correlated = True
                    break
            if not too_correlated:
                selected.append(candidate)
        logger.info(f"Uncorrelated portfolio: {[s['symbol'] for s in selected]}")
        return selected

    def should_rebalance(self, new_top_etfs=None, vix_regime=None):
        if self.last_rebalance is None:
            return True
        days_since = (datetime.now() - self.last_rebalance).days
        if days_since >= self.REBALANCE_DAYS:
            logger.info(f"Rebalance: {days_since} days elapsed")
            return True
        if new_top_etfs and self.last_top_etfs:
            overlap = len(set(new_top_etfs) & set(self.last_top_etfs))
            if overlap < self.TOP_N:
                logger.info(f"Rebalance: top ETFs changed")
                return True
        if vix_regime and self.last_vix_regime and vix_regime != self.last_vix_regime:
            logger.info(f"Rebalance: VIX regime changed ({self.last_vix_regime} -> {vix_regime})")
            return True
        logger.info(f"No rebalance — {days_since}/{self.REBALANCE_DAYS} days")
        return False

    def check_drawdown(self, symbol, data):
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return True
        try:
            rolling_max = df["close"].expanding().max()
            drawdown    = (df["close"] - rolling_max) / rolling_max
            current_dd  = drawdown.iloc[-1]
            if current_dd < -self.MAX_DRAWDOWN:
                logger.warning(f"{symbol}: drawdown {current_dd:.1%} exceeds limit")
                return False
            return True
        except Exception as e:
            logger.error(f"Drawdown check error for {symbol}: {e}")
            return True

    def check_liquidity(self, symbol, data):
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

    def calculate_position_size(self, symbol, allocation, data):
        df = self._to_dataframe(data)
        if df is None or df.empty:
            return 0
        try:
            atr           = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
            atr_value     = atr.iloc[-1]
            stop_distance = atr_value * 2
            risk_amount   = allocation * float(os.getenv("MAX_RISK_PER_TRADE", 0.02))
            price         = df["close"].iloc[-1]
            if stop_distance <= 0 or price <= 0:
                return 0
            shares         = int(risk_amount / stop_distance)
            position_value = min(shares * price, allocation)
            shares         = int(position_value / price)
            logger.info(f"{symbol}: {shares} shares @ ${price:.2f} = ${position_value:.2f}")
            return shares
        except Exception as e:
            logger.error(f"Position sizing error for {symbol}: {e}")
            return 0

    def detect_broad_bear(self):
        spy_data = self.client.get_historical_data("SPY", period="1Y", bar="1d")
        if not spy_data:
            return False
        df = self._to_dataframe(spy_data)
        if df is None or df.empty:
            return False
        try:
            ema200  = EMAIndicator(df["close"], window=200).ema_indicator()
            rsi     = RSIIndicator(df["close"], window=14).rsi()
            bearish = (df["close"].iloc[-1] < ema200.iloc[-1] and rsi.iloc[-1] < 45)
            if bearish:
                logger.warning("Broad market bear detected")
            return bearish
        except Exception as e:
            logger.error(f"Bear detection error: {e}")
            return False

    def run(self, portfolio_value):
        logger.info("=== Thematic Rotation Strategy (Tier 1) Running ===")
        results = []
        vix_regime, vix_multiplier = self.get_vix_regime()
        allocation = portfolio_value * 0.25 * vix_multiplier
        logger.info(f"Allocation: ${allocation:,.0f} (VIX regime: {vix_regime}, multiplier: {vix_multiplier:.0%})")
        positions       = self.client.get_positions()
        current_symbols = [p.get("ticker") for p in positions]
        pending_symbols = self.client.get_open_order_symbols()
        active_symbols  = set(current_symbols) | pending_symbols
        broad_bear      = self.detect_broad_bear()
        if broad_bear or vix_regime == "CRISIS":
            logger.info(f"Defensive rotation — bear={broad_bear}, regime={vix_regime}")
            for symbol in current_symbols:
                if symbol in self.UNIVERSE:
                    self.client.close_position(symbol)
                    results.append({"symbol": symbol, "action": "CLOSE", "reason": f"defensive ({vix_regime})"})
            per_etf = allocation / len(self.DEFENSIVE)
            for symbol in self.DEFENSIVE:
                if symbol not in active_symbols:
                    data   = self.client.get_historical_data(symbol, period="3M", bar="1d")
                    shares = self.calculate_position_size(symbol, per_etf, data)
                    if shares > 0:
                        self.client.place_order(symbol, "BUY", shares, strategy="thematic_rotation", reason=f"defensive ({vix_regime})")
                        results.append({"symbol": symbol, "action": "BUY", "reason": f"defensive ({vix_regime})"})
        else:
            ranked   = self.rank_etfs()
            if not ranked:
                logger.warning("No ETFs passed scoring")
                return results
            selected     = self.build_uncorrelated_portfolio(ranked)
            new_top_etfs = [s["symbol"] for s in selected]
            if not self.should_rebalance(new_top_etfs, vix_regime):
                logger.info("No rebalance due")
                return results
            for symbol in current_symbols:
                if symbol in self.UNIVERSE and symbol not in new_top_etfs:
                    self.client.close_position(symbol)
                    results.append({"symbol": symbol, "action": "CLOSE", "reason": "rotation out"})
            per_etf = allocation / self.TOP_N
            for item in selected:
                symbol = item["symbol"]
                data   = item["data"]
                if not self.check_drawdown(symbol, data):
                    continue
                if not self.check_liquidity(symbol, data):
                    continue
                if symbol not in active_symbols:
                    shares = self.calculate_position_size(symbol, per_etf, data)
                    if shares > 0:
                        self.client.place_order(symbol, "BUY", shares, strategy="thematic_rotation", reason=f"score={item['score']:.2f} ({vix_regime})")
                        results.append({"symbol": symbol, "action": "BUY", "score": item["score"], "reason": f"multi-factor ({vix_regime})"})
            self.last_top_etfs   = new_top_etfs
            self.last_vix_regime = vix_regime
        self.last_rebalance = datetime.now()
        logger.info(f"Thematic rotation complete — {len(results)} actions")
        return results

    def _to_dataframe(self, data):
        if not data:
            return None
        try:
            df         = pd.DataFrame(data)
            df.columns = [c.lower() for c in df.columns]
            df         = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
            for col in ["close", "high", "low"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
            df = df.dropna(subset=["close"])
            return df
        except Exception as e:
            logger.error(f"DataFrame conversion error: {e}")
            return None
