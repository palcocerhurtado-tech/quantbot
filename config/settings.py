import os
from dotenv import load_dotenv
load_dotenv()

# ── Universo crypto ──────────────────────────────────────────────────────────
QUOTE            = os.getenv("QUOTE_ASSET", "USDT")   # moneda de cotización
TOP_N_SCAN       = int(os.getenv("TOP_N_SCAN",  "40")) # pares a escanear
TOP_N_TRADE      = int(os.getenv("TOP_N_TRADE", "10")) # pares a operar (más líquidos)
BINANCE_API_BASE = "https://api.binance.com"

# ── Ciclos de análisis ────────────────────────────────────────────────────────
# Binance no soporta 90m; usamos "1h" como base del ciclo de 90 minutos
CYCLE_TIMEFRAMES = {
    15:  "15m",
    30:  "30m",
    60:  "1h",
    90:  "1h",   # ciclo cada 90 min analizando velas de 1h
}
OHLCV_LIMIT = 300  # velas a descargar por símbolo por ciclo

# ── Gestión de riesgo ─────────────────────────────────────────────────────────
INITIAL_CAPITAL        = float(os.getenv("INITIAL_CAPITAL", "10000"))
RISK_PER_TRADE         = float(os.getenv("RISK_PER_TRADE",  "0.01"))  # 1 % por trade
MAX_POSITION_PCT       = float(os.getenv("MAX_POSITION_PCT", "0.10")) # máx 10 % por posición
MAX_DRAWDOWN_PCT       = float(os.getenv("MAX_DRAWDOWN_PCT", "0.10")) # pausa si DD > 10 %
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_POSITIONS",   "3"))     # máx posiciones abiertas
KELLY_FRACTION         = 0.25
MIN_SIGNAL_SCORE       = 0.60

# ── Modo de operación ─────────────────────────────────────────────────────────
# "paper" = simulado sin dinero real  |  "live" = órdenes reales en Binance
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

# ── Claves de APIs ────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY",    "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
NEWS_API_KEY       = os.getenv("NEWS_API_KEY",       "")
REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "quantbot/1.0")

# ── Rutas ─────────────────────────────────────────────────────────────────────
SYMBOLS    = []   # ya no se usa; el universo es dinámico desde Binance
INTERVAL   = "15m"
LOOKBACK   = "7d"

import pathlib
BASE_DIR   = pathlib.Path(__file__).parent.parent
LOGS_DIR   = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models" / "saved"
LOGS_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
