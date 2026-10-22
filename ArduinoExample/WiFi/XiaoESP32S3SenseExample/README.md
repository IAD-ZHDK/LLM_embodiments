# XIAO ESP32-S3 Sense example

WiFi example for the Seeed Studio XIAO ESP32-S3 Sense, based on the M5Stack example but without screen, touch, IMU, servo and speaker.

- Microphone: built-in PDM mic (16 kHz mono) streamed to the backend.
- Camera: built-in OV2640, `take_picture` tool sends a QVGA RGB565 frame.
- Tools: `set_led`, `get_String`, `set_String`, `take_picture`.
- Status goes to the Serial monitor (115200 baud). The BOOT button mutes/unmutes the mic.
- Speaker: **not implemented yet.** See the `TODO: speaker` markers in `src/BackendComm.h`. TTS audio is dropped, but `audioEnd` is still acknowledged so the conversation keeps working. The assistant's text reply is printed to Serial.

## Setup

1. Arduino IDE with the **esp32 core 3.x** (needed for `ESP_I2S.h`). Board: **XIAO_ESP32S3**.
2. Tools → **PSRAM: OPI PSRAM** (required for the camera).
3. Install libraries: **ArduinoJson** (v7) and **WebSockets** (Markus Sattler).
4. Copy `WifiSecrets.example.h` to `WifiSecrets.h` and enter your WiFi and backend address.
5. Upload, open the Serial monitor, and start the backend (`./run.sh`).

The persona editor is served by the device on its IP address, as in the M5Stack example.
