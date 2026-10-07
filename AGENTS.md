# Repository Guidance

## Project Map

- This is a Python voice-assistant backend plus M5Stack firmware, not a JavaScript frontend. `package.json` only wraps backend startup; use the repository setup and run scripts.
- Read [README.md](README.md) for the quick install and [MANUAL_INSTALL.md](MANUAL_INSTALL.md) for dependencies and model setup. Treat [config.toml](config.toml) as the backend runtime configuration source of truth.
- [backend/server.py](backend/server.py) owns FastAPI lifecycle and device-session orchestration. Sessions own their LLM, STT, and tool handlers; TTS output is currently shared.
- [backend/device_ws_comm.py](backend/device_ws_comm.py) and [backend/serial_comm.py](backend/serial_comm.py) implement device communication. Keep wire-protocol changes aligned with [device_dummy/](device_dummy/) and [ArduinoExample/WiFi/M5StackExample/](ArduinoExample/WiFi/M5StackExample/).
- Firmware defaults and persona/tools live in `DeviceConfig.h` and `DevicePersona.h`. Real device credentials belong in the ignored `WifiSecrets.h`; use `WifiSecrets.example.h` as the template and never expose or overwrite local credentials.

## Working And Validation

- Before changing behavior, trace the active setting or message through its owning layer: backend config, session handling, protocol adapter, simulator, or firmware. Confirm whether the behavior is host-side or device-side, and preserve per-device session isolation.
- On macOS/Linux, use `./setup.sh` and `./run.sh`. On Windows, use `powershell -ExecutionPolicy Bypass -File .\setup.ps1` and `powershell -ExecutionPolicy Bypass -File .\run.ps1`. These scripts use `backend/venv`; prefer its Python over system Python for checks.
- The backend and Wizard of Oz server both use port 3000. Stop one before starting the other; `run.sh` may terminate a process already using that port during cleanup.
- No general-purpose automated test runner or repository-managed firmware build is configured. For Python syntax validation, run `backend/venv/bin/python -m compileall backend device_dummy Wizard_of_Oz` on macOS/Linux, or `backend\venv\Scripts\python.exe -m compileall backend device_dummy Wizard_of_Oz` in PowerShell. Existing `backend/test_*.py` scripts are targeted diagnostics; inspect their requirements before running, since some need audio or hardware.
- For Wi-Fi device testing, see [device_dummy/README.md](device_dummy/README.md). For hardware tests without the real LLM backend, see [Wizard_of_Oz/README.md](Wizard_of_Oz/README.md).