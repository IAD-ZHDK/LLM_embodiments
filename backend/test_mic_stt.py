#!/usr/bin/env python3
"""Debug-only: speak into this machine's microphone and see live STT transcriptions.

Does not start the FastAPI backend, WebSocket, or LLM - just microphone -> STT -> console.
Reads the same sttBackend/model settings as config.toml by default, or override on the CLI.

Usage (from backend/, with the venv active):
    python3 test_mic_stt.py
    python3 test_mic_stt.py --backend whisper --model small.en
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

import scriptSTT as stt
from Microphone.scriptMicrophone import MicrophoneStream


def _config_defaults() -> dict:
    try:
        from config_loader import load_config
        config = load_config(REPO_ROOT)
    except Exception as exc:
        print(f"Could not read config.toml ({exc}); using built-in defaults.", file=sys.stderr)
        return {}

    language = config.get("activeLanguage", "en")
    speech = config.get("speech", {})
    profile = speech.get("languageProfiles", {}).get(language, {})
    whisper = speech.get("whisper", {})
    return {
        "backend": speech.get("sttBackend", "vosk"),
        "model": profile.get("speechToTextModel", "vosk-model-small-en-us-0.15"),
        "device": whisper.get("device", "auto"),
        "compute_type": whisper.get("computeType", "auto"),
        "device_index": whisper.get("deviceIndex", 0),
        "language": whisper.get("language", "auto"),
    }


def main() -> None:
    defaults = _config_defaults()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", default=defaults.get("backend", "vosk"), choices=["vosk", "whisper"])
    parser.add_argument("--model", default=defaults.get("model", "vosk-model-small-en-us-0.15"))
    parser.add_argument("--device", default=defaults.get("device", "auto"))
    parser.add_argument("--compute-type", default=defaults.get("compute_type", "auto"))
    parser.add_argument("--device-index", type=int, default=defaults.get("device_index", 0))
    parser.add_argument("--language", default=defaults.get("language", "auto"))
    args = parser.parse_args()

    print(f"Python: {sys.executable}")
    print(f"Backend: {args.backend} | Model: {args.model}")
    print("Speak into the microphone. Ctrl+C to stop.\n")

    def on_result(text, partial):
        if text:
            print(f"🎤 {text}")

    mic = MicrophoneStream(rate=stt.RATE, chunk=stt.CHUNK)
    try:
        if args.backend == "whisper":
            if not stt.whisper_available:
                print(f"faster-whisper is not installed for this interpreter ({sys.executable}).", file=sys.stderr)
                print("Run: pip install faster-whisper (with backend/venv activated).", file=sys.stderr)
                return
            recognizer = stt.WhisperRecognizer(
                audio_source=mic,
                callback=on_result,
                rate=stt.RATE,
                chunk=stt.CHUNK,
                modelName=args.model,
                device=args.device,
                compute_type=args.compute_type,
                language=args.language,
                device_index=args.device_index,
            )
        else:
            if not stt.vosk_available:
                print(f"Vosk is not installed for this interpreter ({sys.executable}).", file=sys.stderr)
                print("Run: pip install vosk (with backend/venv activated).", file=sys.stderr)
                return
            stt.current_model = args.model
            recognizer = stt.SpeechRecognizer(audio_source=mic, callback=on_result, rate=stt.RATE, chunk=stt.CHUNK)

        recognizer.run()
    except KeyboardInterrupt:
        pass
    finally:
        mic.close()
        print("\nStopped.")


if __name__ == "__main__":
    main()
