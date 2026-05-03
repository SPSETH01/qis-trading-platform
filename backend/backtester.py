"""
QIS Backtesting Engine
Uses yfinance for historical data — runs independently of IB Gateway.
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from loguru import logger


def fetch_data(symbols, start, end):
    logger.info(f"Fetching {len(symbols)} symbols: {start} to {end}")
    try:
        raw = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False, threads=True)
        if len(symbols) == 1:
            raw.columns = pd.MultiIndex.from_product([raw.columns, symbols])
        close = raw["Close"].dropna(how="all")
        return close
    except Exception as e:
        logger.error(f"Data fetch error: {e}")
        return pd.DataFrame()


def score_symbol(prices, date_idx):
    try:
        p = prices.iloc[:date_idx+1].dropna()
        if len(p) < 126:
            return None
        mom_3m = (p.iloc[-1]/p.iloc[-63]  - 1)*100 if len(p)>=63  else None
        mom_6m = (p.iloc[-1]/p.iloc[-126] - 1)*100 if len(p)>=126 else None
        mom_1m = (p.iloc[-1]/p.iloc[-21]  - 1)*100 if len(p)>=21  else None
        if None in (mom_3m, mom_6m, mom_1m):
            return None
        vol = p.pct_change().dropna().iloc[-63:].std() * (252**0.5) * 100
        return 0.40*mom_3m + 0.30*mom_6m + 0.20*mom_1m - 0.10*vol
    except Exception:
        return None


def get_vix_regime(vix_series, date_idx):
    try:
        v = vix_series.iloc[date_idx]
        if pd.isna(v):   return "NORMAL", 1.0
        if v < 15:       return "LOW_VOL", 1.0
        elif v < 20:     return "NORMAL", 1.0
        elif v < 30:     return "ELEVATED", 0.60
        else:            return "CRISIS", 0.30
    except Exception:
        return "NORMAL", 1.0


def build_uncorrelated(ranked, close, date_idx, top_n=3, max_corr=0.75):
    if not ranked:
        return []
    selected = [ranked[0]]
    for candidate in ranked[1:]:
        if len(selected) >= top_n:
            break
        c_ret = close[candidate].iloc[max(0,date_idx-63):date_idx+1].pct_change().dropna()
        too_corr = False
        for held in selected:
            h_ret = close[held].iloc[max(0,date_idx-63):date_idx+1].pct_change().dropna()
            n = min(len(c_ret), len(h_ret))
            if n < 20:
                continue
            if c_ret.iloc[-n:].corr(h_ret.iloc[-n:]) > max_corr:
                too_corr = True
                break
        if not too_corr:
            selected.append(candidate)
    return selected


def run_backtest(universe, start_date, end_date, starting_capital=1_000_000,
                 allocation_pct=0.25, top_n=3, rebalance_days=30,
                 max_correlation=0.75, use_vix_filter=True,
                 use_multi_factor=True, strategy_name="Tier1"):
    logger.info(f"=== Backtest: {strategy_name} | {start_date} to {end_date} ===")

    all_syms = list(set(universe + ["SPY", "GLD", "TLT", "BND"]))
    close = fetch_data(all_syms, start_date, end_date)
    if close.empty:
        return {"error": "Failed to fetch data"}

    vix_close = pd.Series(dtype=float)
    if use_vix_filter:
        try:
            vr = yf.download("^VIX", start=start_date, end=end_date, auto_adjust=True, progress=False)
            vix_close = vr["Close"].reindex(close.index).ffill()
        except Exception as e:
            logger.warning(f"VIX fetch failed: {e}")
            use_vix_filter = False

    cash = float(starting_capital)
    holdings = {}
    equity_curve = []
    trades = []
    last_reb = -rebalance_days
    dates = close.index.tolist()

    for i, date in enumerate(dates):
        pv = cash + sum(holdings.get(s,0) * float(close[s].iloc[i])
                        for s in holdings if s in close.columns and not pd.isna(close[s].iloc[i]))
        equity_curve.append({"date": date.strftime("%Y-%m-%d"), "value": round(pv, 2)})

        if i - last_reb < rebalance_days:
            continue

        vix_regime, vix_mult = get_vix_regime(vix_close, i) if use_vix_filter else ("NORMAL", 1.0)
        allocation = pv * allocation_pct * vix_mult

        # Score ETFs
        scored = []
        for sym in universe:
            if sym not in close.columns:
                continue
            price = close[sym].iloc[i]
            if pd.isna(price) or price <= 0:
                continue
            if use_multi_factor:
                s = score_symbol(close[sym], i)
            else:
                s = (price/close[sym].iloc[i-63]-1)*100 if i>=63 and close[sym].iloc[i-63]>0 else None
            if s is not None and s > 0:
                scored.append((sym, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = [s[0] for s in scored]

        # Build portfolio
        if use_multi_factor:
            selected = build_uncorrelated(ranked, close, i, top_n, max_correlation)
        else:
            selected = ranked[:top_n]

        # Bear detection
        if "SPY" in close.columns and i >= 200:
            spy_p = close["SPY"].iloc[i-200:i+1]
            ema200 = spy_p.ewm(span=200, adjust=False).mean().iloc[-1]
            if close["SPY"].iloc[i] < ema200:
                selected = ["GLD", "TLT"]

        if vix_regime == "CRISIS":
            selected = ["GLD", "TLT", "BND"]

        # Sell exits
        for sym in list(holdings.keys()):
            if sym not in selected and sym in close.columns:
                price = close[sym].iloc[i]
                if not pd.isna(price) and price > 0:
                    proceeds = holdings[sym] * float(price)
                    cash += proceeds
                    trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                                   "action": "SELL", "shares": holdings[sym],
                                   "price": round(float(price),2), "value": round(proceeds,2),
                                   "regime": vix_regime})
                    del holdings[sym]

        # Buy entries
        per_etf = allocation / len(selected) if selected else 0
        for sym in selected:
            if sym in holdings or sym not in close.columns:
                continue
            price = close[sym].iloc[i]
            if pd.isna(price) or price <= 0:
                continue
            shares = int(min(per_etf, cash) / price)
            if shares <= 0:
                continue
            cost = shares * float(price)
            cash -= cost
            holdings[sym] = shares
            trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                           "action": "BUY", "shares": shares,
                           "price": round(float(price),2), "value": round(cost,2),
                           "regime": vix_regime})

        last_reb = i

    final_value = cash + sum(holdings.get(s,0) * float(close[s].iloc[-1])
                              for s in holdings if s in close.columns)
    metrics = calculate_metrics(equity_curve, starting_capital, trades)
    metrics.update({"final_value": round(final_value,2), "strategy_name": strategy_name,
                    "start_date": start_date, "end_date": end_date})
    logger.info(f"Backtest done | CAGR={metrics['cagr_pct']}% Sharpe={metrics['sharpe']} MaxDD={metrics['max_drawdown_pct']}%")
    return {"metrics": metrics, "equity_curve": equity_curve, "trades": trades[-50:], "total_trades": len(trades)}


def calculate_metrics(equity_curve, starting_capital, trades):
    if not equity_curve:
        return {}
    values = [e["value"] for e in equity_curve]
    dates  = [datetime.strptime(e["date"], "%Y-%m-%d") for e in equity_curve]
    years  = (dates[-1]-dates[0]).days/365.25
    final  = values[-1]
    cagr   = ((final/starting_capital)**(1/years)-1)*100 if years>0 else 0
    rets   = pd.Series(values).pct_change().dropna()
    rf     = 0.04/252
    excess = rets - rf
    sharpe = float(excess.mean()/excess.std()*252**0.5) if excess.std()>0 else 0
    down   = rets[rets<rf]
    sortino= float(excess.mean()/down.std()*252**0.5) if len(down)>0 and down.std()>0 else 0
    peak   = pd.Series(values).expanding().max()
    max_dd = float(((pd.Series(values)-peak)/peak).min()*100)
    return {
        "cagr_pct":         round(cagr,2),
        "total_return_pct": round((final/starting_capital-1)*100,2),
        "sharpe":           round(sharpe,2),
        "sortino":          round(sortino,2),
        "max_drawdown_pct": round(max_dd,2),
        "total_trades":     len(trades),
        "years":            round(years,1),
        "starting_capital": starting_capital,
    }






def check_early_bear(close, spy_symbol, i, lookback=10, threshold=0.05):
    """Early bear detection — SPY drops >5% in 10 days"""
    if spy_symbol not in close.columns or i < lookback:
        return False
    try:
        recent = float(close[spy_symbol].iloc[i])
        past   = float(close[spy_symbol].iloc[i - lookback])
        drop   = (recent - past) / past
        return drop < -threshold
    except Exception:
        return False


def passes_quality_filter(close, symbol, i, max_drawdown_from_high=0.30):
    """Quality filter — exclude ETFs in >30% drawdown from 52-week high"""
    if symbol not in close.columns or i < 20:
        return True
    try:
        lookback = min(252, i)
        prices   = close[symbol].iloc[i - lookback:i + 1].dropna()
        if len(prices) < 20:
            return True
        peak     = float(prices.max())
        current  = float(prices.iloc[-1])
        drawdown = (current - peak) / peak
        passes   = drawdown > -max_drawdown_from_high
        if not passes:
            logger.info(f"{symbol}: quality filter — {drawdown:.1%} from 52w high")
        return passes
    except Exception:
        return True

def run_core_satellite(universe, start_date, end_date, starting_capital=1_000_000,
                       core="QQQ", core_pct=0.60, satellite_pct=0.40,
                       top_n=3, rebalance_days=30, max_correlation=0.75,
                       strategy_name="Tier2_CoreSatellite"):
    """
    Core/Satellite backtest:
    - 60% QQQ core (always held in bull)
    - 40% top satellite ETFs (only if beating QQQ)
    - BEAR: TLT/GLD + SH hedge
    - RECOVERY: re-enter QQQ
    """
    logger.info(f"=== Backtest: {strategy_name} | {start_date} to {end_date} ===")

    all_syms = list(set(universe + ["SPY", "QQQ", "GLD", "TLT", "BND", "SH", "SGOV"]))
    close = fetch_data(all_syms, start_date, end_date)
    if close.empty:
        return {"error": "Failed to fetch data"}

    vix_close = pd.Series(dtype=float)
    try:
        vr = yf.download("^VIX", start=start_date, end=end_date, auto_adjust=True, progress=False)
        vix_close = vr["Close"].reindex(close.index).ffill()
    except Exception as e:
        logger.warning(f"VIX fetch failed: {e}")

    cash = float(starting_capital)
    holdings = {}
    equity_curve = []
    trades = []
    last_reb = -rebalance_days
    last_regime = None
    dates = close.index.tolist()

    defensive = ["SGOV", "GLD", "BND"]  # SGOV=short-term treasury, safer than TLT in rate hike env
    bear_hedge = "SH"

    for i, date in enumerate(dates):
        pv = cash + sum(holdings.get(s, 0) * float(close[s].iloc[i])
                        for s in holdings if s in close.columns and not pd.isna(close[s].iloc[i]))
        equity_curve.append({"date": date.strftime("%Y-%m-%d"), "value": round(pv, 2)})

        if i - last_reb < rebalance_days:
            continue

        # VIX and regime
        vix = float(vix_close.iloc[i]) if len(vix_close) > i and not bool(pd.isna(float(vix_close.iloc[i]))) else 20.0

        spy_above_ema200 = True
        if "SPY" in close.columns and i >= 50:
            spy_p = close["SPY"].iloc[max(0,i-200):i+1]
            ema200 = spy_p.ewm(span=min(200,len(spy_p)), adjust=False).mean().iloc[-1]
            spy_above_ema200 = close["SPY"].iloc[i] > ema200

        # Regime detection — multi-signal with early bear detection
        early_bear = check_early_bear(close, "SPY", i, lookback=10, threshold=0.05)

        if last_regime in ["BEAR", "CRISIS"] and vix < 22 and spy_above_ema200 and not early_bear:
            regime = "RECOVERY"
        elif vix > 35:
            regime = "CRISIS"
        elif not spy_above_ema200 or vix > 25:
            regime = "BEAR"
        elif early_bear or vix > 22:
            regime = "CAUTION"
        else:
            regime = "BULL"

        # BEAR/CRISIS
        if regime in ["BEAR", "CRISIS"]:
            for sym in list(holdings.keys()):
                if sym in [core] + universe:
                    price = close[sym].iloc[i] if sym in close.columns else 0
                    if not pd.isna(price) and price > 0:
                        proceeds = holdings[sym] * float(price)
                        cash += proceeds
                        trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                                       "action": "SELL", "shares": holdings[sym],
                                       "price": round(float(price),2), "value": round(proceeds,2),
                                       "regime": regime})
                        del holdings[sym]

            # Bear allocation (rate-hike safe):
            #   SGOV 20% — short-term treasuries (safe in rate hike env)
            #   GLD  20% — gold hedge
            #   BND  10% — short duration bonds
            #   SH   30% — inverse SPY hedge (bigger position)
            #   cash 20% — dry powder for recovery

            bear_weights = {"SGOV": 0.20, "GLD": 0.20, "BND": 0.10}
            for sym, weight in bear_weights.items():
                if sym not in holdings and sym in close.columns:
                    price = close[sym].iloc[i]
                    if not pd.isna(price) and float(price) > 0:
                        alloc  = pv * weight
                        shares = int(min(alloc, cash) / float(price))
                        if shares > 0:
                            cash -= shares * float(price)
                            holdings[sym] = shares
                            trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                                           "action": "BUY", "shares": shares,
                                           "price": round(float(price),2),
                                           "value": round(shares*float(price),2), "regime": regime})

            # SH hedge (30% — larger position)
            if bear_hedge not in holdings and bear_hedge in close.columns:
                sh_price = close[bear_hedge].iloc[i]
                if not pd.isna(sh_price) and float(sh_price) > 0:
                    sh_alloc = pv * 0.30
                    shares   = int(min(sh_alloc, cash) / float(sh_price))
                    if shares > 0:
                        cash -= shares * float(sh_price)
                        holdings[bear_hedge] = shares
                        trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": bear_hedge,
                                       "action": "BUY", "shares": shares,
                                       "price": round(float(sh_price),2),
                                       "value": round(shares*float(sh_price),2), "regime": regime})

        # RECOVERY
        elif regime == "RECOVERY":
            for sym in list(holdings.keys()):
                if sym in defensive + [bear_hedge]:
                    price = close[sym].iloc[i] if sym in close.columns else 0
                    if not pd.isna(price) and price > 0:
                        proceeds = holdings[sym] * float(price)
                        cash += proceeds
                        trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                                       "action": "SELL", "shares": holdings[sym],
                                       "price": round(float(price),2), "value": round(proceeds,2),
                                       "regime": regime})
                        del holdings[sym]

            # Re-enter QQQ core
            if core not in holdings and core in close.columns:
                price = close[core].iloc[i]
                if not pd.isna(price) and price > 0:
                    alloc  = pv * core_pct
                    shares = int(min(alloc, cash) / float(price))
                    if shares > 0:
                        cash -= shares * float(price)
                        holdings[core] = shares
                        trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": core,
                                       "action": "BUY", "shares": shares,
                                       "price": round(float(price),2),
                                       "value": round(shares*float(price),2), "regime": regime})

        # BULL/CAUTION
        else:
            # Core QQQ (60%)
            if core not in holdings and core in close.columns:
                price = close[core].iloc[i]
                if not pd.isna(price) and price > 0:
                    alloc  = pv * core_pct
                    shares = int(min(alloc, cash) / float(price))
                    if shares > 0:
                        cash -= shares * float(price)
                        holdings[core] = shares
                        trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": core,
                                       "action": "BUY", "shares": shares,
                                       "price": round(float(price),2),
                                       "value": round(shares*float(price),2), "regime": regime})

            # Satellite (40%) — only if beating QQQ
            qqq_mom = 0.0
            if core in close.columns and i >= 63:
                qqq_past = close[core].iloc[i-63]
                qqq_now  = close[core].iloc[i]
                if qqq_past > 0:
                    qqq_mom = (qqq_now - qqq_past) / qqq_past * 100

            scored = []
            for sym in universe:
                if sym not in close.columns or sym == core:
                    continue
                price = close[sym].iloc[i]
                if pd.isna(price) or price <= 0:
                    continue
                if not passes_quality_filter(close, sym, i, max_drawdown_from_high=0.30):
                    continue
                s = score_symbol(close[sym], i)
                if s is None:
                    continue
                if i >= 63 and close[sym].iloc[i-63] > 0:
                    rel_mom = (price - close[sym].iloc[i-63]) / close[sym].iloc[i-63] * 100 - qqq_mom
                    if rel_mom < -5.0:
                        continue
                if s > 0:
                    scored.append((sym, s))

            scored.sort(key=lambda x: x[1], reverse=True)
            ranked   = [s[0] for s in scored]
            selected = build_uncorrelated(ranked, close, i, top_n, max_correlation)

            # Close satellite not in selected
            for sym in list(holdings.keys()):
                if sym in universe and sym not in selected:
                    price = close[sym].iloc[i] if sym in close.columns else 0
                    if not pd.isna(price) and price > 0:
                        proceeds = holdings[sym] * float(price)
                        cash += proceeds
                        trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                                       "action": "SELL", "shares": holdings[sym],
                                       "price": round(float(price),2), "value": round(proceeds,2),
                                       "regime": regime})
                        del holdings[sym]

            # Buy satellite
            sat_alloc = pv * satellite_pct
            per_sat   = sat_alloc / top_n if selected else 0
            for sym in selected:
                if sym in holdings or sym not in close.columns:
                    continue
                price = close[sym].iloc[i]
                if pd.isna(price) or price <= 0:
                    continue
                shares = int(min(per_sat, cash) / float(price))
                if shares > 0:
                    cash -= shares * float(price)
                    holdings[sym] = shares
                    trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                                   "action": "BUY", "shares": shares,
                                   "price": round(float(price),2),
                                   "value": round(shares*float(price),2), "regime": regime})

        last_regime = regime
        last_reb    = i

    final_value = cash + sum(holdings.get(s, 0) * float(close[s].iloc[-1])
                              for s in holdings if s in close.columns)
    metrics = calculate_metrics(equity_curve, starting_capital, trades)
    metrics.update({"final_value": round(final_value, 2), "strategy_name": strategy_name,
                    "start_date": start_date, "end_date": end_date})
    logger.info(f"Done | CAGR={metrics['cagr_pct']}% Sharpe={metrics['sharpe']} MaxDD={metrics['max_drawdown_pct']}%")
    return {"metrics": metrics, "equity_curve": equity_curve, "trades": trades[-50:], "total_trades": len(trades)}



def run_tactical_qqq(start_date, end_date, starting_capital=1_000_000,
                     strategy_name="Tactical_QQQ"):
    """
    Tactical QQQ Strategy:
    - BULL     (SPY>EMA200, VIX<20):  100% QQQ
    - CAUTION  (VIX 20-25):           80% QQQ + 20% SGOV
    - BEAR     (SPY<EMA200, VIX>25):  40% SGOV + 30% GLD + 30% SH
    - CRISIS   (VIX>35):              30% SGOV + 40% GLD + 30% SH
    - RECOVERY (was BEAR, VIX<22):    100% QQQ aggressively

    Thematic satellite: only added when ETF beats QQQ by >10% over 3M
    """
    logger.info(f"=== Tactical QQQ | {start_date} to {end_date} ===")

    symbols   = ["QQQ", "SPY", "SH", "SGOV", "GLD", "TLT", "BND"]
    satellite = ["SOXX", "ITA", "CIBR", "INDA", "XLK", "XLV", "XLE", "BOTZ", "NUKZ"]
    all_syms  = list(set(symbols + satellite))

    close = fetch_data(all_syms, start_date, end_date)
    if close.empty:
        return {"error": "Failed to fetch data"}

    vix_close = pd.Series(dtype=float)
    try:
        vr = yf.download("^VIX", start=start_date, end=end_date, auto_adjust=True, progress=False)
        vix_close = vr["Close"].reindex(close.index).ffill()
    except Exception as e:
        logger.warning(f"VIX fetch failed: {e}")

    cash        = float(starting_capital)
    holdings    = {}
    equity_curve = []
    trades      = []
    last_reb    = -30
    last_regime = None
    dates       = close.index.tolist()

    for i, date in enumerate(dates):
        pv = cash + sum(
            holdings.get(s, 0) * float(close[s].iloc[i])
            for s in holdings
            if s in close.columns and not pd.isna(close[s].iloc[i])
        )
        equity_curve.append({"date": date.strftime("%Y-%m-%d"), "value": round(pv, 2)})

        if i - last_reb < 30:
            continue

        # VIX
        vix = 20.0
        if len(vix_close) > i:
            try:
                v = float(vix_close.iloc[i])
                if not pd.isna(v):
                    vix = v
            except Exception:
                pass

        # SPY vs EMA200
        spy_above = True
        if "SPY" in close.columns and i >= 50:
            spy_p     = close["SPY"].iloc[max(0, i-200):i+1]
            ema200    = spy_p.ewm(span=min(200, len(spy_p)), adjust=False).mean().iloc[-1]
            spy_above = float(close["SPY"].iloc[i]) > float(ema200)

        # Early bear
        early_bear = check_early_bear(close, "SPY", i, lookback=10, threshold=0.05)

        # Regime
        if last_regime in ["BEAR", "CRISIS"] and vix < 22 and spy_above and not early_bear:
            regime = "RECOVERY"
        elif vix > 35:
            regime = "CRISIS"
        elif not spy_above or vix > 25:
            regime = "BEAR"
        elif early_bear or vix > 20:
            regime = "CAUTION"
        else:
            regime = "BULL"

        logger.info(f"{date.strftime('%Y-%m-%d')} regime={regime} VIX={vix:.1f}")

        # ── Target allocation by regime
        if regime == "BULL":
            targets = {"QQQ": 1.00}
        elif regime == "CAUTION":
            targets = {"QQQ": 0.80, "SGOV": 0.20}
        elif regime == "BEAR":
            targets = {"SGOV": 0.40, "GLD": 0.30, "SH": 0.30}
        elif regime == "CRISIS":
            targets = {"SGOV": 0.30, "GLD": 0.40, "SH": 0.30}
        else:  # RECOVERY
            targets = {"QQQ": 1.00}

        # ── Add thematic satellite if beating QQQ by >10% on 3M basis
        if regime in ["BULL", "RECOVERY"] and "QQQ" in close.columns and i >= 63:
            qqq_mom = (float(close["QQQ"].iloc[i]) / float(close["QQQ"].iloc[i-63]) - 1) * 100
            best_sat = None
            best_rel = 10.0  # must beat QQQ by at least 10%

            for sym in satellite:
                if sym not in close.columns or sym == "QQQ":
                    continue
                if not passes_quality_filter(close, sym, i, 0.25):
                    continue
                price = float(close[sym].iloc[i])
                if price <= 0 or i < 63:
                    continue
                past = float(close[sym].iloc[i-63])
                if past <= 0:
                    continue
                rel_mom = (price / past - 1) * 100 - qqq_mom
                if rel_mom > best_rel:
                    best_rel = rel_mom
                    best_sat = sym

            if best_sat:
                # Allocate 20% to best satellite, reduce QQQ to 80%
                targets = {"QQQ": 0.80, best_sat: 0.20}
                logger.info(f"Satellite: {best_sat} rel_mom={best_rel:.1f}% vs QQQ")

        # ── Execute rebalance
        # Close positions not in targets
        for sym in list(holdings.keys()):
            if sym not in targets:
                price = float(close[sym].iloc[i]) if sym in close.columns else 0
                if price > 0 and not pd.isna(price):
                    proceeds = holdings[sym] * price
                    cash    += proceeds
                    trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                                   "action": "SELL", "shares": holdings[sym],
                                   "price": round(price, 2), "value": round(proceeds, 2),
                                   "regime": regime})
                    del holdings[sym]

        # Buy target positions
        for sym, weight in targets.items():
            if sym in holdings or sym not in close.columns:
                continue
            price = float(close[sym].iloc[i])
            if pd.isna(price) or price <= 0:
                continue
            alloc  = pv * weight
            shares = int(min(alloc, cash) / price)
            if shares <= 0:
                continue
            cost    = shares * price
            cash   -= cost
            holdings[sym] = shares
            trades.append({"date": date.strftime("%Y-%m-%d"), "symbol": sym,
                           "action": "BUY", "shares": shares,
                           "price": round(price, 2), "value": round(cost, 2),
                           "regime": regime})

        last_regime = regime
        last_reb    = i

    final_value = cash + sum(
        holdings.get(s, 0) * float(close[s].iloc[-1])
        for s in holdings if s in close.columns
    )
    metrics = calculate_metrics(equity_curve, starting_capital, trades)
    metrics.update({"final_value": round(final_value, 2), "strategy_name": strategy_name,
                    "start_date": start_date, "end_date": end_date})
    logger.info(f"Done | CAGR={metrics['cagr_pct']}% Sharpe={metrics['sharpe']} MaxDD={metrics['max_drawdown_pct']}%")
    return {"metrics": metrics, "equity_curve": equity_curve,
            "trades": trades[-50:], "total_trades": len(trades)}

def run_comparison(universe, start_date, end_date, starting_capital=1_000_000):
    logger.info("Running strategy comparison...")
    original = run_backtest(universe, start_date, end_date, starting_capital,
                            use_vix_filter=False, use_multi_factor=False,
                            strategy_name="Original (3M Momentum)")
    tier1    = run_backtest(universe, start_date, end_date, starting_capital,
                            use_vix_filter=True,  use_multi_factor=True,
                            strategy_name="Tier 1 (Multi-Factor + VIX)")
    spy_ret = spy_cagr = 0.0
    try:
        spy = yf.download("SPY", start=start_date, end=end_date, auto_adjust=True, progress=False)
        if not spy.empty:
            s0 = float(spy["Close"].dropna().iloc[0])
            s1 = float(spy["Close"].dropna().iloc[-1])
            yr = (spy.index[-1]-spy.index[0]).days/365.25
            spy_ret  = (s1/s0-1)*100
            spy_cagr = ((s1/s0)**(1/yr)-1)*100 if yr>0 else 0
    except Exception as e:
        logger.warning(f"SPY benchmark error: {e}")
    tier2 = run_core_satellite(
        universe=universe, start_date=start_date, end_date=end_date,
        starting_capital=starting_capital, strategy_name="Tier 2 (Core/Satellite + SH)"
    )

    return {
        "original":  original,
        "tier1":     tier1,
        "tier2":     tier2,
        "benchmark": {"name": "SPY (Buy & Hold)",
                      "total_return_pct": round(spy_ret,2),
                      "cagr_pct":         round(spy_cagr,2)},
    }
