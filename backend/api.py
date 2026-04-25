import asyncio
import sys
import os
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from dotenv import load_dotenv

from ibkr_client import IBKRClient
from strategies.macro_regime import MacroRegimeStrategy
from strategies.crypto_trend import CryptoTrendStrategy
from strategies.thematic_rotation import ThematicRotationStrategy

load_dotenv()

# ─── APP SETUP ────────────────────────────────────────────────

app = FastAPI(title="QIS Trading Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── CLIENTS ──────────────────────────────────────────────────

client   = IBKRClient()
macro    = MacroRegimeStrategy(client)
crypto   = CryptoTrendStrategy(client)
thematic = ThematicRotationStrategy(client)

starting_capital = float(os.getenv("STARTING_CAPITAL", 500))
peak_value       = starting_capital

# ─── STARTUP ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Auto-connect to TWS on startup"""
    logger.info("QIS Platform starting — connecting to TWS...")
    try:
        result = client.connect()
        if result:
            logger.info("✅ TWS auto-connect successful on startup")
        else:
            logger.warning("⚠️  TWS auto-connect failed — use /api/auth/connect to retry")
    except Exception as e:
        logger.error(f"Startup connect error: {e}")

# ─── STATUS ───────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Engine + connection status"""
    connected = await run_in_threadpool(client.check_connection)
    return {
        "connected":  connected,
        "paper":      os.getenv("IBKR_PAPER", "true"),
        "account_id": os.getenv("IBKR_ACCOUNT_ID"),
        "timestamp":  datetime.now().isoformat(),
    }

# ─── AUTH ─────────────────────────────────────────────────────

@app.get("/api/auth/connect")
async def connect_tws():
    """Connect to TWS"""
    try:
        result = await run_in_threadpool(client.connect)
        return {"connected": result}
    except Exception as e:
        logger.error(f"Connect error: {e}")
        return {"connected": False, "error": str(e)}

@app.get("/api/auth/disconnect")
async def disconnect_tws():
    """Disconnect from TWS"""
    try:
        await run_in_threadpool(client.disconnect)
        return {"disconnected": True}
    except Exception as e:
        return {"disconnected": False, "error": str(e)}

# ─── PORTFOLIO ────────────────────────────────────────────────

@app.get("/api/portfolio")
async def get_portfolio():
    """Portfolio value + P&L"""
    global peak_value
    try:
        value = await run_in_threadpool(client.get_portfolio_value)
        if value > peak_value:
            peak_value = value
        pnl      = value - starting_capital
        pnl_pct  = (pnl / starting_capital) * 100
        drawdown = ((value - peak_value) / peak_value) * 100
        return {
            "portfolio_value":  value,
            "starting_capital": starting_capital,
            "pnl":              round(pnl, 2),
            "pnl_pct":          round(pnl_pct, 2),
            "peak_value":       peak_value,
            "drawdown_pct":     round(drawdown, 2),
            "timestamp":        datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        return {"error": str(e)}

# ─── POSITIONS ────────────────────────────────────────────────

@app.get("/api/positions")
async def get_positions():
    """Open positions"""
    try:
        positions = await run_in_threadpool(client.get_positions)
        result = []
        for pos in positions:
            result.append({
                "symbol":         pos.get("ticker", ""),
                "quantity":       pos.get("position", 0),
                "avg_price":      pos.get("avgCost", 0),
                "market_value":   pos.get("mktValue", 0),
                "unrealized_pnl": pos.get("unrealizedPnl", 0),
                "realized_pnl":   pos.get("realizedPnl", 0),
                "currency":       pos.get("currency", "USD"),
            })
        return {"positions": result, "count": len(result)}
    except Exception as e:
        logger.error(f"Positions error: {e}")
        return {"positions": [], "count": 0}

# ─── ACCOUNT ──────────────────────────────────────────────────

@app.get("/api/account")
async def get_account():
    """Account summary"""
    try:
        accounts = await run_in_threadpool(client.get_account)
        if accounts:
            return {
                "account_id":   accounts[0].get("id"),
                "account_type": accounts[0].get("type"),
                "currency":     accounts[0].get("currency"),
                "paper":        os.getenv("IBKR_PAPER", "true") == "true",
            }
        return {}
    except Exception as e:
        logger.error(f"Account error: {e}")
        return {}

# ─── SIGNALS ──────────────────────────────────────────────────

@app.get("/api/signals")
async def get_signals():
    """Current signal status for all strategies"""
    try:
        def _fetch_signals():
            signals = []

            # VIX signal
            vix = client.get_price("VIX")
            vix_threshold = float(os.getenv("VIX_BEAR_THRESHOLD", 20))
            signals.append({
                "id":        1,
                "name":      f"VIX > {vix_threshold} bear threshold",
                "strategy":  "Macro Regime",
                "status":    "triggered" if vix and vix > vix_threshold else "waiting",
                "value":     str(round(vix, 1)) if vix else "N/A",
                "threshold": str(vix_threshold),
            })

            # BTC EMA signal
            btc_data = client.get_historical_data("BTC", period="1Y", bar="1d")
            if btc_data:
                signal = crypto.get_trend_signal("BTC", btc_data)
                signals.append({
                    "id":        2,
                    "name":      "BTC EMA(50) vs EMA(200) cross",
                    "strategy":  "Crypto Trend",
                    "status":    "triggered" if signal == "BULL" else
                                 "waiting"   if signal == "NEUTRAL" else "inactive",
                    "value":     signal,
                    "threshold": "BULL",
                })

            # ETH EMA signal
            eth_data = client.get_historical_data("ETH", period="1Y", bar="1d")
            if eth_data:
                signal = crypto.get_trend_signal("ETH", eth_data)
                signals.append({
                    "id":        3,
                    "name":      "ETH EMA(50) vs EMA(200) cross",
                    "strategy":  "Crypto Trend",
                    "status":    "triggered" if signal == "BULL" else
                                 "waiting"   if signal == "NEUTRAL" else "inactive",
                    "value":     signal,
                    "threshold": "BULL",
                })

            # SPY regime signal
            spy_data = client.get_historical_data("SPY", period="1Y", bar="1d")
            if spy_data:
                regime = macro.get_regime(vix, spy_data)
                signals.append({
                    "id":        4,
                    "name":      "SPY Market Regime",
                    "strategy":  "Macro Regime",
                    "status":    "triggered",
                    "value":     regime,
                    "threshold": "STRONG_BULL",
                })

            return {"signals": signals, "count": len(signals)}

        return await run_in_threadpool(_fetch_signals)

    except Exception as e:
        logger.error(f"Signals error: {e}")
        return {"signals": [], "count": 0}

# ─── REGIME ───────────────────────────────────────────────────

@app.get("/api/regime")
async def get_regime():
    """Current market regime"""
    try:
        def _fetch_regime():
            vix      = client.get_price("VIX")
            spy_data = client.get_historical_data("SPY", period="1Y", bar="1d")
            if not spy_data:
                return {"regime": "UNKNOWN", "bear_score": 0, "vix": vix}
            regime     = macro.get_regime(vix, spy_data)
            bear_score = macro.get_bear_score(vix, spy_data)
            return {
                "regime":     regime,
                "bear_score": bear_score,
                "vix":        round(vix, 2) if vix else None,
                "timestamp":  datetime.now().isoformat(),
            }
        return await run_in_threadpool(_fetch_regime)
    except Exception as e:
        logger.error(f"Regime error: {e}")
        return {"regime": "UNKNOWN", "bear_score": 0}

# ─── TRADE EXECUTION ──────────────────────────────────────────

@app.post("/api/trade/run")
async def run_all_strategies():
    """
    Run all 3 strategies and execute paper trades.
    Portfolio allocation:
      - Macro Regime:       40%
      - Crypto Trend:       35%
      - Thematic Rotation:  25%
    """
    if not await run_in_threadpool(client.check_connection):
        raise HTTPException(status_code=503, detail="TWS not connected")

    try:
        def _run_all():
            portfolio_value = client.get_portfolio_value()
            logger.info(f"Running all strategies — portfolio: ${portfolio_value:,.2f}")
            results = {}
            try:
                results["macro_regime"] = macro.run(portfolio_value)
            except Exception as e:
                logger.error(f"Macro regime run error: {e}")
                results["macro_regime"] = {"error": str(e)}
            try:
                results["crypto_trend"] = crypto.run(portfolio_value)
            except Exception as e:
                logger.error(f"Crypto trend run error: {e}")
                results["crypto_trend"] = {"error": str(e)}
            try:
                results["thematic_rotation"] = thematic.run(portfolio_value)
            except Exception as e:
                logger.error(f"Thematic rotation run error: {e}")
                results["thematic_rotation"] = {"error": str(e)}
            return portfolio_value, results

        portfolio_value, results = await run_in_threadpool(_run_all)
        return {
            "status":          "executed",
            "portfolio_value": portfolio_value,
            "timestamp":       datetime.now().isoformat(),
            "results":         results,
        }
    except Exception as e:
        logger.error(f"Run all strategies error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade/run/{strategy}")
async def run_strategy(strategy: str):
    """
    Run a single strategy by name.
    Valid values: macro_regime | crypto_trend | thematic_rotation
    """
    if not await run_in_threadpool(client.check_connection):
        raise HTTPException(status_code=503, detail="TWS not connected")

    valid = ["macro_regime", "crypto_trend", "thematic_rotation"]
    if strategy not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{strategy}'. Valid: {valid}"
        )

    try:
        def _run():
            portfolio_value = client.get_portfolio_value()
            logger.info(f"Running {strategy} — portfolio: ${portfolio_value:,.2f}")
            strategy_map = {
                "macro_regime":      macro,
                "crypto_trend":      crypto,
                "thematic_rotation": thematic,
            }
            result = strategy_map[strategy].run(portfolio_value)
            return portfolio_value, result

        portfolio_value, result = await run_in_threadpool(_run)
        return {
            "status":          "executed",
            "strategy":        strategy,
            "portfolio_value": portfolio_value,
            "timestamp":       datetime.now().isoformat(),
            "result":          result,
        }
    except Exception as e:
        logger.error(f"Strategy run error ({strategy}): {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade/order")
async def place_manual_order(symbol: str, side: str, quantity: float, order_type: str = "MKT"):
    """
    Place a manual order directly.
    side: BUY | SELL
    order_type: MKT | LMT
    """
    if not await run_in_threadpool(client.check_connection):
        raise HTTPException(status_code=503, detail="TWS not connected")

    side = side.upper()
    if side not in ["BUY", "SELL"]:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")

    try:
        result = await run_in_threadpool(
            lambda: client.place_order(symbol, side, quantity, order_type)
        )
        if result:
            return {
                "status":       "submitted",
                "symbol":       symbol,
                "side":         side,
                "quantity":     quantity,
                "order_type":   order_type,
                "order_id":     result.get("orderId"),
                "order_status": result.get("status"),
                "timestamp":    datetime.now().isoformat(),
            }
        raise HTTPException(status_code=500, detail="Order placement failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Manual order error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade/close/{symbol}")
async def close_position(symbol: str):
    """Close a single position by symbol"""
    if not await run_in_threadpool(client.check_connection):
        raise HTTPException(status_code=503, detail="TWS not connected")
    try:
        result = await run_in_threadpool(
            lambda: client.close_position(symbol.upper())
        )
        return {
            "status":    "closed" if result else "no_position",
            "symbol":    symbol.upper(),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Close position error ({symbol}): {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade/killswitch")
async def kill_switch():
    """Emergency — close ALL open positions immediately"""
    if not await run_in_threadpool(client.check_connection):
        raise HTTPException(status_code=503, detail="TWS not connected")
    try:
        logger.warning("⚠️  KILL SWITCH triggered via API")
        await run_in_threadpool(client.close_all_positions)
        return {
            "status":    "all_positions_closed",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Kill switch error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── MAIN ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=False,   # ← change this
        workers=1       # ← add this
    )