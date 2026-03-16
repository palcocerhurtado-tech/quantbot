
from datetime import datetime
from execution.risk import RiskManager
from data.market import get_latest_price
from logs.logger import get_logger, trade_logger

log = get_logger("executor")

class PaperTrader:
    """
    Ejecuta trades simulados (paper trading).
    No toca dinero real. Perfecto para validar la estrategia.
    """
    def __init__(self):
        self.risk   = RiskManager()
        self.trades = []

    def execute_signal(self, symbol: str, signal: dict) -> dict:
        """Procesa una señal y ejecuta el trade si pasa el risk check."""

        can_trade, reason = self.risk.can_trade(symbol, signal)

        if not can_trade:
            log.info(f"{symbol}: trade rechazado — {reason}")
            return {"executed": False, "reason": reason}

        price  = get_latest_price(symbol)
        if price <= 0:
            return {"executed": False, "reason": "Precio no disponible"}

        size   = self.risk.kelly_position_size(
            signal["confidence"],
            self.risk.current_capital
        )
        shares = size / price
        action = signal["signal"]

        trade = {
            "symbol":     symbol,
            "action":     action,
            "price":      round(price, 4),
            "size_usd":   round(size, 2),
            "shares":     round(shares, 6),
            "confidence": signal["confidence"],
            "timestamp":  datetime.utcnow().isoformat(),
            "capital_before": round(self.risk.current_capital, 2)
        }

        # Registra posición abierta
        self.risk.open_positions[symbol] = trade
        trade_logger.log_trade(trade)
        self.trades.append(trade)

        log.info(
            f"TRADE EJECUTADO: {action} {symbol} "
            f"@ ${price:.2f} | ${size:.2f} ({shares:.4f} shares)"
        )
        return {"executed": True, "trade": trade}

    def close_position(self, symbol: str) -> dict:
        """Cierra una posición abierta y calcula el PnL."""
        if symbol not in self.risk.open_positions:
            return {"closed": False, "reason": "No hay posición abierta"}

        entry  = self.risk.open_positions[symbol]
        price  = get_latest_price(symbol)
        action = entry["action"]

        if action == "BUY":
            pnl = (price - entry["price"]) * entry["shares"]
        else:
            pnl = (entry["price"] - price) * entry["shares"]

        self.risk.update_capital(pnl)
        del self.risk.open_positions[symbol]

        result = {
            "symbol":      symbol,
            "entry_price": entry["price"],
            "exit_price":  round(price, 4),
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl / entry["size_usd"] * 100, 2),
            "timestamp":   datetime.utcnow().isoformat()
        }
        trade_logger.log_trade({**result, "type": "CLOSE"})
        log.info(f"POSICIÓN CERRADA: {symbol} PnL=${pnl:.2f} ({result['pnl_pct']:.1f}%)")
        return result

    def get_portfolio(self) -> dict:
        return {
            "status":   self.risk.get_status(),
            "positions": list(self.risk.open_positions.keys()),
            "n_trades": len(self.trades)
        }
