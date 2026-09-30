#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None

try:
    import websockets
except Exception as exc:  # pragma: no cover
    print("Missing dependency 'websockets'. Install: pip install -r device_dummy/requirements.txt", file=sys.stderr)
    raise


@dataclass
class DummyTool:
    name: str
    description: str
    data_type: str = "string"
    comm_type: str = "readWrite"


class AudioPlayback:
    def __init__(self, enabled: bool, verbose: bool = False) -> None:
        self.enabled = enabled
        self.verbose = verbose
        self.sample_rate = 16000
        self.channels = 1
        self.dtype = "int16"
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._stream = None
        self._play_task: Optional[asyncio.Task[Any]] = None
        self._running = False

    def start(self, sample_rate: int, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._running = True
        if self.verbose:
            print(f"[audio] start playback sr={sample_rate} ch={channels}")

        if not self.enabled:
            return
        if sd is None or np is None:
            print("[audio] sounddevice/numpy unavailable, skipping speaker playback")
            return

        if self._play_task is None or self._play_task.done():
            self._play_task = asyncio.create_task(self._playback_worker())

    async def _playback_worker(self) -> None:
        def callback(outdata: Any, frames: int, _time_info: Any, status: Any) -> None:
            if status and self.verbose:
                print(f"[audio] playback status: {status}")

            needed_bytes = frames * self.channels * 2
            try:
                chunk = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                outdata[:] = 0
                return

            if len(chunk) < needed_bytes:
                chunk = chunk + b"\x00" * (needed_bytes - len(chunk))
            elif len(chunk) > needed_bytes:
                chunk = chunk[:needed_bytes]

            data = np.frombuffer(chunk, dtype=np.int16)
            if self.channels > 1:
                data = data.reshape(-1, self.channels)
            else:
                data = data.reshape(-1, 1)
            outdata[:] = data

        blocksize = max(160, int(self.sample_rate * 0.02))

        try:
            with sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                blocksize=blocksize,
                callback=callback,
            ):
                while self._running:
                    await asyncio.sleep(0.02)
        except Exception as exc:
            print(f"[audio] playback error: {exc}")

    async def push(self, chunk: bytes) -> None:
        if not self._running:
            return
        if self._queue.full():
            try:
                _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(chunk)

    async def stop(self) -> None:
        self._running = False
        if self.verbose:
            print("[audio] stop playback")
        if self._play_task and not self._play_task.done():
            try:
                await asyncio.wait_for(self._play_task, timeout=0.5)
            except Exception:
                self._play_task.cancel()
        self._play_task = None


class DeviceDummy:
    def __init__(
        self,
        url: str,
        name: str,
        enable_mic: bool,
        synthetic_mic: bool,
        enable_speaker: bool,
        sample_rate: int,
        chunk_ms: int,
        verbose: bool,
    ) -> None:
        self.url = url
        self.name = name
        self.enable_mic = enable_mic
        self.synthetic_mic = synthetic_mic
        self.enable_speaker = enable_speaker
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.verbose = verbose

        self.ws: Optional[Any] = None
        self.running = True
        self.audio_playback = AudioPlayback(enabled=enable_speaker, verbose=verbose)
        self.state: Dict[str, Any] = {
            "led": "off",
            "servo": "90",
            "temperature": "22.3",
            "fan": "off",
        }
        self.tools: List[DummyTool] = [
            DummyTool("set_led", "Set LED state to on/off", "string", "write"),
            DummyTool("set_servo", "Set servo angle 0-180", "integer", "write"),
            DummyTool("set_fan", "Set fan speed level 0-3", "integer", "write"),
            DummyTool("get_temperature", "Read dummy ambient temperature in Celsius", "none", "read"),
            DummyTool("get_status", "Read current dummy device status", "none", "read"),
        ]

    def device_info_payload(self) -> Dict[str, Any]:
        history = [
            {"role": "user", "content": "Hello device."},
            {"role": "assistant", "content": "Dummy device connected and listening."},
        ]

        tool_entries = [
            {
                "name": t.name,
                "description": t.description,
                "dataType": t.data_type,
                "commType": t.comm_type,
            }
            for t in self.tools
        ]

        return {
            "deviceInfo": {
                "persona": (
                    "You are talking to a simulated ESP32 device. "
                    "Use tool calls for hardware actions and keep responses short."
                ),
                "generation": {
                    "model": "gemma4:e4b",
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "top_k": 40,
                    "max_tokens": 1024,
                    "repeat_penalty": 1.1,
                },
                "notificationGuidance": [
                    {
                        "name": "device_alert",
                        "instruction": "Treat this as asynchronous device telemetry and react briefly.",
                    }
                ],
                "tools": tool_entries,
                "history": history,
            }
        }

    async def send_json(self, payload: Dict[str, Any]) -> None:
        if self.ws is None:
            return
        await self.ws.send(json.dumps(payload))
        if self.verbose:
            print(f"-> {json.dumps(payload, ensure_ascii=True)}")

    async def send_notification(self, name: str, value: str) -> None:
        await self.send_json({"notification": {"name": name, "value": value}})

    async def terminal_input_loop(self) -> None:
        loop = asyncio.get_running_loop()
        print("[input] Enter 'temp <value>' to send temperature, or 'quit' to stop.")

        while self.running:
            print("device-dummy> ", end="", flush=True)
            line_ready: asyncio.Future[str] = loop.create_future()
            try:
                stdin_fd = sys.stdin.fileno()
                loop.add_reader(stdin_fd, lambda: line_ready.set_result(sys.stdin.readline()) if not line_ready.done() else None)
            except (AttributeError, NotImplementedError, PermissionError):
                line = await asyncio.to_thread(sys.stdin.readline)
            else:
                try:
                    line = await line_ready
                finally:
                    loop.remove_reader(stdin_fd)

            if not line:
                self.running = False
                if self.ws:
                    await self.ws.close()
                return

            command, _, value = line.strip().partition(" ")
            if command.lower() in ("quit", "exit"):
                self.running = False
                if self.ws:
                    await self.ws.close()
                return

            if command.lower() != "temp" or not value.strip():
                print("Enter a temperature as: temp 23.5")
                continue

            try:
                temperature = float(value)
            except ValueError:
                print("Temperature must be a number, e.g. temp 23.5")
                continue

            if not self.ws:
                print("Not connected to the backend.")
                continue

            self.state["temperature"] = f"{temperature:g}"
            await self.send_notification("device_alert", f"temp={self.state['temperature']}C")
            print(f"[sent] temp={self.state['temperature']}C")

    async def handle_tool_call(self, tool_call: Dict[str, Any]) -> None:
        name = str(tool_call.get("name", "")).strip()
        value = str(tool_call.get("value", "")).strip()
        if not name:
            return

        result = "ok"

        if name == "set_led":
            self.state["led"] = value.lower() if value else "off"
            result = f"led={self.state['led']}"
        elif name == "set_servo":
            try:
                angle = max(0, min(180, int(value)))
            except Exception:
                angle = 90
            self.state["servo"] = str(angle)
            result = f"servo={angle}"
        elif name == "set_fan":
            try:
                level = max(0, min(3, int(value)))
            except Exception:
                level = 0
            self.state["fan"] = str(level)
            result = f"fan={level}"
        elif name == "get_temperature":
            base = float(self.state.get("temperature", "22.3"))
            wobble = 0.4 * math.sin(time.time() / 8.0)
            result = f"{base + wobble:.1f}"
        elif name == "get_status":
            result = json.dumps(self.state, ensure_ascii=True)
        else:
            result = f"unknown_tool:{name}"

        await self.send_notification(name, result)

    async def on_text_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            if self.verbose:
                print(f"[warn] invalid json from backend: {raw[:120]}")
            return

        if self.verbose:
            print(f"<- {json.dumps(data, ensure_ascii=True)}")

        if isinstance(data.get("config"), dict):
            tools = data["config"].get("tools", [])
            print(f"[config] received {len(tools)} backend tools")
            return

        if isinstance(data.get("toolCall"), dict):
            await self.handle_tool_call(data["toolCall"])
            return

        if isinstance(data.get("assistantResponse"), str):
            print(f"[assistant] {data['assistantResponse']}")
            return

        if isinstance(data.get("audioStart"), dict):
            start = data["audioStart"]
            sample_rate = int(start.get("sampleRate", self.sample_rate))
            channels = int(start.get("channels", 1))
            self.audio_playback.start(sample_rate=sample_rate, channels=channels)
            return

        if data.get("audioEnd") is True:
            await self.audio_playback.stop()
            await self.send_json({"audio": "finished"})
            return

        if isinstance(data.get("debug"), str):
            print(f"[debug] {data['debug']}")

    async def on_binary_message(self, payload: bytes) -> None:
        await self.audio_playback.push(payload)

    async def receiver_loop(self) -> None:
        assert self.ws is not None
        while self.running:
            message = await self.ws.recv()
            if isinstance(message, bytes):
                await self.on_binary_message(message)
            else:
                await self.on_text_message(message)

    async def mic_loop(self) -> None:
        if not self.enable_mic:
            return
        if self.synthetic_mic:
            await self.synthetic_mic_loop()
            return
        if sd is None or np is None:
            print("[mic] sounddevice/numpy unavailable, mic disabled")
            return

        assert self.ws is not None

        blocksize = max(160, int(self.sample_rate * self.chunk_ms / 1000))
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=32)
        loop = asyncio.get_running_loop()

        def callback(indata: Any, _frames: int, _time_info: Any, status: Any) -> None:
            if status and self.verbose:
                print(f"[mic] status: {status}")
            try:
                payload = bytes(indata)
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            except asyncio.QueueFull:
                if self.verbose:
                    print("[mic] dropped frame (queue full)")

        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=blocksize,
                callback=callback,
            ):
                await self.send_json({"mic": "unmuted"})
                while self.running:
                    payload = await queue.get()
                    await self.ws.send(payload)
        except Exception as exc:
            print(f"[mic] input stream error: {exc}")

    async def synthetic_mic_loop(self) -> None:
        assert self.ws is not None
        print("[mic] streaming synthetic audio")

        samples_per_chunk = max(160, int(self.sample_rate * self.chunk_ms / 1000))
        phase = 0.0
        freq = 220.0
        two_pi = 2.0 * math.pi

        await self.send_json({"mic": "unmuted"})

        while self.running:
            if np is not None:
                t = (np.arange(samples_per_chunk) + phase) / float(self.sample_rate)
                wave = 0.15 * np.sin(two_pi * freq * t)
                pcm = (wave * 32767.0).astype(np.int16)
                payload = pcm.tobytes()
            else:
                payload = b"\x00\x00" * samples_per_chunk

            phase += samples_per_chunk
            await self.ws.send(payload)
            await asyncio.sleep(self.chunk_ms / 1000.0)

    async def run_once(self) -> None:
        print(f"[connect] {self.url} as {self.name}")
        async with websockets.connect(self.url, max_size=4 * 1024 * 1024) as ws:
            self.ws = ws
            await self.send_json(self.device_info_payload())

            tasks = [
                asyncio.create_task(self.receiver_loop()),
            ]
            if self.enable_mic:
                tasks.append(asyncio.create_task(self.mic_loop()))

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc

    async def run_forever(self) -> None:
        backoff = 1.0
        while self.running:
            try:
                await self.run_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[disconnect] {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)

    def stop(self) -> None:
        self.running = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ESP32 WiFi device dummy for LLM_embodiments backend")
    parser.add_argument("--url", default="ws://127.0.0.1:3000/device", help="Backend /device websocket URL")
    parser.add_argument("--name", default="device-dummy", help="Friendly dummy name for logs")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Microphone stream sample rate")
    parser.add_argument("--chunk-ms", type=int, default=20, help="Microphone chunk duration (ms)")
    parser.add_argument("--no-mic", action="store_true", help="Disable microphone streaming to backend")
    parser.add_argument("--synthetic-mic", action="store_true", help="Send synthetic tone instead of real microphone")
    parser.add_argument("--no-speaker", action="store_true", help="Disable speaker playback for backend audio")
    parser.add_argument("--verbose", action="store_true", help="Print detailed protocol logs")
    return parser.parse_args()


async def _main() -> int:
    args = parse_args()

    dummy = DeviceDummy(
        url=args.url,
        name=args.name,
        enable_mic=not args.no_mic,
        synthetic_mic=args.synthetic_mic,
        enable_speaker=not args.no_speaker,
        sample_rate=args.sample_rate,
        chunk_ms=args.chunk_ms,
        verbose=args.verbose,
    )

    loop = asyncio.get_running_loop()

    def stop_handler() -> None:
        print("[shutdown] stopping device dummy")
        dummy.stop()
        if dummy.ws:
            asyncio.create_task(dummy.ws.close())

    try:
        loop.add_signal_handler(signal.SIGINT, stop_handler)
        loop.add_signal_handler(signal.SIGTERM, stop_handler)
    except NotImplementedError:
        pass

    device_task = asyncio.create_task(dummy.run_forever())
    input_task = asyncio.create_task(dummy.terminal_input_loop())
    done, pending = await asyncio.wait(
        (device_task, input_task),
        return_when=asyncio.FIRST_COMPLETED,
    )
    dummy.stop()
    if dummy.ws:
        await dummy.ws.close()
    for task in pending:
        task.cancel()
    await asyncio.gather(*done, *pending, return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
