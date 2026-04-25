import os
import asyncio
import threading
import math
from dotenv import load_dotenv
from loguru import logger
from ib_insync import IB, Stock, Index, Crypto, MarketOrder, LimitOrder

load_dotenv()


class IBKRClient:
    """
    IBKR TWS client that runs ib_insync in a dedicated background thread
    with its own event loop — completely isolated from FastAPI's event loop.

    All public methods are synchronous and safe to call from any thread,
    including FastAPI route handlers and threadpool workers.
    """

    def __init__(self):
        self.paper      = os.getenv("IBKR_PAPER", "true").lower() == "true"
        self.account_id = os.getenv("IBKR_ACCOUNT_ID", "DU25402501")
        self.host       = "127.0.0.1"
        self.port       = int(os.getenv("TWS_PORT", 7497))
        self.client_id  = 1

        # ib_insync instance — only touched from _tws_thread
        self.ib = IB()

        # Dedicated event loop + thread for all TWS calls
        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="tws-event-loop",
            daemon=True
        )
        self._thread.start()

        # Crypto native contracts — PAXOS exchange, 24/7 trading
        # Falls back to ETF proxies if PAXOS not available on account
        self.CRYPTO_NATIVE = {"BTC", "ETH"}
        self.CRYPTO_EXCHANGE = os.getenv("CRYPTO_EXCHANGE", "PAXOS")  # PAXOS or ZEROHASH

        # ETF proxies fallback (used if USE_CRYPTO_PROXY=true in .env)
        self.USE_CRYPTO_PROXY = os.getenv("USE_CRYPTO_PROXY", "false").lower() == "true"
        self.CRYPTO_PROXY = {
            "BTC": "BITO",
            "ETH": "ETHE",
        }

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
            "BITO": 485478546,
            "ETHE": 532641611,
            "VIX":  13455763,
            # Crypto conIds vary by exchange
            "BTC_PAXOS":     479624278,
            "ETH_PAXOS":     532640249,
            "BTC_ZEROHASH":  541686651,
            "ETH_ZEROHASH":  541686654,
        }
        logger.info(f"IBKR TWS Client initialized — Paper: {self.paper}")

    # ─── PRIVATE: EVENT LOOP THREAD ───────────────────────────

    def _run_loop(self):
        """Run the dedicated asyncio event loop forever in background thread"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro):
        """
        Submit a coroutine to the TWS event loop and block until result.
        Safe to call from any thread including FastAPI workers.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    # ─── CONNECTION ────────────────────────────────────────────

    def connect(self):
        """Connect to TWS — blocks until connected or timeout"""
        return self._run(self._connect())

    async def _connect(self):
        try:
            logger.info(
                f"Attempting TWS connection on {self.host}:{self.port} "
                f"clientId:{self.client_id}"
            )
            await self.ib.connectAsync(
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
        self._run(self._disconnect())

    async def _disconnect(self):
        self.ib.disconnect()
        logger.info("Disconnected from TWS")

    def check_connection(self):
        try:
            connected = self.ib.isConnected()
            if not connected:
                connected = self.connect()
            logger.info(f"TWS Connection: {'✅ Connected' if connected else '❌ Disconnected'}")
            return connected
        except Exception as e:
            logger.error(f"Connection check failed: {e}")
            return False

    # ─── ACCOUNT ───────────────────────────────────────────────

    def get_account(self):
        return self._run(self._get_account())

    async def _get_account(self):
        try:
            accounts = self.ib.managedAccounts()
            return [{"id": acc} for acc in accounts]
        except Exception as e:
            logger.error(f"Failed to get account: {e}")
            return None

    def get_portfolio_value(self):
        return self._run(self._get_portfolio_value())

    async def _get_portfolio_value(self):
        try:
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
        return self._run(self._get_contract(symbol))

    async def _get_contract(self, symbol):
        try:
            # Native crypto contracts
            if symbol in self.CRYPTO_NATIVE and not self.USE_CRYPTO_PROXY:
                exchange = self.CRYPTO_EXCHANGE
                contract = Crypto(symbol, exchange, "USD")
                logger.info(f"Crypto contract: {symbol} on {exchange}")
                await self.ib.qualifyContractsAsync(contract)
                return contract

            # ETF proxy fallback
            if symbol in self.CRYPTO_PROXY and self.USE_CRYPTO_PROXY:
                resolved = self.CRYPTO_PROXY[symbol]
                logger.info(f"Crypto proxy: {symbol} -> {resolved}")
                contract = Stock(resolved, "SMART", "USD")
                await self.ib.qualifyContractsAsync(contract)
                return contract

            # VIX index
            if symbol == "VIX":
                contract = Index("VIX", "CBOE")
                await self.ib.qualifyContractsAsync(contract)
                return contract

            # Standard stock/ETF
            contract = Stock(symbol, "SMART", "USD")
            await self.ib.qualifyContractsAsync(contract)
            return contract

        except Exception as e:
            logger.error(f"Failed to get contract for {symbol}: {e}")
            # If native crypto fails, try ETF proxy as fallback
            if symbol in self.CRYPTO_NATIVE:
                logger.warning(f"Native crypto failed for {symbol}, falling back to ETF proxy")
                try:
                    resolved = self.CRYPTO_PROXY.get(symbol, symbol)
                    contract = Stock(resolved, "SMART", "USD")
                    await self.ib.qualifyContractsAsync(contract)
                    return contract
                except Exception as e2:
                    logger.error(f"ETF proxy fallback also failed for {symbol}: {e2}")
            return None

    def get_price(self, symbol):
        return self._run(self._get_price(symbol))

    async def _get_price(self, symbol):
        try:
            contract = await self._get_contract(symbol)
            if not contract:
                return None
            ticker = self.ib.reqMktData(contract, "", False, False)
            await asyncio.sleep(2)
            price = ticker.last if ticker.last and not math.isnan(ticker.last) else None
            if price is None:
                price = ticker.close if ticker.close and not math.isnan(ticker.close) else None
            logger.info(f"{symbol} price: ${price}")
            return float(price) if price else None
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return None

    def get_historical_data(self, symbol, period="1M", bar="1d"):
        return self._run(self._get_historical_data(symbol, period, bar))

    async def _get_historical_data(self, symbol, period="1M", bar="1d"):
        try:
            contract = await self._get_contract(symbol)
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

            # Crypto uses MIDPOINT and trades 24/7 (useRTH=False)
            is_crypto = symbol in self.CRYPTO_NATIVE
            what_to_show = "MIDPOINT" if is_crypto else "TRADES"
            use_rth = False if is_crypto else True

            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth
            )

            if not bars:
                return None

            return [
                {
                    "open":   b.open,
                    "high":   b.high,
                    "low":    b.low,
                    "close":  b.close,
                    "volume": b.volume
                }
                for b in bars
            ]

        except Exception as e:
            logger.error(f"Failed to get historical data for {symbol}: {e}")
            return None

    # ─── ORDERS ────────────────────────────────────────────────

    def place_order(self, symbol, side, quantity, order_type="MKT"):
        return self._run(self._place_order(symbol, side, quantity, order_type))

    async def _place_order(self, symbol, side, quantity, order_type="MKT"):
        try:
            contract = await self._get_contract(symbol)
            if not contract:
                return None

            if order_type == "MKT":
                order = MarketOrder(side.upper(), quantity)
            else:
                order = LimitOrder(side.upper(), quantity, 0)

            # Queue outside regular trading hours — fills at next market open
            order.outsideRth = True

            trade = self.ib.placeOrder(contract, order)
            await asyncio.sleep(1)
            logger.info(
                f"Order placed: {side} {quantity} {symbol} "
                f"→ {trade.orderStatus.status}"
            )
            return {
                "status":  trade.orderStatus.status,
                "orderId": trade.order.orderId
            }
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    def get_positions(self):
        return self._run(self._get_positions())

    async def _get_positions(self):
        try:
            positions = self.ib.positions(self.account_id)
            return [
                {
                    "ticker":        pos.contract.symbol,
                    "position":      pos.position,
                    "avgCost":       pos.avgCost,
                    "mktValue":      pos.position * pos.avgCost,
                    "unrealizedPnl": 0,
                    "realizedPnl":   0,
                    "currency":      pos.contract.currency
                }
                for pos in positions
            ]
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def close_position(self, symbol):
        return self._run(self._close_position(symbol))

    async def _close_position(self, symbol):
        try:
            positions = await self._get_positions()
            for pos in positions:
                if pos.get("ticker") == symbol:
                    quantity = abs(pos.get("position", 0))
                    side     = "SELL" if pos.get("position", 0) > 0 else "BUY"
                    return await self._place_order(symbol, side, quantity)
            logger.warning(f"No open position found for {symbol}")
            return None
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return None

    def close_all_positions(self):
        return self._run(self._close_all_positions())

    async def _close_all_positions(self):
        logger.warning("⚠️  KILL SWITCH ACTIVATED — closing all positions")
        positions = await self._get_positions()
        for pos in positions:
            symbol = pos.get("ticker")
            if symbol:
                await self._close_position(symbol)
                logger.info(f"Closed position: {symbol}")