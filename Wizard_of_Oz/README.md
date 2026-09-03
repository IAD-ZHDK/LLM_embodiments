# Wizard of Oz test server

A drop-in replacement for the real Python backend's ESP32-facing side, with no LLM involved.
Use this to test an Arduino device (mic, sensors, tool calls) end-to-end without touching
Ollama/OpenAI, STT, or TTS — a human ("the wizard") plays the role of the model instead.

- **Audio**: streamed straight to your computer's speakers as it arrives, while Whisper also prints
  live transcription and PCM signal levels for diagnostics.
- **Text out**: nothing is spoken back to the device. No text-to-speech.
- **Tools**: whatever the device declares on connect (its persona + MCP-style tool list, the
  same `deviceInfo` message the real backend consumes) is printed to the terminal. You can
  call any of them yourself from a simple console prompt.

## Requirements

Uses the same Python environment as the main backend (`fastapi`, `uvicorn`, `sounddevice` are
already in `backend/requirements.txt`):

```bash
cd "LLM_embodiments"
source backend/venv/bin/activate
```

## Run

```bash
cd Wizard_of_Oz
python3 server.py
```

This listens on `0.0.0.0:3000` at `/device` — the same host/port/path the M5Stack sketch
already targets, so no changes are needed on the device. **Stop the real backend first**
(`lsof -ti :3000 | xargs kill`) since only one process can bind port 3000 at a time.

Flash/power on the M5Stack as usual and connect it to the same WiFi network as this machine.

## Using it

Once the device connects, you'll see its persona and declared tools printed, e.g.:

```
✅ ESP32 connected.
🧠 Persona: You are a friendly assistant embodied in an M5Stack device...
🛠️  Tools (3):
   - set_vibration (bool): Turns the vibration motor on or off. value=1 turns it on, value=0 turns it off.
   - get_String (none): Reads back the string currently stored on the device.
   - set_String (string): Stores a new string value on the device for later retrieval.
```

At the `woz>` prompt:

- `<tool_name> [value]` — send that tool call to the device, e.g. `set_vibration 1`
- `tools` — reprint the persona + tool list
- `quit` / `exit` — stop the console prompt (the server keeps running; Ctrl+C to fully stop)

Anything the device reports (button presses, sensor readings, mic mute state) prints live to
the terminal as `🔔 Notification: ...` / `🎙️  Mic: ...` lines. Its mic audio plays out of your
speakers in real time; every two seconds the Wizard also prints a `🎚️ PCM level` line and, after
every three seconds of received audio, prints `📝 Transcript: ...` from Whisper. This diagnostic
path deliberately bypasses voice-activity detection, so it reports `(no speech recognized)` when
Whisper receives audio but cannot recognize it.

Whisper defaults to `small.en`, the same model configured for English in the main backend. To use
a different local model or GPU, set these before starting the Wizard:

```bash
WIZARD_WHISPER_MODEL=base.en WIZARD_WHISPER_DEVICE=cuda WIZARD_WHISPER_COMPUTE_TYPE=float16 python3 server.py
```

Set `WIZARD_TRANSCRIBE_WINDOW_SECONDS` to change the three-second diagnostic window.
