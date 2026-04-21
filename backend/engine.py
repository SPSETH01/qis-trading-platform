import os
import time
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ibkr_client import IBKRClient
from strategies.macro_regime import MacroRegimeStrategy
from strategies.crypto_trend import CryptoTrendStrategy
from strategies.thematic_rotation import ThematicRotationStrategy

load_dotenv()

# ─── LOGGING SETUP ────────────────────────────────────────────

logger.add(
    "logs/engine_{time}.log",
    rotation="1 day",
    retention="30 days",
    level="INFO"
)

class TradingEngine:
    """
    Main QIS Trading Engine
    Orchestrates all 3 strategies
    Manages risk + kill switch
    Runs on scheduler 24/7
    """

    def __init__(self):
        logger.info("🚀 QIS Trading Engine initialising...")

        # IBKR client
        self.client = IBKRClient()

        # Strategies
        self.macro_regime  = MacroRegimeStrategy(self.client)
        self.crypto_trend  = CryptoTrendStrategy(self.client)
        self.thematic      = ThematicRotationStrategy(self.client)

        # Portfolio settings
        self.starting_capital = float(os.getenv("STARTING_CAPITAL", 500))
        self.kill_switch_pct  = float(os.getenv("KILL_SWITCH_DRAWDOWN", 0.15))

        # State tracking
        self.peak_value       = self.starting_capital
        self.kill_switch_active = False
        self.trade_log        = []

        logger.info(f"Engine ready — Starting capital: ${self.starting_capital:,.2f}")

    # ─── RISK MANAGEMENT ──────────────────────────────────────

    def check_kill_switch(self, portfolio_value):
        """
        Portfolio level kill switch
        Closes all positions if drawdown exceeds threshold
        """
        # Update peak
        if portfolio_value > self.peak_value:
            self.peak_value = portfolio_value

        # Calculate drawdown
        drawdown = (portfolio_value - self.peak_value) / self.peak_value

        logger.info(
            f"Portfolio: ${portfolio_value:,.2f} | "
            f"Peak: ${self.peak_value:,.2f} | "
            f"Drawdown: {drawdown:.1%}"
        )

        if drawdown <= -self.kill_switch_pct:
            logger.warning(
                f"⚠️  KILL SWITCH — drawdown {drawdown:.1%} "
                f"exceeds {self.kill_switch_pct:.1%} threshold"
            )
            self.client.close_all_positions()
            self.kill_switch_active = True
            self._log_trade({
                "timestamp": datetime.now().isoformat(),
                "action":    "KILL_SWITCH",
                "reason":    f"Drawdown {drawdown:.1%}",
                "portfolio": portfolio_value
            })
            return True

        # Reset kill switch if portfolio recovers
        if self.kill_switch_active and drawdown > -self.kill_switch_pct * 0.5:
            logger.info("Kill switch reset — portfolio recovered")
            self.kill_switch_active = False

        return False

    def is_market_open(self):
        """Check if US market is open"""
        now   = datetime.now()
        # Monday=0, Friday=4
        if now.weekday() > 4:
            return False
        hour = now.hour
        minute = now.minute
        # 9:30am - 4:00pm ET (approximate — adjust for your timezone)
        market_open  = (hour == 9 and minute >= 30) or (hour > 9 and hour < 16)
        return market_open

    # ─── STRATEGY RUNNERS ─────────────────────────────────────

    def run_macro_regime(self):
        """Run macro regime strategy — weekdays at market open"""
        logger.info("── Running Macro Regime Strategy ──")
        try:
            portfolio_value = self.client.get_portfolio_value()
            if self.check_kill_switch(portfolio_value):
                return
            result = self.macro_regime.run(portfolio_value)
            if result:
                self._log_trade({
                    "timestamp": datetime.now().isoformat(),
                    "strategy":  "macro_regime",
                    "result":    result
                })
        except Exception as e:
            logger.error(f"Macro regime runner error: {e}")

    def run_crypto_trend(self):
        """Run crypto trend strategy — every 4 hours (24/7)"""
        logger.info("── Running Crypto Trend Strategy ──")
        try:
            portfolio_value = self.client.get_portfolio_value()
            if self.check_kill_switch(portfolio_value):
                return
            results = self.crypto_trend.run(portfolio_value)
            for result in results:
                self._log_trade({
                    "timestamp": datetime.now().isoformat(),
                    "strategy":  "crypto_trend",
                    "result":    result
                })
        except Exception as e:
            logger.error(f"Crypto trend runner error: {e}")

    def run_thematic_rotation(self):
        """Run thematic rotation — every Monday at market open"""
        logger.info("── Running Thematic Rotation Strategy ──")
        try:
            portfolio_value = self.client.get_portfolio_value()
            if self.check_kill_switch(portfolio_value):
                return
            results = self.thematic.run(portfolio_value)
            for result in results:
                self._log_trade({
                    "timestamp": datetime.now().isoformat(),
                    "strategy":  "thematic_rotation",
                    "result":    result
                })
        except Exception as e:
            logger.error(f"Thematic rotation runner error: {e}")

    def run_all(self):
        """Run all strategies — used for testing"""
        logger.info("=== Running All Strategies ===")
        portfolio_value = self.client.get_portfolio_value()
        logger.info(f"Portfolio value: ${portfolio_value:,.2f}")

        if self.check_kill_switch(portfolio_value):
            logger.warning("Kill switch active — no strategies running")
            return

        self.run_macro_regime()
        self.run_crypto_trend()
        self.run_thematic_rotation()

    # ─── TRADE LOG ────────────────────────────────────────────

    def _log_trade(self, entry):
        """Log trade to memory + file"""
        self.trade_log.append(entry)
        logger.info(f"Trade logged: {entry}")

    def get_trade_log(self):
        """Return full trade log"""
        return self.trade_log

    # ─── STATUS ───────────────────────────────────────────────

    def get_status(self):
        """Get engine status summary"""
        portfolio_value = self.client.get_portfolio_value()
        positions       = self.client.get_positions()
        pnl             = portfolio_value - self.starting_capital
        pnl_pct         = (pnl / self.starting_capital) * 100

        return {
            "timestamp":      datetime.now().isoformat(),
            "portfolio_value": portfolio_value,
            "starting_capital": self.starting_capital,
            "pnl":            pnl,
            "pnl_pct":        pnl_pct,
            "peak_value":     self.peak_value,
            "kill_switch":    self.kill_switch_active,
            "positions":      len(positions),
            "trades_today":   len(self.trade_log),
            "market_open":    self.is_market_open()
        }

# ─── SCHEDULER ────────────────────────────────────────────────

def start_scheduler(engine):
    """
    Schedule all strategies
    Crypto runs 24/7 every 4 hours
    ETF strategies run on market hours
    """
    scheduler = BlockingScheduler(timezone="America/New_York")

    # Macro regime — weekdays at 9:35am ET (5 mins after open)
    scheduler.add_job(
        engine.run_macro_regime,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=35),
        id="macro_regime",
        name="Macro Regime Strategy"
    )

    # Crypto trend — every 4 hours, 7 days a week
    scheduler.add_job(
        engine.run_crypto_trend,
        CronTrigger(hour="0,4,8,12,16,20", minute=0),
        id="crypto_trend",
        name="Crypto Trend Strategy"
    )

    # Thematic rotation — every Monday at 9:40am ET
    scheduler.add_job(
        engine.run_thematic_rotation,
        CronTrigger(day_of_week="mon", hour=9, minute=40),
        id="thematic_rotation",
        name="Thematic Rotation Strategy"
    )

    logger.info("📅 Scheduler started:")
    logger.info("  Macro Regime    → weekdays 9:35am ET")
    logger.info("  Crypto Trend    → every 4 hours 24/7")
    logger.info("  Thematic Rotation → Mondays 9:40am ET")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Engine stopped by user")
        scheduler.shutdown()

# ─── MAIN ─────────────────────────────────────────────────────

if __name__ == "__main__":
    # Create logs directory
    os.makedirs("logs", exist_ok=True)

    # Check IBKR connection
    engine = TradingEngine()
    connected = engine.client.check_connection()

    if not connected:
        logger.warning(
            "⚠️  IBKR gateway not connected — "
            "start Client Portal Gateway first"
        )
        logger.info("Running in simulation mode...")

    # Start scheduler
    logger.info("Starting QIS Trading Engine...")
    start_scheduler(engine)