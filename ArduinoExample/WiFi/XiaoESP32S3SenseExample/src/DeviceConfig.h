#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

// Real credentials live in WifiSecrets.h (gitignored); copy WifiSecrets.example.h to create it.
#include "WifiSecrets.h"

// One entry per tool the backend told us about.
struct RemoteTool
{
    String name;
    String deviceCommand;
    String dataType;
};

#define MAX_REMOTE_TOOLS 16
inline RemoteTool remoteTools[MAX_REMOTE_TOOLS];
inline int remoteToolCount = 0;

inline void clearRemoteTools() { remoteToolCount = 0; }

inline void addRemoteTool(const String &name, const String &deviceCommand, const String &dataType)
{
    if (remoteToolCount >= MAX_REMOTE_TOOLS)
        return;
    remoteTools[remoteToolCount++] = {name, deviceCommand, dataType};
}

// XIAO ESP32-S3 Sense hardware.
namespace XiaoPins
{
    // Onboard PDM microphone (Sense expansion board)
    constexpr int kPdmClk = 42;
    constexpr int kPdmData = 41;

    // User LED (active LOW) and BOOT button
    constexpr int kUserLed = 21;
    constexpr int kBootButton = 0;

    // Adafruit Audio BFF (MAX98357 I2S amp): DAT=A1, LR=A2, BCLK=A3
    constexpr int kSpkData = 2;  // A1
    constexpr int kSpkLrc = 3;   // A2
    constexpr int kSpkBclk = 4;  // A3
} // namespace XiaoPins

// Set to false if no Audio BFF is attached: TTS audio is then skipped and the device behaves
// exactly like the speaker-less version. An I2S amp can't be detected electronically, so this is
// a manual switch. If the I2S output can't be started, the device falls back automatically.
constexpr bool kSpeakerEnabled = true;
constexpr int kSpeakerVolumePercent = 60; // software volume, 0-100

// On-device person detection (TFLite Micro). After kPresenceHitsRequired consecutive confident
// checks (one every kPresenceIntervalMs), a "presence" notification is sent to the backend.
constexpr bool kPresenceDetectionEnabled = true;
constexpr uint32_t kPresenceIntervalMs = 2000;
constexpr float kPresenceConfidence = 0.75f; // 0..1
constexpr int kPresenceHitsRequired = 2;

// PDM microphones are quiet; samples are shifted left by this many bits (clipped to int16).
constexpr int kMicGainShift = 2;
