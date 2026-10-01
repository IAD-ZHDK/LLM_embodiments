from __future__ import annotations

import base64
import json
import threading
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
        self._camera_image_pending = False
        self._camera_image_event = threading.Event()
        self._camera_image_result: Optional[Dict[str, Any]] = None
        self._camera_image_expected = 0
        self._camera_image_width = 0
        self._camera_image_height = 0
        self._camera_image_buffer = bytearray()

    def attach(self, ws: Any) -> None:
        self.ws = ws
        self.connected = True
        # self.callback("The WiFi device is connected")

    def detach(self) -> None:
        self.ws = None
        self.connected = False
        if self._camera_image_pending:
            self._complete_camera_image({"description": "Error", "value": "device disconnected during camera capture"})

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

    def read_image(self, command: Dict[str, Any]) -> Dict[str, Any]:
        if not self.connected:
            return {"description": "Error", "value": "no WiFi device connected", "error": True}
        if self._camera_image_pending:
            return {"description": "Error", "value": "another camera capture is already in progress", "error": True}

        self._camera_image_event.clear()
        self._camera_image_result = None
        self._camera_image_expected = 0
        self._camera_image_buffer.clear()
        self._camera_image_pending = True
        sent = self._send_json({
            "toolCall": {
                "name": command.get("name", ""),
                "value": "",
                "dataType": "read",
                "responseType": command.get("responseType", "image/rgb565"),
            }
        })
        if not sent:
            self._complete_camera_image({"description": "Error", "value": "failed to request a camera image from the device"})
        elif not self._camera_image_event.wait(timeout=20):
            self._complete_camera_image({"description": "Error", "value": "camera image response timed out"})

        result = self._camera_image_result or {"description": "Error", "value": "camera image response failed"}
        self._camera_image_pending = False
        self._camera_image_expected = 0
        self._camera_image_buffer.clear()
        return result

    def begin_camera_image(self, metadata: Dict[str, Any]) -> bool:
        if not self._camera_image_pending:
            return False

        try:
            length = int(metadata.get("length", 0))
            width = int(metadata.get("width", 0))
            height = int(metadata.get("height", 0))
        except (TypeError, ValueError):
            self._complete_camera_image({"description": "Error", "value": "invalid camera image metadata"})
            return True

        if (
            metadata.get("format") != "rgb565"
            or width < 1
            or width > 640
            or height < 1
            or height > 480
            or length != width * height * 2
            or length > 640 * 480 * 2
        ):
            self._complete_camera_image({"description": "Error", "value": "unsupported or invalid camera image format"})
            return True

        self._camera_image_expected = length
        self._camera_image_width = width
        self._camera_image_height = height
        self._camera_image_buffer.clear()
        return True

    def receive_camera_image_chunk(self, chunk: bytes) -> bool:
        if not self._camera_image_pending or not self._camera_image_expected:
            return False
        if len(self._camera_image_buffer) + len(chunk) > self._camera_image_expected:
            self._complete_camera_image({"description": "Error", "value": "camera image exceeded its declared size"})
            return True

        self._camera_image_buffer.extend(chunk)
        if len(self._camera_image_buffer) == self._camera_image_expected:
            try:
                import cv2
                import numpy as np

                rgb565 = np.frombuffer(bytes(self._camera_image_buffer), dtype=np.uint8).reshape(
                    self._camera_image_height, self._camera_image_width, 2
                )
                bgr = cv2.cvtColor(rgb565, cv2.COLOR_BGR5652BGR)
                encoded, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not encoded:
                    raise ValueError("JPEG encoding failed")
                self._complete_camera_image({
                    "description": "Camera image",
                    "value": "M5Stack photo captured",
                    "image_base64": base64.b64encode(jpeg.tobytes()).decode("ascii"),
                    "mime_type": "image/jpeg",
                })
            except Exception as exc:
                self._complete_camera_image({"description": "Error", "value": f"could not convert camera image: {exc}"})
        return True

    def fail_camera_image(self, message: str) -> None:
        if self._camera_image_pending:
            self._complete_camera_image({"description": "Error", "value": message})

    def _complete_camera_image(self, result: Dict[str, Any]) -> None:
        self._camera_image_result = result
        self._camera_image_pending = False
        self._camera_image_event.set()

    def close(self) -> None:
        self.detach()

    def send_response(self, message: str) -> bool:
        """Display an assistant reply on this WiFi device."""
        return self._send_json({"assistantResponse": message}, wait=False)

    def send_audio_state(self, mic_muted: bool, speaker_muted: bool) -> bool:
        return self._send_json(
            {"audioState": {"micMuted": bool(mic_muted), "speakerMuted": bool(speaker_muted)}},
            wait=False,
        )

    def send_audio_start(self, sample_rate: int) -> bool:
        return self._send_json({"audioStart": {"sampleRate": sample_rate, "format": "pcm_s16le", "channels": 1}}, wait=False)

    def send_audio(self, audio: bytes) -> bool:
        if not self.ws:
            return False
        future = self._submit_coro(self.ws.send_bytes(audio))
        return future is not None

    def send_audio_end(self) -> bool:
        return self._send_json({"audioEnd": True}, wait=False)

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

    def _send_json(self, payload: Dict[str, Any], wait: bool = True) -> bool:
        # write()/read() run on a worker thread, so the send is bridged onto the asyncio loop and awaited synchronously.
        if not self.ws:
            return False
        future = self._submit_coro(self.ws.send_text(json.dumps(payload)))
        if future is None:
            return False
        if not wait:
            return True
        try:
            future.result(timeout=3)
            return True
        except Exception:
            return False
