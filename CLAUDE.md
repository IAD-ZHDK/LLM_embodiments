# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Voice-controlled LLM embodiment system for prototyping "Large Language Objects" — connects a local LLM (Ollama) or OpenAI to speech I/O (Vosk STT + Piper TTS) and to physical devices over serial (Arduino) or BLE. Optimized for Raspberry Pi but also runs on macOS and Linux. The Python backend serves both the API and a p5.js frontend on port 3000.

## Common commands

### Install
```bash
chmod +x setup.sh run.sh run_backend_python.sh
./setup.sh                # one-shot installer: python venv, pip packages, ollama
```

### Run
```bash
./run.sh                  # kiosk mode: backend + USB watcher + chromium (Pi); browser launch on macOS
./run_backend_python.sh   # backend only (no kiosk / no chromium) — preferred for development
npm start                 # alias for run_backend_python.sh
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

Logs go to `logs/kiosk.log` when launched via `run.sh`; `stdout` when launched via `run_backend_python.sh`.

## Architecture

```
┌────────────┐  WebSocket  ┌────────────────────┐  subprocess  ┌────────────┐
│  Frontend  │◄──────────►│   Python Backend   │◄───────────►│ scriptSTT  │
│ (p5.js +   │  /api/*     │  (FastAPI/uvicorn)  │  JSON stdio │ scriptTTS  │
│  plain JS) │             │     :3000           │◄───────────►│ serial io  │
└────────────┘             └────────────────────┘             └────────────┘
                                       │
                                       ├── LLMAPI ──► Ollama / OpenAI / AI HAT+
                                       ├── FunctionHandler ──► SerialCommunication (Arduino)
                                       └── outputSanitizer ──► pseudo-tool-call detection
```

### Runtime entrypoints

- `run.sh` — kiosk orchestrator. Pulls git updates, activates venv, starts backend, watches for USB sticks with a `config.toml` (hot-reload trigger), launches Chromium in kiosk mode on Linux. macOS branch launches default browser via `open`.
- `run_backend_python.sh` — kills anything on port 3000, activates venv, execs `python3 -m backend_python.server`.
- `setup.sh` — OS-detecting installer. Creates `python/venv` with Python 3.13.3, installs `python/requirements.txt`, onnxruntime, etc.

### Backend (`backend_python/`)

- `server.py` — FastAPI app. Owns `BackendState` (singletons for serial, LLM, STT, TTS). WebSocket `/` accepts `pause | resume | setVolume | protocol | reload-config | sendMessage | text | frontEnd`. Falls back to `index.html` for unknown paths so the p5.js client can be served here. `state.latest_image` is exposed via `/api/latest-image` for the frontend gallery.
- `config_loader.py` — loads `config.toml` via Python's stdlib `tomllib` and deep-merging over `DEFAULT_CONFIG`. This is the single source of truth for runtime config.
- `llm_api.py` — provider-agnostic LLM client. Supports `ollama` (Ollama chat + native `tool_calls`), `openai` (functions), and `archFunction` mode (parses `<tool_call>...</tool_call>` blocks). AI HAT+ auto-routing is detected via `/dev/hailo*` and `hailortcli`. Tool policy filter (`toolPolicy.enableIntentFilter` + `commandKeywords`) gates which calls go through.
- `function_handler.py` — turns the flat `functions.tools` map in `config.toml` into OpenAI-style function specs and routes calls to one of three targets: `device` (serial write/read), `frontEnd` (broadcast to browser, returns `{role:"function"}`), or `notification` (passive response).
- `serial_comm.py` — auto-detects Arduino via USB manufacturer string, opens pyserial at 115200, runs a reader thread, marshals read/write to the device. Notification strings (incoming `name:value` lines) are looked up in `config.functions.notifications` and forwarded to the callback as JSON.
- `speech_workers.py` — spawns `scriptSTT.py` and `scriptTTS.py` as long-lived subprocesses under `python/`. Communication is line-delimited JSON over stdio. TTS start/stop events call back into `state.stt.pause()/resume()` to prevent echo.

### Frontend (`frontend/`)

- `index.html` — p5.js + 4 dialog divs (`.user`, `.assistant`, `.error`, `.system`) + visible debug console.
- `websocket.js` — single `WebSocket('ws://localhost:3000')` with auto-reconnect; routes `data.backEnd.messageType` into the right div; on `functionName` it looks up `window.frontendFunctions[name]` and posts the return value back. Implements a click-and-drag volume slider.
- `frontEndFunctions.js` — registers `frontendFunctions` on `window`. The keys here must match the `name` field of any `functions.tools[*]` entry whose `target` is `"frontEnd"`. Also polls `/api/latest-image` every 2s.
- `sketch.js` — p5.js draw loop. Loaded after the others.

### Python utilities (`python/`)

- `scriptSTT.py` — Vosk-based STT worker. Reads mic via sounddevice, writes `{interimResult}` / `{confirmedText}` JSON lines to stdout. Accepts `{STT: "pause|resume"}` on stdin.
- `scriptTTS.py` — Piper TTS worker. Reads `{text, model, volume}` and `{tts: "pause|resume|stop"}` from stdin, writes `{tts: "started|stopped|..."}` to stdout.
- `model_downloader.py` — helper for fetching STT/TTS models.
- `Microphone/` — ReSpeaker Lite HAT tuning utilities (Pi-only).
- `STTmodels/`, `TTSmodels/` — gitignored. Drop model folders/files here. Set the active name in `config.toml` under `speech.languageProfiles[lang].speechToTextModel` / `textToSpeechModel`.

### Config (`config.toml`)

Single user-facing config (loaded via `node --input-type=module`):

- `activeLanguage` + `speech.languageProfiles` — pick STT + TTS together.
- `llmSettings.provider` — `ollama` or `openai`.
- `llmSettings.aiHatPlus` — auto-routes to a local OpenAI-compatible endpoint when a Hailo device is detected.
- `llmSettings.outputSanitizer` — strips pseudo tool calls like `set_LED(1)` from assistant text and (optionally) executes them inline.
- `functions.tools` — the unified tool list. `target: "device" | "frontEnd" | "notification"`. `valueRules` translate keyword phrases ("off", "on") into numeric `value`.
- `conversationProtocol` — system prompt and prior history. The current prompt is an intentionally rude agent; edit here for persona changes.

## Conventions

- Backend state lives in `backend_python.server.state` (module-level singleton). WebSocket callbacks submit coroutines back to the FastAPI loop via `_submit()` + `asyncio.run_coroutine_threadsafe`.
- LLM calls are serialized through `state.llm_lock` and tagged with a `llm_seq` request id for log correlation.
- New functions go in `config.toml` under `functions.tools`. For device calls add a matching `deviceCommand` or a method on `SerialCommunication`. For UI calls add the same name in `frontend/frontEndFunctions.js`.
- New language: add a key under `speech.languageProfiles` and the model folders under `python/STTmodels` / `python/TTSmodels`.
- The frontend expects a single WebSocket connection from `ws://localhost:3000`; it retries on close.

## Common pitfalls

- `config.toml` is loaded via Python's stdlib `tomllib` (Python 3.11+). Keep it valid TOML — tables use `[section]` headers, lists of objects use `[[section]]` repeated headers, multi-line strings use triple-quoted `"""..."""`.
- Port 3000 must be free; `run_backend_python.sh` kills stale listeners, but external servers will conflict.
- Vosk model names in `config.toml` are folder names under `python/STTmodels`, not numeric indexes.
- AI HAT+ auto-routing silently overrides `llmSettings.url` if a Hailo device is detected and `aiHatPlus.preferWhenAvailable` is true.
- `outputSanitizer.executeInlinePseudoCalls` will execute any pseudo call whose name matches a configured tool — keep the configured tool set tight.
- The kiosk `run.sh` uses `set -m` job control and SIGUSR1 for hot restart; don't change signal handling without checking the watcher at the top of the file.

## Known TODOs (from README)

- Auto-restart on Arduino disconnect
- Image LLM API recent changes need a fix
- Physical-button full-app restart
- BLE integration
