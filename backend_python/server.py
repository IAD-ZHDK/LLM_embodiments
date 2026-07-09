from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

try:
    from .config_loader import load_config
    from .function_handler import FunctionHandler
    from .llm_api import LLMAPI
    from .serial_comm import SerialCommunication
    from .speech_workers import SpeechToTextWorker, TextToSpeechWorker
except ImportError:
    from config_loader import load_config
    from function_handler import FunctionHandler
    from llm_api import LLMAPI
    from serial_comm import SerialCommunication
    from speech_workers import SpeechToTextWorker, TextToSpeechWorker

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"

app = FastAPI(title="LLM Embodiments Python Backend")


class BackendState:
    def __init__(self) -> None:
        self.clients: List[WebSocket] = []
        self.latest_image = "/scratch_files/latest.jpg"
        self.volume = 50
        self.config: Dict[str, Any] = {}
        self.serial = None
        self.function_handler = None
        self.llm_api = None
        self.stt = None
        self.tts = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.llm_lock: Optional[asyncio.Lock] = None
        self.llm_seq = 0

    def get_speech_settings(self) -> Dict[str, str]:
        language = self.config.get("activeLanguage", "en")
        speech = self.config.get("speech", {})
        profile = speech.get("languageProfiles", {}).get(language, {})
        return {
            "sttBackend": speech.get("sttBackend", "vosk"),
            "speechToTextModel": profile.get("speechToTextModel", "vosk-model-small-en-us-0.15"),
            "textToSpeechModel": profile.get("textToSpeechModel", "en_GB-alan-low.onnx"),
        }


state = BackendState()


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


def _configured_function_names() -> List[str]:
    names: List[str] = []
    if not state.function_handler:
        return names
    for fn in state.function_handler.get_all_functions():
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


async def _process_inline_pseudo_calls(message: str) -> str:
    sanitizer = _output_sanitizer_settings()
    strip_enabled = bool(sanitizer.get("stripPseudoToolCalls", True))
    execute_enabled = bool(sanitizer.get("executeInlinePseudoCalls", False))

    names = _configured_function_names()
    if not names:
        return message

    name_pattern = "|".join(re.escape(name) for name in names)
    call_pattern = re.compile(rf"(?i)(?<![A-Za-z0-9_])(?P<name>{name_pattern})\s*\(\s*(?P<args>[^()\n]{{0,180}})\s*\)")
    matches = list(call_pattern.finditer(message))
    if not matches:
        return message

    if execute_enabled and state.function_handler:
        for match in matches:
            fn_name = match.group("name")
            raw_args = (match.group("args") or "").strip()
            arg_value = _parse_inline_argument(raw_args)
            payload = {} if raw_args == "" else {"value": arg_value}
            try:
                result = state.function_handler.handle_call(fn_name, payload)
                await _handle_llm_response(result)
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


async def _handle_llm_response(return_object: Dict[str, Any]) -> None:
    role = return_object.get("role")
    message_preview = str(return_object.get("message", ""))[:160].replace("\n", " ")
    print(f"🧭 LLM response role={role} message={message_preview!r}")

    if role == "assistant":
        message = _clean_assistant_message(str(return_object.get("message", "")))
        message = await _process_inline_pseudo_calls(message)
        if not message:
            return
        await _update_frontend(message, "assistant")
        settings = state.get_speech_settings()
        if state.tts:
            state.tts.say(message, settings["textToSpeechModel"], int(state.volume))
        return

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
                # Do not call the LLM again here, otherwise function-capable models can recurse.
                await _update_frontend("Done.", "assistant")
            return

        await _update_frontend(value, "system")
        return

    if role in ("error", "system", "notification"):
        await _update_frontend(
            str(return_object.get("message", "")),
            "error" if role == "error" else "system",
        )


async def _call_llm(text: str, role: str, source: str) -> Optional[Dict[str, Any]]:
    if not state.llm_api:
        return None

    if state.llm_lock is None:
        state.llm_lock = asyncio.Lock()

    state.llm_seq += 1
    req_id = state.llm_seq
    preview = text[:80].replace("\n", " ")
    print(f"🤖 LLM[{req_id}] {source} queued role={role} text={preview!r}")

    try:
        async with state.llm_lock:
            print(f"🤖 LLM[{req_id}] {source} started")
            response = await asyncio.to_thread(state.llm_api.send, text, role)
            print(f"🤖 LLM[{req_id}] {source} completed")
            return response
    except Exception as exc:
        print(f"⚠️ LLM[{req_id}] {source} failed: {exc}")
        return {"role": "error", "message": f"LLM call failed: {exc}"}


def _com_callback(message: str) -> None:
    _submit(_process_system_message(message))


async def _process_system_message(message: str) -> None:
    response = await _call_llm(message, "system", "system-callback")
    if response:
        await _handle_llm_response(response)


def _stt_callback(msg: Dict[str, Any]) -> None:
    _submit(_process_stt_message(msg))


async def _process_stt_message(msg: Dict[str, Any]) -> None:
    complete = False
    speech = ""
    if msg.get("confirmedText"):
        complete = True
        speech = str(msg["confirmedText"])
        print(f"🎤 STT confirmed text -> LLM: {speech}")
        response = await _call_llm(speech, "user", "stt")
        if response:
            await _handle_llm_response(response)
    elif msg.get("interimResult"):
        speech = str(msg["interimResult"])
    await _update_frontend(speech, "user", complete)


def _tts_callback(msg: Dict[str, Any]) -> None:
    status = msg.get("tts")
    if status in ("started", "resumed") and state.stt:
        state.stt.pause()
    elif status in ("stopped", "paused") and state.stt:
        state.stt.resume()


def _reload_runtime() -> None:
    if state.stt:
        state.stt.close()
    if state.tts:
        state.tts.close()
    if state.serial:
        state.serial.close()

    state.config = load_config(REPO_ROOT)
    state.volume = int(state.config.get("volume", 50))

    state.serial = SerialCommunication(_com_callback, state.config)
    if state.config.get("communicationMethod", "Serial") == "Serial":
        state.serial.connect()

    state.function_handler = FunctionHandler(state.config, state.serial)
    state.llm_api = LLMAPI(state.config, state.function_handler)

    speech = state.get_speech_settings()
    if not state.config.get("muteMicrophone", False):
        state.stt = SpeechToTextWorker(
            REPO_ROOT,
            _stt_callback,
            speech["speechToTextModel"],
            speech["sttBackend"],
        )
    else:
        state.stt = None

    state.tts = TextToSpeechWorker(REPO_ROOT, _tts_callback)


@app.on_event("startup")
async def startup() -> None:
    state.loop = asyncio.get_running_loop()
    state.llm_lock = asyncio.Lock()
    _reload_runtime()


@app.on_event("shutdown")
async def shutdown() -> None:
    if state.stt:
        state.stt.close()
    if state.tts:
        state.tts.close()
    if state.serial:
        state.serial.close()
    state.llm_lock = None
    state.loop = None


@app.websocket("/")
async def websocket_root(ws: WebSocket) -> None:
    client_host = ws.client.host if ws.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        await ws.close()
        return

    await ws.accept()
    state.clients.append(ws)

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

            if cmd.get("command") == "pause" and state.stt:
                state.stt.pause()
            elif cmd.get("command") == "resume" and state.stt:
                state.stt.resume()
            elif cmd.get("command") == "setVolume":
                try:
                    state.volume = int(cmd.get("value", state.volume))
                except Exception:
                    pass
            elif cmd.get("command") == "protocol":
                await ws.send_text(json.dumps(state.config.get("conversationProtocol", [])))
            elif cmd.get("command") == "reload-config":
                _reload_runtime()
            elif cmd.get("command") == "sendMessage":
                text = str(cmd.get("message", "")).strip()
                if text and state.llm_api:
                    await _update_frontend(text, "user", True)
                    response = await _call_llm(text, "user", "ws-sendMessage")
                    if response:
                        await _handle_llm_response(response)
            elif cmd.get("text"):
                if state.llm_api:
                    response = await _call_llm(cmd["text"], "user", "ws-text")
                    if response:
                        await _handle_llm_response(response)
            elif cmd.get("frontEnd"):
                payload = cmd.get("frontEnd", {})
                await _update_frontend(f"frontEnd return: {payload}", "system")
    except WebSocketDisconnect:
        if ws in state.clients:
            state.clients.remove(ws)


@app.get("/api/latest-image")
async def get_latest_image() -> JSONResponse:
    return JSONResponse({"image": state.latest_image})


@app.post("/api/latest-image")
async def post_latest_image(payload: Dict[str, Any]) -> JSONResponse:
    state.latest_image = str(payload.get("image", state.latest_image))
    return JSONResponse({"ok": True, "image": state.latest_image})


@app.get("/{path:path}")
async def serve_frontend(path: str):
    target = FRONTEND_DIR / path
    if path == "" or path == "/":
        return FileResponse(FRONTEND_DIR / "index.html")
    if target.exists() and target.is_file():
        return FileResponse(target)

    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend_python.server:app", host="0.0.0.0", port=3000, reload=False)
