
from config.settings import (
    INITIAL_CAPITAL, MAX_POSITION_PCT,
    MAX_DRAWDOWN_PCT, KELLY_FRACTION,
    RISK_PER_TRADE, MAX_CONCURRENT_POSITIONS,
)
from logs.logger import get_logger

log = get_logger("risk")


class RiskManager:
    def __init__(self):
        self.initial_capital = INITIAL_CAPITAL
        self.current_capital = INITIAL_CAPITAL
        self.peak_capital    = INITIAL_CAPITAL
        self.open_positions  = {}

    # ── Sizing ────────────────────────────────────────────────────────────────

    def fixed_risk_position_size(self, entry_price: float, sl_price: float) -> float:
        """
        Tamaño de posición basado en riesgo fijo (RISK_PER_TRADE % del capital).

        Fórmula:
            risk_amount  = capital × RISK_PER_TRADE
            stop_pct     = |entry - sl| / entry
            size_usd     = risk_amount / stop_pct
            (limitado a MAX_POSITION_PCT del capital)
        """
        if entry_price <= 0 or sl_price <= 0:
            return 0.0
        stop_pct = abs(entry_price - sl_price) / entry_price
        if stop_pct <= 0:
            return 0.0
        risk_amount = self.current_capital * RISK_PER_TRADE
        size = risk_amount / stop_pct
        size = min(size, self.current_capital * MAX_POSITION_PCT)
        log.info(
            f"FixedRisk: entry={entry_price:.2f} SL={sl_price:.2f} "
            f"stop_pct={stop_pct:.2%} size=${size:.2f}"
        )
        return round(size, 2)

    def kelly_position_size(self, confidence: float, capital: float) -> float:
        """Kelly Criterion fraccionado (fallback si no hay SL definido)."""
        win_prob  = confidence
        loss_prob = 1 - confidence
        kelly_pct = (win_prob - loss_prob) * KELLY_FRACTION
        kelly_pct = min(max(kelly_pct, 0.0), MAX_POSITION_PCT)
        amount    = capital * kelly_pct
        log.info(f"Kelly: conf={confidence:.1%} → size={kelly_pct:.1%} → ${amount:.2f}")
        return round(amount, 2)

    # ── Validación pre-trade ──────────────────────────────────────────────────

    def can_trade(self, symbol: str, signal: dict) -> tuple[bool, str]:
        """
        Comprueba todas las reglas de riesgo antes de abrir posición.
        Devuelve (puede_operar: bool, razón: str)
        """
        direction = signal.get("signal", "HOLD")
        confidence = signal.get("confidence", 0)

        if direction == "HOLD":
            return False, "Señal HOLD"

        if confidence < 0.50:
            return False, f"Confianza insuficiente: {confidence:.1%}"

        drawdown = self._drawdown()
        if drawdown >= MAX_DRAWDOWN_PCT:
            return False, f"Drawdown máximo alcanzado: {drawdown:.1%}"

        if symbol in self.open_positions:
            return False, f"Ya hay posición abierta en {symbol}"

        if len(self.open_positions) >= MAX_CONCURRENT_POSITIONS:
            return False, f"Máximo de posiciones concurrentes alcanzado ({MAX_CONCURRENT_POSITIONS})"

        if self.current_capital < 50:
            return False, "Capital insuficiente (<$50)"

        return True, "OK"

    # ── Capital ───────────────────────────────────────────────────────────────

    def update_capital(self, pnl: float):
        self.current_capital += pnl
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital
        log.info(f"Capital: ${self.current_capital:.2f} | DD: {self._drawdown():.1%}")

    def _drawdown(self) -> float:
        if self.peak_capital <= 0:
            return 0.0
        return (self.peak_capital - self.current_capital) / self.peak_capital

    def get_status(self) -> dict:
        return {
            "capital":   round(self.current_capital, 2),
            "peak":      round(self.peak_capital, 2),
            "drawdown":  round(self._drawdown(), 4),
            "positions": len(self.open_positions),
            "pnl_total": round(self.current_capital - self.initial_capital, 2),
        }
