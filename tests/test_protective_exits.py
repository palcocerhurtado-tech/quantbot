import unittest
from unittest.mock import patch

from execution.executor import PaperTrader


class ProtectiveExitTests(unittest.TestCase):
    def setUp(self):
        self.live_mode_patcher = patch("execution.executor.LIVE_TRADING_ENABLED", False)
        self.live_mode_patcher.start()

    def tearDown(self):
        self.live_mode_patcher.stop()

    def test_buy_size_uses_risk_budget_and_atr(self):
        trader = PaperTrader()
        with patch.object(trader, "_get_market", side_effect=RuntimeError("skip market validation")):
            result = trader.execute_signal(
                "BTCUSDC",
                {
                    "signal": "BUY",
                    "confidence": 0.95,
                    "price": 100.0,
                    "atr": 5.0,
                    "candle_time": "2026-04-10T10:00:00+00:00",
                },
            )

        self.assertTrue(result["executed"])
        self.assertAlmostEqual(result["trade"]["size_usd"], 333.3333333333, places=4)
        self.assertAlmostEqual(result["trade"]["stop_distance"], 9.0, places=6)
        self.assertAlmostEqual(result["trade"]["risk_budget"], 30.0, places=6)

    def test_partial_take_profit_sells_fraction_and_arms_breakeven(self):
        trader = PaperTrader()
        with patch.object(trader, "_get_market", side_effect=RuntimeError("skip market validation")):
            trader.execute_signal(
                "ETHUSDC",
                {
                    "signal": "BUY",
                    "confidence": 0.95,
                    "price": 100.0,
                    "atr": 5.0,
                    "candle_time": "2026-04-10T10:00:00+00:00",
                },
            )

            managed = trader.manage_position(
                "ETHUSDC",
                {
                    "price": 111.0,
                    "atr": 5.0,
                    "candle_time": "2026-04-10T10:15:00+00:00",
                },
            )

        self.assertTrue(managed["executed"])
        self.assertEqual(managed["signal_label"], "TP")
        self.assertIn("ETHUSDC", trader.open_positions)
        self.assertLess(trader.open_positions["ETHUSDC"]["shares"], 3.34)
        self.assertTrue(trader.exit_plans["ETHUSDC"]["partial_taken"])
        self.assertGreaterEqual(
            trader.exit_plans["ETHUSDC"]["stop_price"],
            trader.exit_plans["ETHUSDC"]["entry_price"],
        )

    def test_trailing_stop_closes_position_after_pullback(self):
        trader = PaperTrader()
        with patch.object(trader, "_get_market", side_effect=RuntimeError("skip market validation")):
            trader.execute_signal(
                "SOLUSDC",
                {
                    "signal": "BUY",
                    "confidence": 0.95,
                    "price": 100.0,
                    "atr": 5.0,
                    "candle_time": "2026-04-10T10:00:00+00:00",
                },
            )

            trader.exit_plans["SOLUSDC"]["partial_taken"] = True
            trader.manage_position(
                "SOLUSDC",
                {
                    "price": 115.0,
                    "atr": 5.0,
                    "candle_time": "2026-04-10T10:15:00+00:00",
                },
            )
            stop_price = trader.exit_plans["SOLUSDC"]["stop_price"]
            managed = trader.manage_position(
                "SOLUSDC",
                {
                    "price": stop_price - 0.5,
                    "atr": 5.0,
                    "candle_time": "2026-04-10T10:30:00+00:00",
                },
            )

        self.assertTrue(managed["executed"])
        self.assertEqual(managed["signal_label"], "STOP")
        self.assertNotIn("SOLUSDC", trader.open_positions)
        self.assertNotIn("SOLUSDC", trader.exit_plans)


if __name__ == "__main__":
    unittest.main()
