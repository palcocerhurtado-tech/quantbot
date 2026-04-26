import math
from datetime import datetime, timezone
from typing import Any, Optional


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


class PerformanceTracker:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.equity_curve: list[dict[str, Any]] = []

    def record_equity(self, equity: float, timestamp: Optional[datetime] = None) -> None:
        timestamp = timestamp or datetime.now(timezone.utc)
        self.equity_curve.append(
            {
                "timestamp": timestamp.isoformat(),
                "equity": float(equity),
            }
        )
        if len(self.equity_curve) > 4096:
            self.equity_curve = self.equity_curve[-4096:]

    def _returns(self) -> list[float]:
        returns: list[float] = []
        for previous, current in zip(self.equity_curve, self.equity_curve[1:]):
            prev_equity = _safe_float(previous.get("equity"))
            curr_equity = _safe_float(current.get("equity"))
            if prev_equity <= 0:
                continue
            returns.append((curr_equity - prev_equity) / prev_equity)
        return returns

    def _max_drawdown(self) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for point in self.equity_curve:
            equity = _safe_float(point.get("equity"))
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        return max_drawdown

    def _ratio(self, returns: list[float], *, downside_only: bool = False) -> float:
        if not returns:
            return 0.0
        if downside_only:
            sample = [value for value in returns if value < 0]
            if not sample:
                return 0.0
        else:
            sample = returns

        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in sample) / len(sample)
        if variance <= 0:
            return 0.0
        return mean / math.sqrt(variance)

    def snapshot(self, ledger, equity: float) -> dict[str, float]:
        self.record_equity(equity)
        orders = list(ledger.orders.values())
        closed_trades = list(ledger.closed_trades)
        lat_submit_ack = [
            _safe_float(order.get("latency_submit_to_ack_ms"))
            for order in orders
            if order.get("latency_submit_to_ack_ms") is not None
        ]
        lat_ack_fill = [
            _safe_float(order.get("latency_ack_to_fill_ms"))
            for order in orders
            if order.get("latency_ack_to_fill_ms") is not None
        ]
        slippage_bps = [
            _safe_float(order.get("slippage_bps"))
            for order in orders
            if order.get("slippage_bps") is not None
        ]
        fees_quote = sum(_safe_float(order.get("fee_quote_total")) for order in orders)
        pnl_values = [_safe_float(trade.get("pnl_quote")) for trade in closed_trades]
        win_count = sum(1 for pnl in pnl_values if pnl > 0)
        loss_count = sum(1 for pnl in pnl_values if pnl < 0)
        trade_count = len(pnl_values)
        win_rate = (win_count / trade_count) if trade_count else 0.0
        expectancy = (sum(pnl_values) / trade_count) if trade_count else 0.0
        returns = self._returns()

        return {
            "slippage_bps_avg": sum(slippage_bps) / len(slippage_bps) if slippage_bps else 0.0,
            "fees_quote_total": fees_quote,
            "latency_submit_to_ack_ms_avg": sum(lat_submit_ack) / len(lat_submit_ack) if lat_submit_ack else 0.0,
            "latency_ack_to_fill_ms_avg": sum(lat_ack_fill) / len(lat_ack_fill) if lat_ack_fill else 0.0,
            "win_rate": win_rate,
            "expectancy_quote": expectancy,
            "max_drawdown": self._max_drawdown(),
            "sharpe": self._ratio(returns, downside_only=False),
            "sortino": self._ratio(returns, downside_only=True),
            "closed_trades": trade_count,
            "wins": win_count,
            "losses": loss_count,
        }
