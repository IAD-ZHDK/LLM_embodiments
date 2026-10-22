// LLM embodiment example for the Seeed Studio XIAO ESP32-S3 Sense (built-in OV2640 camera + PDM mic).
// Based on the M5Stack example, without screen, touch, IMU, servo and speaker (speaker: TODO).
#include "src/BackendComm.h"
#include "esp_camera.h"

// ---- Camera (OV2640 on the XIAO ESP32-S3 Sense) ----
static camera_config_t cameraConfig = {
    .pin_pwdn = -1,
    .pin_reset = -1,
    .pin_xclk = 10,
    .pin_sscb_sda = 40,
    .pin_sscb_scl = 39,
    .pin_d7 = 48,
    .pin_d6 = 11,
    .pin_d5 = 12,
    .pin_d4 = 14,
    .pin_d3 = 16,
    .pin_d2 = 18,
    .pin_d1 = 17,
    .pin_d0 = 15,
    .pin_vsync = 38,
    .pin_href = 47,
    .pin_pclk = 13,
    .xclk_freq_hz = 10000000, // 10 MHz is cleaner than 20 MHz for RGB565 on this board
    .ledc_timer = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    .pixel_format = PIXFORMAT_RGB565, // the backend expects RGB565
    .frame_size = FRAMESIZE_QVGA,     // 320x240
    .jpeg_quality = 12,
    .fb_count = 2,
    .fb_location = CAMERA_FB_IN_PSRAM,
    .grab_mode = CAMERA_GRAB_LATEST,
};

static bool cameraReady = false;

static void initializeCamera()
{
    if (!psramFound())
    {
        Serial.println("[Camera] PSRAM not enabled. Set Tools > PSRAM > \"OPI PSRAM\" and re-upload.");
        return;
    }
    esp_err_t err = esp_camera_init(&cameraConfig);
    if (err != ESP_OK)
    {
        Serial.printf("[Camera] Init failed: 0x%x (is PSRAM set to OPI PSRAM?)\n", err);
        return;
    }
    cameraReady = true;
    sensor_t *s = esp_camera_sensor_get();
    if (s)
    {
        s->set_gain_ctrl(s, 1);
        s->set_gainceiling(s, GAINCEILING_4X); // cap auto-gain: less amplified noise in the dark
        s->set_exposure_ctrl(s, 1);
        s->set_aec2(s, 1);        // night-mode style auto exposure
        s->set_ae_level(s, 1);    // slightly brighter target
        s->set_whitebal(s, 1);
        s->set_awb_gain(s, 1);
        s->set_bpc(s, 1);         // black pixel correction
        s->set_wpc(s, 1);         // white pixel correction
        s->set_raw_gma(s, 1);
        s->set_lenc(s, 1);
        s->set_denoise(s, 1);     // no-op on OV2640, harmless
    }
    // Let auto exposure/white balance settle; the first frames are poor.
    for (int i = 0; i < 10; i++)
    {
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) esp_camera_fb_return(fb);
        delay(50);
    }
    Serial.println("[Camera] Ready.");
}

static void setLed(bool on)
{
    digitalWrite(XiaoPins::kUserLed, on ? LOW : HIGH); // active LOW
}

// ---- Tool handlers ----
String storedString = "";

static void handleSetLed(const String &value)
{
    setLed(value == "true" || value == "1");
}

static void handleGetString(const String &value)
{
    (void)value;
    BackendComm::sendNotification("string_value", storedString);
}

static void handleSetString(const String &value)
{
    storedString = value;
}

static void handleTakePicture(const String &value)
{
    (void)value;
    if (!cameraReady)
    {
        BackendComm::sendCameraError("Camera not initialized");
        return;
    }

    // Discard one possibly stale frame, then grab a fresh one.
    camera_fb_t *stale = esp_camera_fb_get();
    if (stale)
        esp_camera_fb_return(stale);

    setLed(true);
    camera_fb_t *frame = esp_camera_fb_get();
    setLed(false);
    if (!frame)
    {
        BackendComm::sendCameraError("Camera capture failed");
        return;
    }

    if (frame->format == PIXFORMAT_RGB565)
    {
        if (!BackendComm::sendCameraFrame(frame->buf, frame->len, frame->width, frame->height))
            Serial.println("[Camera] Failed to send frame.");
    }
    else
    {
        BackendComm::sendCameraError("Unsupported camera format");
    }
    esp_camera_fb_return(frame);
}

// ---- Tool table (declared in src/DeviceTools.h) ----
DeviceTool deviceTools[] = {
    {"set_led", "Turn the small yellow LED on the device on or off.", "bool", "write", handleSetLed, ""},
    {"get_String", "Read back the text that was previously stored on the device.", "none", "read", handleGetString, ""},
    {"set_String", "Store a short text on the device.", "string", "write", handleSetString, ""},
    {"take_picture", "Take a photo with the device camera and look at it.", "none", "read", handleTakePicture, "image/rgb565"},
};
const size_t deviceToolCount = sizeof(deviceTools) / sizeof(deviceTools[0]);

void setup()
{
    pinMode(XiaoPins::kUserLed, OUTPUT);
    setLed(false);
    Serial.begin(115200);
    delay(500);
    Serial.printf("[Camera] PSRAM: %s, size %u bytes\n", psramFound() ? "found" : "NOT found", (unsigned)ESP.getPsramSize());
    // Camera first: it needs a clean LEDC timer / GPIO state, before WiFi and the PDM mic start.
    initializeCamera();
    BackendComm::begin();
}

void loop()
{
    BackendComm::loop();
}
