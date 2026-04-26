import json
import logging
from datetime import datetime, timezone

from config.settings import LOGS_DIR


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    file_handler = logging.FileHandler(LOGS_DIR / f"{name}.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger


class TradeLogger:
    def __init__(self):
        self.path = LOGS_DIR / "trades.jsonl"

    def log_trade(self, trade: dict):
        trade["logged_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "a") as handle:
            handle.write(json.dumps(trade) + "\n")

    def load_trades(self) -> list:
        if not self.path.exists():
            return []
        with open(self.path) as handle:
            return [json.loads(line) for line in handle if line.strip()]


trade_logger = TradeLogger()
