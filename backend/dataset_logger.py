"""Fine-tuning dataset output shared by the backend and the Wizard of Oz server.

Each conversation is one JSON line in logs/dataset/<device name>/ (git-ignored), so
`cat logs/dataset/<device>/*.jsonl > train.jsonl` combines one device's runs from both sources:
{"messages": [system/user/assistant(+tool_calls)/tool ...], "tools": [OpenAI function schemas], "metadata": {...}}
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DATASET_DIR = Path(__file__).resolve().parent.parent / "logs" / "dataset"

_THINK_BLOCK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_JSON_TYPES = {"bool": "boolean", "boolean": "boolean", "int": "integer", "integer": "integer",
               "float": "number", "number": "number", "string": "string"}


def new_dataset_path(source: str, device_name: str = "") -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", device_name).strip("_").lower() or "device"
    return DATASET_DIR / slug / f"{source}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"


def tool_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI-style schema for a device-declared tool (name/description/dataType)."""
    properties: Dict[str, Any] = {}
    data_type = str(tool.get("dataType", "string"))
    if data_type != "none":
        properties["value"] = {"type": _JSON_TYPES.get(data_type, "string"), "description": tool.get("description", "")}
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": {"type": "object", "properties": properties},
        },
    }


def normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Text-only chat messages: no images, no <think> blocks, tool turns in one shape."""
    out: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if message.get("images") or (content is not None and not isinstance(content, str)):
            continue
        content = content or ""

        if role == "assistant":
            entry: Dict[str, Any] = {"role": "assistant", "content": _THINK_BLOCK.sub("", content).strip()}
            calls = message.get("tool_calls")
            legacy = message.get("function_call")
            if calls:
                entry["tool_calls"] = [
                    {"type": "function", "function": {"name": c["function"]["name"], "arguments": c["function"].get("arguments", {})}}
                    for c in calls
                ]
            elif isinstance(legacy, dict):
                try:
                    arguments = json.loads(legacy.get("arguments", "{}"))
                except ValueError:
                    arguments = {}
                entry["tool_calls"] = [{"type": "function", "function": {"name": legacy.get("name", ""), "arguments": arguments}}]
            elif not entry["content"]:
                continue
            out.append(entry)
        elif role in ("tool", "function"):
            out.append({"role": "tool", "name": message.get("tool_name") or message.get("name", ""), "content": content})
        elif role in ("system", "user"):
            out.append({"role": role, "content": content})
    return out


def save_conversation(path: Path, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                      metadata: Optional[Dict[str, Any]] = None) -> None:
    """Rewrites the conversation's single JSONL line; skipped until the model/wizard has replied."""
    normalized = normalize_messages(messages)
    if not any(m["role"] == "assistant" for m in normalized):
        return
    record = {
        "messages": normalized,
        "tools": tools,
        "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), **(metadata or {})},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(temp_name, path)
