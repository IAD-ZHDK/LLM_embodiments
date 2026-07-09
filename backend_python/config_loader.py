from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict


DEFAULT_CONFIG: Dict[str, Any] = {
    "activeLanguage": "en",
    "speech": {
        "sttBackend": "vosk",
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
    },
    "functions": {"actions": {}, "notifications": {}, "frontEnd": {}},
    "conversationProtocol": [],
    "communicationMethod": "Serial",
    "volume": 50,
}


def _node_import_config(config_path: Path, cwd: Path) -> Dict[str, Any]:
    script = """
import { pathToFileURL } from 'url';
const target = process.argv[1];
const mod = await import(pathToFileURL(target).href + '?t=' + Date.now());
console.log(JSON.stringify(mod.config || mod.default || {}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(config_path)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout.strip().splitlines()
    if not output:
        raise RuntimeError("No config output from Node import")
    return json.loads(output[-1])


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(repo_root: Path) -> Dict[str, Any]:
    config_path = repo_root / "config.js"
    if not config_path.exists():
        return DEFAULT_CONFIG

    try:
        loaded = _node_import_config(config_path, repo_root)
        return _deep_merge(DEFAULT_CONFIG, loaded)
    except Exception as exc:
        print(f"⚠️ Failed to load config.js via Node import: {exc}")
        return DEFAULT_CONFIG
