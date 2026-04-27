from datetime import datetime

from execution.risk import RiskManager
from data.market import get_latest_price, get_usdt_balance
from logs.logger import get_logger, trade_logger

log = get_logger("executor")


class PaperTrader:
    """
    Ejecuta trades simulados (paper trading).
    Usa el capital de Binance como referencia cuando está disponible,
    pero opera sobre capital simulado sin tocar dinero real.
    """

    def __init__(self):
        self.risk   = RiskManager()
        self.trades = []

    def get_balance(self) -> float:
        """Returns available capital (simulated). Logs Binance balance for reference."""
        try:
            live_balance = get_usdt_balance()
            if live_balance > 0:
                log.info(f"Balance Binance (ref): ${live_balance:,.2f} USDT")
        except Exception as e:
            log.error(f"Error obteniendo balance: {e}")
        return self.risk.current_capital

    def execute_signal(self, symbol: str, signal: dict) -> dict:
        can_trade, reason = self.risk.can_trade(symbol, signal)
        if not can_trade:
            log.info(f"{symbol}: trade rechazado — {reason}")
            return {"executed": False, "reason": reason}

        price = get_latest_price(symbol)
        if price <= 0:
            return {"executed": False, "reason": "Precio no disponible"}

        size   = self.risk.kelly_position_size(
            signal["confidence"], self.risk.current_capital
        )
        shares = size / price
        action = signal["signal"]

        trade = {
            "symbol":         symbol,
            "action":         action,
            "price":          round(price, 4),
            "size_usd":       round(size, 2),
            "shares":         round(shares, 6),
            "confidence":     signal["confidence"],
            "timestamp":      datetime.utcnow().isoformat(),
            "capital_before": round(self.risk.current_capital, 2),
        }
        self.risk.open_positions[symbol] = trade
        trade_logger.log_trade(trade)
        self.trades.append(trade)

        log.info(
            f"TRADE EJECUTADO: {action} {symbol} "
            f"@ ${price:.4f} | ${size:.2f} ({shares:.6f})"
        )
        return {"executed": True, "trade": trade}

    def close_position(self, symbol: str) -> dict:
        if symbol not in self.risk.open_positions:
            return {"closed": False, "reason": "No hay posición abierta"}

        entry = self.risk.open_positions[symbol]
        price = get_latest_price(symbol)
        pnl   = (
            (price - entry["price"]) * entry["shares"]
            if entry["action"] == "BUY"
            else (entry["price"] - price) * entry["shares"]
        )

        self.risk.update_capital(pnl)
        del self.risk.open_positions[symbol]

        result = {
            "symbol":      symbol,
            "entry_price": entry["price"],
            "exit_price":  round(price, 4),
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl / entry["size_usd"] * 100, 2),
            "timestamp":   datetime.utcnow().isoformat(),
        }
        trade_logger.log_trade({**result, "type": "CLOSE"})
        log.info(
            f"POSICIÓN CERRADA: {symbol} PnL=${pnl:.2f} ({result['pnl_pct']:.1f}%)"
        )
        return result

    def get_portfolio(self) -> dict:
        return {
            "status":    self.risk.get_status(),
            "positions": list(self.risk.open_positions.keys()),
            "n_trades":  len(self.trades),
        }
