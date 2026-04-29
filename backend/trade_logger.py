import json
import os
from datetime import datetime
from loguru import logger

TRADE_LOG_FILE = os.path.join(
    os.path.dirname(__file__), "logs", "trades.json"
)

def _load_trades():
    if not os.path.exists(TRADE_LOG_FILE):
        return []
    try:
        with open(TRADE_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def _save_trades(trades):
    os.makedirs(os.path.dirname(TRADE_LOG_FILE), exist_ok=True)
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2, default=str)

def log_trade(symbol, action, shares, price, reason="", strategy=""):
    trade = {
        "id":        len(_load_trades()) + 1,
        "timestamp": datetime.now().isoformat(),
        "symbol":    symbol,
        "action":    action,
        "shares":    shares,
        "price":     price,
        "value":     round(shares * price, 2),
        "reason":    reason,
        "strategy":  strategy,
    }
    trades = _load_trades()
    trades.append(trade)
    _save_trades(trades)
    logger.info(f"Trade logged: {action} {shares} {symbol} @ ${price}")
    return trade

def get_trades(limit=100):
    trades = _load_trades()
    return sorted(trades, key=lambda x: x["timestamp"], reverse=True)[:limit]

def get_trade_summary():
    trades = _load_trades()
    summary = {}
    for t in trades:
        strat = t.get("strategy", "unknown")
        if strat not in summary:
            summary[strat] = {"trades": 0, "buys": 0, "sells": 0}
        summary[strat]["trades"] += 1
        if t["action"] == "BUY":
            summary[strat]["buys"] += 1
        else:
            summary[strat]["sells"] += 1
    return summary
