#!/usr/bin/env python3
"""Wizard of Oz test server.

Replicates the ESP32-facing half of the real backend (backend/server.py) so the
M5Stack WiFi example connects unmodified, but there is no LLM in the loop: a human operator
sees everything the device reports and manually issues tool calls from a console prompt.

- Audio: incoming PCM16 chunks are streamed straight to your speakers, live. No STT.
- Text: nothing is spoken back. No TTS.
- Tools: whatever the device declares via `deviceInfo` (persona + MCP-style tool list) is
  printed on connect; type `<tool_name> [value]` at the prompt to send that tool call to
  the device, exactly as if the LLM had decided to call it.

Run with the same Python venv as the main backend (it already has fastapi/uvicorn/sounddevice):
    source ../backend/venv/bin/activate
    python3 server.py
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

try:
    import sounddevice as sd
except Exception:
    sd = None

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

try:
    from faster_whisper import WhisperModel
    from Microphone.vad_utils import VAD
except Exception:
    WhisperModel = None
    VAD = None

app = FastAPI(title="Wizard of Oz test server")

PORT = 3000
SAMPLE_RATE = 16000  # must match kSampleRate in the M5Stack sketch / scriptRemoteSTT.py
AUDIO_CHUNK_BYTES = 1024  # 512 mono int16 samples = 32 ms at 16 kHz
AUDIO_STARTUP_CHUNKS = 6  # 192 ms of jitter protection before playback begins
AUDIO_MAX_CHUNKS = 20     # cap delay at 640 ms; discard oldest audio if it falls behind
VAD_FRAME_BYTES = SAMPLE_RATE * 30 // 1000 * 2
WHISPER_MODEL = os.getenv("WIZARD_WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("WIZARD_WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.getenv("WIZARD_WHISPER_COMPUTE_TYPE", "auto")
TRANSCRIBE_WINDOW_SECONDS = float(os.getenv("WIZARD_TRANSCRIBE_WINDOW_SECONDS", "3"))
TRANSCRIBE_WINDOW_BYTES = int(SAMPLE_RATE * TRANSCRIBE_WINDOW_SECONDS * 2)
WHISPER_NO_SPEECH_THRESHOLD = float(os.getenv("WIZARD_NO_SPEECH_THRESHOLD", "0.8"))
WHISPER_LOG_PROB_THRESHOLD = float(os.getenv("WIZARD_LOG_PROB_THRESHOLD", "-0.7"))

device_ws: Optional[WebSocket] = None
device_tools: List[Dict[str, Any]] = []
device_persona: str = ""
loop: Optional[asyncio.AbstractEventLoop] = None
audio_stream = None
audio_queue: Optional[queue.Queue[bytes]] = None
audio_stop_event = threading.Event()
audio_thread: Optional[threading.Thread] = None
transcription_queue: Optional[queue.Queue[bytes]] = None
transcription_stop_event = threading.Event()
transcription_thread: Optional[threading.Thread] = None


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "unknown"


def _print_tools() -> None:
    print(f"\n🧠 Persona: {device_persona or '(none set)'}")
    if not device_tools:
        print("🛠️  No tools declared yet.")
        return
    print(f"🛠️  Tools ({len(device_tools)}):")
    for tool in device_tools:
        name = tool.get("name", "?")
        description = tool.get("description", "")
        data_type = tool.get("dataType", "string")
        print(f"   - {name} ({data_type}): {description}")


def _start_audio_playback() -> None:
    global audio_stream, audio_queue, audio_thread
    if sd is None:
        print("⚠️ sounddevice not installed - audio will not be played. `pip install sounddevice`")
        return
    if audio_stream is not None:
        return
    audio_queue = queue.Queue(maxsize=AUDIO_MAX_CHUNKS)
    audio_stop_event.clear()
    audio_stream = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=512)
    audio_stream.start()
    audio_thread = threading.Thread(target=_audio_playback_loop, daemon=True)
    audio_thread.start()
    print(f"🔊 Audio buffer: {AUDIO_STARTUP_CHUNKS * 32} ms startup, {AUDIO_MAX_CHUNKS * 32} ms maximum.")


def _stop_audio_playback() -> None:
    global audio_stream, audio_queue, audio_thread
    audio_stop_event.set()
    if audio_thread is not None:
        audio_thread.join(timeout=1)
        audio_thread = None
    if audio_stream is not None:
        try:
            audio_stream.stop()
            audio_stream.close()
        except Exception:
            pass
        audio_stream = None
    audio_queue = None


def _start_transcription() -> None:
    global transcription_queue, transcription_thread
    if WhisperModel is None or VAD is None:
        print("⚠️ Whisper unavailable - install faster-whisper to enable live transcription.")
        return
    if transcription_thread is not None:
        return
    transcription_queue = queue.Queue(maxsize=AUDIO_MAX_CHUNKS * 10)
    transcription_stop_event.clear()
    transcription_thread = threading.Thread(target=_transcription_loop, daemon=True)
    transcription_thread.start()


def _stop_transcription() -> None:
    global transcription_queue, transcription_thread
    transcription_stop_event.set()
    if transcription_thread is not None:
        transcription_thread.join(timeout=2)
        transcription_thread = None
    transcription_queue = None


def _transcription_loop() -> None:
    device = WHISPER_DEVICE
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
        except Exception:
            device = "cpu"
    compute_type = WHISPER_COMPUTE_TYPE
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    try:
        print(f"📝 Loading Whisper '{WHISPER_MODEL}' ({device}, {compute_type})...")
        model = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)
    except Exception as exc:
        print(f"⚠️ Whisper startup failed: {exc}")
        return

    audio = bytearray()
    last_level_log = 0.0
    print(f"📝 Whisper transcription ready; decoding {TRANSCRIBE_WINDOW_SECONDS:g}-second raw-audio windows.")

    while not transcription_stop_event.is_set():
        if transcription_queue is None:
            return
        try:
            chunk = transcription_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        now = time.monotonic()
        samples = np.frombuffer(chunk, dtype=np.int16)
        if now - last_level_log >= 2:
            rms = int(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            peak = int(np.max(np.abs(samples.astype(np.int32))))
            print(f"🎚️  PCM level: rms={rms}, peak={peak}")
            last_level_log = now

        audio.extend(chunk)
        if len(audio) < TRANSCRIBE_WINDOW_BYTES:
            continue

        pcm, audio = bytes(audio[:TRANSCRIBE_WINDOW_BYTES]), audio[TRANSCRIBE_WINDOW_BYTES:]
        try:
            data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _ = model.transcribe(
                data,
                language="en",
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
            )
            accepted = [
                segment.text.strip()
                for segment in segments
                if segment.no_speech_prob < WHISPER_NO_SPEECH_THRESHOLD
                and segment.avg_logprob >= WHISPER_LOG_PROB_THRESHOLD
            ]
            text = " ".join(accepted).strip()
            print(f"📝 Transcript: {text or '(no speech recognized)'}")
        except Exception as exc:
            print(f"⚠️ Whisper transcription failed: {exc}")


def _audio_playback_loop() -> None:
    buffered = False
    while not audio_stop_event.is_set():
        if audio_queue is None or audio_stream is None:
            return
        try:
            chunk = audio_queue.get(timeout=0.1)
        except queue.Empty:
            buffered = False
            continue

        if not buffered:
            while audio_queue.qsize() < AUDIO_STARTUP_CHUNKS - 1 and not audio_stop_event.is_set():
                audio_stop_event.wait(0.01)
            if audio_stop_event.is_set():
                return
            buffered = True

        try:
            audio_stream.write(chunk)
        except Exception as exc:
            print(f"⚠️ Audio playback error: {exc}")
            return

        if audio_queue.empty():
            buffered = False


def _queue_audio_chunk(chunk: bytes) -> None:
    if len(chunk) != AUDIO_CHUNK_BYTES:
        return
    for destination in (audio_queue, transcription_queue):
        if destination is None:
            continue
        try:
            destination.put_nowait(chunk)
        except queue.Full:
            try:
                destination.get_nowait()
            except queue.Empty:
                pass
            try:
                destination.put_nowait(chunk)
            except queue.Full:
                pass


async def _send_tool_call(name: str, value: str) -> None:
    if device_ws is None:
        print("⚠️ No device connected.")
        return
    payload = {"toolCall": {"name": name, "value": value}}
    await device_ws.send_text(json.dumps(payload))
    print(f"➡️  Sent tool call: {name} = {value!r}")


def _console_loop() -> None:
    print("\nType `<tool_name> [value]` to call a tool, `tools` to list them, or `quit` to stop typing.")
    while True:
        try:
            line = input("woz> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "tools":
            _print_tools()
            continue

        parts = line.split(maxsplit=1)
        name = parts[0]
        value = parts[1] if len(parts) > 1 else ""
        if loop is not None:
            asyncio.run_coroutine_threadsafe(_send_tool_call(name, value), loop)


@app.on_event("startup")
async def startup() -> None:
    global loop
    loop = asyncio.get_running_loop()
    print(f"🌐 Wizard of Oz server listening on {_local_ip()}:{PORT}/device")
    threading.Thread(target=_console_loop, daemon=True).start()


@app.websocket("/device")
async def websocket_device(ws: WebSocket) -> None:
    global device_ws, device_tools, device_persona

    await ws.accept()
    device_ws = ws
    print("\n✅ ESP32 connected.")
    _start_audio_playback()
    _start_transcription()

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            audio_chunk = message.get("bytes")
            if audio_chunk is not None:
                _queue_audio_chunk(audio_chunk)
                continue

            text = message.get("text")
            if not text:
                continue
            try:
                data = json.loads(text)
            except Exception:
                continue

            notification = data.get("notification")
            if isinstance(notification, dict):
                print(f"🔔 Notification: {notification.get('name')} = {notification.get('value')}")
            elif data.get("mic") in ("muted", "unmuted"):
                print(f"🎙️  Mic: {data['mic']}")
            elif isinstance(data.get("deviceInfo"), dict):
                device_info = data["deviceInfo"]
                device_persona = str(device_info.get("persona", ""))
                tools = device_info.get("tools")
                device_tools = tools if isinstance(tools, list) else []
                _print_tools()
    except WebSocketDisconnect:
        pass
    finally:
        device_ws = None
        _stop_transcription()
        _stop_audio_playback()
        print("❌ ESP32 disconnected.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
