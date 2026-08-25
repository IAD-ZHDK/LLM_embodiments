#pragma once

// Core server-communication plumbing for the M5Stack WiFi example: WiFi bootstrap, the
// backend WebSocket connection, audio streaming, and the JSON message protocol. The main
// .ino only needs to declare its own tools (see DeviceTools.h) and call BackendComm::begin()/loop().
#include <M5Unified.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <ArduinoJson.h>

#include "DeviceConfig.h"
#include "DevicePersona.h"
#include "DeviceTools.h"

namespace BackendComm
{

    // Audio streaming settings (must match RATE in backend/scriptRemoteSTT.py)
    static const uint32_t kSampleRate = 16000;
    static const size_t kAudioChunkSamples = 512; // ~32ms per chunk at 16kHz
    static int16_t audioBuffer[kAudioChunkSamples];

    static Preferences preferences;
    static WebSocketsClient webSocket;

    static bool micMuted = false;
    static bool serverConnected = false;
    static bool webSocketStarted = false;
    static unsigned long nextWifiRetryAt = 0;

    static String wifiSsid;
    static String wifiPassword;
    static String backendHost;
    static uint16_t backendPort;
    static String backendPath;

    inline void sendNotification(const String &name, const String &value)
    {
        displayState.lastNotification = name + " = " + value;
        JsonDocument doc;
        JsonObject notification = doc["notification"].to<JsonObject>();
        notification["name"] = name;
        notification["value"] = value;
        String payload;
        serializeJson(doc, payload);
        webSocket.sendTXT(payload);
    }

    inline void _sendMicState()
    {
        JsonDocument doc;
        doc["mic"] = micMuted ? "muted" : "unmuted";
        String payload;
        serializeJson(doc, payload);
        webSocket.sendTXT(payload);
    }

    // Uploads the persona + optional example history + this device's MCP-style tool declarations
    // so the backend can install them as the live system prompt/history and function-calling
    // schema (see DevicePersona.h / DeviceTools.h).
    inline void _sendDeviceInfo()
    {
        JsonDocument doc;
        doc["deviceInfo"]["persona"] = kSystemPrompt;
        JsonObject generation = doc["deviceInfo"]["generation"].to<JsonObject>();
        generation["model"] = kGenerationSettings.model;
        generation["temperature"] = kGenerationSettings.temperature;
        generation["top_p"] = kGenerationSettings.topP;
        generation["top_k"] = kGenerationSettings.topK;
        generation["max_tokens"] = kGenerationSettings.maxTokens;
        generation["repeat_penalty"] = kGenerationSettings.repeatPenalty;
        JsonArray tools = doc["deviceInfo"]["tools"].to<JsonArray>();
        for (size_t i = 0; i < deviceToolCount; i++)
        {
            JsonObject tool = tools.add<JsonObject>();
            tool["name"] = deviceTools[i].name;
            tool["description"] = deviceTools[i].description;
            tool["dataType"] = deviceTools[i].dataType;
        }
        JsonArray history = doc["deviceInfo"]["history"].to<JsonArray>();
        for (size_t i = 0; i < kPersonaHistoryCount; i++)
        {
            JsonObject turn = history.add<JsonObject>();
            turn["role"] = kPersonaHistory[i].role;
            turn["content"] = kPersonaHistory[i].content;
        }
        String payload;
        serializeJson(doc, payload);
        webSocket.sendTXT(payload);
    }

    inline void _handleToolCall(const String &name, const String &value)
    {
        displayState.lastToolCall = name + " = " + value;

        for (size_t i = 0; i < deviceToolCount; i++)
        {
            if (name == deviceTools[i].name)
            {
                deviceTools[i].handler(value);
                break;
            }
        }

        redrawDisplay();
    }

    inline void _handleConfigMessage(JsonObjectConst config)
    {
        clearRemoteTools();
        for (JsonObjectConst tool : config["tools"].as<JsonArrayConst>())
        {
            addRemoteTool(tool["name"].as<String>(), tool["deviceCommand"].as<String>(), tool["dataType"].as<String>());
        }
        displayState.lastDebug = "Config: " + String(remoteToolCount) + " tools";
        redrawDisplay();
    }

    inline void _onWebSocketText(uint8_t *payload, size_t length)
    {
        JsonDocument doc;
        if (deserializeJson(doc, payload, length) != DeserializationError::Ok)
            return;

        if (doc["config"].is<JsonObject>())
        {
            _handleConfigMessage(doc["config"].as<JsonObjectConst>());
        }
        else if (doc["toolCall"].is<JsonObject>())
        {
            JsonObjectConst toolCall = doc["toolCall"].as<JsonObjectConst>();
            _handleToolCall(toolCall["name"].as<String>(), toolCall["value"].as<String>());
        }
        else if (doc["debug"].is<const char *>())
        {
            displayState.lastDebug = doc["debug"].as<String>();
            redrawDisplay();
        }
    }

    inline void _onWebSocketEvent(WStype_t type, uint8_t *payload, size_t length)
    {
        switch (type)
        {
        case WStype_CONNECTED:
            serverConnected = true;
            displayState.wsStatus = "Server: connected";
            Serial.println("[WS] Connected");
            _sendDeviceInfo();
            redrawDisplay();
            break;
        case WStype_DISCONNECTED:
            serverConnected = false;
            displayState.wsStatus = "Server: disconnected";
            Serial.println("[WS] Disconnected");
            redrawDisplay();
            break;
        case WStype_ERROR:
            Serial.printf("[WS] Error: %s\n", payload ? (const char *)payload : "(no message)");
            break;
        case WStype_TEXT:
            _onWebSocketText(payload, length);
            break;
        default:
            break;
        }
    }

    inline void _loadBootstrapConfig()
    {
        preferences.begin("device-cfg", true);
        wifiSsid = preferences.getString("ssid", "");
        wifiPassword = preferences.getString("password", "");
        backendHost = preferences.getString("backendHost", DeviceConfig::kFallbackBackendHost);
        backendPort = preferences.getUShort("backendPort", DeviceConfig::kFallbackBackendPort);
        backendPath = preferences.getString("backendPath", DeviceConfig::kFallbackBackendPath);
        preferences.end();
    }

    inline void _connectWifi()
    {
        displayState.wifiStatus = "WiFi: connecting...";
        redrawDisplay();
        WiFi.mode(WIFI_STA);

        const unsigned long kConnectionTimeoutMs = 10000;
        bool hasSavedCredentials = !wifiSsid.isEmpty();
        size_t credentialCount = DeviceConfig::kWifiCredentialCount + (hasSavedCredentials ? 1 : 0);
        Serial.printf("[WiFi] Starting connection: %u credential set(s), timeout %lums.\n",
                      static_cast<unsigned int>(credentialCount), kConnectionTimeoutMs);
        for (size_t index = 0; index < credentialCount; index++)
        {
            bool useSavedCredentials = hasSavedCredentials && index == 0;
            size_t configuredIndex = hasSavedCredentials ? index - 1 : index;
            const char *ssid = useSavedCredentials ? wifiSsid.c_str() : DeviceConfig::kWifiCredentials[configuredIndex].ssid;
            const char *password = useSavedCredentials ? wifiPassword.c_str() : DeviceConfig::kWifiCredentials[configuredIndex].password;
            if (!ssid || !ssid[0])
            {
                Serial.printf("[WiFi] Skipping empty credential set %u.\n", static_cast<unsigned int>(index + 1));
                continue;
            }

            Serial.printf("[WiFi] Attempt %u/%u using %s SSID '%s'.\n",
                          static_cast<unsigned int>(index + 1),
                          static_cast<unsigned int>(credentialCount),
                          useSavedCredentials ? "saved" : "configured",
                          ssid);
            WiFi.disconnect(true, true);
            delay(100);
            WiFi.begin(ssid, password);
            unsigned long startedAt = millis();
            while (WiFi.status() != WL_CONNECTED && millis() - startedAt < kConnectionTimeoutMs)
            {
                delay(250);
                Serial.print(".");
            }

            if (WiFi.status() == WL_CONNECTED)
            {
                wifiSsid = ssid;
                wifiPassword = password;
                displayState.wifiStatus = "WiFi: " + WiFi.localIP().toString();
                Serial.printf("\n[WiFi] Connected after %lums.\n", millis() - startedAt);
                Serial.printf("[WiFi] IP: %s, gateway: %s, RSSI: %d dBm.\n",
                              WiFi.localIP().toString().c_str(),
                              WiFi.gatewayIP().toString().c_str(),
                              WiFi.RSSI());
                redrawDisplay();
                return;
            }

            Serial.printf("\n[WiFi] Attempt failed after %lums; status code: %d.\n",
                          millis() - startedAt,
                          static_cast<int>(WiFi.status()));
        }

        displayState.wifiStatus = "WiFi: unavailable";
        Serial.printf("[WiFi] No configured network could be reached; final status code: %d.\n",
                      static_cast<int>(WiFi.status()));
        redrawDisplay();
    }

    inline void _startWebSocket()
    {
        if (webSocketStarted)
            return;
        webSocket.begin(backendHost.c_str(), backendPort, backendPath.c_str());
        webSocket.onEvent(_onWebSocketEvent);
        webSocket.setReconnectInterval(2000);
        webSocketStarted = true;
        Serial.println("[WS] Starting backend connection.");
    }

    inline void _streamMicAudio()
    {
        if (micMuted || !serverConnected)
            return;

        if (!M5.Mic.record(audioBuffer, kAudioChunkSamples, kSampleRate))
            return;

        long sumSquares = 0;
        for (size_t i = 0; i < kAudioChunkSamples; i++)
        {
            sumSquares += (long)audioBuffer[i] * (long)audioBuffer[i];
        }
        float rms = sqrt((float)sumSquares / kAudioChunkSamples);
        displayState.micLevel = constrain((int)(rms / 300.0f * 100.0f), 0, 100); // 300 is a rough mic-gain calibration divisor

        static unsigned long lastBarUpdate = 0;
        unsigned long now = millis();
        if (now - lastBarUpdate >= 100) // ~10Hz, avoids hammering the SPI display
        {
            drawMicLevelBar();
            lastBarUpdate = now;
        }

        webSocket.sendBIN(reinterpret_cast<uint8_t *>(audioBuffer), kAudioChunkSamples * sizeof(int16_t));
    }

    inline void _checkMuteButton()
    {
        M5.update();
        if (M5.BtnA.wasPressed())
        {
            micMuted = !micMuted;
            displayState.micStatus = micMuted ? "Mic: muted" : "Mic: live";
            displayState.micLevel = 0;
            _sendMicState();
            redrawDisplay();
            drawMicLevelBar();
        }
    }

    // Call once from setup(): brings up the M5Stack, mic, WiFi, and the backend WebSocket.
    inline void begin()
    {
        Serial.begin(115200);
        auto cfg = M5.config();
        M5.begin(cfg);
        M5.Mic.begin();
        bool imuFound = M5.Imu.begin();
        Serial.printf("[IMU] begin() -> %s, type=%d (0 = imu_none: no IMU detected on this board)\n",
                      imuFound ? "OK" : "FAILED", static_cast<int>(M5.Imu.getType()));

        _loadBootstrapConfig();
        displayState.wsTarget = "Target: " + backendHost + ":" + String(backendPort) + backendPath;
        Serial.printf("[Config] Target backend: ws://%s:%u%s\n", backendHost.c_str(), backendPort, backendPath.c_str());
        _connectWifi();
        if (WiFi.status() == WL_CONNECTED)
            _startWebSocket();
        else
            nextWifiRetryAt = millis() + 10000;

        drawMicLevelBar();
    }

    // Call every loop() iteration: services the WebSocket, mic streaming, and mute button.
    // Add your own sensor/notification checks (see checkShake() in the .ino) alongside this call.
    inline void loop()
    {
        _checkMuteButton();
        if (WiFi.status() != WL_CONNECTED)
        {
            if (millis() >= nextWifiRetryAt)
            {
                Serial.println("[WiFi] Retrying configured networks.");
                _connectWifi();
                nextWifiRetryAt = millis() + 10000;
                if (WiFi.status() == WL_CONNECTED)
                    _startWebSocket();
            }
            return;
        }

        _startWebSocket();
        webSocket.loop();
        _streamMicAudio();
    }

} // namespace BackendComm
