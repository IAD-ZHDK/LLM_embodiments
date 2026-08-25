from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

try:
    import serial
    import serial.tools.list_ports
except Exception:
    serial = None


class SerialCommunication:
    def __init__(self, callback: Callable[[str], None], config: Dict[str, Any]):
        self.callback = callback
        self.config = config
        self.connected = False
        self.baud_rate = 115200
        self.port = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_reader = threading.Event()
        self._pending_read: Optional[Callable[[Dict[str, str]], None]] = None

    def connect(self) -> Dict[str, Any]:
        if serial is None:
            return {"description": "Connection Status", "value": "Error: pyserial is not installed", "error": True}

        if self.connected and self.port:
            return {"description": "Connection Status", "value": f"Already connected to serial device on {self.port.port}"}

        ports = list(serial.tools.list_ports.comports())
        port_obj = next((p for p in ports if p.manufacturer and "arduino" in p.manufacturer.lower()), None)
        if not port_obj:
            return {"description": "Connection Status", "value": "Error: No serial device found", "error": True}

        try:
            self.port = serial.Serial(port_obj.device, self.baud_rate, timeout=0.2)
            self.connected = True
            self._stop_reader.clear()
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            self.callback("The serial device is connected")
            return {"description": "Connection Status", "value": f"Connected to serial device on {port_obj.device}"}
        except Exception as exc:
            self.connected = False
            return {"description": "Connection Status", "value": f"Error: {exc}", "error": True}

    def checkConection(self) -> Dict[str, Any]:
        return {"description": "Connection Status", "value": "Connected" if self.connected else "Disconnected"}

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.port or not self.connected:
            return {"description": "Writing to Serial", "value": "Error: Serial port not open", "error": True}

        data_to_send = f"{data.get('name', '')}{data.get('value', '')}".strip()
        try:
            self.port.write((data_to_send + "\n").encode("utf-8"))
            return {"description": "Writing to Serial", "value": data_to_send}
        except Exception as exc:
            return {"description": "Writing to Serial", "value": f"Error: {exc}", "error": True}

    def read(self, command: Dict[str, Any]) -> Dict[str, Any]:
        if not self.port or not self.connected:
            return {"description": "response", "value": "Error: Serial port not open", "error": True}

        command_name = str(command.get("name", ""))
        result_holder: Dict[str, Any] = {"done": False, "result": None}

        def _resolve(new_data: Dict[str, str]) -> None:
            result_holder["done"] = True
            result_holder["result"] = {"description": "response", "value": new_data}

        self._pending_read = _resolve
        self.port.write((command_name + "\n").encode("utf-8"))

        timeout = time.time() + 3
        while time.time() < timeout:
            if result_holder["done"]:
                self._pending_read = None
                return result_holder["result"]
            time.sleep(0.02)

        self._pending_read = None
        return {"description": "response", "value": "Error: Serial read timed out", "error": True}

    def close(self) -> None:
        self._stop_reader.set()
        if self.port:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None
        self.connected = False

    def _read_loop(self) -> None:
        while not self._stop_reader.is_set() and self.port:
            try:
                raw = self.port.readline().decode("utf-8", errors="ignore").strip()
                if raw:
                    self.receive(raw)
            except Exception:
                self.connected = False
                break

    def receive(self, new_data: str) -> None:
        parts = new_data.split(":", 1)
        if len(parts) < 2:
            return

        command_name = parts[0]
        value = parts[1].strip()
        update_object = {"description": command_name, "value": value}

        if self._pending_read:
            self._pending_read(update_object)
            return

        notifications = self.config.get("functions", {}).get("notifications", {})
        notify_object = notifications.get(command_name)
        if notify_object:
            payload = {
                "description": command_name,
                "value": value,
                "type": notify_object.get("dataType", "string"),
            }
            self.callback(__import__("json").dumps(payload))
