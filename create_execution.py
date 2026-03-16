from pathlib import Path
base = Path.home() / "Desktop" / "quantbot"

(base / "execution" / "risk.py").write_text('''
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
''')

(base / "execution" / "executor.py").write_text('''
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
''')

(base / "test_full_pipeline.py").write_text('''
from data.market import fetch_ohlcv
from data.news import get_news_sentiment
from models.features import add_technical_features, add_sentiment_features
from models.predictor import TradingPredictor
from execution.executor import PaperTrader

SYMBOL = "AAPL"

print("=" * 50)
print("PIPELINE COMPLETO DE TRADING")
print("=" * 50)

# 1. Datos
print("\\n[1] Descargando datos...")
df   = fetch_ohlcv(SYMBOL, interval="1d", period="180d")
sent = get_news_sentiment(SYMBOL)
df   = add_technical_features(df)
df   = add_sentiment_features(df, sent)
print(f"    {len(df)} filas con {len(df.columns)} features")

# 2. Modelo
print("\\n[2] Entrenando modelo...")
predictor = TradingPredictor(SYMBOL)
stats     = predictor.train(df)
print(f"    Accuracy CV: {stats['accuracy_cv']}")

# 3. Señal
print("\\n[3] Generando señal...")
signal = predictor.predict(df)
print(f"    Señal     : {signal['signal']}")
print(f"    Confianza : {signal['confidence']:.1%}")
print(f"    Prob subir: {signal['prob_up']:.1%}")

# 4. Ejecución
print("\\n[4] Ejecutando trade (paper)...")
trader = PaperTrader()
result = trader.execute_signal(SYMBOL, signal)
if result["executed"]:
    t = result["trade"]
    print(f"    Acción    : {t['action']} {t['symbol']}")
    print(f"    Precio    : ${t['price']:.2f}")
    print(f"    Invertido : ${t['size_usd']:.2f}")
    print(f"    Shares    : {t['shares']:.4f}")
else:
    print(f"    No ejecutado: {result['reason']}")

# 5. Portfolio
print("\\n[5] Estado del portfolio...")
portfolio = trader.get_portfolio()
status    = portfolio["status"]
print(f"    Capital   : ${status['capital']:.2f}")
print(f"    Drawdown  : {status['drawdown']:.1%}")
print(f"    Trades    : {portfolio['n_trades']}")
print(f"    Posiciones: {portfolio['positions']}")

print("\\n" + "=" * 50)
print("SISTEMA COMPLETO FUNCIONANDO")
print("=" * 50)
''')

print("Archivos creados:")
print("  execution/risk.py")
print("  execution/executor.py")
print("  test_full_pipeline.py")
