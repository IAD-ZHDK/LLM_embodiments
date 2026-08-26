#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <M5Unified.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

// Real credentials live in WifiSecrets.h (gitignored); copy WifiSecrets.example.h to create it.
#include "WifiSecrets.h"

// One entry per tool the backend told us about (see handleConfigMessage in the .ino).
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

// Mirrors the on-screen status block; updated by the .ino as state changes.
struct DisplayState
{
    String wifiStatus = "WiFi: connecting...";
    String wifiIP = "";
    String wsStatus = "Server: disconnected";
    String wsTarget = "";
    String micStatus = "Mic: live";
    String lastDebug = "";
    String lastToolCall = "";
    String lastNotification = "";
    int micLevel = 0; // 0-100, RMS of the last audio chunk from the ESP32 mic
};

inline DisplayState displayState;

// Geometry for the mic level bar, drawn below the status text block.
inline const int kMicBarX = 0;
inline const int kMicBarY = 110;
inline const int kMicBarWidth = 200;
inline const int kMicBarHeight = 16;

inline void redrawDisplay()
{
    M5.Lcd.fillScreen(BLACK);
    M5.Lcd.setCursor(0, 0);
    M5.Lcd.setTextColor(WHITE);
    M5.Lcd.println(displayState.wifiStatus);
    M5.Lcd.println(displayState.wifiIP);
    M5.Lcd.println(displayState.wsStatus);
    M5.Lcd.println(displayState.wsTarget);
    M5.Lcd.println(displayState.micStatus);
    M5.Lcd.println("---");
    M5.Lcd.println(displayState.lastToolCall);
    M5.Lcd.println(displayState.lastNotification);
    M5.Lcd.println(displayState.lastDebug);
}

// Partial redraw only (no fillScreen) so this can run every audio chunk without flicker.
inline void drawMicLevelBar()
{
    int fillWidth = map(constrain(displayState.micLevel, 0, 100), 0, 100, 0, kMicBarWidth);
    M5.Lcd.fillRect(kMicBarX, kMicBarY, kMicBarWidth, kMicBarHeight, BLACK);
    M5.Lcd.drawRect(kMicBarX, kMicBarY, kMicBarWidth, kMicBarHeight, WHITE);
    if (fillWidth > 0)
    {
        M5.Lcd.fillRect(kMicBarX + 1, kMicBarY + 1, fillWidth - 1, kMicBarHeight - 2, GREEN);
    }
}
