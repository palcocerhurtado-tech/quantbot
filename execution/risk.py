
from config.settings import (
    INITIAL_CAPITAL, MAX_POSITION_PCT,
    MAX_DRAWDOWN_PCT, KELLY_FRACTION
)
from logs.logger import get_logger

log = get_logger("risk")

class RiskManager:
    def __init__(self):
        self.initial_capital = INITIAL_CAPITAL
        self.current_capital = INITIAL_CAPITAL
        self.peak_capital    = INITIAL_CAPITAL
        self.open_positions  = {}

    def kelly_position_size(self, confidence: float, capital: float) -> float:
        """
        Kelly Criterion fraccionado.
        confidence = probabilidad de acertar (ej: 0.78)
        Devuelve cuánto dinero apostar.
        """
        win_prob  = confidence
        loss_prob = 1 - confidence
        # Asumimos ratio ganancia/pérdida de 1:1 para simplificar
        kelly_pct = (win_prob - loss_prob)
        # Aplicamos fracción de Kelly para ser conservadores
        kelly_pct = kelly_pct * KELLY_FRACTION
        # Nunca más del máximo por posición
        kelly_pct = min(kelly_pct, MAX_POSITION_PCT)
        # Nunca negativo
        kelly_pct = max(kelly_pct, 0.0)
        amount    = capital * kelly_pct
        log.info(f"Kelly: confianza={confidence:.1%} → size={kelly_pct:.1%} → ${amount:.2f}")
        return round(amount, 2)

    def can_trade(self, symbol: str, signal: dict) -> tuple:
        """
        Comprueba todas las reglas de riesgo antes de operar.
        Devuelve (puede_operar: bool, razón: str)
        """
        confidence = signal.get("confidence", 0)
        direction  = signal.get("signal", "HOLD")

        # Regla 1 — señal debe ser BUY o SELL
        if direction == "HOLD":
            return False, "Señal HOLD, no operar"

        # Regla 2 — confianza mínima
        if confidence < 0.55:
            return False, f"Confianza insuficiente: {confidence:.1%}"

        # Regla 3 — drawdown máximo
        drawdown = (self.peak_capital - self.current_capital) / self.peak_capital
        if drawdown >= MAX_DRAWDOWN_PCT:
            return False, f"Drawdown máximo alcanzado: {drawdown:.1%}"

        # Regla 4 — no abrir posición si ya tenemos una en ese símbolo
        if symbol in self.open_positions:
            return False, f"Ya hay posición abierta en {symbol}"

        # Regla 5 — capital mínimo para operar
        if self.current_capital < 100:
            return False, "Capital insuficiente (<$100)"

        return True, "OK"

    def update_capital(self, pnl: float):
        """Actualiza el capital tras cerrar una posición."""
        self.current_capital += pnl
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital
        drawdown = (self.peak_capital - self.current_capital) / self.peak_capital
        log.info(f"Capital: ${self.current_capital:.2f} | Drawdown: {drawdown:.1%}")

    def get_status(self) -> dict:
        drawdown = (self.peak_capital - self.current_capital) / self.peak_capital
        return {
            "capital":    round(self.current_capital, 2),
            "peak":       round(self.peak_capital, 2),
            "drawdown":   round(drawdown, 4),
            "positions":  len(self.open_positions),
            "pnl_total":  round(self.current_capital - self.initial_capital, 2)
        }
