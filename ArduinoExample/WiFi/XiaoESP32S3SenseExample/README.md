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
3. Tools → **Partition Scheme** → **8M with spiffs (3MB APP/1.5MB SPIFFS)**. This gives the example a 3 MB application partition and a 1.5 MB filesystem partition for the system prompt.
4. Install libraries: **ArduinoJson** (v7) and **WebSockets** (Markus Sattler).
5. Copy `WifiSecrets.example.h` to `WifiSecrets.h` and enter your WiFi and backend address.
6. Upload, open the Serial monitor, and start the backend (`./run.sh`).

The persona editor is served by the device on its IP address, as in the M5Stack example.

## Persona editor storage

The default system prompt is defined in `DevicePersona.h`. Saving a prompt in the device's web editor overrides that default and stores the edited prompt in flash as `/prompt.txt` on LittleFS. Other persona text fields and settings are stored in the ESP32's NVS settings area.

The filesystem partition is important for larger prompts. Without one, the editor falls back to NVS, which has only about 20 KB shared by all saved settings; a prompt can fail to save even when it is only a few thousand bytes. The editor reports the submitted size and its per-field limit when a save fails, but available NVS capacity may be lower because the other settings share it. The 3 MB application allocation is the recommended starting point for compiling this example; if your installed core or libraries report that the sketch is too large, choose a partition scheme with a larger application allocation that still includes a filesystem.

Changing the partition scheme or erasing the device's flash can erase saved persona values. Use **Reset persona to defaults** in the editor to remove saved values and return to the prompt in `DevicePersona.h`.

## Presence detection

The sketch runs the TensorFlow Lite Micro "person detection" model (96x96 grayscale, about 250 KB, bundled with the Arduino ESP32 core's esp-tflite-micro) on the camera. Every `kPresenceIntervalMs` (2 s) it grabs a frame, centre-crops it and scores it. After `kPresenceHitsRequired` (2) consecutive frames with confidence >= `kPresenceConfidence` (0.75), it sends a `presence` notification to the backend (once per visit; it re-arms after a few empty checks). The `presence` guidance in `DevicePersona.h` tells the model how to react.

Notes:
- It detects a *person in view*, not gaze direction. Someone facing the camera is the strongest trigger.
- Tune `kPresenceDetectionEnabled`, `kPresenceIntervalMs`, `kPresenceConfidence`, `kPresenceHitsRequired` in `src/DeviceConfig.h`. The Serial monitor prints `[Presence] confidence x.xx` per check.
- Needs PSRAM (the tensor arena is allocated there). The large `src/person_detect_model_data.cc` is the model; compiling it takes a moment.
