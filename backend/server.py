from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

try:
    from .config_loader import load_config
    from .device_ws_comm import DeviceWebSocketCommunication
    from .function_handler import FunctionHandler
    from .llm_api import LLMAPI
    from .serial_comm import SerialCommunication
    from .speech_workers import SpeechToTextWorker, TextToSpeechWorker
    from . import tui
except ImportError:
    from config_loader import load_config
    from device_ws_comm import DeviceWebSocketCommunication
    from function_handler import FunctionHandler
    from llm_api import LLMAPI
    from serial_comm import SerialCommunication
    from speech_workers import SpeechToTextWorker, TextToSpeechWorker
    import tui

BACKEND_PORT = 3000
MAX_SESSIONS_HARD_CAP = 10


class DeviceSession:
    """One connected device (an ESP32/WiFi device, or the single legacy Serial/BLE device).

    Each session owns its own comm object, tool set, and LLM conversation history, so multiple
    devices can be connected at once without cross-talk. TTS is intentionally not session-scoped
    right now (see config.toml's ttsEnabled) - it is disabled by default; a future version may
    stream synthesized speech back to a specific device's own speaker.
    """

    def __init__(self, session_id: str, comm: Any, config: Dict[str, Any], kind: str = "Device") -> None:
        self.session_id = session_id
        self.kind = kind
        self.comm = comm
        self.config = config
        self.function_handler = FunctionHandler(config, comm)
        self.llm_api = LLMAPI(
            config,
            self.function_handler,
            on_delta=self._on_llm_delta,
            on_thinking=self._on_llm_thinking,
        )
        self.stt: Optional[SpeechToTextWorker] = None
        # Per session, so one device's turns stay ordered without blocking the other devices.
        self.lock = asyncio.Lock()

    def _on_llm_delta(self, partial: str) -> None:
        tui.update_response(self.session_id, partial)

    def _on_llm_thinking(self, reasoning: str) -> None:
        tui.update_thinking(self.session_id, reasoning)

    def close(self) -> None:
        if self.stt:
            self.stt.close()
            self.stt = None
        close_fn = getattr(self.comm, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass


def _submit_terminal_prompt(session_id: Optional[str], text: str) -> None:
    _submit(_process_terminal_prompt(session_id, text))


def _log_llm_startup() -> None:
    # Built from a throwaway config/handler pair just to report the model that new sessions will
    # use; not tied to any live device.
    temp_config = _build_session_config()
    temp_handler = FunctionHandler(temp_config, None)
    temp_llm = LLMAPI(temp_config, temp_handler)

    details = temp_llm.get_model_details()
    tui.set_model_summary(f"LLM: {details['model']} ({details['provider']})")
    print("🧠 LLM configuration")
    print(f"   Provider: {details['provider']}")
    print(f"   Model: {details['model']}")
    print(f"   Endpoint: {details['url']}")
    print(
        "   Generation: "
        f"temperature={details['temperature']}, top_p={details['top_p']}, "
        f"top_k={details['top_k']}, max_tokens={details['max_tokens']}, "
        f"repeat_penalty={details['repeat_penalty']}"
    )
    print(f"   Tool calling: {len(temp_handler.get_all_functions())} configured tool(s)")

    ollama = details.get("ollama")
    if isinstance(ollama, dict):
        metadata = [
            str(ollama[key])
            for key in ("family", "parameter_size", "quantization_level", "format")
            if ollama.get(key)
        ]
        if metadata:
            print(f"   Ollama model: {', '.join(metadata)}")


def _request_shutdown() -> None:
    import os
    import signal

    os.kill(os.getpid(), signal.SIGTERM)

REPO_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="LLM Embodiments Python Backend")


class BackendState:
    def __init__(self) -> None:
        self.clients: List[WebSocket] = []
        self.latest_image = "/scratch_files/latest.jpg"
        self.volume = 50
        self.config: Dict[str, Any] = {}
        self.sessions: Dict[str, DeviceSession] = {}
        self.session_counter = 0
        self.max_sessions = 10
        self.comm_method = "Serial"
        self.tts = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.llm_seq = 0

    def get_speech_settings(self) -> Dict[str, Any]:
        language = self.config.get("activeLanguage", "en")
        speech = self.config.get("speech", {})
        profile = speech.get("languageProfiles", {}).get(language, {})
        whisper = speech.get("whisper", {}) if isinstance(speech.get("whisper"), dict) else {}
        return {
            "sttBackend": speech.get("sttBackend", "vosk"),
            "speechToTextModel": profile.get("speechToTextModel", "vosk-model-small-en-us-0.15"),
            "textToSpeechModel": profile.get("textToSpeechModel", "en_GB-alan-low.onnx"),
            "whisperDevice": whisper.get("device", "auto"),
            "whisperComputeType": whisper.get("computeType", "auto"),
            "whisperDeviceIndex": whisper.get("deviceIndex", 0),
            "whisperLanguage": whisper.get("language", "auto"),
        }


state = BackendState()


def _build_session_config() -> Dict[str, Any]:
    """Fresh per-session config: shares static settings (functions.tools, speech, etc.) with the
    global config, but gets its own conversationProtocol/llmSettings so devices don't share
    conversation history or generation overrides."""
    session_config = dict(state.config)
    session_config["conversationProtocol"] = [dict(msg) for msg in state.config.get("conversationProtocol", [])]
    session_config["llmSettings"] = dict(state.config.get("llmSettings", {}))
    return session_config


def _submit(coro: Any) -> Optional[Future]:
    """Submit coroutine from worker threads to the running FastAPI event loop."""
    if state.loop is None or state.loop.is_closed():
        try:
            coro.close()
        except Exception:
            pass
        print("⚠️ Event loop not ready; dropped async task")
        return None

    try:
        return asyncio.run_coroutine_threadsafe(coro, state.loop)
    except Exception as exc:
        try:
            coro.close()
        except Exception:
            pass
        print(f"⚠️ Failed to submit async task: {exc}")
        return None


def _clean_assistant_message(raw_message: str) -> str:
    message = raw_message or ""
    message = re.sub(r"<think>[\s\S]*?</think>", "", message, flags=re.IGNORECASE)
    message = re.sub(r"^\s*assistant\s*\n+", "", message, flags=re.IGNORECASE)
    message = re.sub(r"^\s*(assistant|system)\s*:\s*", "", message, flags=re.IGNORECASE)
    message = re.sub(r"<\|im_start\|>|<\|im_end\|>|<\|assistant\|>", "", message)
    return message.strip()


def _output_sanitizer_settings() -> Dict[str, Any]:
    settings = state.config.get("llmSettings", {}) if isinstance(state.config, dict) else {}
    sanitizer = settings.get("outputSanitizer", {}) if isinstance(settings, dict) else {}
    return sanitizer if isinstance(sanitizer, dict) else {}


def _configured_function_names(session: Optional[DeviceSession]) -> List[str]:
    names: List[str] = []
    if not session or not session.function_handler:
        return names
    for fn in session.function_handler.get_all_functions():
        name = str(fn.get("name", "")).strip()
        if name:
            names.append(name)
    return sorted(set(names), key=len, reverse=True)


def _parse_inline_argument(raw: str) -> Any:
    token = raw.strip()
    if not token:
        return ""

    lowered = token.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "none"):
        return ""

    if re.fullmatch(r"-?\d+", token):
        try:
            return int(token)
        except Exception:
            pass
    if re.fullmatch(r"-?\d+\.\d+", token):
        try:
            return float(token)
        except Exception:
            pass

    if (token.startswith("{") and token.endswith("}")) or (token.startswith("[") and token.endswith("]")):
        try:
            return json.loads(token)
        except Exception:
            pass

    if len(token) >= 2 and ((token[0] == '"' and token[-1] == '"') or (token[0] == "'" and token[-1] == "'")):
        return token[1:-1]

    return token


def _strip_inline_pseudo_calls(message: str, pattern: re.Pattern[str]) -> str:
    cleaned = pattern.sub("", message)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    return cleaned.strip()


async def _process_inline_pseudo_calls(session: DeviceSession, message: str) -> str:
    sanitizer = _output_sanitizer_settings()
    strip_enabled = bool(sanitizer.get("stripPseudoToolCalls", True))
    execute_enabled = bool(sanitizer.get("executeInlinePseudoCalls", False))

    names = _configured_function_names(session)
    if not names:
        return message

    name_pattern = "|".join(re.escape(name) for name in names)
    call_pattern = re.compile(rf"(?i)(?<![A-Za-z0-9_])(?P<name>{name_pattern})\s*\(\s*(?P<args>[^()\n]{{0,180}})\s*\)")
    matches = list(call_pattern.finditer(message))
    if not matches:
        return message

    if execute_enabled and session.function_handler:
        for match in matches:
            fn_name = match.group("name")
            raw_args = (match.group("args") or "").strip()
            arg_value = _parse_inline_argument(raw_args)
            payload = {} if raw_args == "" else {"value": arg_value}
            try:
                result = session.function_handler.handle_call(fn_name, payload)
                _log_tool_call(session, fn_name, payload, result)
                await _handle_llm_response(session, result)
            except Exception as exc:
                await _update_frontend(f"Inline tool execution failed for {fn_name}: {exc}", "error")

    if strip_enabled:
        return _strip_inline_pseudo_calls(message, call_pattern)
    return message


async def _broadcast(payload: Dict[str, Any]) -> None:
    dead: List[WebSocket] = []
    text = json.dumps(payload)
    for ws in state.clients:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in state.clients:
            state.clients.remove(ws)


async def _update_frontend(
    message: str | None = None,
    message_type: str | None = None,
    complete: bool | None = None,
) -> None:
    data: Dict[str, Any] = {"backEnd": {}}
    if message is not None:
        data["backEnd"]["message"] = message
    if message_type is not None:
        data["backEnd"]["messageType"] = message_type
    if complete is not None:
        data["backEnd"]["complete"] = complete
    await _broadcast(data)


async def _frontend_function(function_name: str, args: Any) -> None:
    await _broadcast({"backEnd": {"functionName": function_name, "arguments": args}})


def _show_watermelons(args: Any) -> str:
    try:
        count = int(args.get("value", 3)) if isinstance(args, dict) else 3
    except (TypeError, ValueError):
        count = 3
    count = max(0, min(count, 20))
    return f"Watermelon test ({count}): {'🍉' * count}"


def _log_tool_call(session: DeviceSession, name: str, args: Any, result: Dict[str, Any]) -> None:
    if isinstance(args, dict):
        arg_text = ", ".join(f"{key}={value!r}" for key, value in args.items())
    else:
        arg_text = "" if args is None else repr(args)

    outcome = str(result.get("value", "")).strip() or str(result.get("message", "")).strip()
    try:
        parsed = json.loads(outcome)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        # Device replies are wrapped, e.g. {"response": {"description": ..., "value": ...}}.
        label, payload = next(iter(parsed.items())) if len(parsed) == 1 else ("", parsed)
        if isinstance(payload, dict) and "value" in payload:
            payload = payload["value"]
        outcome = f"{label}: {payload}" if label else str(payload)

    line = f"\U0001F6E0\uFE0F  {name}({arg_text})"
    if outcome:
        line += f" \u2192 {outcome}"
    print(f"\U0001F6E0\uFE0F  [{session.session_id}] {line}")
    tui.log_device(session.session_id, line)


async def _emit_assistant(session: DeviceSession, raw_message: str) -> None:
    message = _clean_assistant_message(raw_message)
    message = await _process_inline_pseudo_calls(session, message)
    if not message:
        return
    tui.update_response(session.session_id, message)
    tui.log_device(session.session_id, f"🤖 {message}")
    await _update_frontend(message, "assistant")
    if state.tts and state.config.get("ttsEnabled", False):
        settings = state.get_speech_settings()
        state.tts.say(message, settings["textToSpeechModel"], int(state.volume))


async def _handle_llm_response(session: DeviceSession, return_object: Dict[str, Any]) -> None:
    role = return_object.get("role")
    message_preview = str(return_object.get("message", ""))[:160].replace("\n", " ")
    print(f"🧭 [{session.session_id}] LLM response role={role} message={message_preview!r}")
    tool_call = return_object.get("toolCall")
    if isinstance(tool_call, dict):
        _log_tool_call(session, str(tool_call.get("name", "?")), tool_call.get("arguments"), return_object)
    if role == "assistant":
        await _emit_assistant(session, str(return_object.get("message", "")))
        return

    # A tool call still gets a spoken answer, so the device doesn't go silent after acting.
    spoken = str(return_object.get("spokenReply", ""))
    if spoken:
        await _emit_assistant(session, spoken)

    if role == "function":
        await _frontend_function(str(return_object.get("message", "")), return_object.get("arguments", {}))
        await _update_frontend(str(return_object.get("message", "")), "system")
        return

    if role == "functionReturnValue":
        value = str(return_object.get("value", ""))
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None

        if isinstance(parsed, dict) and "Writing to Serial" in parsed:
            serial_value = str(parsed["Writing to Serial"])
            if serial_value.startswith("Error:"):
                await _update_frontend(serial_value, "error")
            else:
                await _update_frontend(f"Executed: {serial_value}", "system")
                if not spoken:
                    await _update_frontend("Done.", "assistant")
            return

        await _update_frontend(value, "system")
        return

    if role == "notification" and return_object.get("function_name") == "watermelon_test":
        watermelons = _show_watermelons(return_object.get("arguments", {}))
        print(f"🍉 [{session.session_id}] {watermelons}")
        tui.log_device(session.session_id, watermelons)
        await _update_frontend(watermelons, "system")
        return

    if role in ("error", "system", "notification"):
        await _update_frontend(
            str(return_object.get("message", "")),
            "error" if role == "error" else "system",
        )


async def _call_llm(session: DeviceSession, text: str, role: str, source: str) -> Optional[Dict[str, Any]]:
    if not session.llm_api:
        return None

    state.llm_seq += 1
    req_id = state.llm_seq
    preview = text[:80].replace("\n", " ")
    print(f"🤖 LLM[{req_id}] [{session.session_id}] {source} queued role={role} text={preview!r}")
    tui.update_prompt(session.session_id, text)
    tui.reset_thinking(session.session_id)
    tui.log_device(session.session_id, f"💬 {text}")

    try:
        # Only this device's own turns are serialized; other devices run concurrently and are
        # queued by the model server itself (see Ollama's OLLAMA_NUM_PARALLEL).
        async with session.lock:
            print(f"🤖 LLM[{req_id}] [{session.session_id}] {source} started")
            response = await asyncio.to_thread(session.llm_api.send, text, role)
            tui.reset_thinking(session.session_id)
            print(f"🤖 LLM[{req_id}] [{session.session_id}] {source} completed")
            return response
    except Exception as exc:
        print(f"⚠️ LLM[{req_id}] [{session.session_id}] {source} failed: {exc}")
        return {"role": "error", "message": f"LLM call failed: {exc}"}


def _com_callback(session_id: str, message: str) -> None:
    _submit(_process_system_message(session_id, message))


async def _process_system_message(session_id: str, message: str) -> None:
    session = state.sessions.get(session_id)
    if not session:
        return
    response = await _call_llm(session, message, "system", "system-callback")
    if response:
        await _handle_llm_response(session, response)


async def _process_terminal_prompt(session_id: Optional[str], text: str) -> None:
    session = state.sessions.get(session_id) if session_id else None
    if session is None:
        session = next(iter(state.sessions.values()), None)
    if session is None:
        print("⚠️ No connected device session to send the test prompt to.")
        return
    await _update_frontend(text, "user", True)
    response = await _call_llm(session, text, "user", "terminal-input")
    if response:
        await _handle_llm_response(session, response)


def _stt_callback(session_id: str, msg: Dict[str, Any]) -> None:
    _submit(_process_stt_message(session_id, msg))


async def _process_stt_message(session_id: str, msg: Dict[str, Any]) -> None:
    session = state.sessions.get(session_id)
    if not session:
        return
    complete = False
    speech = ""
    if msg.get("confirmedText"):
        complete = True
        speech = str(msg["confirmedText"])
        print(f"🎤 [{session_id}] STT confirmed text -> LLM: {speech}")
        tui.update_stt(session_id, speech)
        tui.log_device(session_id, f"🎤 {speech}")
        response = await _call_llm(session, speech, "user", "stt")
        if response:
            await _handle_llm_response(session, response)
    elif msg.get("interimResult"):
        speech = str(msg["interimResult"])
        tui.update_stt(session_id, speech)
    await _update_frontend(speech, "user", complete)


def _tts_callback(msg: Dict[str, Any]) -> None:
    # TTS is currently global/disabled (see config.toml's ttsEnabled); when re-enabled, pause/resume
    # every session's STT so the shared speaker output isn't picked back up by any mic.
    status = msg.get("tts")
    if status in ("started", "resumed"):
        for session in state.sessions.values():
            if session.stt:
                session.stt.pause()
    elif status in ("stopped", "paused"):
        for session in state.sessions.values():
            if session.stt:
                session.stt.resume()


async def _push_device_config(session: DeviceSession) -> None:
    ws = getattr(session.comm, "ws", None)
    if not ws:
        return

    tools: List[Dict[str, Any]] = []
    for fn in session.function_handler.get_all_functions():
        if fn.get("target") != "device":
            continue
        tools.append({
            "name": fn.get("name"),
            "deviceCommand": fn.get("deviceCommand"),
            "dataType": fn.get("parameters", {}).get("properties", {}).get("value", {}).get("type", "string"),
            "description": fn.get("description", ""),
        })

    payload = {
        "config": {
            "tools": tools,
            "activeLanguage": state.config.get("activeLanguage", "en"),
            "volume": state.volume,
        }
    }
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


def _apply_device_info(session: DeviceSession, device_info: Dict[str, Any]) -> None:
    # Applied to this session's own config only, so it takes effect immediately for this device
    # without affecting any other connected device.
    persona = device_info.get("persona")
    if isinstance(persona, str) and persona.strip():
        protocol = session.config.setdefault("conversationProtocol", [])
        for msg in protocol:
            if msg.get("role") == "system":
                msg["content"] = persona.strip()
                break
        else:
            protocol.insert(0, {"role": "system", "content": persona.strip()})
        print(f"🧠 [{session.session_id}] Device system prompt installed: {persona.strip()!r}")

    history = device_info.get("history")
    if isinstance(history, list):
        protocol = session.config.setdefault("conversationProtocol", [])
        system_msg = next((m for m in protocol if m.get("role") == "system"), None)
        new_protocol = [system_msg] if system_msg else []
        for turn in history:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "")).strip()
            content = str(turn.get("content", "")).strip()
            if role in ("user", "assistant") and content:
                new_protocol.append({"role": role, "content": content})
        session.config["conversationProtocol"] = new_protocol
        print(f"📜 [{session.session_id}] Device conversation history installed ({len(new_protocol) - (1 if system_msg else 0)} turn(s))")

    notification_guidance = device_info.get("notificationGuidance")
    if isinstance(notification_guidance, list):
        instructions: List[str] = []
        for entry in notification_guidance:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            instruction = str(entry.get("instruction", "")).strip()
            if name and instruction:
                instructions.append(f"- Notification `{name}`: {instruction}")
        if instructions:
            protocol = session.config.setdefault("conversationProtocol", [])
            system_msg = next((message for message in protocol if message.get("role") == "system"), None)
            if system_msg is not None:
                system_msg["content"] = (
                    f"{str(system_msg.get('content', '')).rstrip()}\n\n"
                    "Device notification instructions:\n"
                    + "\n".join(instructions)
                )
                print(f"🔔 [{session.session_id}] Device notification guidance installed ({len(instructions)} rule(s))")

    generation = device_info.get("generation")
    if isinstance(generation, dict):
        settings = session.config.setdefault("llmSettings", {})
        if isinstance(settings, dict):
            applied: List[str] = []
            model = generation.get("model")
            if isinstance(model, str) and model.strip():
                settings["model"] = model.strip()
                applied.append(f"model={model.strip()}")

            ranges = {
                "temperature": (-2.0, 2.0),
                "top_p": (0.0, 1.0),
                "repeat_penalty": (0.0, 2.0),
            }
            for name, (minimum, maximum) in ranges.items():
                try:
                    value = float(generation[name])
                except (KeyError, TypeError, ValueError):
                    continue
                if minimum <= value <= maximum:
                    settings[name] = value
                    applied.append(f"{name}={value:g}")

            limits = {"top_k": (1, 100), "max_tokens": (1, 8192)}
            for name, (minimum, maximum) in limits.items():
                try:
                    value = int(generation[name])
                except (KeyError, TypeError, ValueError):
                    continue
                if minimum <= value <= maximum:
                    settings[name] = value
                    applied.append(f"{name}={value}")

            if applied:
                print(f"🎛️  [{session.session_id}] Device generation settings installed: {', '.join(applied)}")

    tools = device_info.get("tools")
    if isinstance(tools, list) and session.function_handler:
        session.function_handler.register_device_tools(tools)
        clean_tools = [tool for tool in tools if isinstance(tool, dict)]
        tui.update_tools(session.session_id, clean_tools)
        tui.update_session(session.session_id, kind=session.kind, status=f"connected, {len(tools)} tool(s) synced")
        print(f"🛠️  [{session.session_id}] Device tools registered ({len(tools)}):")
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name", "?")
            description = tool.get("description", "")
            data_type = tool.get("dataType", "string")
            print(f"   - {name} ({data_type}): {description}")



def _start_session_stt(session: DeviceSession, source: str) -> None:
    if state.config.get("muteMicrophone", False):
        return
    speech = state.get_speech_settings()
    session.stt = SpeechToTextWorker(
        REPO_ROOT,
        lambda msg, sid=session.session_id: _stt_callback(sid, msg),
        speech["speechToTextModel"],
        speech["sttBackend"],
        source=source,
        device=speech["whisperDevice"],
        compute_type=speech["whisperComputeType"],
        device_index=speech["whisperDeviceIndex"],
        language=speech["whisperLanguage"],
    )


def _reload_runtime() -> None:
    for session in list(state.sessions.values()):
        session.close()
    state.sessions.clear()
    tui.clear_devices()

    if state.tts:
        state.tts.close()
        state.tts = None

    state.config = load_config(REPO_ROOT)
    state.volume = int(state.config.get("volume", 50))
    state.max_sessions = max(1, min(MAX_SESSIONS_HARD_CAP, int(state.config.get("maxDeviceSessions", 10))))

    comm_method = state.config.get("communicationMethod", "Serial")
    state.comm_method = comm_method

    if comm_method != "WiFi":
        # Single legacy Serial/BLE device, modeled as a fixed one-entry session so it reuses the
        # same LLM/tool-call machinery as WiFi devices.
        session_config = _build_session_config()
        comm = SerialCommunication(lambda msg: _com_callback("device-1", msg), session_config)
        result = comm.connect()
        session = DeviceSession("device-1", comm, session_config, kind="Serial")
        state.sessions[session.session_id] = session
        tui.add_device_tab(session.session_id, kind="Serial")
        tui.update_session(session.session_id, kind="Serial", status=None if result.get("error") else "connected")
        _start_session_stt(session, source="local")
    else:
        print(f"📡 Waiting for WiFi device connections (up to {state.max_sessions})...")

    _log_llm_startup()

    if bool(state.config.get("ttsEnabled", False)):
        state.tts = TextToSpeechWorker(REPO_ROOT, _tts_callback)


@app.on_event("startup")
async def startup() -> None:
    state.loop = asyncio.get_running_loop()
    asyncio.create_task(tui.start(port=BACKEND_PORT, on_quit=_request_shutdown, on_prompt=_submit_terminal_prompt))
    _reload_runtime()


@app.on_event("shutdown")
async def shutdown() -> None:
    for session in list(state.sessions.values()):
        session.close()
    state.sessions.clear()
    if state.tts:
        state.tts.close()
    state.loop = None
    app_instance = tui.get_app()
    if app_instance is not None:
        app_instance.exit()


@app.websocket("/")
async def websocket_root(ws: WebSocket) -> None:
    client_host = ws.client.host if ws.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        await ws.close()
        return

    await ws.accept()
    state.clients.append(ws)
    tui.update_session("Frontend UI", kind="Frontend", status=f"{len(state.clients)} client(s)")

    last_assistant = next(
        (m for m in reversed(state.config.get("conversationProtocol", [])) if m.get("role") == "assistant"),
        None,
    )
    if last_assistant:
        await _update_frontend(last_assistant.get("content", ""), "assistant")

    try:
        while True:
            message = await ws.receive_text()
            try:
                cmd = json.loads(message)
            except Exception:
                cmd = {"text": message.strip()}

            if cmd.get("command") == "pause":
                for session in state.sessions.values():
                    if session.stt:
                        session.stt.pause()
            elif cmd.get("command") == "resume":
                for session in state.sessions.values():
                    if session.stt:
                        session.stt.resume()
            elif cmd.get("command") == "setVolume":
                try:
                    state.volume = int(cmd.get("value", state.volume))
                except Exception:
                    pass
            elif cmd.get("command") == "protocol":
                await ws.send_text(json.dumps(state.config.get("conversationProtocol", [])))
            elif cmd.get("command") == "reload-config":
                _reload_runtime()
                for session in state.sessions.values():
                    await _push_device_config(session)
            elif cmd.get("command") == "sendMessage":
                text = str(cmd.get("message", "")).strip()
                target = next(iter(state.sessions.values()), None)
                if text and target:
                    await _update_frontend(text, "user", True)
                    response = await _call_llm(target, text, "user", "ws-sendMessage")
                    if response:
                        await _handle_llm_response(target, response)
            elif cmd.get("text"):
                target = next(iter(state.sessions.values()), None)
                if target:
                    response = await _call_llm(target, cmd["text"], "user", "ws-text")
                    if response:
                        await _handle_llm_response(target, response)
            elif cmd.get("frontEnd"):
                payload = cmd.get("frontEnd", {})
                await _update_frontend(f"frontEnd return: {payload}", "system")
    except WebSocketDisconnect:
        if ws in state.clients:
            state.clients.remove(ws)
        if state.clients:
            tui.update_session("Frontend UI", kind="Frontend", status=f"{len(state.clients)} client(s)")
        else:
            tui.remove_session("Frontend UI")


@app.websocket("/device")
async def websocket_device(ws: WebSocket) -> None:
    # LAN-only prototyping endpoint for WiFi devices (e.g. M5Stack); no auth like websocket_root's
    # localhost check. Supports up to state.max_sessions devices connected at once, each with its
    # own tools, conversation history, and STT session.
    if state.comm_method != "WiFi":
        await ws.close()
        return

    if len(state.sessions) >= state.max_sessions:
        print(f"⚠️ Rejected device connection: max of {state.max_sessions} devices already connected")
        await ws.close(code=1013)
        return

    await ws.accept()
    state.session_counter += 1
    session_id = f"esp32-{state.session_counter}"

    session_config = _build_session_config()
    comm = DeviceWebSocketCommunication(lambda msg, sid=session_id: _com_callback(sid, msg), session_config, _submit)
    comm.attach(ws)
    session = DeviceSession(session_id, comm, session_config, kind="WiFi")
    state.sessions[session_id] = session

    tui.add_device_tab(session_id, kind="WiFi")
    tui.update_session(session_id, kind="WiFi", status="connected")
    tui.update_tools(session_id, [])
    _start_session_stt(session, source="remote")
    await _push_device_config(session)

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            audio_chunk = message.get("bytes")
            if audio_chunk is not None:
                if session.stt and hasattr(session.stt, "push_audio"):
                    session.stt.push_audio(audio_chunk)
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
                comm.receive(str(notification.get("name", "")), str(notification.get("value", "")))
            elif data.get("mic") == "muted" and session.stt:
                session.stt.pause()
            elif data.get("mic") == "unmuted" and session.stt:
                session.stt.resume()
            elif isinstance(data.get("deviceInfo"), dict):
                _apply_device_info(session, data["deviceInfo"])
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
        state.sessions.pop(session_id, None)
        tui.remove_device_tab(session_id)
        tui.remove_session(session_id)
        print(f"🔌 [{session_id}] Device disconnected.")


@app.get("/api/latest-image")
async def get_latest_image() -> JSONResponse:
    return JSONResponse({"image": state.latest_image})


@app.post("/api/latest-image")
async def post_latest_image(payload: Dict[str, Any]) -> JSONResponse:
    state.latest_image = str(payload.get("image", state.latest_image))
    return JSONResponse({"ok": True, "image": state.latest_image})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.server:app", host="0.0.0.0", port=BACKEND_PORT, reload=False)
