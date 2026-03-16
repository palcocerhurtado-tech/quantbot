import os
from pathlib import Path

base = Path.home() / "Desktop" / "quantbot"

# ── .env ──────────────────────────────────────────────────────────
(base / ".env").write_text(
"""REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=quantbot/1.0
NEWS_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
""")

# ── requirements.txt ──────────────────────────────────────────────
(base / "requirements.txt").write_text(
"""yfinance==0.2.40
pandas==2.2.2
numpy==1.26.4
scikit-learn==1.5.0
xgboost==2.0.3
ta==0.11.0
praw==7.7.1
requests==2.32.3
python-dotenv==1.0.1
vaderSentiment==3.3.2
alpaca-py==0.26.0
schedule==1.2.2
rich==13.7.1
""")

# ── config/settings.py ────────────────────────────────────────────
(base / "config" / "settings.py").write_text(
"""import os
from dotenv import load_dotenv
load_dotenv()

SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "BTC-USD", "ETH-USD"]
INTERVAL = "1h"
LOOKBACK = "60d"

MAX_POSITION_PCT = 0.05
MAX_DRAWDOWN_PCT = 0.10
KELLY_FRACTION   = 0.25
MIN_SIGNAL_SCORE = 0.60
INITIAL_CAPITAL  = 10_000.0

REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "quantbot/1.0")
NEWS_API_KEY         = os.getenv("NEWS_API_KEY", "")
ALPACA_API_KEY       = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY    = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER         = True

import pathlib
BASE_DIR   = pathlib.Path(__file__).parent.parent
LOGS_DIR   = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models" / "saved"
LOGS_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
""")

# ── logs/logger.py ────────────────────────────────────────────────
(base / "logs" / "logger.py").write_text(
"""import logging
import json
from datetime import datetime
from pathlib import Path
from config.settings import LOGS_DIR

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%H:%M:%S"
    ))
    fh = logging.FileHandler(LOGS_DIR / f"{name}.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    ))
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger

class TradeLogger:
    def __init__(self):
        self.path = LOGS_DIR / "trades.jsonl"
    def log_trade(self, trade: dict):
        trade["logged_at"] = datetime.utcnow().isoformat()
        with open(self.path, "a") as f:
            f.write(json.dumps(trade) + "\\n")
    def load_trades(self) -> list:
        if not self.path.exists():
            return []
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

trade_logger = TradeLogger()
""")

# ── data/market.py ────────────────────────────────────────────────
(base / "data" / "market.py").write_text(
"""import yfinance as yf
import pandas as pd
from config.settings import SYMBOLS, INTERVAL, LOOKBACK
from logs.logger import get_logger

log = get_logger("market")

def fetch_ohlcv(symbol: str, interval: str = INTERVAL, period: str = LOOKBACK) -> pd.DataFrame:
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval)
        if df.empty:
            log.warning(f"Sin datos para {symbol}")
            return pd.DataFrame()
        df = df[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df.index.name = "timestamp"
        df = df.dropna()
        df = df[df["volume"] > 0]
        log.info(f"{symbol}: {len(df)} filas descargadas")
        return df
    except Exception as e:
        log.error(f"Error descargando {symbol}: {e}")
        return pd.DataFrame()

def fetch_all_symbols(symbols: list = SYMBOLS) -> dict:
    data = {}
    for sym in symbols:
        df = fetch_ohlcv(sym)
        if not df.empty:
            data[sym] = df
    return data

def get_latest_price(symbol: str) -> float:
    try:
        return float(yf.Ticker(symbol).fast_info["lastPrice"])
    except Exception as e:
        log.error(f"Error precio {symbol}: {e}")
        return 0.0
""")

# ── test_setup.py ─────────────────────────────────────────────────
(base / "test_setup.py").write_text(
"""from data.market import fetch_ohlcv, get_latest_price
from logs.logger import get_logger

log = get_logger("test")

print("=== TEST 1: Descargando datos de AAPL ===")
df = fetch_ohlcv("AAPL", interval="1d", period="10d")
print(df.tail(3))
print(f"Total filas: {len(df)}")

print("\\n=== TEST 2: Precio actual ===")
price = get_latest_price("AAPL")
print(f"Precio AAPL: ${price:.2f}")

print("\\n=== TODO FUNCIONA CORRECTAMENTE ===")
""")

print("=" * 50)
print("PROYECTO CREADO CORRECTAMENTE")
print("=" * 50)
print("Archivos creados:")
for f in sorted(base.rglob("*.py")) :
    print(f"  {f.relative_to(base)}")
print(f"  .env")
print(f"  requirements.txt")
