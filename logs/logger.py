import logging
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
            f.write(json.dumps(trade) + "\n")
    def load_trades(self) -> list:
        if not self.path.exists():
            return []
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

trade_logger = TradeLogger()
