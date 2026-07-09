from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests


class LLMAPI:
    def __init__(self, config: Dict[str, Any], function_handler: Any):
        self.config = config
        self.function_handler = function_handler
        settings = config.get("llmSettings", {})
        self.provider = settings.get("provider", "openai").lower()
        self.url = settings.get("url") or self._default_url(self.provider)
        self.model = settings.get("model", "llama3.2:3b")
        self.max_tokens = settings.get("max_tokens", 2048)
        self.user_id = settings.get("user_id", "1")

    @staticmethod
    def _default_url(provider: str) -> str:
        if provider in ("ollama", "local"):
            return "http://127.0.0.1:11434/api/chat"
        return "https://api.openai.com/v1/chat/completions"

    def _build_messages(self, text: str, role: str, function_name: Optional[str]) -> List[Dict[str, Any]]:
        messages = list(self.config.get("conversationProtocol", []))
        msg: Dict[str, Any] = {"role": role, "content": text}
        if function_name:
            msg["name"] = function_name
        messages.append(msg)
        self.config.setdefault("conversationProtocol", []).append(msg)
        return messages

    def _build_ollama_request(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        tools = []
        for fn in self.function_handler.get_all_functions():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
            )
        return {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "tools": tools,
            "options": {
                "temperature": self.config.get("llmSettings", {}).get("temperature", 0.9),
                "num_predict": self.max_tokens,
            },
        }

    def _build_openai_request(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "model": self.model,
            "user": self.user_id,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.config.get("llmSettings", {}).get("temperature", 0.9),
            "frequency_penalty": self.config.get("llmSettings", {}).get("frequency_penalty", 0.0),
            "presence_penalty": self.config.get("llmSettings", {}).get("presence_penalty", 0.0),
            "functions": self.function_handler.get_all_functions(),
        }

    def send(self, text: str, role: str, function_name: Optional[str] = None) -> Dict[str, Any]:
        if not text:
            return {"role": "assistant", "message": ""}

        messages = self._build_messages(text, role, function_name)
        headers: Dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}

        if self.provider in ("ollama", "local"):
            payload = self._build_ollama_request(messages)
        else:
            payload = self._build_openai_request(messages)
            api_key = self.config.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                return {"role": "error", "message": "OpenAI API key not found"}
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.post(self.url, headers=headers, data=json.dumps(payload), timeout=120)
            data = response.json()
        except Exception as exc:
            return {"role": "error", "message": f"Error fetching {self.url}: {exc}"}

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            if isinstance(err, dict):
                return {"role": "error", "message": err.get("message", str(err))}
            return {"role": "error", "message": str(err)}

        openai_fc = data.get("choices", [{}])[0].get("message", {}).get("function_call") if isinstance(data, dict) else None
        ollama_tc = data.get("message", {}).get("tool_calls", []) if isinstance(data, dict) else []

        if openai_fc and openai_fc.get("name"):
            try:
                args = json.loads(openai_fc.get("arguments", "{}"))
            except Exception:
                args = {}
            return self.function_handler.handle_call(openai_fc["name"], args)

        if ollama_tc:
            first = ollama_tc[0].get("function", {})
            name = first.get("name")
            raw_args = first.get("arguments", {})
            if name:
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                return self.function_handler.handle_call(name, raw_args or {})

        message = ""
        if isinstance(data, dict):
            message = (
                data.get("choices", [{}])[0].get("message", {}).get("content")
                or data.get("choices", [{}])[0].get("text")
                or data.get("message", {}).get("content")
                or ""
            )

        self.config.setdefault("conversationProtocol", []).append({"role": "assistant", "content": message})
        return {"role": "assistant", "message": message}
