import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.executor import PaperTrader
from execution.ledger import OrderLedger
from execution.risk import RiskManager


class FakeTrader:
    def __init__(self, capital=10_000.0, open_positions=None):
        self._capital = capital
        self.open_positions = open_positions or {}

    def get_portfolio(self):
        return {
            "status": {"capital": self._capital},
            "positions": len(self.open_positions),
        }


class RiskChecksTests(unittest.TestCase):
    def test_symbol_exposure_limit_rejects_trade(self):
        risk = RiskManager()
        trader = FakeTrader(capital=10_000.0, open_positions={})
        signal = {"signal": "BUY", "confidence": 0.95, "price": 100.0}

        can_trade, reason = risk.can_trade(
            "BTCUSDC",
            signal,
            trader=trader,
            context={"price": 100.0, "requested_qty": 6.0, "notional": 600.0},
        )

        self.assertFalse(can_trade)
        self.assertIn("símbolo", reason.lower())

    def test_spread_guardrail_rejects_trade(self):
        risk = RiskManager()
        trader = FakeTrader(capital=10_000.0, open_positions={})
        signal = {"signal": "BUY", "confidence": 0.95, "price": 100.0}

        can_trade, reason = risk.can_trade(
            "ETHUSDC",
            signal,
            trader=trader,
            context={"price": 100.0, "requested_qty": 0.1, "notional": 10.0, "spread_pct": 0.10},
        )

        self.assertFalse(can_trade)
        self.assertIn("spread", reason.lower())

    def test_executor_logs_rejection_reason_with_client_order_id(self):
        with patch("execution.executor.LIVE_TRADING_ENABLED", False):
            trader = PaperTrader()
        with tempfile.TemporaryDirectory() as tmpdir:
            trader.ledger = OrderLedger(path=Path(tmpdir) / "ledger.jsonl")
            trader.open_positions["BTCUSDC"] = {
                "symbol": "BTCUSDC",
                "entry_price": 100.0,
                "shares": 0.1,
                "size_usd": 10.0,
                "opened_at": "2026-01-01T00:00:00+00:00",
                "last_price": 100.0,
                "source": "paper",
            }
            result = trader.execute_signal("BTCUSDC", {"signal": "BUY", "confidence": 0.95, "price": 100.0})

            self.assertFalse(result["executed"])
            self.assertIn("client_order_id", result)
            self.assertTrue(trader.ledger.rejections)
            rejection = trader.ledger.rejections[-1]
            self.assertEqual(rejection["client_order_id"], result["client_order_id"])
            self.assertIn("posición", rejection["reason"].lower())


if __name__ == "__main__":
    unittest.main()
