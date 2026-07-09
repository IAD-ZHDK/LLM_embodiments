from __future__ import annotations

import json
import os
import re
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

    def _debug_enabled(self) -> bool:
        settings = self.config.get("llmSettings", {})
        if not isinstance(settings, dict):
            return False
        return bool(settings.get("debugRawModelOutput", False))

    @staticmethod
    def _safe_json_dump(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=True)
        except Exception:
            return str(value)

    def _debug_log(self, label: str, value: Any) -> None:
        text = self._safe_json_dump(value)
        limit = 4000
        if len(text) > limit:
            text = text[:limit] + "... [truncated]"
        print(f"🔎 LLM DEBUG {label}: {text}")

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

    def _build_ollama_request(
        self,
        messages: List[Dict[str, Any]],
        include_tools: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": self.config.get("llmSettings", {}).get("temperature", 0.9),
                "num_predict": self.max_tokens,
            },
        }
        if include_tools:
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
            payload["tools"] = tools
        return payload

    def _build_openai_request(
        self,
        messages: List[Dict[str, Any]],
        include_functions: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "user": self.user_id,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.config.get("llmSettings", {}).get("temperature", 0.9),
            "frequency_penalty": self.config.get("llmSettings", {}).get("frequency_penalty", 0.0),
            "presence_penalty": self.config.get("llmSettings", {}).get("presence_penalty", 0.0),
        }
        if include_functions:
            payload["functions"] = self.function_handler.get_all_functions()
        return payload

    @staticmethod
    def _extract_message_text(data: Dict[str, Any]) -> str:
        return (
            data.get("choices", [{}])[0].get("message", {}).get("content")
            or data.get("choices", [{}])[0].get("text")
            or data.get("message", {}).get("content")
            or ""
        )

    def _tool_policy(self) -> Dict[str, Any]:
        return self.config.get("llmSettings", {}).get("toolPolicy", {})

    @staticmethod
    def _function_catalog(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        functions = config.get("functions", {})

        tools = functions.get("tools", {}) if isinstance(functions, dict) else {}
        if isinstance(tools, dict):
            for name, meta in tools.items():
                if isinstance(meta, dict):
                    out[str(name)] = meta
        elif isinstance(tools, list):
            for entry in tools:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if name:
                    out[name] = entry
        return out

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        lowered = text.lower()
        return any(isinstance(k, str) and k.strip() and k.lower() in lowered for k in keywords)

    @staticmethod
    def _derive_keywords(tool_name: str, meta: Dict[str, Any]) -> List[str]:
        words = re.split(r"[^a-zA-Z0-9]+", tool_name)
        derived = [w.lower() for w in words if len(w) >= 3]
        desc = str(meta.get("description", ""))
        desc_words = re.split(r"[^a-zA-Z0-9]+", desc)
        for w in desc_words:
            lw = w.lower()
            if len(lw) >= 4 and lw not in derived:
                derived.append(lw)
        return derived[:16]

    def _tool_keywords(self, tool_name: str) -> List[str]:
        catalog = self._function_catalog(self.config)
        meta = catalog.get(tool_name, {})
        config_keywords = meta.get("triggerKeywords", []) if isinstance(meta, dict) else []
        if isinstance(config_keywords, list) and config_keywords:
            return [str(k).lower() for k in config_keywords if str(k).strip()]

        return self._derive_keywords(tool_name, meta if isinstance(meta, dict) else {})

    def _is_command_like(self, text: str) -> bool:
        policy = self._tool_policy()
        command_keywords = policy.get("commandKeywords", []) if isinstance(policy, dict) else []
        if not isinstance(command_keywords, list) or not command_keywords:
            return True
        return self._contains_any(text, [str(k) for k in command_keywords])

    def _allow_tool_call(self, role: str, text: str, tool_name: str) -> bool:
        policy = self._tool_policy()
        enabled = bool(policy.get("enableIntentFilter", False)) if isinstance(policy, dict) else False
        if not enabled:
            return role == "user"
        if role != "user":
            return False
        if not self._is_command_like(text):
            return False
        return self._contains_any(text, self._tool_keywords(tool_name))

    def _normalize_tool_args(self, tool_name: str, args: Dict[str, Any], text: str) -> Dict[str, Any]:
        normalized = dict(args or {})
        catalog = self._function_catalog(self.config)
        meta = catalog.get(tool_name, {})
        value_rules = meta.get("valueRules", []) if isinstance(meta, dict) else []

        if isinstance(value_rules, list):
            for item in value_rules:
                if not isinstance(item, dict):
                    continue
                keywords = item.get("keywords", [])
                if isinstance(keywords, list) and self._contains_any(text, [str(k) for k in keywords]):
                    if "value" in item:
                        normalized["value"] = item["value"]
                    break
        return normalized

    @staticmethod
    def _request_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=120)
        return response.json()

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

        if self._debug_enabled():
            self._debug_log("request.payload", payload)

        try:
            data = self._request_json(self.url, headers, payload)
        except Exception as exc:
            return {"role": "error", "message": f"Error fetching {self.url}: {exc}"}

        if self._debug_enabled():
            self._debug_log("response.raw", data)

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            if isinstance(err, dict):
                return {"role": "error", "message": err.get("message", str(err))}
            return {"role": "error", "message": str(err)}

        openai_fc = data.get("choices", [{}])[0].get("message", {}).get("function_call") if isinstance(data, dict) else None
        ollama_tc = data.get("message", {}).get("tool_calls", []) if isinstance(data, dict) else []

        if self._debug_enabled():
            self._debug_log(
                "response.tool_fields",
                {"openai_function_call": openai_fc, "ollama_tool_calls": ollama_tc},
            )

        if openai_fc and openai_fc.get("name"):
            name = openai_fc["name"]
            try:
                args = json.loads(openai_fc.get("arguments", "{}"))
            except Exception:
                args = {}
            if self._allow_tool_call(role, text, name):
                args = self._normalize_tool_args(name, args, text)
                return self.function_handler.handle_call(name, args)

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
                if self._allow_tool_call(role, text, name):
                    raw_args = self._normalize_tool_args(name, raw_args or {}, text)
                    return self.function_handler.handle_call(name, raw_args)

        message = self._extract_message_text(data) if isinstance(data, dict) else ""

        # If the model tried a blocked tool-call and returned no text, retry once without tools/functions.
        if not message:
            try:
                if self.provider in ("ollama", "local"):
                    fallback_payload = self._build_ollama_request(messages, include_tools=False)
                else:
                    fallback_payload = self._build_openai_request(messages, include_functions=False)
                fallback_data = self._request_json(self.url, headers, fallback_payload)
                if isinstance(fallback_data, dict):
                    message = self._extract_message_text(fallback_data)
            except Exception:
                pass

        self.config.setdefault("conversationProtocol", []).append({"role": "assistant", "content": message})
        return {"role": "assistant", "message": message}
