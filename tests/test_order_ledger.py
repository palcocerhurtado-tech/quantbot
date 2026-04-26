import tempfile
import unittest
from pathlib import Path

from execution.ledger import OrderLedger
from execution.metrics import PerformanceTracker


class OrderLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = OrderLedger(path=Path(self.tempdir.name) / "ledger.jsonl")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_partial_fills_and_fees_keep_ledger_consistent(self):
        self.ledger.record_submission(
            symbol="BTCUSDC",
            side="BUY",
            expected_price=100.0,
            requested_qty=3.0,
            mode="live",
            client_order_id="cid-1",
        )
        self.ledger.apply_execution_report(
            {
                "e": "executionReport",
                "E": 1,
                "s": "BTCUSDC",
                "S": "BUY",
                "c": "cid-1",
                "i": 123,
                "x": "NEW",
                "X": "NEW",
                "q": "3",
            }
        )
        self.ledger.apply_execution_report(
            {
                "e": "executionReport",
                "E": 2,
                "s": "BTCUSDC",
                "S": "BUY",
                "c": "cid-1",
                "i": 123,
                "x": "TRADE",
                "X": "PARTIALLY_FILLED",
                "l": "1",
                "L": "101",
                "Y": "101",
                "n": "0.10",
                "N": "USDC",
                "q": "3",
                "t": 1,
            }
        )
        order = self.ledger.apply_execution_report(
            {
                "e": "executionReport",
                "E": 3,
                "s": "BTCUSDC",
                "S": "BUY",
                "c": "cid-1",
                "i": 123,
                "x": "TRADE",
                "X": "FILLED",
                "l": "2",
                "L": "102",
                "Y": "204",
                "n": "0.20",
                "N": "USDC",
                "q": "3",
                "t": 2,
            }
        )

        self.assertIsNotNone(order)
        self.assertEqual(order["status"], "FILLED")
        self.assertAlmostEqual(order["filled_qty"], 3.0)
        self.assertAlmostEqual(order["fee_quote_total"], 0.30)
        self.assertGreaterEqual(order["latency_submit_to_ack_ms"], 0.0)
        self.assertGreaterEqual(order["latency_ack_to_fill_ms"], 0.0)

        rebuilt = self.ledger.rebuild_positions_from_fills()
        self.assertIn("BTCUSDC", rebuilt)
        self.assertAlmostEqual(rebuilt["BTCUSDC"]["qty"], self.ledger.positions["BTCUSDC"]["qty"])
        self.assertAlmostEqual(
            rebuilt["BTCUSDC"]["avg_entry_price"],
            self.ledger.positions["BTCUSDC"]["avg_entry_price"],
        )

    def test_reconcile_positions_bootstraps_exchange_snapshot(self):
        positions = self.ledger.reconcile_positions(
            {
                "ETHUSDC": {
                    "shares": 0.5,
                    "entry_price": 2500.0,
                    "source": "exchange_sync",
                }
            }
        )

        self.assertIn("ETHUSDC", positions)
        self.assertAlmostEqual(positions["ETHUSDC"]["qty"], 0.5)
        self.assertEqual(positions["ETHUSDC"]["source"], "exchange_sync")

    def test_metrics_include_fees_win_rate_and_ratios(self):
        self.ledger.record_submission(
            symbol="SOLUSDC",
            side="BUY",
            expected_price=10.0,
            requested_qty=2.0,
            mode="paper",
            client_order_id="buy-1",
        )
        self.ledger.record_rest_ack("buy-1", order_id="buy-1", status="NEW", event_time=1)
        self.ledger.record_fill(
            client_order_id="buy-1",
            symbol="SOLUSDC",
            side="BUY",
            qty=2.0,
            price=10.0,
            quote_qty=20.0,
            fee_amount=0.1,
            fee_asset="USDC",
            order_id="buy-1",
            event_time=2,
            source="paper",
        )
        self.ledger.record_submission(
            symbol="SOLUSDC",
            side="SELL",
            expected_price=12.0,
            requested_qty=2.0,
            mode="paper",
            client_order_id="sell-1",
        )
        self.ledger.record_rest_ack("sell-1", order_id="sell-1", status="NEW", event_time=3)
        self.ledger.record_fill(
            client_order_id="sell-1",
            symbol="SOLUSDC",
            side="SELL",
            qty=2.0,
            price=12.0,
            quote_qty=24.0,
            fee_amount=0.1,
            fee_asset="USDC",
            order_id="sell-1",
            event_time=4,
            source="paper",
        )

        metrics = PerformanceTracker(initial_capital=100.0)
        metrics.record_equity(100.0)
        metrics.record_equity(104.0)
        metrics.record_equity(103.0)
        snapshot = metrics.snapshot(self.ledger, 103.0)

        self.assertGreater(snapshot["fees_quote_total"], 0.0)
        self.assertEqual(snapshot["closed_trades"], 1)
        self.assertEqual(snapshot["wins"], 1)
        self.assertGreater(snapshot["win_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
