import os
import pathlib
from dotenv import load_dotenv
load_dotenv()

# ── Binance ───────────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
BINANCE_BASE_URL   = "https://api.binance.com"
BINANCE_TIMEOUT    = (5, 30)    # (connect_timeout, read_timeout) seconds
BINANCE_TIMEOUT_BULK = (5, 60)  # for /ticker/24hr (large payload)
BINANCE_RECVWINDOW = 5000

# ── Networking / retry ────────────────────────────────────────────────────────
RETRY_TOTAL       = 3   # total retry attempts
RETRY_CONNECT     = 3   # retries on connection/DNS errors
RETRY_BACKOFF     = 2   # exponential backoff factor (2s, 4s, 8s)
CIRCUIT_COOLDOWN  = 60  # seconds to wait after network failure before retrying

# ── Trading ───────────────────────────────────────────────────────────────────
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT",
    "XRPUSDT", "TONUSDT", "BNBUSDT", "ORCAUSDT",
]
TIMEFRAMES       = ["15m", "30m", "1h"]
KLINES_LIMIT     = 300
INITIAL_CAPITAL  = 10_000.0
MAX_POSITION_PCT = 0.05
MAX_DRAWDOWN_PCT = 0.10
KELLY_FRACTION   = 0.25
MIN_SIGNAL_SCORE = 0.60

# ── Legacy / compat ───────────────────────────────────────────────────────────
INTERVAL = "1h"
LOOKBACK = "60d"

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = pathlib.Path(__file__).parent.parent
LOGS_DIR   = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models" / "saved"
LOGS_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
