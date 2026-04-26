import json
import threading
import time
from typing import Any, Callable, Optional

import websocket

from config.settings import BINANCE_API_KEY, USER_STREAM_STALE_SECONDS
from execution.ratelimit import request_json
from logs.logger import get_logger

log = get_logger("user_stream")


class BinanceUserDataStream:
    def __init__(
        self,
        *,
        rest_base_url: str,
        ws_base_url: str = "wss://stream.binance.com:9443/ws",
        api_key: str = BINANCE_API_KEY,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.rest_base_url = rest_base_url.rstrip("/")
        self.ws_base_url = ws_base_url.rstrip("/")
        self.api_key = api_key
        self.on_event = on_event
        self.listen_key: Optional[str] = None
        self.ws_app = None
        self.thread: Optional[threading.Thread] = None
        self.keepalive_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.last_event_at = 0.0

    def start(self) -> bool:
        if not self.api_key:
            log.warning("User Data Stream no iniciado: falta API key")
            return False
        if self.thread and self.thread.is_alive():
            return True

        try:
            payload = request_json(
                f"{self.rest_base_url}/userDataStream",
                method="POST",
                headers={"X-MBX-APIKEY": self.api_key},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"No se pudo abrir listenKey de Binance: {exc}")
            return False

        self.listen_key = str(payload.get("listenKey") or "").strip()
        if not self.listen_key:
            log.warning("Binance devolvió listenKey vacío")
            return False

        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_ws, name="binance-user-stream", daemon=True)
        self.thread.start()
        self.keepalive_thread = threading.Thread(target=self._keepalive_loop, name="binance-listen-key-keepalive", daemon=True)
        self.keepalive_thread.start()
        log.info("User Data Stream de Binance iniciado")
        return True

    def stop(self) -> None:
        self.stop_event.set()
        if self.ws_app is not None:
            try:
                self.ws_app.close()
            except Exception:  # noqa: BLE001
                pass

    def is_healthy(self) -> bool:
        return bool(self.listen_key) and (time.time() - self.last_event_at) <= USER_STREAM_STALE_SECONDS

    def _keepalive_loop(self) -> None:
        while not self.stop_event.wait(30 * 60):
            if not self.listen_key:
                return
            try:
                request_json(
                    f"{self.rest_base_url}/userDataStream",
                    method="PUT",
                    params={"listenKey": self.listen_key},
                    headers={"X-MBX-APIKEY": self.api_key},
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"Keepalive listenKey falló: {exc}")

    def _run_ws(self) -> None:
        if not self.listen_key:
            return

        def _on_message(_ws, message: str):
            try:
                payload = json.loads(message)
            except ValueError:
                log.warning("Mensaje WS inválido de Binance")
                return
            self.last_event_at = time.time()
            if self.on_event:
                self.on_event(payload)

        def _on_error(_ws, error):
            log.warning(f"User Data Stream error: {error}")

        def _on_close(_ws, status_code, msg):
            log.warning(f"User Data Stream cerrado: {status_code} {msg}")

        url = f"{self.ws_base_url}/{self.listen_key}"
        while not self.stop_event.is_set():
            self.ws_app = websocket.WebSocketApp(
                url,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            try:
                self.ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"Reconectando User Data Stream tras error: {exc}")
            if self.stop_event.wait(5):
                return
