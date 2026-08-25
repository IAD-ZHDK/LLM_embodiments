// M5Stack (ESP32) example: streams built-in mic audio to the Python backend over WiFi,
// sends sensor/button notifications, and receives tool calls to drive attached actuators.
// Companion to ArduinoExample/Serial and ArduinoExample/BLE, using a WiFi WebSocket instead.
// Requires Library Manager installs: "M5Unified", "WebSockets" (Markus Sattler/Links2004), "ArduinoJson".
//
// All WiFi/WebSocket/audio plumbing lives in BackendComm.h - this file only needs to:
//   1. Edit DevicePersona.h to set the model's personality (sent to the backend as the system prompt).
//   2. Write a small handler function for each action, like set_vibration() below.
//   3. Add one line per tool to the deviceTools[] table (name, description, dataType, handler),
//      mirroring the Command table in ArduinoExample/Serial - descriptions follow the same
//      MCP-style name/description/dataType shape used by config.toml's functions.tools.
//   4. Write your own notification checks, like checkShake() below, and call them from loop().
//      Use BackendComm::sendNotification(name, value) to report a sensor/button event to the model.
#include "BackendComm.h"

// --- Device state ---
bool vibrationOn = false;
String storedString = "hello from the M5Stack";

// --- Tool handlers: called when the model asks this device to do something ---
void set_vibration(const String &value)
{
    vibrationOn = value == "1" || value == "true";
    // M5Stack has no built-in LED; use the built-in vibration motor instead (no-op on boards without one).
    M5.Power.setVibration(vibrationOn ? 255 : 0);
}

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
    {"set_vibration", "Turns the vibration motor on or off. value=1 turns it on, value=0 turns it off.", "bool", set_vibration},
    {"get_String", "Reads back the string currently stored on the device.", "none", get_String},
    {"set_String", "Stores a new string value on the device for later retrieval.", "string", set_String},
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
        BackendComm::sendNotification("shake", "true");
        lastShakeMillis = now;
    }
}

void setup()
{
    BackendComm::begin();
}

void loop()
{
    BackendComm::loop();
    checkShake();
}
