import asyncio
import sys
import os
from datetime import datetime
import pytz
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

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

# ─── SCHEDULER + SIGNAL MONITOR ──────────────────────────────

ET = pytz.timezone("America/New_York")

scheduler = BackgroundScheduler(timezone=ET)

scheduler_state = {
    "running":        False,
    "paused":         False,
    "last_run":       {},    # strategy -> last run timestamp
    "last_error":     {},    # strategy -> last error message
    "error_count":    0,
    "signal_history": {},    # signal_id -> last known value
    "last_signal_check": None,
}

STRATEGY_MAP = lambda: {
    "macro_regime":      macro,
    "crypto_trend":      crypto,
    "thematic_rotation": thematic,
}

# ── Signal trigger rules ──────────────────────────────────────
# Each rule defines when a signal change should trigger a strategy run
SIGNAL_TRIGGERS = [
    {
        "signal_id":  "vix_bear",
        "strategy":   "macro_regime",
        "description": "VIX crosses bear threshold",
    },
    {
        "signal_id":  "spy_regime",
        "strategy":   "macro_regime",
        "description": "SPY regime changes",
    },
    {
        "signal_id":  "btc_trend",
        "strategy":   "crypto_trend",
        "description": "BTC trend signal changes",
    },
    {
        "signal_id":  "eth_trend",
        "strategy":   "crypto_trend",
        "description": "ETH trend signal changes",
    },
]

def _execute_strategy(strategy_name: str, trigger: str = "scheduled"):
    """
    Core strategy execution — used by both signal monitor and scheduled runs.
    On error — pauses all jobs and logs failure.
    """
    if scheduler_state["paused"]:
        logger.warning(f"Scheduler paused — skipping {strategy_name}")
        return None

    logger.info(f"🚀 Running {strategy_name} [trigger: {trigger}]")

    try:
        if not client.check_connection():
            raise RuntimeError("TWS not connected")

        portfolio_value = client.get_portfolio_value()
        result = STRATEGY_MAP()[strategy_name].run(portfolio_value)

        scheduler_state["last_run"][strategy_name] = datetime.now(ET).isoformat()
        scheduler_state["last_error"].pop(strategy_name, None)
        logger.info(f"✅ {strategy_name} complete [{trigger}] — result: {result}")
        return result

    except Exception as e:
        error_msg = str(e)
        scheduler_state["last_error"][strategy_name] = error_msg
        scheduler_state["error_count"] += 1
        logger.error(f"❌ {strategy_name} FAILED [{trigger}]: {error_msg}")
        logger.error("🛑 Pausing all scheduled runs — use /api/scheduler/resume to restart")
        scheduler_state["paused"] = True
        for job in scheduler.get_jobs():
            job.pause()
        return None

def _fetch_current_signals():
    """
    Fetch current signal values from TWS.
    Returns dict of signal_id -> current value.
    Lightweight — only fetches what's needed for change detection.
    """
    signals = {}
    try:
        # VIX bear threshold
        vix = client.get_price("VIX")
        vix_threshold = float(os.getenv("VIX_BEAR_THRESHOLD", 20))
        signals["vix_bear"] = "triggered" if vix and vix > vix_threshold else "waiting"

        # SPY regime
        spy_data = client.get_historical_data("SPY", period="1Y", bar="1d")
        if spy_data:
            regime = macro.get_regime(vix, spy_data)
            signals["spy_regime"] = regime

        # BTC trend
        btc_data = client.get_historical_data("BTC", period="1Y", bar="1d")
        if btc_data:
            signals["btc_trend"] = crypto.get_trend_signal("BTC", btc_data)

        # ETH trend
        eth_data = client.get_historical_data("ETH", period="1Y", bar="1d")
        if eth_data:
            signals["eth_trend"] = crypto.get_trend_signal("ETH", eth_data)

    except Exception as e:
        logger.error(f"Signal fetch error: {e}")

    return signals

def _signal_monitor():
    """
    Runs every 15 minutes.
    Fetches current signals, compares to previous state.
    Triggers relevant strategy if any signal changes.
    """
    if scheduler_state["paused"]:
        return

    logger.info("🔍 Signal monitor checking...")
    scheduler_state["last_signal_check"] = datetime.now(ET).isoformat()

    try:
        current_signals = _fetch_current_signals()
        previous_signals = scheduler_state["signal_history"]
        triggered_strategies = set()

        for trigger_rule in SIGNAL_TRIGGERS:
            signal_id  = trigger_rule["signal_id"]
            strategy   = trigger_rule["strategy"]
            desc       = trigger_rule["description"]

            current_val  = current_signals.get(signal_id)
            previous_val = previous_signals.get(signal_id)

            if current_val is None:
                continue

            # Signal changed state
            if current_val != previous_val:
                logger.info(
                    f"🔔 Signal change detected: {signal_id} "
                    f"{previous_val} → {current_val} ({desc})"
                )
                triggered_strategies.add(strategy)

        # Update signal history
        scheduler_state["signal_history"].update(current_signals)

        # Execute triggered strategies (deduplicated)
        for strategy_name in triggered_strategies:
            logger.info(f"⚡ Signal-driven trigger: {strategy_name}")
            _execute_strategy(strategy_name, trigger="signal_change")

        if not triggered_strategies:
            logger.info(f"✅ Signal monitor: no changes detected — signals: {current_signals}")

    except Exception as e:
        logger.error(f"Signal monitor error: {e}")

def _setup_scheduler():
    """Configure signal monitor + fallback scheduled runs"""

    # ── PRIMARY: Signal monitor every 15 minutes ──────────────
    scheduler.add_job(
        func=_signal_monitor,
        trigger=IntervalTrigger(minutes=15, timezone=ET),
        id="signal_monitor",
        name="Signal Monitor — every 15min",
        replace_existing=True,
    )

    # ── FALLBACK: Scheduled safety nets ───────────────────────

    # Macro Regime — weekdays 9:35 AM ET
    scheduler.add_job(
        func=_execute_strategy,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=ET),
        args=["macro_regime", "scheduled_fallback"],
        id="macro_regime",
        name="Macro Regime — 9:35 AM ET fallback",
        replace_existing=True,
    )

    # Crypto Trend — every 4 hours 24/7
    scheduler.add_job(
        func=_execute_strategy,
        trigger=IntervalTrigger(hours=4, timezone=ET),
        args=["crypto_trend", "scheduled_fallback"],
        id="crypto_trend",
        name="Crypto Trend — every 4h fallback",
        replace_existing=True,
    )

    # Thematic Rotation — weekdays 9:40 AM ET
    scheduler.add_job(
        func=_execute_strategy,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=40, timezone=ET),
        args=["thematic_rotation", "scheduled_fallback"],
        id="thematic_rotation",
        name="Thematic Rotation — 9:40 AM ET fallback",
        replace_existing=True,
    )

    scheduler.start()
    scheduler_state["running"] = True
    logger.info("⏰ Scheduler started:")
    logger.info("   signal_monitor    → every 15 minutes (primary)")
    logger.info("   macro_regime      → weekdays 9:35 AM ET (fallback)")
    logger.info("   crypto_trend      → every 4h (fallback)")
    logger.info("   thematic_rotation → weekdays 9:40 AM ET (fallback)")

# ─── STARTUP ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Auto-connect to TWS on startup and start scheduler"""
    logger.info("QIS Platform starting — connecting to TWS...")
    try:
        result = client.connect()
        if result:
            logger.info("✅ TWS auto-connect successful on startup")
        else:
            logger.warning("⚠️  TWS auto-connect failed — use /api/auth/connect to retry")
    except Exception as e:
        logger.error(f"Startup connect error: {e}")

    # Start scheduler
    try:
        _setup_scheduler()
    except Exception as e:
        logger.error(f"Scheduler startup error: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully shut down scheduler"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

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

# ─── SCHEDULER MANAGEMENT ────────────────────────────────────

@app.get("/api/scheduler/status")
async def get_scheduler_status():
    """Get scheduler state, signal history and next run times"""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id":       job.id,
            "name":     job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "paused":   job.next_run_time is None,
        })
    return {
        "running":            scheduler_state["running"],
        "paused":             scheduler_state["paused"],
        "error_count":        scheduler_state["error_count"],
        "last_run":           scheduler_state["last_run"],
        "last_error":         scheduler_state["last_error"],
        "last_signal_check":  scheduler_state["last_signal_check"],
        "current_signals":    scheduler_state["signal_history"],
        "jobs":               jobs,
        "timestamp":          datetime.now(ET).isoformat(),
    }

@app.post("/api/scheduler/check-signals")
async def check_signals_now():
    """Manually trigger a signal check immediately"""
    await run_in_threadpool(_signal_monitor)
    return {
        "status":          "checked",
        "current_signals": scheduler_state["signal_history"],
        "timestamp":       datetime.now(ET).isoformat(),
    }

@app.post("/api/scheduler/pause")
async def pause_scheduler():
    """Pause all scheduled jobs"""
    scheduler_state["paused"] = True
    for job in scheduler.get_jobs():
        job.pause()
    logger.warning("⏸️  Scheduler paused manually")
    return {"status": "paused"}

@app.post("/api/scheduler/resume")
async def resume_scheduler():
    """Resume all scheduled jobs and clear error state"""
    scheduler_state["paused"] = False
    scheduler_state["error_count"] = 0
    scheduler_state["last_error"] = {}
    for job in scheduler.get_jobs():
        job.resume()
    logger.info("▶️  Scheduler resumed")
    return {"status": "resumed"}

@app.post("/api/scheduler/run/{strategy}")
async def trigger_scheduled_strategy(strategy: str):
    """Manually trigger a scheduled strategy immediately"""
    valid = ["macro_regime", "crypto_trend", "thematic_rotation"]
    if strategy not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown strategy. Valid: {valid}")
    await run_in_threadpool(_run_scheduled_strategy, strategy)
    return {
        "status":    "triggered",
        "strategy":  strategy,
        "timestamp": datetime.now(ET).isoformat(),
    }

# ─── MAIN ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )