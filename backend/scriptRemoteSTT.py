#!/usr/bin/env python3
# filepath: /Users/lfranzke/Documents/ZHdK/11_Physical Computing Lab/Technology/LLM_embodiments/python/scriptRemoteSTT.py

import argparse
import importlib.util
import json
import os
import struct
import sys
import time
from typing import Optional

import numpy as np

vosk_available = importlib.util.find_spec("vosk") is not None
if vosk_available:
    from vosk import KaldiRecognizer, Model

whisper_available = importlib.util.find_spec("faster_whisper") is not None
if whisper_available:
    from faster_whisper import WhisperModel

try:
    from model_downloader import check_model_exists, download_and_extract_model
except ImportError:
    def check_model_exists(model_name, model_path):
        return os.path.exists(os.path.join(model_path, model_name))

    def download_and_extract_model(model_name, model_path, base_url=""):
        print(f"Model downloader not available. Please download model manually to {os.path.join(model_path, model_name)}", file=sys.stderr)
        return False

from Microphone.vad_utils import VAD

MODEL_PATH = "STTmodels/"
RATE = 16000  # must match the PCM16 mono sample rate streamed by the remote device


def _cuda_available() -> bool:
    """Best-effort GPU probe for the "auto" whisper device setting."""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def send_message(name: str, value: str) -> None:
    print(json.dumps({name: value}))
    sys.stdout.flush()


def read_frame(stream) -> Optional[bytes]:
    # Frames are length-prefixed so raw PCM bytes can share stdin with JSON control messages.
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack(">I", header)
    payload = b""
    while len(payload) < length:
        chunk = stream.read(length - len(payload))
        if not chunk:
            return None
        payload += chunk
    return payload


class WhisperSession:
    """Segments incoming PCM16 audio with WebRTC VAD and transcribes each utterance with
    faster-whisper. Each remote device gets its own instance/model, so a future version that
    runs one worker per GPU (via device_index) can start multiple of these side by side."""

    def __init__(self, model_name: str, device: str, compute_type: str, device_index: int, language: str,
                 silence_hangover: float = 0.6):
        resolved_device = device if device != "auto" else ("cuda" if _cuda_available() else "cpu")
        resolved_compute_type = compute_type
        if resolved_compute_type == "auto":
            resolved_compute_type = "float16" if resolved_device == "cuda" else "int8"

        print(
            f"Loading Whisper model '{model_name}' (device={resolved_device}, index={device_index}, "
            f"compute_type={resolved_compute_type})...",
            file=sys.stderr,
        )
        self.model = WhisperModel(
            model_name, device=resolved_device, device_index=device_index, compute_type=resolved_compute_type
        )
        self.language = None if str(language).lower() in ("auto", "", "none") else str(language)
        self.silence_hangover = silence_hangover
        self.vad = VAD(aggressiveness=2, sampling_rate=RATE, frame_duration_ms=30)
        self._buffer = bytearray()
        self._speaking = False
        self._silence_since: Optional[float] = None

    def push(self, chunk: bytes) -> Optional[str]:
        is_speech = self.vad.process(chunk)
        now = time.time()
        if is_speech:
            self._buffer.extend(chunk)
            self._silence_since = None
            self._speaking = True
            return None
        if self._speaking:
            if self._silence_since is None:
                self._silence_since = now
                return None
            if now - self._silence_since >= self.silence_hangover:
                self._speaking = False
                self._silence_since = None
                return self._finalize()
        return None

    def reset(self) -> None:
        self._buffer = bytearray()
        self._speaking = False
        self._silence_since = None

    def _finalize(self) -> Optional[str]:
        buffered, self._buffer = self._buffer, bytearray()
        if not buffered:
            return None
        audio_np = np.frombuffer(bytes(buffered), dtype=np.int16).astype(np.float32) / 32768.0
        try:
            segments, _info = self.model.transcribe(audio_np, language=self.language, beam_size=1, vad_filter=False)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            print(f"Whisper transcription error: {exc}", file=sys.stderr)
            return None
        return text or None


def _run_vosk(model_name: str) -> None:
    if not vosk_available:
        send_message("error", "Vosk is not installed")
        return

    os.makedirs(MODEL_PATH, exist_ok=True)
    if not check_model_exists(model_name, MODEL_PATH):
        print(f"Model '{model_name}' not found. Attempting to download...", file=sys.stderr)
        download_and_extract_model(model_name, MODEL_PATH)

    model_path = os.path.join(MODEL_PATH, model_name)
    try:
        model = Model(model_path)
        recognizer = KaldiRecognizer(model, RATE)
    except Exception as exc:
        send_message("error", f"Failed to load model: {exc}")
        return

    print(f"✅ Remote speech recognizer initialized with {model_name}", file=sys.stderr)

    paused = False
    stdin = sys.stdin.buffer
    while True:
        payload = read_frame(stdin)
        if payload is None:
            break
        frame_type, body = payload[0:1], payload[1:]

        if frame_type == b"C":
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                continue
            command = data.get("STT")
            if command == "pause":
                paused = True
                print("Pausing remote speech to text", file=sys.stderr)
            elif command == "resume":
                paused = False
                print("Resuming remote speech to text", file=sys.stderr)
            continue

        if frame_type != b"A" or paused:
            continue

        if recognizer.AcceptWaveform(body):
            result = json.loads(recognizer.Result())
            text = result.get("text", "")
            if text:
                send_message("confirmedText", text)
        else:
            partial = json.loads(recognizer.PartialResult())
            text = partial.get("partial", "")
            if text:
                send_message("interimResult", text)


def _run_whisper(model_name: str, device: str, compute_type: str, device_index: int, language: str) -> None:
    if not whisper_available:
        send_message("error", "faster-whisper is not installed")
        return

    try:
        session = WhisperSession(model_name, device, compute_type, device_index, language)
    except Exception as exc:
        send_message("error", f"Failed to load model: {exc}")
        return

    paused = False
    stdin = sys.stdin.buffer
    while True:
        payload = read_frame(stdin)
        if payload is None:
            break
        frame_type, body = payload[0:1], payload[1:]

        if frame_type == b"C":
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                continue
            command = data.get("STT")
            if command == "pause":
                paused = True
                session.reset()
                print("Pausing remote speech to text", file=sys.stderr)
            elif command == "resume":
                paused = False
                print("Resuming remote speech to text", file=sys.stderr)
            continue

        if frame_type != b"A" or paused:
            continue

        text = session.push(body)
        if text:
            send_message("confirmedText", text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remote (network-fed) Speech to Text Service")
    parser.add_argument("--backend", default="vosk", choices=["vosk", "whisper"])
    parser.add_argument("--model", default="vosk-model-small-en-us-0.15")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                         help="Whisper only: compute device")
    parser.add_argument("--compute-type", default="auto", choices=["auto", "int8", "float16", "float32"],
                         help="Whisper only: CTranslate2 compute type")
    parser.add_argument("--device-index", type=int, default=0,
                         help="Whisper only: GPU index to use when device resolves to cuda")
    parser.add_argument("--language", default="auto",
                         help='Whisper only: force a language (e.g. "en", "de") or "auto" to detect')
    args = parser.parse_args()

    if args.backend == "whisper":
        _run_whisper(str(args.model), args.device, args.compute_type, args.device_index, args.language)
    else:
        _run_vosk(str(args.model))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
