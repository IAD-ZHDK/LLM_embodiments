# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Voice-controlled LLM embodiment system for prototyping "Large Language Objects" — connects a local LLM (Ollama) or OpenAI to speech I/O (Vosk STT + Piper TTS) and to physical devices over Serial, BLE, or WiFi (ESP32/M5Stack). Runs on macOS and Linux. The Python backend has no frontend/kiosk UI; it's controlled over its WebSocket API (see Debug below) and a Textual terminal UI.

## Common commands

### Install
```bash
chmod +x setup.sh run.sh
./setup.sh                # one-shot installer: python venv, pip packages, ollama
```

### Run
```bash
./run.sh                  # backend + USB watcher (single entrypoint)
npm start                 # alias for `python3 -m backend.server` (no USB watcher)
```

### LLM / STT / TTS model management
```bash
ollama pull llama3.2:3b
ollama pull hf.co/LiquidAI/LFM2-1.2B-Tool-GGUF:Q4_K_M
ollama list
```

### Debug
```bash
wscat -c ws://localhost:3000                           # raw websocket client
# send text-only messages:
{"command":"protocol"}
{"command":"sendMessage","message":"hello"}
# toggle llmSettings.debugRawModelOutput in config.toml to dump raw provider payloads
```

Terminal output (console log, connected sessions, live STT/prompt/response) is rendered by the Textual UI in `backend/tui.py`.

## Architecture

```
┌────────────┐  Serial/BLE/WiFi  ┌────────────────────┐  subprocess  ┌────────────┐
│  Device    │◄─────────────────►│   Python Backend   │◄───────────►│ scriptSTT  │
│ (Arduino/  │  WebSocket (/device│  (FastAPI/uvicorn)  │  JSON stdio │ scriptTTS  │
│  ESP32)    │  for WiFi)         │     :3000           │◄───────────►│ scriptRemoteSTT│
└────────────┘                    └────────────────────┘             └────────────┘
                                       │
                                       ├── LLMAPI ──► Ollama / OpenAI / AI HAT+
                                       ├── FunctionHandler ──► SerialCommunication / DeviceWebSocketCommunication
                                       ├── outputSanitizer ──► pseudo-tool-call detection
                                       └── tui.py ──► Textual terminal UI (console + sessions + live status)
```

### Runtime entrypoints

- `run.sh` — single entrypoint. Activates venv, detects `LLM_EMBODIMENT_PROFILE` (mac/linux), clears port 3000, starts `python3 -m backend.server`, watches for USB sticks with a `config.toml` (hot-reload trigger).
- `setup.sh` — OS-detecting installer. Creates `backend/venv` with Python 3.13.3, installs `backend/requirements.txt`, onnxruntime, etc.

### Backend (`backend/`)

- `server.py` — FastAPI app. Owns `BackendState` (singletons for serial, LLM, STT, TTS). WebSocket `/` accepts `pause | resume | setVolume | protocol | reload-config | sendMessage | text | frontEnd` (localhost only). WebSocket `/device` accepts a WiFi device (ESP32/M5Stack) connection — no localhost restriction, LAN-only prototyping endpoint. `state.latest_image` is exposed via `/api/latest-image`.
- `config_loader.py` — loads `config.toml` via Python's stdlib `tomllib` and deep-merges over `DEFAULT_CONFIG`, then overlays `config.<profile>.toml` (from `LLM_EMBODIMENT_PROFILE`) or `config.local.toml`. This is the single source of truth for runtime config.
- `llm_api.py` — provider-agnostic LLM client. Supports `ollama` (Ollama chat + native `tool_calls`), `openai` (functions), and `archFunction` mode (parses `<tool_call>...</tool_call>` blocks). AI HAT+ auto-routing is detected via `/dev/hailo*` and `hailortcli`. Tool policy filter (`toolPolicy.enableIntentFilter` + `commandKeywords`) gates which calls go through.
- `function_handler.py` — turns the flat `functions.tools` map in `config.toml` into OpenAI-style function specs and routes calls to one of three targets: `device` (serial/WiFi write/read), `frontEnd` (broadcast to browser-less clients, returns `{role:"function"}`), or `notification` (passive response). Also supports dynamically registering/clearing tools declared live by a connected WiFi device (`register_device_tools`/`clear_device_tools`).
- `serial_comm.py` — auto-detects Arduino via USB manufacturer string, opens pyserial at 115200, runs a reader thread, marshals read/write to the device. Notification strings (incoming `name:value` lines) are looked up in `config.functions.notifications` and forwarded to the callback as JSON.
- `device_ws_comm.py` — WiFi-device equivalent of `serial_comm.py`; same interface (`write`/`read`/`connect`/`checkConection`), backed by the `/device` WebSocket instead of a serial port.
- `speech_workers.py` — spawns `scriptSTT.py`/`scriptTTS.py`/`scriptRemoteSTT.py` as subprocesses under `backend/`. Communication is line-delimited JSON over stdio (or length-prefixed binary frames for remote audio). TTS start/stop events call back into `state.stt.pause()/resume()` to prevent echo.
- `tui.py` — Textual-based terminal UI: scrolling console log (via a `print()` monkey-patch, since Textual's own renderer also uses `sys.stdout` and can't be redirected) and a sticky sidebar showing connected sessions plus live STT/prompt/response.
- `scriptSTT.py`, `scriptTTS.py`, `scriptRemoteSTT.py`, `model_downloader.py`, `Microphone/` — STT/TTS worker scripts and helpers, run as subprocesses (not imported as a package).
- `STTmodels/`, `TTSmodels/` — gitignored. Drop model folders/files here. Set the active name in `config.toml` under `speech.languageProfiles[lang].speechToTextModel` / `textToSpeechModel`.

### Config (`config.toml`)

Single user-facing config, loaded via Python's stdlib `tomllib`:

- `activeLanguage` + `speech.languageProfiles` — pick STT + TTS together.
- `communicationMethod` — `"Serial"`, `"BLE"`, or `"WiFi"` (see `ArduinoExample/WiFi/M5StackExample`).
- `llmSettings.provider` — `ollama` or `openai`.
- `llmSettings.aiHatPlus` — auto-routes to a local OpenAI-compatible endpoint when a Hailo device is detected.
- `llmSettings.outputSanitizer` — strips pseudo tool calls like `set_LED(1)` from assistant text and (optionally) executes them inline.
- `functions.tools` — the unified tool list. `target: "device" | "frontEnd" | "notification"`. `valueRules` translate keyword phrases ("off", "on") into numeric `value`.
- `conversationProtocol` — system prompt and prior history.
- `config.mac.toml` / `config.local.toml` — optional profile overlays merged on top of `config.toml` (see `config_loader.py`). Not full duplicates — only the deltas for that profile.

## Conventions

- Backend state lives in `backend.server.state` (module-level singleton). WebSocket callbacks submit coroutines back to the FastAPI loop via `_submit()` + `asyncio.run_coroutine_threadsafe`.
- LLM calls are serialized through `state.llm_lock` and tagged with a `llm_seq` request id for log correlation.
- New functions go in `config.toml` under `functions.tools`. For device calls add a matching `deviceCommand` or a method on `SerialCommunication`/`DeviceWebSocketCommunication`.
- New language: add a key under `speech.languageProfiles` and the model folders under `backend/STTmodels` / `backend/TTSmodels`.
- A WiFi device (M5Stack) can declare its own tools/persona at connect time via a `deviceInfo` message (see `function_handler.register_device_tools` and `server._apply_device_info`) instead of `config.toml`.

## Common pitfalls

- `config.toml` is loaded via Python's stdlib `tomllib` (Python 3.11+). Keep it valid TOML — tables use `[section]` headers, lists of objects use `[[section]]` repeated headers, multi-line strings use triple-quoted `"""..."""`. A bare key/value placed after a `[table]` header (with no reset) gets silently nested INTO that table instead of root scope — this has bitten this project before (`communicationMethod`/`volume` ended up nested under `[speech.languageProfiles.de]`); keep root-level keys before the first `[table]` header.
- Port 3000 must be free; `run.sh` kills stale listeners, but external servers will conflict.
- Vosk model names in `config.toml` are folder names under `backend/STTmodels`, not numeric indexes.
- AI HAT+ auto-routing silently overrides `llmSettings.url` if a Hailo device is detected and `aiHatPlus.preferWhenAvailable` is true.
- `outputSanitizer.executeInlinePseudoCalls` will execute any pseudo call whose name matches a configured tool — keep the configured tool set tight.
- `run.sh` uses `set -m` job control and SIGUSR1 for hot restart; don't change signal handling without checking the watcher at the top of the file.
- `backend/tui.py` intercepts `print()` (not `sys.stdout`) to feed the console pane — Textual's own screen renderer also writes through `sys.stdout`, so redirecting that stream directly causes a feedback loop of garbled escape codes.

## Known TODOs (from README)

- Auto-restart on Arduino disconnect
- Image LLM API recent changes need a fix
- Physical-button full-app restart
- BLE integration
