import os
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
