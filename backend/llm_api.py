from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests


class LLMAPI:
    def __init__(
        self,
        config: Dict[str, Any],
        function_handler: Any,
        on_delta: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.function_handler = function_handler
        settings = config.get("llmSettings", {})
        self.provider = settings.get("provider", "openai").lower()
        self.url = settings.get("url") or self._default_url(self.provider)
        self.model = settings.get("model", "llama3.2:3b")
        self.max_tokens = settings.get("max_tokens", 2048)
        self.user_id = settings.get("user_id", "1")
        self.on_delta = on_delta
        self.on_thinking = on_thinking
        self.ai_hat_status = self._detect_ai_hat_plus()
        self._apply_ai_hat_routing(settings)

    @staticmethod
    def _split_reasoning(text: str) -> tuple[str, str]:
        """Separate <think> reasoning from spoken text; the block may still be open mid-stream."""
        reasoning: List[str] = []

        def capture(match: "re.Match[str]") -> str:
            reasoning.append(match.group(1))
            return ""

        visible = re.sub(r"<think>([\s\S]*?)</think>", capture, text or "", flags=re.IGNORECASE)
        open_idx = visible.lower().rfind("<think>")
        if open_idx != -1:
            reasoning.append(visible[open_idx + len("<think>"):])
            visible = visible[:open_idx]
        return visible, "\n".join(part.strip() for part in reasoning if part.strip())

    def _reasoning_of(self, message: Dict[str, Any], content: str) -> str:
        # Newer Ollama splits reasoning into message.thinking; older templates inline <think> tags.
        native = str(message.get("thinking") or "").strip()
        inline = self._split_reasoning(content)[1]
        return "\n".join(part for part in (native, inline) if part)

    def _emit_reasoning(self, data: Dict[str, Any]) -> None:
        if not self.on_thinking or not isinstance(data, dict):
            return
        message = data.get("message")
        if not isinstance(message, dict):
            choices = data.get("choices") or [{}]
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if not isinstance(message, dict):
            return
        reasoning = self._reasoning_of(message, str(message.get("content") or ""))
        if reasoning:
            try:
                self.on_thinking(reasoning)
            except Exception:
                pass

    def _streaming_enabled(self) -> bool:
        settings = self.config.get("llmSettings", {})
        if not isinstance(settings, dict):
            return False
        # Only the Ollama line-delimited JSON stream is handled here.
        return bool(settings.get("streamResponses", True)) and self.provider in ("ollama", "local")

    def _debug_enabled(self) -> bool:
        settings = self.config.get("llmSettings", {})
        if not isinstance(settings, dict):
            return False
        return bool(settings.get("debugRawModelOutput", False))

    def get_model_details(self) -> Dict[str, Any]:
        """Return effective runtime settings and, for Ollama, installed model metadata."""
        settings = self.config.get("llmSettings", {})
        model = str(settings.get("model", self.model))
        details: Dict[str, Any] = {
            "provider": self.provider,
            "model": model,
            "url": self.url,
            "temperature": settings.get("temperature", 0.9),
            "top_p": settings.get("top_p", 0.9),
            "top_k": settings.get("top_k", 40),
            "max_tokens": settings.get("max_tokens", self.max_tokens),
            "repeat_penalty": settings.get("repeat_penalty", 1.1),
        }
        if self.provider not in ("ollama", "local"):
            return details

        show_url = self.url.split("/api/", 1)[0].rstrip("/") + "/api/show"
        try:
            response = requests.post(show_url, json={"name": model}, timeout=2)
            if response.ok:
                ollama_details = response.json().get("details", {})
                if isinstance(ollama_details, dict):
                    details["ollama"] = ollama_details
        except Exception:
            pass
        return details

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

    def _protocol_trace(self, event: str, value: Any) -> None:
        if not self._debug_enabled():
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "provider": self.provider,
            "model": self.config.get("llmSettings", {}).get("model", self.model),
            "data": value,
        }
        try:
            log_path = Path(__file__).resolve().parent.parent / "logs" / "llm_protocol.jsonl"
            log_path.parent.mkdir(exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            print(f"⚠️ Could not write LLM protocol trace: {exc}")

    @staticmethod
    def _run_command(command: List[str]) -> Optional[subprocess.CompletedProcess]:
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
        except Exception:
            return None

    def _detect_ai_hat_plus(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "attached": False,
            "devicePaths": [],
            "hailortcli": False,
            "details": [],
        }

        for path in ("/dev/hailo0", "/dev/hailo1", "/dev/hailort0", "/dev/hailort"):
            if os.path.exists(path):
                status["attached"] = True
                status["devicePaths"].append(path)

        hailo_cli = shutil.which("hailortcli")
        if hailo_cli:
            status["hailortcli"] = True
            probe = self._run_command([hailo_cli, "fw-control", "identify"])
            if probe and probe.returncode == 0:
                status["attached"] = True
                output = (probe.stdout or "").strip()
                if output:
                    status["details"].append(output[:300])

        return status

    def _apply_ai_hat_routing(self, settings: Dict[str, Any]) -> None:
        ai_hat_cfg = settings.get("aiHatPlus", {}) if isinstance(settings, dict) else {}
        if not isinstance(ai_hat_cfg, dict):
            return

        auto_detect = bool(ai_hat_cfg.get("autoDetect", True))
        prefer = bool(ai_hat_cfg.get("preferWhenAvailable", True))
        endpoint = str(ai_hat_cfg.get("url", "")).strip()
        provider = str(ai_hat_cfg.get("provider", "openai")).strip().lower()

        if auto_detect and prefer and self.ai_hat_status.get("attached") and endpoint:
            self.provider = provider or self.provider
            self.url = endpoint
            print(f"🧠 AI HAT+ detected. Routing LLM requests to {self.url}")
        elif auto_detect and self.ai_hat_status.get("attached") and not endpoint:
            print("🧠 AI HAT+ detected, but llmSettings.aiHatPlus.url is empty. Keeping default LLM endpoint.")

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
        if self._is_arch_function_mode():
            messages = self._apply_arch_system_prompt(messages)
        return messages

    def _is_arch_function_mode(self) -> bool:
        settings = self.config.get("llmSettings", {})
        arch_cfg = settings.get("archFunction", {}) if isinstance(settings, dict) else {}
        explicit = bool(arch_cfg.get("enabled", False)) if isinstance(arch_cfg, dict) else False
        return explicit or ("arch-function" in str(self.model).lower())

    def _tools_for_prompt(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
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
        return tools

    def _apply_arch_system_prompt(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tools = self._tools_for_prompt()
        tool_text = "\n".join(json.dumps(t, ensure_ascii=True) for t in tools)
        arch_format = (
            "# Tools\n"
            "You may call one or more functions to assist with the user query.\n"
            "Function signatures are inside <tools></tools> tags.\n"
            "<tools>\n"
            f"{tool_text}\n"
            "</tools>\n\n"
            "For each function call, return a JSON object wrapped in <tool_call></tool_call>:\n"
            "<tool_call>\n"
            '{"name": "<function-name>", "arguments": {"key": "value"}}\n'
            "</tool_call>"
        )

        out = [dict(m) for m in messages]
        for idx, msg in enumerate(out):
            if msg.get("role") == "system":
                content = str(msg.get("content", "")).strip()
                if "<tool_call>" not in content or "<tools>" not in content:
                    msg["content"] = f"{content}\n\n{arch_format}" if content else arch_format
                out[idx] = msg
                return out

        out.insert(0, {"role": "system", "content": arch_format})
        return out

    def _build_ollama_request(
        self,
        messages: List[Dict[str, Any]],
        include_tools: bool = True,
    ) -> Dict[str, Any]:
        settings = self.config.get("llmSettings", {})
        options = {
            "temperature": settings.get("temperature", 0.9),
            "num_predict": settings.get("max_tokens", self.max_tokens),
            "top_p": settings.get("top_p", 0.9),
            "top_k": settings.get("top_k", 40),
            "repeat_penalty": settings.get("repeat_penalty", 1.1),
        }
        payload = {
            "model": settings.get("model", self.model),
            "stream": self._streaming_enabled(),
            "messages": messages,
            "options": options,
        }
        if include_tools:
            payload["tools"] = self._tools_for_prompt()
        return payload

    def _build_openai_request(
        self,
        messages: List[Dict[str, Any]],
        include_functions: bool = True,
    ) -> Dict[str, Any]:
        settings = self.config.get("llmSettings", {})
        payload = {
            "model": settings.get("model", self.model),
            "user": self.user_id,
            "messages": messages,
            "max_tokens": settings.get("max_tokens", self.max_tokens),
            "temperature": settings.get("temperature", 0.9),
            "top_p": settings.get("top_p", 0.9),
            "frequency_penalty": settings.get("frequency_penalty", 0.0),
            "presence_penalty": settings.get("presence_penalty", 0.0),
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

        if "value" not in normalized:
            for key in ("is_on", "on", "enabled", "active"):
                val = normalized.get(key)
                if isinstance(val, bool):
                    normalized["value"] = 1 if val else 0
                    break

        if "value" not in normalized:
            state_like = normalized.get("state")
            if isinstance(state_like, str):
                lowered = state_like.strip().lower()
                if lowered in ("on", "true", "enabled", "active", "start"):
                    normalized["value"] = 1
                elif lowered in ("off", "false", "disabled", "inactive", "stop"):
                    normalized["value"] = 0

        return normalized

    @staticmethod
    def _tool_result_content(result: Dict[str, Any]) -> str:
        payload = {
            key: value
            for key, value in result.items()
            if key in {"message", "value", "description", "arguments"}
        }
        return json.dumps(payload, ensure_ascii=True)

    def _execute_tool_call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool and retain the complete exchange for the next model turn."""
        result = self.function_handler.handle_call(name, args)
        self._protocol_trace("tool_execution", {"name": name, "arguments": args, "result": result})
        history = self.config.setdefault("conversationProtocol", [])
        content = self._tool_result_content(result)

        if self.provider in ("ollama", "local"):
            history.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }],
            })
            history.append({"role": "tool", "tool_name": name, "content": content})
        else:
            history.append({
                "role": "assistant",
                "content": "",
                "function_call": {"name": name, "arguments": json.dumps(args)},
            })
            history.append({"role": "function", "name": name, "content": content})

        result = dict(result)
        result["toolCall"] = {"name": name, "arguments": args}

        reply = self._reply_after_tool()
        if reply:
            history.append({"role": "assistant", "content": reply})
            result["spokenReply"] = reply

        return result

    def _build_headers(self) -> tuple[Dict[str, str], Optional[str]]:
        headers: Dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.provider in ("ollama", "local"):
            return headers, None

        api_key = self.config.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        settings = self.config.get("llmSettings", {}) if isinstance(self.config, dict) else {}
        require_key = bool(settings.get("requireApiKey", str(self.url).startswith("https://api.openai.com")))
        if require_key and not api_key:
            return headers, "OpenAI API key not found"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers, None

    def _reply_after_tool(self) -> str:
        """Second half of the tool-calling loop: the model sees its own tool result and answers in
        character. Sent without tools so it produces speech instead of chaining another call."""
        headers, error = self._build_headers()
        if error:
            return ""

        messages = list(self.config.get("conversationProtocol", []))
        if self._is_arch_function_mode():
            messages = self._apply_arch_system_prompt(messages)

        if self.provider in ("ollama", "local"):
            payload = self._build_ollama_request(messages, include_tools=False)
        else:
            payload = self._build_openai_request(messages, include_functions=False)

        try:
            self._protocol_trace("tool_reply_request", payload)
            data = self._post(headers, payload)
            self._protocol_trace("tool_reply_response", data)
        except Exception:
            return ""

        if not isinstance(data, dict) or data.get("error"):
            return ""

        self._emit_reasoning(data)
        message = self._strip_arch_markup(self._extract_message_text(data))
        message = self._split_reasoning(message)[0]
        if message and (self._extract_arch_tool_call(message) or self._extract_fenced_json_tool_call(message)):
            return ""
        return message

    @staticmethod
    def _request_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=120)
        return response.json()

    def _request_stream(self, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Consume Ollama's line-delimited JSON stream and rebuild one non-streamed response.

        Total generation time is unchanged; this exists so on_delta can show text as it arrives.
        """
        content_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        final: Dict[str, Any] = {}
        last_visible = ""
        last_reasoning = ""

        with requests.post(url=self.url, headers=headers, data=json.dumps(payload), timeout=120, stream=True) as response:
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                if chunk.get("error"):
                    return chunk

                message = chunk.get("message") or {}
                thinking_piece = str(message.get("thinking") or "")
                if thinking_piece:
                    thinking_parts.append(thinking_piece)

                piece = str(message.get("content") or "")
                if piece:
                    content_parts.append(piece)

                if piece or thinking_piece:
                    visible, inline_reasoning = self._split_reasoning("".join(content_parts))
                    reasoning = "\n".join(
                        part for part in ("".join(thinking_parts).strip(), inline_reasoning) if part
                    )
                    if self.on_thinking and reasoning and reasoning != last_reasoning:
                        last_reasoning = reasoning
                        try:
                            self.on_thinking(reasoning)
                        except Exception:
                            pass
                    if self.on_delta and visible and visible != last_visible:
                        last_visible = visible
                        try:
                            self.on_delta(visible)
                        except Exception:
                            pass

                calls = message.get("tool_calls")
                if isinstance(calls, list) and calls:
                    tool_calls.extend(calls)
                if chunk.get("done"):
                    final = chunk

        merged = dict(final)
        message = dict(merged.get("message") or {})
        message["role"] = message.get("role", "assistant")
        message["content"] = "".join(content_parts)
        if thinking_parts:
            message["thinking"] = "".join(thinking_parts)
        if tool_calls:
            message["tool_calls"] = tool_calls
        merged["message"] = message
        return merged

    def _post(self, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("stream"):
            return self._request_stream(headers, payload)
        return self._request_json(self.url, headers, payload)

    def _format_provider_error(self, err: Any) -> str:
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        lowered = message.lower()

        if self.provider in ("ollama", "local") and "not found" in lowered and "model" in lowered:
            if "/" in str(self.model):
                return (
                    f"Model '{self.model}' is a Hugging Face model ID, not an Ollama tag. "
                    "Use an Ollama model tag (e.g. 'qwen2.5:3b') with provider='ollama', "
                    "or set llmSettings.aiHatPlus.url to your AI HAT+ OpenAI-compatible endpoint "
                    "and set provider='openai'."
                )
            return (
                f"Model '{self.model}' not found in Ollama. Run 'ollama pull {self.model}' "
                "or pick an installed model from 'ollama list'."
            )

        return message

    @staticmethod
    def _extract_arch_tool_call(message: str) -> Optional[Dict[str, Any]]:
        if not message:
            return None
        match = re.search(r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>", message, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        args = data.get("arguments", {})
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(args, dict):
            args = {}
        return {"name": name.strip(), "arguments": args}

    @staticmethod
    def _extract_fenced_json_tool_call(message: str) -> Optional[Dict[str, Any]]:
        if not message:
            return None

        blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", message, flags=re.IGNORECASE)
        for block in blocks:
            payload = block.strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            # Common direct tool-call shape: {"name":"...", "arguments":{...}}
            name = data.get("name")
            args = data.get("arguments", {})
            if isinstance(name, str) and name.strip():
                if not isinstance(args, dict):
                    args = {}
                return {"name": name.strip(), "arguments": args}

            # Some small models emit wrapped shape: {"function": {...}}.
            # We only accept executable calls and explicitly flag schema blobs.
            fn_obj = data.get("function")
            if isinstance(fn_obj, dict):
                fn_name = fn_obj.get("name")
                fn_args = fn_obj.get("arguments", {})

                if isinstance(fn_name, str) and fn_name.strip() and isinstance(fn_args, dict):
                    return {"name": fn_name.strip(), "arguments": fn_args}

                fn_params = fn_obj.get("parameters")
                has_schema_signature = isinstance(fn_params, dict) and any(
                    key in fn_params for key in ("properties", "required", "type", "title")
                )
                if has_schema_signature:
                    return {"_schema_only": True}
        return None

    def _resolve_tool_alias(self, raw_name: str, args: Dict[str, Any]) -> Optional[str]:
        name = (raw_name or "").strip()
        if not name:
            return None

        catalog = self._function_catalog(self.config)
        if name in catalog:
            return name

        arg_name = str(args.get("name", "")).strip().lower()
        if name.lower() in {"toggle", "switch", "set", "set_state", "inform"} and arg_name == "led" and "set_LED" in catalog:
            return "set_LED"

        return None

    @staticmethod
    def _strip_arch_markup(message: str) -> str:
        if not message:
            return ""
        cleaned = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", message, flags=re.IGNORECASE)
        cleaned = re.sub(r"<tool_response>[\s\S]*?</tool_response>", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def send(self, text: str, role: str, function_name: Optional[str] = None) -> Dict[str, Any]:
        if not text:
            return {"role": "assistant", "message": ""}

        messages = self._build_messages(text, role, function_name)
        headers, header_error = self._build_headers()
        if header_error:
            return {"role": "error", "message": header_error}

        if self.provider in ("ollama", "local"):
            payload = self._build_ollama_request(messages)
        else:
            payload = self._build_openai_request(messages)

        if self._debug_enabled():
            self._debug_log("request.payload", payload)
            self._protocol_trace("request", payload)

        try:
            data = self._post(headers, payload)
        except Exception as exc:
            return {"role": "error", "message": f"Error fetching {self.url}: {exc}"}

        if self._debug_enabled():
            self._debug_log("response.raw", data)
            self._protocol_trace("response", data)

        self._emit_reasoning(data)

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            return {"role": "error", "message": self._format_provider_error(err)}

        openai_fc = data.get("choices", [{}])[0].get("message", {}).get("function_call") if isinstance(data, dict) else None
        ollama_tc = data.get("message", {}).get("tool_calls", []) if isinstance(data, dict) else []

        if self._debug_enabled():
            self._debug_log(
                "response.tool_fields",
                {"openai_function_call": openai_fc, "ollama_tool_calls": ollama_tc},
            )
            self._protocol_trace(
                "tool_fields",
                {"openai_function_call": openai_fc, "ollama_tool_calls": ollama_tc},
            )

        blocked_tool_attempt = False

        if openai_fc and openai_fc.get("name"):
            name = openai_fc["name"]
            try:
                args = json.loads(openai_fc.get("arguments", "{}"))
            except Exception:
                args = {}
            args = self._normalize_tool_args(name, args, text)
            return self._execute_tool_call(name, args)

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
                raw_args = self._normalize_tool_args(name, raw_args or {}, text)
                return self._execute_tool_call(name, raw_args)

        message = self._extract_message_text(data) if isinstance(data, dict) else ""
        # Reasoning is shown live in the UI; it must not reach history or the spoken reply.
        message = self._split_reasoning(message)[0]

        arch_call = self._extract_arch_tool_call(message)
        if arch_call:
            name = arch_call["name"]
            args = self._normalize_tool_args(name, arch_call.get("arguments", {}), text)
            return self._execute_tool_call(name, args)

        fenced_call = self._extract_fenced_json_tool_call(message)
        if fenced_call:
            if fenced_call.get("_schema_only"):
                blocked_tool_attempt = True
                message = ""
            else:
                raw_name = fenced_call.get("name", "")
                args = fenced_call.get("arguments", {})
                resolved_name = self._resolve_tool_alias(str(raw_name), args if isinstance(args, dict) else {})
                if resolved_name:
                    norm_args = self._normalize_tool_args(resolved_name, args if isinstance(args, dict) else {}, text)
                    return self._execute_tool_call(resolved_name, norm_args)
                blocked_tool_attempt = True

        message = self._strip_arch_markup(message)

        if blocked_tool_attempt:
            message = ""

        # If tool-calling output was blocked or no text was produced, retry once without tools/functions.
        if not message:
            try:
                if self.provider in ("ollama", "local"):
                    fallback_payload = self._build_ollama_request(messages, include_tools=False)
                else:
                    fallback_payload = self._build_openai_request(messages, include_functions=False)
                fallback_data = self._post(headers, fallback_payload)
                self._protocol_trace("fallback_request", fallback_payload)
                self._protocol_trace("fallback_response", fallback_data)
                if isinstance(fallback_data, dict):
                    message = self._extract_message_text(fallback_data)
            except Exception:
                pass

        # Last-resort cleanup for small models that keep emitting pseudo tool-call JSON.
        if message and (self._extract_arch_tool_call(message) or self._extract_fenced_json_tool_call(message)):
            message = ""

        self.config.setdefault("conversationProtocol", []).append({"role": "assistant", "content": message})
        return {"role": "assistant", "message": message}
