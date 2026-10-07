// M5Stack CoreS3 (ESP32-S3) example: streams built-in mic audio to the Python backend over WiFi,
// sends sensor/button notifications, and receives tool calls to drive attached actuators.
// The built-in camera tool requires M5Stack Board Manager 3.2.2+ and M5Unified 0.2.11+.
// Companion to ArduinoExample/Serial and ArduinoExample/BLE, using a WiFi WebSocket instead.
// Requires Library Manager installs: "M5Unified", "WebSockets" (Markus Sattler/Links2004), "ArduinoJson".
//
// All WiFi/WebSocket/audio plumbing lives in BackendComm.h - this file only needs to:
//   1. Edit DevicePersona.h to set the model's personality (sent to the backend as the system prompt).
//   2. Write a small handler function for each action, like set_vibration() below.
//   3. Add one line per tool to the deviceTools[] table (name, description, dataType, commType, handler,
//      and optional responseType),
//      mirroring the Command table in ArduinoExample/Serial - descriptions follow the same
//      MCP-style name/description/dataType shape used by config.toml's functions.tools.
//   4. Write your own notification checks, like checkShake() below, and call them from loop().
//      Use BackendComm::sendNotification(name, value) to report a sensor/button event to the model.
#include "src/BackendComm.h"
#include "esp_camera.h"

// --- Device state ---
bool soundOn = false;
String soundSequence = "1000:500"; // comma-separated "frequency:durationMs" segments
String storedString = "hello from the M5Stack";

// --- Tool handlers: called when the model asks this device to do something ---

void get_String(const String &value)
{
    BackendComm::sendNotification("get_String", storedString);
}

void set_String(const String &value)
{
    storedString = value;
}



// --- Tools available to the model (MCP-style: name, description, dataType, handler) ---
DeviceTool deviceTools[] = {
    {"set_sound", "Plays a tone or a sequence of tones. Value is comma-separated \"frequency:durationMs\" segments in Hz:milliseconds, e.g. \"880:200,:100,660:200\"; leave frequency blank for a silent pause.", "string", "write", set_sound, ""},
    {"set_String", "Saves a short note in the device's memory slot. Only call this when explicitly asked to store or remember something; never for ordinary conversation.", "string", "write", set_String, ""},
    {"show_yellow_circle", "Shows a yellow circle on the screen for five seconds.", "none", "write", show_yellow_circle, ""},
    {"MotorPosition", "Capture a photo with the built-in CoreS3 camera and inspect it to answer visual questions.", "none", "read", take_picture, "image/rgb565"},
};
const size_t deviceToolCount = sizeof(deviceTools) / sizeof(deviceTools[0]);

// --- Notification checks: things this device reports to the model on its own, without being asked.
//     Add your own here (e.g. a button press or another sensor threshold) and call it from loop(). ---
void checkShake()
{

    if (!BackendComm::serverConnected)
        return;

    // update() actually polls the sensor over I2C; getImuData() only returns the last stored
    // reading, so without this the values never change after the first successful read.
    M5.Imu.update();
    auto imu = M5.Imu.getImuData();

    static unsigned long lastDebugMillis = 0;
    unsigned long now = millis();
    if (now - lastDebugMillis >= 500) // throttled so it's readable, not flooding the console
    {
        // Serial.printf("[IMU] accel x=%.3f y=%.3f z=%.3f\n", imu.accel.x, imu.accel.y, imu.accel.z);
        lastDebugMillis = now;
    }

    static unsigned long lastShakeMillis = 0;

    bool shaking = imu.accel.x > 2.0f || imu.accel.y > 2.0f || imu.accel.z > 2.0f;

    if (shaking && now - lastShakeMillis >= 2000) // only allow notifications at most every 2 seconds
    {
        Serial.printf("[IMU] Shake");
        BackendComm::sendNotification("shake", "true");
        lastShakeMillis = now;
    }
}

void setup()
{
    BackendComm::begin();

    BackendComm::playTone(2000, 100);
}

void loop()
{
    BackendComm::loop();
    checkShake();

    if (soundOn)
    {
        BackendComm::playToneSequence(soundSequence);
        soundOn = false;
    }

    if (yellowCircleUntil && !yellowCircleShown)
    {
        M5.Lcd.fillScreen(BLACK);
        M5.Lcd.fillCircle(M5.Lcd.width() / 2, M5.Lcd.height() / 2, min(M5.Lcd.width(), M5.Lcd.height()) / 3, YELLOW);
        yellowCircleShown = true;
    }
    else if (yellowCircleUntil && millis() >= yellowCircleUntil)
    {
        yellowCircleUntil = 0;
        yellowCircleShown = false;
        redrawDisplay();
        drawMicLevelBar();
    }
}
