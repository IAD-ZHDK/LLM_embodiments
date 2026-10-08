#pragma once

// WiFiNINA + WebSocket transport for the Arduino MKR WiFi 1010 (also works on Nano 33 IoT).
// Same API as the M5Stack example's BackendComm: begin(), loop(), sendNotification(), serverConnected.
// The message formats live in BackendProtocol.h; this file only handles WiFi and the socket.
// There is no mic/speaker/camera support here; audio is handled by the backend machine.

#include <SPI.h>
#include <WiFiNINA.h>
#include <WebSocketsClient.h>

#include "../WifiSecrets.h"
#include "BackendProtocol.h"

namespace BackendComm
{
    static WebSocketsClient webSocket;
    static bool serverConnected = false;
    static bool webSocketStarted = false;
    static unsigned long nextWifiRetryAt = 0;

    inline void sendNotification(const String &name, const String &value)
    {
        if (!serverConnected)
            return;
        String payload = BackendProtocol::notificationMessage(name, value);
        webSocket.sendTXT(payload);
    }

    inline void _onWebSocketEvent(WStype_t type, uint8_t *payload, size_t length)
    {
        switch (type)
        {
        case WStype_CONNECTED:
        {
            serverConnected = true;
            Serial.println("[WS] Connected");
            String info = BackendProtocol::deviceInfoMessage();
            webSocket.sendTXT(info);
            break;
        }
        case WStype_DISCONNECTED:
            serverConnected = false;
            Serial.println("[WS] Disconnected");
            break;
        case WStype_TEXT:
            BackendProtocol::handleMessage(payload, length);
            break;
        default: // binary TTS audio and pings are not used on this board
            break;
        }
    }

    // Tries each configured network in turn; returns true once connected.
    inline bool _connectWifi()
    {
        const unsigned long kConnectionTimeoutMs = 20000;
        for (size_t i = 0; i < DeviceConfig::kWifiCredentialCount; i++)
        {
            Serial.print("[WiFi] Connecting to ");
            Serial.println(DeviceConfig::kWifiCredentials[i].ssid);
            WiFi.begin(DeviceConfig::kWifiCredentials[i].ssid, DeviceConfig::kWifiCredentials[i].password);
            unsigned long start = millis();
            while (WiFi.status() != WL_CONNECTED && millis() - start < kConnectionTimeoutMs)
                delay(250);
            if (WiFi.status() == WL_CONNECTED)
            {
                Serial.print("[WiFi] Connected, IP ");
                Serial.println(WiFi.localIP());
                return true;
            }
            WiFi.disconnect();
        }
        return false;
    }

    inline void _startWebSocket()
    {
        // WiFiNINA cannot resolve .local (mDNS) names, so use an IP address in WifiSecrets.h.
        webSocket.begin(DeviceConfig::kFallbackBackendHost, DeviceConfig::kFallbackBackendPort, DeviceConfig::kFallbackBackendPath);
        webSocket.onEvent(_onWebSocketEvent);
        webSocket.setReconnectInterval(3000);
        webSocket.enableHeartbeat(15000, 3000, 2);
        webSocketStarted = true;
    }

    inline void begin()
    {
        Serial.begin(115200);
        if (WiFi.status() == WL_NO_MODULE)
            Serial.println("[WiFi] Communication with the WiFi module failed!");
    }

    inline void loop()
    {
        if (WiFi.status() != WL_CONNECTED)
        {
            serverConnected = false;
            if (webSocketStarted)
            {
                webSocket.disconnect();
                webSocketStarted = false;
            }
            if (millis() >= nextWifiRetryAt)
            {
                if (!_connectWifi())
                    nextWifiRetryAt = millis() + 5000;
            }
            return;
        }
        if (!webSocketStarted)
            _startWebSocket();
        webSocket.loop();
    }
} // namespace BackendComm
