import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]


BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models" / "saved"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MAX_POSITION_PCT = _env_float("QUANTBOT_MAX_POSITION_PCT", 0.05)
MAX_DRAWDOWN_PCT = _env_float("QUANTBOT_MAX_DRAWDOWN_PCT", 0.10)
MAX_PORTFOLIO_EXPOSURE_PCT = _env_float("QUANTBOT_MAX_PORTFOLIO_EXPOSURE_PCT", 0.95)
MAX_SPREAD_PCT = _env_float("QUANTBOT_MAX_SPREAD_PCT", 0.015)
MAX_SLIPPAGE_BPS = _env_float("QUANTBOT_MAX_SLIPPAGE_BPS", 80.0)
KELLY_FRACTION = _env_float("QUANTBOT_KELLY_FRACTION", 0.25)
MIN_SIGNAL_SCORE = _env_float("QUANTBOT_MIN_SIGNAL_SCORE", 0.60)
INITIAL_CAPITAL = _env_float("QUANTBOT_INITIAL_CAPITAL", 10_000.0)
TRADE_SIZE_USD = _env_float("QUANTBOT_TRADE_SIZE_USD", 6.0)
RISK_PER_TRADE = _env_float("QUANTBOT_RISK_PER_TRADE_PCT", _env_float("RISK_PER_TRADE", 0.01))
STOP_ATR_MULT = _env_float("QUANTBOT_STOP_ATR_MULT", 1.8)
MIN_STOP_DISTANCE_PCT = _env_float("QUANTBOT_MIN_STOP_DISTANCE_PCT", 0.008)
BREAKEVEN_TRIGGER_R = _env_float("QUANTBOT_BREAKEVEN_TRIGGER_R", 1.0)
TRAILING_TRIGGER_R = _env_float("QUANTBOT_TRAILING_TRIGGER_R", 1.5)
TRAILING_ATR_MULT = _env_float("QUANTBOT_TRAILING_ATR_MULT", 2.2)
PARTIAL_TAKE_PROFIT_R = _env_float("QUANTBOT_PARTIAL_TAKE_PROFIT_R", 1.2)
PARTIAL_TAKE_PROFIT_PCT = _env_float("QUANTBOT_PARTIAL_TAKE_PROFIT_PCT", 0.50)
MAX_CONCURRENT_POSITIONS = _env_int("QUANTBOT_MAX_POSITIONS", _env_int("MAX_POSITIONS", 3))
REQUEST_TIMEOUT_SECONDS = _env_float("QUANTBOT_REQUEST_TIMEOUT", 12.0)
RETRY_MAX_ATTEMPTS = _env_int("QUANTBOT_RETRY_MAX_ATTEMPTS", 5)
RETRY_BACKOFF_BASE_SECONDS = _env_float("QUANTBOT_RETRY_BACKOFF_BASE_SECONDS", 0.75)
RETRY_BACKOFF_MAX_SECONDS = _env_float("QUANTBOT_RETRY_BACKOFF_MAX_SECONDS", 30.0)
MAX_WORKERS = _env_int("QUANTBOT_MAX_WORKERS", 10)
TOP_CRYPTO_LIMIT = _env_int("QUANTBOT_TOP_CRYPTO_LIMIT", 333)
SCAN_EVERY_MINUTES = _env_int("QUANTBOT_SCAN_EVERY_MINUTES", 13)
QUOTE_ASSET = os.getenv("QUANTBOT_QUOTE_ASSET", os.getenv("QUOTE_ASSET", "USDC")).upper()
QUOTE = QUOTE_ASSET
TOP_N_SCAN = _env_int("QUANTBOT_TOP_N_SCAN", _env_int("TOP_N_SCAN", TOP_CRYPTO_LIMIT))
TOP_N_TRADE = _env_int("QUANTBOT_TOP_N_TRADE", _env_int("TOP_N_TRADE", 10))
TRADING_MODE = os.getenv("QUANTBOT_TRADING_MODE", "paper").strip().lower()
SYMBOLS = _env_list(
    "QUANTBOT_SYMBOLS",
    [f"BTC{QUOTE_ASSET}", f"ETH{QUOTE_ASSET}", f"SOL{QUOTE_ASSET}", f"BNB{QUOTE_ASSET}", f"XRP{QUOTE_ASSET}"],
)
INTERVAL = os.getenv("QUANTBOT_INTERVAL", "1h")
LOOKBACK = os.getenv("QUANTBOT_LOOKBACK", "60d")
CYCLE_TIMEFRAMES = {15: "15m", 30: "30m", 60: "1h", 90: "1h"}
OHLCV_LIMIT = _env_int("QUANTBOT_OHLCV_LIMIT", 300)

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "quantbot/1.0")
NEWS_API_KEY = os.getenv("QUANTBOT_NEWS_API_KEY", os.getenv("NEWS_API_KEY", ""))
NEWS_API_URL = os.getenv("QUANTBOT_NEWS_API_URL", "https://newsapi.org/v2/everything")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = _env_bool("ALPACA_PAPER", True)
BINANCE_API_KEY = os.getenv("QUANTBOT_BINANCE_API_KEY", os.getenv("BINANCE_API_KEY", ""))
BINANCE_SECRET_KEY = os.getenv("QUANTBOT_BINANCE_SECRET_KEY", os.getenv("BINANCE_SECRET_KEY", ""))
BINANCE_BASE_URL = os.getenv("QUANTBOT_BINANCE_BASE_URL", "https://api.binance.com/api/v3")
BINANCE_API_BASE = BINANCE_BASE_URL.removesuffix("/api/v3")
LIVE_TRADING_ENABLED = TRADING_MODE == "live"
USER_STREAM_STALE_SECONDS = _env_float("QUANTBOT_USER_STREAM_STALE_SECONDS", 75 * 60)
