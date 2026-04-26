import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import LOGS_DIR, QUOTE_ASSET
from logs.logger import get_logger

log = get_logger("ledger")
LEDGER_PATH = LOGS_DIR / "order_ledger.jsonl"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        # Binance suele enviar ms
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    text = str(value or "").strip()
    return text or _utc_now_iso()


def _iso_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float
    pnl_quote: float
    fee_quote: float
    opened_at: str
    closed_at: str

    @property
    def return_pct(self) -> float:
        basis = self.entry_price * self.qty
        if basis <= 0:
            return 0.0
        return self.pnl_quote / basis


class OrderLedger:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or LEDGER_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.orders: dict[str, dict[str, Any]] = {}
        self.orders_by_exchange_id: dict[str, str] = {}
        self.positions: dict[str, dict[str, Any]] = {}
        self.closed_trades: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []
        self.processed_event_ids: set[str] = set()
        self.processed_stream_event_ids: set[str] = set()
        self._load_existing()

    def _load_existing(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open() as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    event_id = str(event.get("event_id") or "")
                    if event_id:
                        self.processed_event_ids.add(event_id)
        except (OSError, ValueError) as exc:
            log.warning(f"No se pudo precargar el ledger {self.path.name}: {exc}")

    def _write_event(self, event: dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("event_id", f"evt-{len(self.processed_event_ids) + 1}-{abs(hash(json.dumps(event, sort_keys=True, default=str)))}")
        event.setdefault("logged_at", _utc_now_iso())
        event_id = str(event["event_id"])
        if event_id in self.processed_event_ids:
            return
        self.processed_event_ids.add(event_id)
        with self.path.open("a") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True, default=str) + "\n")

    def _new_order(self, client_order_id: str, *, symbol: str, side: str, expected_price: float, requested_qty: float, mode: str) -> dict[str, Any]:
        order = self.orders.get(client_order_id)
        if order:
            return order
        order = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "mode": mode,
            "status": "SUBMITTED",
            "expected_price": expected_price,
            "requested_qty": requested_qty,
            "filled_qty": 0.0,
            "filled_quote": 0.0,
            "avg_fill_price": 0.0,
            "fees": [],
            "fee_quote_total": 0.0,
            "submitted_at": _utc_now_iso(),
            "ack_at": None,
            "filled_at": None,
            "rejected_at": None,
            "events": [],
        }
        self.orders[client_order_id] = order
        return order

    def record_submission(
        self,
        *,
        symbol: str,
        side: str,
        expected_price: float,
        requested_qty: float,
        mode: str,
        client_order_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        with self.lock:
            order = self._new_order(
                client_order_id,
                symbol=symbol,
                side=side,
                expected_price=expected_price,
                requested_qty=requested_qty,
                mode=mode,
            )
            order.update(metadata or {})
            event = {
                "kind": "order_submitted",
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "expected_price": expected_price,
                "requested_qty": requested_qty,
                "mode": mode,
            }
            order["events"].append(event)
            self._write_event(event)
            return dict(order)

    def record_rejection(
        self,
        *,
        symbol: str,
        side: str,
        reason: str,
        signal: Optional[dict[str, Any]] = None,
        client_order_id: Optional[str] = None,
        order_id: Optional[str] = None,
        mode: str = "paper",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        with self.lock:
            client_order_id = client_order_id or f"reject-{symbol}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
            order = self._new_order(
                client_order_id,
                symbol=symbol,
                side=side,
                expected_price=_safe_float((signal or {}).get("price")),
                requested_qty=_safe_float((metadata or {}).get("requested_qty")),
                mode=mode,
            )
            order["status"] = "REJECTED"
            order["rejected_at"] = _utc_now_iso()
            order["rejection_reason"] = reason
            if order_id is not None:
                order["order_id"] = str(order_id)
                self.orders_by_exchange_id[str(order_id)] = client_order_id
            event = {
                "kind": "order_rejected",
                "client_order_id": client_order_id,
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "reason": reason,
                "signal": signal or {},
                "mode": mode,
                "metadata": metadata or {},
            }
            order["events"].append(event)
            self.rejections.append(event)
            self._write_event(event)
            return dict(order)

    def record_rest_ack(
        self,
        client_order_id: str,
        *,
        order_id: Optional[Any],
        status: Optional[str] = None,
        event_time: Optional[Any] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        with self.lock:
            order = self.orders.setdefault(client_order_id, {"client_order_id": client_order_id, "events": [], "fees": []})
            order["order_id"] = str(order_id) if order_id is not None else order.get("order_id")
            if order.get("order_id"):
                self.orders_by_exchange_id[order["order_id"]] = client_order_id
            ack_at = _ensure_iso(event_time)
            order["ack_at"] = order.get("ack_at") or ack_at
            if status:
                order["status"] = status
            event = {
                "kind": "order_ack",
                "client_order_id": client_order_id,
                "order_id": order.get("order_id"),
                "status": order.get("status"),
                "ack_at": ack_at,
                "source": "rest",
                "payload": payload or {},
            }
            order["events"].append(event)
            self._finalize_latencies(order)
            self._write_event(event)
            return dict(order)

    def bootstrap_position(
        self,
        *,
        symbol: str,
        qty: float,
        entry_price: float,
        source: str = "exchange_sync",
        opened_at: Optional[str] = None,
    ) -> None:
        if qty <= 0:
            self.positions.pop(symbol, None)
            return
        with self.lock:
            self.positions[symbol] = {
                "symbol": symbol,
                "qty": qty,
                "avg_entry_price": entry_price,
                "realized_pnl_quote": self.positions.get(symbol, {}).get("realized_pnl_quote", 0.0),
                "opened_at": opened_at or _utc_now_iso(),
                "last_updated_at": _utc_now_iso(),
                "source": source,
            }

    def reconcile_positions(self, snapshot_positions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        with self.lock:
            for symbol, snapshot in snapshot_positions.items():
                qty = _safe_float(snapshot.get("shares"))
                entry_price = _safe_float(snapshot.get("entry_price"))
                if qty <= 0:
                    self.positions.pop(symbol, None)
                    continue
                current = self.positions.get(symbol)
                if not current or abs(current.get("qty", 0.0) - qty) > 1e-9:
                    self.bootstrap_position(
                        symbol=symbol,
                        qty=qty,
                        entry_price=entry_price or _safe_float(snapshot.get("last_price")),
                        source=str(snapshot.get("source") or "exchange_sync"),
                        opened_at=snapshot.get("opened_at"),
                    )
            for symbol in list(self.positions):
                if symbol not in snapshot_positions and self.positions[symbol].get("source") == "exchange_sync":
                    self.positions.pop(symbol, None)
            event = {
                "kind": "positions_reconciled",
                "positions": {
                    symbol: {
                        "qty": round(position.get("qty", 0.0), 12),
                        "avg_entry_price": round(position.get("avg_entry_price", 0.0), 12),
                    }
                    for symbol, position in sorted(self.positions.items())
                },
            }
            self._write_event(event)
            return {symbol: dict(position) for symbol, position in self.positions.items()}

    def record_fill(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        quote_qty: Optional[float] = None,
        fee_amount: float = 0.0,
        fee_asset: Optional[str] = None,
        order_id: Optional[Any] = None,
        event_time: Optional[Any] = None,
        status: str = "FILLED",
        source: str = "synthetic",
    ) -> dict[str, Any]:
        with self.lock:
            order = self._new_order(
                client_order_id,
                symbol=symbol,
                side=side,
                expected_price=price,
                requested_qty=qty,
                mode=str(source),
            )
            if order_id is not None:
                order["order_id"] = str(order_id)
                self.orders_by_exchange_id[str(order_id)] = client_order_id
            if not order.get("ack_at"):
                order["ack_at"] = _ensure_iso(event_time)

            fee_asset = fee_asset or QUOTE_ASSET
            fill_qty = max(qty, 0.0)
            fill_quote = quote_qty if quote_qty is not None else (fill_qty * price)
            fee_quote = fee_amount if fee_asset == QUOTE_ASSET else 0.0

            order["filled_qty"] = _safe_float(order.get("filled_qty")) + fill_qty
            order["filled_quote"] = _safe_float(order.get("filled_quote")) + fill_quote
            order["avg_fill_price"] = (
                order["filled_quote"] / order["filled_qty"] if order["filled_qty"] > 0 else 0.0
            )
            order["status"] = status
            order["filled_at"] = _ensure_iso(event_time)
            order["last_event_time"] = order["filled_at"]
            order["fee_quote_total"] = _safe_float(order.get("fee_quote_total")) + fee_quote
            order.setdefault("fees", []).append(
                {
                    "asset": fee_asset,
                    "amount": fee_amount,
                    "event_time": order["filled_at"],
                }
            )
            fill_event = {
                "kind": "fill",
                "client_order_id": client_order_id,
                "order_id": order.get("order_id"),
                "symbol": symbol,
                "side": side,
                "qty": fill_qty,
                "price": price,
                "quote_qty": fill_quote,
                "fee_amount": fee_amount,
                "fee_asset": fee_asset,
                "status": status,
                "source": source,
                "event_time": order["filled_at"],
            }
            order["events"].append(fill_event)
            self._apply_position_fill(
                symbol=symbol,
                side=side,
                qty=fill_qty,
                price=price,
                fee_amount=fee_amount,
                fee_asset=fee_asset,
                event_time=order["filled_at"],
            )
            self._finalize_latencies(order)
            self._write_event(fill_event)
            return dict(order)

    def apply_execution_report(self, report: dict[str, Any]) -> Optional[dict[str, Any]]:
        if str(report.get("e") or "") != "executionReport":
            return None

        order_id = str(report.get("i") or "")
        client_order_id = str(report.get("c") or "") or self.orders_by_exchange_id.get(order_id) or order_id
        event_id = "|".join(
            [
                str(report.get("E") or ""),
                order_id,
                client_order_id,
                str(report.get("x") or ""),
                str(report.get("X") or ""),
                str(report.get("l") or ""),
                str(report.get("L") or ""),
                str(report.get("t") or ""),
            ]
        )
        with self.lock:
            if event_id in self.processed_stream_event_ids:
                return None
            self.processed_stream_event_ids.add(event_id)

            symbol = str(report.get("s") or "")
            side = str(report.get("S") or "")
            status = str(report.get("X") or "")
            execution_type = str(report.get("x") or "")
            order = self._new_order(
                client_order_id,
                symbol=symbol,
                side=side,
                expected_price=_safe_float(report.get("p") or report.get("L")),
                requested_qty=_safe_float(report.get("q")),
                mode="live",
            )
            order["order_id"] = order_id or order.get("order_id")
            if order.get("order_id"):
                self.orders_by_exchange_id[order["order_id"]] = client_order_id

            event_time = _ensure_iso(report.get("E") or report.get("T") or report.get("O"))
            if execution_type == "NEW" or status == "NEW":
                order["ack_at"] = order.get("ack_at") or event_time
                order["status"] = status or "NEW"
                ack_event = {
                    "kind": "order_ack",
                    "client_order_id": client_order_id,
                    "order_id": order.get("order_id"),
                    "status": order["status"],
                    "ack_at": order["ack_at"],
                    "source": "executionReport",
                    "event_id": event_id,
                }
                order["events"].append(ack_event)
                self._finalize_latencies(order)
                self._write_event(ack_event)
                return dict(order)

            if execution_type in {"REJECTED", "EXPIRED"}:
                order["status"] = status or execution_type
                order["rejected_at"] = event_time
                order["rejection_reason"] = str(report.get("r") or execution_type)
                rejection = {
                    "kind": "order_rejected",
                    "client_order_id": client_order_id,
                    "order_id": order.get("order_id"),
                    "symbol": symbol,
                    "side": side,
                    "reason": order["rejection_reason"],
                    "source": "executionReport",
                    "event_id": event_id,
                }
                order["events"].append(rejection)
                self.rejections.append(rejection)
                self._write_event(rejection)
                return dict(order)

            if execution_type == "TRADE" or _safe_float(report.get("l")) > 0:
                return self.record_fill(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    qty=_safe_float(report.get("l")),
                    price=_safe_float(report.get("L") or report.get("p")),
                    quote_qty=_safe_float(report.get("Y") or 0.0) or None,
                    fee_amount=_safe_float(report.get("n")),
                    fee_asset=str(report.get("N") or QUOTE_ASSET),
                    order_id=order.get("order_id"),
                    event_time=event_time,
                    status=status or "FILLED",
                    source="executionReport",
                )

            order["status"] = status or order.get("status", "UNKNOWN")
            generic_event = {
                "kind": "order_update",
                "client_order_id": client_order_id,
                "order_id": order.get("order_id"),
                "status": order["status"],
                "execution_type": execution_type,
                "event_time": event_time,
                "event_id": event_id,
            }
            order["events"].append(generic_event)
            self._write_event(generic_event)
            return dict(order)

    def _apply_position_fill(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        fee_amount: float,
        fee_asset: str,
        event_time: str,
    ) -> None:
        if qty <= 0:
            return

        position = self.positions.get(symbol, {
            "symbol": symbol,
            "qty": 0.0,
            "avg_entry_price": 0.0,
            "realized_pnl_quote": 0.0,
            "opened_at": event_time,
            "source": "ledger",
        })
        fee_quote = fee_amount if fee_asset == QUOTE_ASSET else 0.0

        if side == "BUY":
            effective_qty = qty - fee_amount if fee_asset == symbol.replace(QUOTE_ASSET, "") else qty
            effective_qty = max(effective_qty, 0.0)
            total_cost = (position["qty"] * position["avg_entry_price"]) + (qty * price) + fee_quote
            new_qty = position["qty"] + effective_qty
            position["qty"] = new_qty
            position["avg_entry_price"] = total_cost / new_qty if new_qty > 0 else 0.0
            position["opened_at"] = position.get("opened_at") or event_time
        else:
            sell_qty = min(qty, position.get("qty", 0.0))
            if sell_qty <= 0:
                return
            pnl_quote = (sell_qty * price) - (sell_qty * position["avg_entry_price"]) - fee_quote
            position["qty"] = max(position["qty"] - sell_qty, 0.0)
            position["realized_pnl_quote"] = _safe_float(position.get("realized_pnl_quote")) + pnl_quote
            closed_trade = ClosedTrade(
                symbol=symbol,
                side=side,
                qty=sell_qty,
                entry_price=position["avg_entry_price"],
                exit_price=price,
                pnl_quote=pnl_quote,
                fee_quote=fee_quote,
                opened_at=position.get("opened_at") or event_time,
                closed_at=event_time,
            )
            self.closed_trades.append(closed_trade.__dict__ | {"return_pct": closed_trade.return_pct})
            if position["qty"] <= 1e-12:
                self.positions.pop(symbol, None)
                return

        position["last_updated_at"] = event_time
        self.positions[symbol] = position

    def _finalize_latencies(self, order: dict[str, Any]) -> None:
        submitted_at = _iso_to_dt(order.get("submitted_at"))
        ack_at = _iso_to_dt(order.get("ack_at"))
        filled_at = _iso_to_dt(order.get("filled_at"))
        if submitted_at and ack_at:
            order["latency_submit_to_ack_ms"] = max((ack_at - submitted_at).total_seconds() * 1000.0, 0.0)
        if ack_at and filled_at:
            order["latency_ack_to_fill_ms"] = max((filled_at - ack_at).total_seconds() * 1000.0, 0.0)
        expected_price = _safe_float(order.get("expected_price"))
        avg_fill_price = _safe_float(order.get("avg_fill_price"))
        side = str(order.get("side") or "")
        if expected_price > 0 and avg_fill_price > 0:
            if side == "BUY":
                order["slippage_bps"] = ((avg_fill_price - expected_price) / expected_price) * 10_000.0
            else:
                order["slippage_bps"] = ((expected_price - avg_fill_price) / expected_price) * 10_000.0

    def rebuild_positions_from_fills(self) -> dict[str, dict[str, Any]]:
        rebuilt: dict[str, dict[str, Any]] = {}
        for order in sorted(self.orders.values(), key=lambda item: item.get("submitted_at") or ""):
            side = str(order.get("side") or "")
            symbol = str(order.get("symbol") or "")
            if side not in {"BUY", "SELL"} or not symbol:
                continue
            running = rebuilt.get(symbol, {
                "symbol": symbol,
                "qty": 0.0,
                "avg_entry_price": 0.0,
            })
            for event in order.get("events", []):
                if event.get("kind") != "fill":
                    continue
                qty = _safe_float(event.get("qty"))
                price = _safe_float(event.get("price"))
                fee_amount = _safe_float(event.get("fee_amount"))
                fee_asset = str(event.get("fee_asset") or QUOTE_ASSET)
                if side == "BUY":
                    effective_qty = qty - fee_amount if fee_asset == symbol.replace(QUOTE_ASSET, "") else qty
                    fee_quote = fee_amount if fee_asset == QUOTE_ASSET else 0.0
                    total_cost = (running["qty"] * running["avg_entry_price"]) + (qty * price) + fee_quote
                    running["qty"] += max(effective_qty, 0.0)
                    running["avg_entry_price"] = total_cost / running["qty"] if running["qty"] > 0 else 0.0
                else:
                    running["qty"] = max(running["qty"] - qty, 0.0)
            if running["qty"] > 1e-12:
                rebuilt[symbol] = running
        return rebuilt

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "orders": [dict(order) for order in self.orders.values()],
                "positions": {symbol: dict(position) for symbol, position in self.positions.items()},
                "closed_trades": list(self.closed_trades),
                "rejections": list(self.rejections),
            }
