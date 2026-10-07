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
    bool micMuted = false;
    bool speakerMuted = false;
    String lastDebug = "";
    String lastToolCall = "";
    String lastNotification = "";
    int micLevel = 0; // 0-100, RMS of the last audio chunk from the ESP32 mic
};

inline DisplayState displayState;

// Geometry for the volume bar, drawn below the status text block and spanning the full display width.
inline const int kVolumeBarY = 110;
inline const int kVolumeBarHeight = 16;
inline const int kMuteButtonWidth = 108;
inline const int kMuteButtonHeight = 32;
inline const int kMuteButtonGap = 8;
inline const int kMuteButtonMargin = 8;

inline int volumeBarWidth() { return M5.Lcd.width(); }

inline int muteButtonX() { return (M5.Lcd.width() - (2 * kMuteButtonWidth + kMuteButtonGap)) / 2; }
inline int speakerMuteButtonX() { return muteButtonX() + kMuteButtonWidth + kMuteButtonGap; }
inline int muteButtonY() { return M5.Lcd.height() - kMuteButtonHeight - kMuteButtonMargin; }

inline bool muteButtonContains(int x, int y, int buttonX)
{
    int buttonY = muteButtonY();
    return x >= buttonX && x < buttonX + kMuteButtonWidth &&
           y >= buttonY && y < buttonY + kMuteButtonHeight;
}

// 0 disables M5Unified's built-in filter; 64 is a conservative starting point for CoreS3's ES7210 mics.
inline const uint8_t kMicNoiseFilterLevel = 64;

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

    M5.Lcd.setCursor(0, kVolumeBarY - 10);
    M5.Lcd.print("Volume");

    int buttonY = muteButtonY();
    int micButtonX = muteButtonX();
    int speakerButtonX = speakerMuteButtonX();
    M5.Lcd.setTextSize(1);
    uint16_t micFill = displayState.micMuted ? BLACK : WHITE;
    M5.Lcd.fillRect(micButtonX, buttonY, kMuteButtonWidth, kMuteButtonHeight, micFill);
    M5.Lcd.drawRect(micButtonX, buttonY, kMuteButtonWidth, kMuteButtonHeight, WHITE);
    M5.Lcd.setTextColor(displayState.micMuted ? WHITE : BLACK);
    M5.Lcd.setCursor(micButtonX + 27, buttonY + 12);
    M5.Lcd.print(displayState.micMuted ? "MIC MUTED" : "MUTE MIC");
    uint16_t spkFill = displayState.speakerMuted ? BLACK : WHITE;
    M5.Lcd.fillRect(speakerButtonX, buttonY, kMuteButtonWidth, kMuteButtonHeight, spkFill);
    M5.Lcd.drawRect(speakerButtonX, buttonY, kMuteButtonWidth, kMuteButtonHeight, WHITE);
    M5.Lcd.setTextColor(displayState.speakerMuted ? WHITE : BLACK);
    M5.Lcd.setCursor(speakerButtonX + 27, buttonY + 12);
    M5.Lcd.print(displayState.speakerMuted ? "SPK MUTED" : "MUTE SPK");
    M5.Lcd.setTextSize(1);
    M5.Lcd.setTextColor(WHITE);
}

// Partial redraw only (no fillScreen) so this can run every audio chunk without flicker.
inline void drawMicLevelBar()
{
    int barWidth = volumeBarWidth();
    int fillWidth = map(constrain(displayState.micLevel, 0, 100), 0, 100, 0, barWidth);
    M5.Lcd.fillRect(0, kVolumeBarY, barWidth, kVolumeBarHeight, BLACK);
    M5.Lcd.drawRect(0, kVolumeBarY, barWidth, kVolumeBarHeight, WHITE);
    if (fillWidth > 0)
    {
        M5.Lcd.fillRect(1, kVolumeBarY + 1, fillWidth - 1, kVolumeBarHeight - 2, WHITE);
    }
}
