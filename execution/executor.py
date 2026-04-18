
from datetime import datetime
from execution.risk import RiskManager
from data.market import get_latest_price
from logs.logger import get_logger, trade_logger

log = get_logger("executor")

class PaperTrader:
    """
    Ejecuta trades simulados (paper trading).
    No toca dinero real. Perfecto para validar la estrategia.

    Soporta dos modos de gestión de salida:
      1. SL/TP automático  — cuando la señal incluye 'sl' y 'tp'
         (usado por ElliottStrategy y estrategias de reglas fijas)
      2. Señal de cambio   — sale cuando llega señal SELL/BUY contraria
         (usado por el predictor ML original)
    """

    def __init__(self):
        self.risk      = RiskManager()
        self.trades    = []
        # Niveles SL/TP por símbolo: {symbol: {"sl": float, "tp": float}}
        self._sl_tp: dict = {}

    # ── Apertura de posición ────────────────────────────────────────────────

    def execute_signal(self, symbol: str, signal: dict) -> dict:
        """
        Procesa una señal y abre posición si pasa el risk check.

        Si la señal incluye 'sl' y 'tp' los almacena para evaluarlos
        en cada ciclo con check_exits().
        """
        can_trade, reason = self.risk.can_trade(symbol, signal)
        if not can_trade:
            log.info(f"{symbol}: trade rechazado — {reason}")
            return {"executed": False, "reason": reason}

        price = get_latest_price(symbol)
        if price <= 0:
            return {"executed": False, "reason": "Precio no disponible"}

        # Tamaño de posición
        size   = self.risk.kelly_position_size(signal["confidence"], self.risk.current_capital)
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
            # Guardar SL/TP si vienen en la señal
            "sl":             signal.get("sl", None),
            "tp":             signal.get("tp", None),
        }

        self.risk.open_positions[symbol] = trade

        # Registrar SL/TP para evaluación en check_exits()
        if signal.get("sl") and signal.get("tp"):
            self._sl_tp[symbol] = {
                "sl":     signal["sl"],
                "tp":     signal["tp"],
                "shares": shares,
            }
            log.info(
                f"SL/TP registrado para {symbol}: SL={signal['sl']:.2f} "
                f"TP={signal['tp']:.2f}"
            )

        trade_logger.log_trade(trade)
        self.trades.append(trade)
        log.info(
            f"TRADE ABIERTO: {action} {symbol} @ ${price:.2f} "
            f"| ${size:.2f} ({shares:.6f} unidades)"
        )
        return {"executed": True, "trade": trade}

    # ── Evaluación de SL/TP en cada ciclo ──────────────────────────────────

    def check_exits(self, symbol: str) -> dict:
        """
        Comprueba si el precio actual ha tocado SL o TP de una posición abierta.
        Llama a esto en cada ciclo del bot para los símbolos que usan SL/TP fijo.

        Devuelve {"checked": False}  si no hay posición con SL/TP
        Devuelve {"closed": True/False, "reason": ..., "pnl": ...}  si comprueba
        """
        if symbol not in self._sl_tp or symbol not in self.risk.open_positions:
            return {"checked": False}

        levels = self._sl_tp[symbol]
        price  = get_latest_price(symbol)
        if price <= 0:
            return {"checked": True, "closed": False, "reason": "Precio no disponible"}

        sl, tp = levels["sl"], levels["tp"]
        entry  = self.risk.open_positions[symbol]["price"]
        shares = levels["shares"]

        hit_sl = price <= sl
        hit_tp = price >= tp

        if hit_sl or hit_tp:
            exit_price = sl if hit_sl else tp
            exit_type  = "SL" if hit_sl else "TP"
            pnl = (exit_price - entry) * shares
            self.risk.update_capital(pnl)
            del self.risk.open_positions[symbol]
            del self._sl_tp[symbol]

            result = {
                "checked":     True,
                "closed":      True,
                "symbol":      symbol,
                "exit_type":   exit_type,
                "entry_price": entry,
                "exit_price":  round(exit_price, 4),
                "current_price": round(price, 4),
                "pnl":         round(pnl, 2),
                "timestamp":   datetime.utcnow().isoformat(),
            }
            trade_logger.log_trade({**result, "type": "CLOSE"})
            emoji = "✓" if pnl > 0 else "✗"
            log.info(
                f"{emoji} {exit_type} alcanzado: {symbol} "
                f"entry={entry:.2f} exit={exit_price:.2f} PnL=${pnl:.2f}"
            )
            return result

        # Posición abierta, SL/TP no tocados aún
        unrealized = (price - entry) * shares
        log.info(
            f"{symbol} posición abierta | precio={price:.2f} "
            f"SL={sl:.2f} TP={tp:.2f} PnL_no_realizado=${unrealized:.2f}"
        )
        return {
            "checked":       True,
            "closed":        False,
            "current_price": round(price, 4),
            "sl":            sl,
            "tp":            tp,
            "unrealized_pnl": round(unrealized, 2),
        }

    # ── Cierre manual ───────────────────────────────────────────────────────

    def close_position(self, symbol: str) -> dict:
        """Cierra manualmente una posición abierta al precio de mercado."""
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
        self._sl_tp.pop(symbol, None)

        result = {
            "symbol":      symbol,
            "entry_price": entry["price"],
            "exit_price":  round(price, 4),
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl / entry["size_usd"] * 100, 2),
            "timestamp":   datetime.utcnow().isoformat(),
        }
        trade_logger.log_trade({**result, "type": "CLOSE_MANUAL"})
        log.info(f"POSICIÓN CERRADA MANUAL: {symbol} PnL=${pnl:.2f} ({result['pnl_pct']:.1f}%)")
        return result

    # ── Portfolio ───────────────────────────────────────────────────────────

    def get_portfolio(self) -> dict:
        positions_detail = {}
        for sym, pos in self.risk.open_positions.items():
            price = get_latest_price(sym)
            unrealized = (price - pos["price"]) * pos["shares"] if price > 0 else 0
            positions_detail[sym] = {
                "entry":       pos["price"],
                "current":     round(price, 4),
                "unrealized":  round(unrealized, 2),
                "sl":          self._sl_tp.get(sym, {}).get("sl"),
                "tp":          self._sl_tp.get(sym, {}).get("tp"),
            }
        return {
            "status":           self.risk.get_status(),
            "positions":        list(self.risk.open_positions.keys()),
            "positions_detail": positions_detail,
            "n_trades":         len(self.trades),
        }
