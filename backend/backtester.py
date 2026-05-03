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
    return {
        "original":  original,
        "tier1":     tier1,
        "benchmark": {"name": "SPY (Buy & Hold)",
                      "total_return_pct": round(spy_ret,2),
                      "cagr_pct":         round(spy_cagr,2)},
    }
