#!/usr/bin/env python3
# filepath: /Users/lfranzke/Documents/ZHdK/11_Physical Computing Lab/Technology/LLM_embodiments/python/scriptRemoteSTT.py

import argparse
import importlib.util
import json
import os
import struct
import sys
from typing import Optional

vosk_available = importlib.util.find_spec("vosk") is not None
if vosk_available:
    from vosk import KaldiRecognizer, Model

try:
    from model_downloader import check_model_exists, download_and_extract_model
except ImportError:
    def check_model_exists(model_name, model_path):
        return os.path.exists(os.path.join(model_path, model_name))

    def download_and_extract_model(model_name, model_path, base_url=""):
        print(f"Model downloader not available. Please download model manually to {os.path.join(model_path, model_name)}", file=sys.stderr)
        return False

MODEL_PATH = "STTmodels/"
RATE = 16000  # must match the PCM16 mono sample rate streamed by the remote device


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Remote (network-fed) Speech to Text Service")
    parser.add_argument("--model", default="vosk-model-small-en-us-0.15")
    args = parser.parse_args()

    if not vosk_available:
        send_message("error", "Vosk is not installed")
        return

    os.makedirs(MODEL_PATH, exist_ok=True)
    model_name = str(args.model)
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
