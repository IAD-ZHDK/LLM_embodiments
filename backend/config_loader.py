from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Dict


DEFAULT_CONFIG: Dict[str, Any] = {
    "activeLanguage": "en",
    "maxDeviceSessions": 10,
    "ttsEnabled": False,
    "deviceResponseDisplay": True,
    "deviceTtsEnabled": True,
    "speech": {
        "sttBackend": "vosk",
        "whisper": {
            "device": "auto",       # "auto" | "cpu" | "cuda"
            "deviceIndex": 0,       # GPU index when device resolves to "cuda"; lets each future parallel session pin its own GPU
            "computeType": "auto",  # "auto" | "int8" | "float16" | "float32"
            "language": "auto",    # "auto" | "en" | "de" - forcing a language skips detection and is faster
        },
        "languageProfiles": {
            "en": {
                "speechToTextModel": "vosk-model-small-en-us-0.15",
                "textToSpeechModel": "en_GB-alan-low.onnx",
            },
            "de": {
                "speechToTextModel": "vosk-model-small-de-0.15",
                "textToSpeechModel": "de_DE-thorsten-medium.onnx",
            },
        },
    },
    "llmSettings": {
        "provider": "ollama",
        "model": "llama3.2:3b",
        "url": "http://127.0.0.1:11434/api/chat",
        "temperature": 0.9,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
        "max_tokens": 2048,
        "user_id": "1",
        "archFunction": {
            "enabled": False,
        },
        "aiHatPlus": {
            "autoDetect": True,
            "preferWhenAvailable": True,
            "provider": "openai",
            "url": "",
        },
        "streamResponses": True,
    },
    "functions": {"tools": {}},
    "conversationProtocol": [],
    "communicationMethod": "Serial",
    "deviceWifi": {
        "ssid": "",
        "password": "",
        "backendUrl": "",
    },
    "volume": 50,
}


def _toml_load_config(config_path: Path) -> Dict[str, Any]:
    # tomllib is read-only by design and lives in the Python 3.11+ stdlib;
    # the project venv is 3.13.3 so no extra dependency is needed.
    with config_path.open("rb") as f:
        return tomllib.load(f)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(repo_root: Path) -> Dict[str, Any]:
    config_path = repo_root / "config.toml"
    config = dict(DEFAULT_CONFIG)

    if not config_path.exists():
        loaded: Dict[str, Any] = {}
    else:
        try:
            loaded = _toml_load_config(config_path)
        except tomllib.TOMLDecodeError as exc:
            # Refuse to silently fall back to DEFAULT_CONFIG — a bad config means
            # the user's tools, language profiles, persona, and AI HAT+ settings
            # would all be ignored at runtime, which is much harder to debug than
            # a startup crash with the parser's line/column in the traceback.
            raise RuntimeError(
                f"Failed to parse {config_path} as TOML. Fix the file and restart "
                f"the backend. Parser error: {exc}"
            ) from exc

    config = _deep_merge(config, loaded)

    profile = os.getenv("LLM_EMBODIMENT_PROFILE", "").strip().lower()
    override_paths = []
    if profile:
        override_paths.append(repo_root / f"config.{profile}.toml")
    override_paths.append(repo_root / "config.local.toml")

    for override_path in override_paths:
        if not override_path.exists():
            continue
        try:
            override = _toml_load_config(override_path)
        except tomllib.TOMLDecodeError as exc:
            raise RuntimeError(
                f"Failed to parse {override_path} as TOML. Fix the file and restart "
                f"the backend. Parser error: {exc}"
            ) from exc
        config = _deep_merge(config, override)

    return config
