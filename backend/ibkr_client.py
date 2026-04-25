import os
import asyncio
import sys
from dotenv import load_dotenv
from loguru import logger
from ib_insync import *

# Fix for Python 3.14 event loop
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

load_dotenv()

class IBKRClient:
    def __init__(self):
        self.paper = os.getenv("IBKR_PAPER", "true").lower() == "true"
        self.account_id = os.getenv("IBKR_ACCOUNT_ID", "U25402501")
        self.host = "127.0.0.1"
        self.port = int(os.getenv("TWS_PORT", 7497))
        self.client_id = 1

        # ib_insync client
        self.ib = IB()

        # Hardcoded conids
        self.CONIDS = {
            "SPY":  756733,
            "QQQ":  320227571,
            "GLD":  51529211,
            "TLT":  15547841,
            "SH":   738523410,
            "SDS":  828937764,
            "BOTZ": 247691382,
            "BLOK": 302902491,
            "BTC":  541686651,
            "ETH":  541686654,
            "VIX":  13455763,
        }
        logger.info(f"IBKR TWS Client initialized — Paper: {self.paper}")

    # ─── CONNECTION ────────────────────────────────────────────

    def connect(self):
        """Connect to TWS"""
        try:
            logger.info(f"Attempting TWS connection on {self.host}:{self.port} clientId:{self.client_id}")
            import nest_asyncio
            nest_asyncio.apply()
            self.ib.connect(
                self.host, 
                self.port, 
                clientId=self.client_id,
                timeout=10
            )
            logger.info(f"✅ Connected to TWS on port {self.port}")
            return True
        except Exception as e:
            logger.error(f"TWS Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from TWS"""
        self.ib.disconnect()
        logger.info("Disconnected from TWS")

    def check_connection(self):
        """Check if connected to TWS"""
        try:
            if not self.ib.isConnected():
                self.connect()
            connected = self.ib.isConnected()
            logger.info(f"TWS Connection: {'✅ Connected' if connected else '❌ Disconnected'}")
            return connected
        except Exception as e:
            logger.error(f"Connection check failed: {e}")
            return False

    # ─── ACCOUNT ───────────────────────────────────────────────

    def get_account(self):
        """Get account details"""
        try:
            if not self.ib.isConnected():
                self.connect()
            accounts = self.ib.managedAccounts()
            return [{"id": acc} for acc in accounts]
        except Exception as e:
            logger.error(f"Failed to get account: {e}")
            return None

    def get_portfolio_value(self):
        """Get total portfolio value"""
        try:
            if not self.ib.isConnected():
                self.connect()
            account_values = self.ib.accountValues(self.account_id)
            for av in account_values:
                if av.tag == "NetLiquidation" and av.currency == "USD":
                    value = float(av.value)
                    logger.info(f"Portfolio value: ${value:,.2f}")
                    return value
            return float(os.getenv("STARTING_CAPITAL", 500))
        except Exception as e:
            logger.error(f"Failed to get portfolio value: {e}")
            return float(os.getenv("STARTING_CAPITAL", 500))

    # ─── MARKET DATA ───────────────────────────────────────────

    def get_contract(self, symbol):
        """Get IBKR contract for a symbol"""
        try:
            if symbol in ["BTC", "ETH", "SOL"]:
                contract = Crypto(symbol, "PAXOS", "USD")
            elif symbol == "VIX":
                contract = Index("VIX", "CBOE")
            else:
                contract = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            return contract
        except Exception as e:
            logger.error(f"Failed to get contract for {symbol}: {e}")
            return None

    def get_conid(self, symbol):
        """Get contract ID for symbol"""
        if symbol in self.CONIDS:
            return self.CONIDS[symbol]
        return None

    def get_price(self, symbol):
        """Get current price for a symbol"""
        try:
            if not self.ib.isConnected():
                self.connect()
            contract = self.get_contract(symbol)
            if not contract:
                return None
            ticker = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(2)
            price = ticker.last or ticker.close
            logger.info(f"{symbol} price: ${price}")
            return float(price) if price else None
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return None

    def get_historical_data(self, symbol, period="1M", bar="1d"):
        """Get historical OHLCV data"""
        try:
            if not self.ib.isConnected():
                self.connect()
            contract = self.get_contract(symbol)
            if not contract:
                return None

            duration_map = {
                "1M": "1 M", "3M": "3 M", "6M": "6 M",
                "1Y": "1 Y", "2Y": "2 Y"
            }
            bar_map = {
                "1d": "1 day", "1h": "1 hour",
                "30m": "30 mins", "15m": "15 mins"
            }
            duration = duration_map.get(period, "1 M")
            bar_size = bar_map.get(bar, "1 day")

            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True
            )

            if not bars:
                return None

            data = []
            for bar in bars:
                data.append({
                    "open":   bar.open,
                    "high":   bar.high,
                    "low":    bar.low,
                    "close":  bar.close,
                    "volume": bar.volume
                })
            return data

        except Exception as e:
            logger.error(f"Failed to get historical data for {symbol}: {e}")
            return None

    # ─── ORDERS ────────────────────────────────────────────────

    def place_order(self, symbol, side, quantity, order_type="MKT"):
        """Place a trade order"""
        try:
            if not self.ib.isConnected():
                self.connect()
            contract = self.get_contract(symbol)
            if not contract:
                return None

            if order_type == "MKT":
                order = MarketOrder(side.upper(), quantity)
            else:
                order = LimitOrder(side.upper(), quantity, 0)

            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)
            logger.info(f"Order placed: {side} {quantity} {symbol} → {trade.orderStatus.status}")
            return {"status": trade.orderStatus.status, "orderId": trade.order.orderId}

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    def get_positions(self):
        """Get current open positions"""
        try:
            if not self.ib.isConnected():
                self.connect()
            positions = self.ib.positions(self.account_id)
            result = []
            for pos in positions:
                result.append({
                    "ticker":        pos.contract.symbol,
                    "position":      pos.position,
                    "avgCost":       pos.avgCost,
                    "mktValue":      pos.position * pos.avgCost,
                    "unrealizedPnl": 0,
                    "realizedPnl":   0,
                    "currency":      pos.contract.currency
                })
            return result
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def close_position(self, symbol):
        """Close an open position"""
        try:
            positions = self.get_positions()
            for pos in positions:
                if pos.get("ticker") == symbol:
                    quantity = abs(pos.get("position", 0))
                    side = "SELL" if pos.get("position", 0) > 0 else "BUY"
                    return self.place_order(symbol, side, quantity)
            logger.warning(f"No open position found for {symbol}")
            return None
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return None

    def close_all_positions(self):
        """Emergency — close all positions (kill switch)"""
        logger.warning("⚠️  KILL SWITCH ACTIVATED — closing all positions")
        positions = self.get_positions()
        for pos in positions:
            symbol = pos.get("ticker")
            if symbol:
                self.close_position(symbol)
                logger.info(f"Closed position: {symbol}")