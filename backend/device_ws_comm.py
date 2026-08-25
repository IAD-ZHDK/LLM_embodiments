from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional


class DeviceWebSocketCommunication:
    """Drop-in replacement for SerialCommunication that talks to a WiFi device (e.g. M5Stack) over a WebSocket."""

    def __init__(self, callback: Callable[[str], None], config: Dict[str, Any], submit_coro: Callable[[Any], Any]):
        self.callback = callback
        self.config = config
        self._submit_coro = submit_coro
        self.ws: Optional[Any] = None
        self.connected = False
        self._pending_read: Optional[Callable[[Dict[str, str]], None]] = None

    def attach(self, ws: Any) -> None:
        self.ws = ws
        self.connected = True
        # self.callback("The WiFi device is connected")

    def detach(self) -> None:
        self.ws = None
        self.connected = False

    def connect(self) -> Dict[str, Any]:
        if self.connected:
            return {"description": "Connection Status", "value": "Already connected to WiFi device"}
        return {"description": "Connection Status", "value": "Error: waiting for device to connect over WiFi", "error": True}

    def checkConection(self) -> Dict[str, Any]:
        return {"description": "Connection Status", "value": "Connected" if self.connected else "Disconnected"}

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.connected:
            return {"description": "Writing to Device", "value": "Error: no WiFi device connected", "error": True}

        data_to_send = f"{data.get('name', '')}{data.get('value', '')}".strip()
        payload = {
            "toolCall": {
                "name": data.get("name", ""),
                "value": data.get("value", ""),
                "dataType": data.get("dataType", "string"),
            }
        }
        if not self._send_json(payload):
            return {"description": "Writing to Device", "value": "Error: failed to send to device", "error": True}
        return {"description": "Writing to Device", "value": data_to_send}

    def read(self, command: Dict[str, Any]) -> Dict[str, Any]:
        if not self.connected:
            return {"description": "response", "value": "Error: no WiFi device connected", "error": True}

        command_name = str(command.get("name", ""))
        result_holder: Dict[str, Any] = {"done": False, "result": None}

        def _resolve(new_data: Dict[str, str]) -> None:
            result_holder["done"] = True
            result_holder["result"] = {"description": "response", "value": new_data}

        self._pending_read = _resolve
        sent = self._send_json({"toolCall": {"name": command_name, "value": "", "dataType": "read"}})
        if not sent:
            self._pending_read = None
            return {"description": "response", "value": "Error: failed to send to device", "error": True}

        timeout = time.time() + 3
        while time.time() < timeout:
            if result_holder["done"]:
                self._pending_read = None
                return result_holder["result"]
            time.sleep(0.02)

        self._pending_read = None
        return {"description": "response", "value": "Error: device read timed out", "error": True}

    def close(self) -> None:
        self.connected = False
        self.ws = None

    def receive(self, name: str, value: str) -> None:
        update_object = {"description": name, "value": value}

        if self._pending_read:
            self._pending_read(update_object)
            return

        if not name:
            return

        notifications = self.config.get("functions", {}).get("notifications", {})
        notify_object = notifications.get(name, {})
        payload = {
            "description": name,
            "value": value,
            "type": notify_object.get("dataType", "string") if isinstance(notify_object, dict) else "string",
        }
        print(f"🔔 Device notification: {name} = {value}")
        self.callback(json.dumps(payload))

    def _send_json(self, payload: Dict[str, Any]) -> bool:
        # write()/read() run on a worker thread, so the send is bridged onto the asyncio loop and awaited synchronously.
        if not self.ws:
            return False
        future = self._submit_coro(self.ws.send_text(json.dumps(payload)))
        if future is None:
            return False
        try:
            future.result(timeout=3)
            return True
        except Exception:
            return False
