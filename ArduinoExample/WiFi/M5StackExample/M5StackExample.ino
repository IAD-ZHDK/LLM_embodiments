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
unsigned long yellowCircleUntil = 0;
bool yellowCircleShown = false;
bool cameraReady = false;

static camera_config_t cameraConfig = {
    .pin_pwdn = -1,
    .pin_reset = -1,
    .pin_xclk = -1,
    .pin_sscb_sda = 12,
    .pin_sscb_scl = 11,
    .pin_d7 = 47,
    .pin_d6 = 48,
    .pin_d5 = 16,
    .pin_d4 = 15,
    .pin_d3 = 42,
    .pin_d2 = 41,
    .pin_d1 = 40,
    .pin_d0 = 39,
    .pin_vsync = 46,
    .pin_href = 38,
    .pin_pclk = 45,
    .xclk_freq_hz = 20000000,
    .ledc_timer = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    .pixel_format = PIXFORMAT_RGB565,
    .frame_size = FRAMESIZE_QVGA,
    .jpeg_quality = 0,
    .fb_count = 1,
    .fb_location = CAMERA_FB_IN_PSRAM,
    .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
    .sccb_i2c_port = -1,
};

// --- Tool handlers: called when the model asks this device to do something ---
void set_sound(const String &value)
{
    soundSequence = value;
    soundOn = true;
}

void get_String(const String &value)
{
    BackendComm::sendNotification("get_String", storedString);
}

void set_String(const String &value)
{
    storedString = value;
}

void show_yellow_circle(const String &value)
{
    yellowCircleUntil = millis() + 5000;
    yellowCircleShown = false;
}

bool initializeCamera()
{
    M5.In_I2C.release();
    esp_err_t error = esp_camera_init(&cameraConfig);
    if (error != ESP_OK)
    {
        Serial.printf("[Camera] Initialization failed: 0x%x\n", error);
        return false;
    }

    sensor_t *sensor = esp_camera_sensor_get();
    if (!sensor)
    {
        esp_camera_deinit();
        Serial.println("[Camera] Sensor unavailable.");
        return false;
    }
    sensor->set_framesize(sensor, FRAMESIZE_QVGA);
    cameraReady = true;
    return true;
}

void take_picture(const String &value)
{
    if (!cameraReady && !initializeCamera())
    {
        BackendComm::sendCameraError("CoreS3 camera initialization failed.");
        return;
    }

    camera_fb_t *frame = esp_camera_fb_get();
    if (!frame)
    {
        BackendComm::sendCameraError("CoreS3 camera did not return a frame.");
        return;
    }

    // Shutter feedback: white flash plus a short high tone (blocks ~80 ms), then restore the UI.
    M5.Lcd.fillScreen(WHITE);
    BackendComm::playTone(1800, 80);
    redrawDisplay();
    drawMicLevelBar();

    bool sent = frame->format == PIXFORMAT_RGB565 &&
                BackendComm::sendCameraFrame(frame->buf, frame->len, frame->width, frame->height);
    esp_camera_fb_return(frame);
    if (!sent)
    {
        BackendComm::sendCameraError("Could not send the CoreS3 camera frame to the backend.");
    }
}

// --- Tools available to the model (MCP-style: name, description, dataType, handler) ---
DeviceTool deviceTools[] = {
    {"set_sound", "Plays a tone or a sequence of tones. Value is comma-separated \"frequency:durationMs\" segments in Hz:milliseconds, e.g. \"880:200,:100,660:200\"; leave frequency blank for a silent pause.", "string", "write", set_sound, ""},
    {"set_String", "Saves a short note in the device's memory slot. Only call this when explicitly asked to store or remember something; never for ordinary conversation.", "string", "write", set_String, ""},
    {"show_yellow_circle", "Shows a yellow circle on the screen for five seconds.", "none", "write", show_yellow_circle, ""},
    {"take_picture", "Capture a photo with the built-in CoreS3 camera and inspect it to answer visual questions.", "none", "read", take_picture, "image/rgb565"},
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
