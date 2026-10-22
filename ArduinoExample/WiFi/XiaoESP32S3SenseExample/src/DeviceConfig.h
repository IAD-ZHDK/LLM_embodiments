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
} // namespace XiaoPins

// PDM microphones are quiet; samples are shifted left by this many bits (clipped to int16).
constexpr int kMicGainShift = 2;
