# M5Stack CoreS3 WiFi example

This example streams the CoreS3's built-in microphone to the Python backend over WiFi, plays TTS through its built-in speaker, and supports the display, touch controls, camera, and Port A servo.

## Setup

1. Install the **M5Stack Board Manager** and select **M5Stack-CoreS3** in Arduino IDE. Use M5Stack Board Manager **3.2.2 or newer** for the built-in camera support.
2. Select a partition scheme for the CoreS3's 16 MB flash that includes a filesystem partition compatible with LittleFS (typically listed as **SPIFFS** or **FS**) and leaves enough application space for the example and its libraries. The sketch uses PSRAM for camera frame buffers.
3. Install **M5Unified** (version **0.2.11 or newer** for the camera), **WebSockets** (Markus Sattler/Links2004), and **ArduinoJson**.
4. Copy `WifiSecrets.example.h` to `WifiSecrets.h` and enter your WiFi credentials and backend address.
5. Upload the sketch, start the backend with `./run.sh`, and open the Serial monitor. The device IP is shown on the display and in Serial output.

## Persona editor storage

The default system prompt is defined in `DevicePersona.h`. Saving a prompt in the device's web editor overrides that default and stores the edited prompt in flash as `/prompt.txt` on LittleFS. Other persona text fields and settings are stored in the ESP32's NVS settings area.

The filesystem partition is important for larger prompts. Without one, the editor falls back to NVS, which has only about 20 KB shared by all saved settings; a prompt can fail to save even when it is only a few thousand bytes. On a failed save, the editor reports the submitted size and its per-field limit, but available NVS capacity may be lower because other settings share it.

Changing the partition scheme or erasing flash can erase saved persona values. Use **Reset persona to defaults** in the editor to remove saved values and return to the prompt in `DevicePersona.h`.
