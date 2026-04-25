import asyncio
import sys

# Fix for Python 3.14 event loop
if sys.version_info >= (3, 10):
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


import os
import sys
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

client = IBKRClient()
macro   = MacroRegimeStrategy(client)
crypto  = CryptoTrendStrategy(client)
thematic = ThematicRotationStrategy(client)

starting_capital = float(os.getenv("STARTING_CAPITAL", 500))
peak_value       = starting_capital

# ─── ROUTES ───────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    """Engine + connection status"""
    connected = client.check_connection()
    return {
        "connected":    connected,
        "paper":        os.getenv("IBKR_PAPER", "true"),
        "account_id":   os.getenv("IBKR_ACCOUNT_ID"),
        "timestamp":    datetime.now().isoformat(),
    }

@app.get("/api/auth/init")
def init_auth():
    """Initialise brokerage session after gateway login"""
    try:
        # Step 1 — tickle
        client.session.post(
            f"{client.base_url}/tickle",
            verify=False,
            timeout=10
        )
        # Step 2 — init SSO session
        response = client.session.get(
            f"{client.base_url}/iserver/auth/ssodh/init?publish=true&compete=true",
            verify=False,
            timeout=10
        )
        logger.info(f"SSO init response: {response.text}")
        
        # Step 3 — check status
        status = client.check_connection()
        return {"initialized": True, "connected": status, "response": response.text}
    except Exception as e:
        logger.error(f"Auth init error: {e}")
        return {"initialized": False, "error": str(e)}


@app.get("/api/auth/reauth")
def reauth():
    """Reauthenticate brokerage session"""
    try:
        response = client.session.post(
            f"{client.base_url}/iserver/reauthenticate",
            verify=False,
            timeout=10
        )
        logger.info(f"Reauth response: {response.text}")
        return {"response": response.text, "status": response.status_code}
    except Exception as e:
        logger.error(f"Reauth error: {e}")
        return {"error": str(e)}
    
@app.get("/api/auth/debug")
def debug_auth():
    """Debug authentication state"""
    try:
        results = {}
        
        # Test 1 — tickle
        r1 = client.session.post(f"{client.base_url}/tickle", verify=False, timeout=10)
        results["tickle"] = {"status": r1.status_code, "response": r1.text[:200]}
        
        # Test 2 — auth status
        r2 = client.session.get(f"{client.base_url}/iserver/auth/status", verify=False, timeout=10)
        results["auth_status"] = {"status": r2.status_code, "response": r2.text[:200]}
        
        # Test 3 — accounts
        r3 = client.session.get(f"{client.base_url}/portfolio/accounts", verify=False, timeout=10)
        results["accounts"] = {"status": r3.status_code, "response": r3.text[:200]}
        
        return results
    except Exception as e:
        return {"error": str(e)}    

@app.get("/api/auth/set-cookie")
def set_cookie(cookie: str):
    """Set session cookie from browser"""
    try:
        client.session.cookies.set("x-sess-uuid", cookie, domain="localhost")
        logger.info(f"Cookie set: {cookie[:20]}...")
        connected = client.check_connection()
        return {"cookie_set": True, "connected": connected}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/auth/set-cookies")
def set_cookies(xsess: str, jsession: str, xyzab: str):
    """Set all session cookies from browser"""
    try:
        client.session.cookies.clear()
        client.session.cookies.set("x-sess-uuid", xsess, domain="localhost")
        client.session.cookies.set("JSESSIONID", jsession, domain="localhost", path="/sso")
        client.session.cookies.set("XYZAB", xyzab, domain="localhost")
        connected = client.check_connection()
        return {"cookies_set": True, "connected": connected}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/auth/connect")
def connect_tws():
    """Connect to TWS"""
    try:
        result = client.connect()
        return {"connected": result}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/portfolio")
def get_portfolio():
    """Portfolio value + P&L"""
    global peak_value
    try:
        value = client.get_portfolio_value()
        if value > peak_value:
            peak_value = value
        pnl        = value - starting_capital
        pnl_pct    = (pnl / starting_capital) * 100
        drawdown   = ((value - peak_value) / peak_value) * 100

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

@app.get("/api/positions")
def get_positions():
    """Open positions"""
    try:
        positions = client.get_positions()
        result = []
        for pos in positions:
            result.append({
                "symbol":        pos.get("ticker", ""),
                "quantity":      pos.get("position", 0),
                "avg_price":     pos.get("avgCost", 0),
                "market_value":  pos.get("mktValue", 0),
                "unrealized_pnl": pos.get("unrealizedPnl", 0),
                "realized_pnl":  pos.get("realizedPnl", 0),
                "currency":      pos.get("currency", "USD"),
            })
        return {"positions": result, "count": len(result)}
    except Exception as e:
        logger.error(f"Positions error: {e}")
        return {"positions": [], "count": 0}

@app.get("/api/signals")
def get_signals():
    """Current signal status for all strategies"""
    try:
        signals = []

        # ── VIX signal
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

        # ── BTC EMA signal
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

        # ── ETH signal
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

        # ── SPY regime signal
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

    except Exception as e:
        logger.error(f"Signals error: {e}")
        return {"signals": [], "count": 0}

@app.get("/api/regime")
def get_regime():
    """Current market regime"""
    try:
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
    except Exception as e:
        logger.error(f"Regime error: {e}")
        return {"regime": "UNKNOWN", "bear_score": 0}

@app.get("/api/account")
def get_account():
    """Account summary"""
    try:
        accounts = client.get_account()
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

# ─── MAIN ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )