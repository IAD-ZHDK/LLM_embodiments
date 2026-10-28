# XIAO ESP32-S3 Sense example

WiFi example for the Seeed Studio XIAO ESP32-S3 Sense, based on the M5Stack example but without screen, touch, IMU and servo.

- Microphone: built-in PDM mic (16 kHz mono) streamed to the backend.
- Camera: built-in OV2640, `take_picture` tool sends a QVGA RGB565 frame.
- Tools: `set_sound`, `set_led`, `get_String`, `set_String`, `take_picture`.
- Status goes to the Serial monitor (115200 baud). The BOOT button mutes/unmutes the mic.
- Speaker: optional **Adafruit Audio BFF** (MAX98357 I2S amp) plugged under the XIAO, using A1 (DAT), A2 (LRC) and A3 (BCLK). TTS audio plays through it, with `kSpeakerVolumePercent` in `src/DeviceConfig.h` as software volume. An I2S amp can't be detected electronically, so if you run without the BFF, set `kSpeakerEnabled = false`. Audio is then skipped, `audioEnd` is still acknowledged, and the conversation works as before. The assistant's text reply is always printed to Serial.

## Setup

1. Arduino IDE with the **esp32 core 3.x** (needed for `ESP_I2S.h`). Board: **XIAO_ESP32S3**.
2. Tools → **PSRAM: OPI PSRAM** (required for the camera).
3. Install libraries: **ArduinoJson** (v7) and **WebSockets** (Markus Sattler).
4. Copy `WifiSecrets.example.h` to `WifiSecrets.h` and enter your WiFi and backend address.
5. Upload, open the Serial monitor, and start the backend (`./run.sh`).

The persona editor is served by the device on its IP address, as in the M5Stack example.

## Presence detection

The sketch runs the TensorFlow Lite Micro "person detection" model (96x96 grayscale, about 250 KB, bundled with the Arduino ESP32 core's esp-tflite-micro) on the camera. Every `kPresenceIntervalMs` (2 s) it grabs a frame, centre-crops it and scores it. After `kPresenceHitsRequired` (2) consecutive frames with confidence >= `kPresenceConfidence` (0.75), it sends a `presence` notification to the backend (once per visit; it re-arms after a few empty checks). The `presence` guidance in `DevicePersona.h` tells the model how to react.

Notes:
- It detects a *person in view*, not gaze direction. Someone facing the camera is the strongest trigger.
- Tune `kPresenceDetectionEnabled`, `kPresenceIntervalMs`, `kPresenceConfidence`, `kPresenceHitsRequired` in `src/DeviceConfig.h`. The Serial monitor prints `[Presence] confidence x.xx` per check.
- Needs PSRAM (the tensor arena is allocated there). The large `src/person_detect_model_data.cc` is the model; compiling it takes a moment.
