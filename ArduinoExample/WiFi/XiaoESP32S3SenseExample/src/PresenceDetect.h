#pragma once

// Lightweight on-device person detection: the TensorFlow Lite Micro "person detection" model
// (96x96 grayscale, ~250 KB) running through the esp-tflite-micro library bundled with the
// Arduino ESP32 core. Runs every kPresenceIntervalMs; when kPresenceHitsRequired consecutive
// checks are confident, a "presence" notification is sent to the backend.
#include <Arduino.h>
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "DeviceConfig.h"
#include "BackendComm.h"
#include "person_detect_model_data.h"

#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"

namespace PresenceDetect
{
    constexpr int kInputSize = 96;
    constexpr int kArenaSize = 160 * 1024;

    inline uint8_t *_arena = nullptr;
    inline tflite::MicroInterpreter *_interpreter = nullptr;
    inline TfLiteTensor *_input = nullptr;
    inline bool _ready = false;
    inline uint32_t _lastRun = 0;
    inline int _hits = 0;
    inline bool _present = false;
    inline int _missesSincePresent = 0;

    inline bool begin()
    {
        if (!kPresenceDetectionEnabled)
            return false;

        const tflite::Model *model = tflite::GetModel(g_person_detect_model_data);
        if (model->version() != TFLITE_SCHEMA_VERSION)
        {
            Serial.println("[Presence] Model schema version mismatch, presence detection disabled.");
            return false;
        }

        _arena = (uint8_t *)heap_caps_malloc(kArenaSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!_arena)
        {
            Serial.println("[Presence] Could not allocate tensor arena (is PSRAM enabled?), disabled.");
            return false;
        }

        static tflite::MicroMutableOpResolver<5> resolver;
        resolver.AddAveragePool2D();
        resolver.AddConv2D();
        resolver.AddDepthwiseConv2D();
        resolver.AddReshape();
        resolver.AddSoftmax();

        static tflite::MicroInterpreter interpreter(model, resolver, _arena, kArenaSize);
        _interpreter = &interpreter;
        if (_interpreter->AllocateTensors() != kTfLiteOk)
        {
            Serial.println("[Presence] AllocateTensors failed, presence detection disabled.");
            return false;
        }
        _input = _interpreter->input(0);
        if (!_input || _input->type != kTfLiteInt8 || _input->bytes < kInputSize * kInputSize)
        {
            Serial.println("[Presence] Unexpected model input, presence detection disabled.");
            return false;
        }
        _ready = true;
        Serial.printf("[Presence] Ready (arena used: %u bytes).\n", (unsigned)_interpreter->arena_used_bytes());
        return true;
    }

    // Returns person confidence 0..1, or -1 on failure. Frame must be RGB565.
    inline float _score(camera_fb_t *fb)
    {
        if (!fb || fb->format != PIXFORMAT_RGB565 || fb->height > fb->width)
            return -1.0f;

        const int side = fb->height; // centre-crop a square
        const int xOffset = (fb->width - side) / 2;
        const uint16_t *src = (const uint16_t *)fb->buf;
        int8_t *dst = _input->data.int8;

        for (int y = 0; y < kInputSize; y++)
        {
            const int sy = y * side / kInputSize;
            for (int x = 0; x < kInputSize; x++)
            {
                const int sx = xOffset + x * side / kInputSize;
                const uint16_t p = src[sy * fb->width + sx];
                // The camera driver delivers RGB565 byte-swapped relative to the CPU's view.
                const uint16_t v = (p >> 8) | (p << 8);
                const int r = ((v >> 11) & 0x1F) << 3;
                const int g = ((v >> 5) & 0x3F) << 2;
                const int b = (v & 0x1F) << 3;
                const int gray = (r * 77 + g * 150 + b * 29) >> 8;
                dst[y * kInputSize + x] = (int8_t)(gray - 128);
            }
        }

        if (_interpreter->Invoke() != kTfLiteOk)
            return -1.0f;

        const TfLiteTensor *out = _interpreter->output(0);
        // Output order is [no_person, person]; int8 range -128..127 maps to 0..1.
        return (out->data.int8[1] + 128) / 255.0f;
    }

    inline void loop()
    {
        if (!_ready || !BackendComm::serverConnected || BackendComm::deviceSpeaking)
            return;
        const uint32_t now = millis();
        if (now - _lastRun < kPresenceIntervalMs)
            return;
        _lastRun = now;

        // Drop any queued frame so the check sees the current scene.
        camera_fb_t *stale = esp_camera_fb_get();
        if (stale)
            esp_camera_fb_return(stale);
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb)
            return;
        const float score = _score(fb);
        esp_camera_fb_return(fb);
        if (score < 0.0f)
            return;

        Serial.printf("[Presence] confidence %.2f\n", score);

        if (score >= kPresenceConfidence)
        {
            _missesSincePresent = 0;
            if (++_hits >= kPresenceHitsRequired && !_present)
            {
                _present = true;
                BackendComm::sendNotification("presence", "someone is looking at the device");
            }
        }
        else
        {
            _hits = 0;
            // Re-arm only after a few empty checks so one noisy frame doesn't retrigger.
            if (_present && ++_missesSincePresent >= kPresenceHitsRequired)
                _present = false;
        }
    }
} // namespace PresenceDetect
