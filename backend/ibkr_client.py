import os
import urllib3
import requests
from dotenv import load_dotenv
from loguru import logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

class IBKRClient:
    def __init__(self):
        self.base_url = os.getenv("IBKR_BASE_URL", "https://localhost:5001/v1/api")
        self.paper = os.getenv("IBKR_PAPER", "true").lower() == "true"
        self.account_id = os.getenv("IBKR_ACCOUNT_ID", "U25402501")
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })

        # Hardcoded conids for fast lookup
        self.CONIDS = {
            "SPY":  "756733",
            "QQQ":  "320227571",
            "GLD":  "51529211",
            "TLT":  "15547841",
            "SH":   "738523410",
            "SDS":  "828937764",
            "BOTZ": "247691382",
            "BLOK": "302902491",
            "BTC":  "541686651",
            "ETH":  "541686654",
            "VIX":  "13455763",
        }
        logger.info(f"IBKR Client initialized — Paper: {self.paper}")

    # ─── CONNECTION ────────────────────────────────────────────

    def check_connection(self):
        """Check if IBKR gateway is running"""
        try:
            # First tickle to wake up session
            self.session.post(
                f"{self.base_url}/tickle",
                verify=False,
                timeout=10
            )
            # Then check auth status
            response = self.session.get(
                f"{self.base_url}/iserver/auth/status",
                verify=False,
                timeout=10
            )
            if response.status_code == 200 and response.text.strip():
                data = response.json()
                connected = data.get("authenticated", False)
                logger.info(f"IBKR Connection: {'✅ Connected' if connected else '❌ Disconnected'}")
                return connected
            return False
        except Exception as e:
            logger.error(f"IBKR Connection failed: {e}")
            return False

    def tickle(self):
        """Keep session alive"""
        try:
            response = self.session.post(
                f"{self.base_url}/tickle",
                verify=False,
                timeout=10
            )
            return response.json()
        except Exception as e:
            logger.error(f"Tickle failed: {e}")
            return None

    def get_account(self):
        """Get account details"""
        try:
            response = self.session.get(
                f"{self.base_url}/portfolio/accounts",
                verify=False,
                timeout=10
            )
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get account: {e}")
            return None

    def get_portfolio_value(self):
        """Get total portfolio value"""
        try:
            response = self.session.get(
                f"{self.base_url}/portfolio/{self.account_id}/summary",
                verify=False,
                timeout=10
            )
            data = response.json()
            value = data.get("netliquidation", {}).get("amount", 500)
            logger.info(f"Portfolio value: ${value:,.2f}")
            return float(value)
        except Exception as e:
            logger.error(f"Failed to get portfolio value: {e}")
            return float(os.getenv("STARTING_CAPITAL", 500))

    # ─── MARKET DATA ───────────────────────────────────────────

    def get_price(self, symbol):
        """Get current price for a symbol"""
        try:
            conid = self.get_conid(symbol)
            if not conid:
                return None
            response = self.session.get(
                f"{self.base_url}/iserver/marketdata/snapshot",
                params={"conids": conid, "fields": "31,84,86"},
                verify=False,
                timeout=10
            )
            data = response.json()
            if data:
                price = data[0].get("31")
                logger.info(f"{symbol} price: ${price}")
                return float(price) if price else None
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return None

    def get_conid(self, symbol):
        """Get IBKR contract ID for a symbol"""
        # Check hardcoded map first
        if symbol in self.CONIDS and self.CONIDS[symbol]:
            return self.CONIDS[symbol]
        try:
            response = self.session.post(
                f"{self.base_url}/iserver/secdef/search",
                json={"symbol": symbol},
                verify=False,
                timeout=10
            )
            data = response.json()
            if data:
                conid = data[0].get("conid")
                self.CONIDS[symbol] = conid
                return conid
        except Exception as e:
            logger.error(f"Failed to get conid for {symbol}: {e}")
            return None

    def get_historical_data(self, symbol, period="1M", bar="1d"):
        """Get historical OHLCV data"""
        try:
            conid = self.get_conid(symbol)
            if not conid:
                return None
            response = self.session.get(
                f"{self.base_url}/iserver/marketdata/history",
                params={"conid": conid, "period": period, "bar": bar},
                verify=False,
                timeout=10
            )
            data = response.json()
            return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to get historical data for {symbol}: {e}")
            return None

    # ─── ORDERS ────────────────────────────────────────────────

    def place_order(self, symbol, side, quantity, order_type="MKT"):
        """Place a trade order"""
        try:
            conid = self.get_conid(symbol)
            if not conid:
                logger.error(f"No conid found for {symbol}")
                return None

            order = {
                "conid": conid,
                "orderType": order_type,
                "side": side.upper(),
                "quantity": quantity,
                "tif": "DAY"
            }

            response = self.session.post(
                f"{self.base_url}/iserver/account/{self.account_id}/orders",
                json={"orders": [order]},
                verify=False,
                timeout=10
            )
            result = response.json()
            logger.info(f"Order placed: {side} {quantity} {symbol} → {result}")
            return result

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    def get_positions(self):
        """Get current open positions"""
        try:
            response = self.session.get(
                f"{self.base_url}/portfolio/{self.account_id}/positions/0",
                verify=False,
                timeout=10
            )
            return response.json()
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
            logger.error(f"Failed to close position for {symbol}: {e}")
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