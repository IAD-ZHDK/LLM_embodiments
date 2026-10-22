#pragma once

#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <ArduinoJson.h>
#include <ESP_I2S.h>

#include "DeviceConfig.h"
#include "../DevicePersona.h"
#include "PersonaEditor.h"
#include "DeviceTools.h"

// Backend client for the XIAO ESP32-S3 Sense: streams the PDM microphone to the backend over a
// WebSocket, forwards camera frames on request, and executes tool calls. There is no screen, so
// status goes to the Serial monitor. There is no speaker yet: see the "TODO: speaker" markers.
namespace BackendComm
{
    constexpr uint32_t kSampleRate = 16000;
    constexpr size_t kAudioChunkSamples = 512;

    inline I2SClass i2s;
    inline int16_t audioBuffer[kAudioChunkSamples];

    inline Preferences preferences;
    inline WebSocketsClient webSocket;

    inline bool micMuted = false;
    inline bool speakerMuted = false;
    inline bool deviceSpeaking = false;
    inline bool serverConnected = false;
    inline bool webSocketStarted = false;
    inline bool mdnsStarted = false;
    inline bool micReady = false;
    inline unsigned long nextWifiRetryAt = 0;

    inline String wifiSsid;
    inline String wifiPassword;
    inline String backendHost;
    inline uint16_t backendPort = 3000;
    inline String backendPath;

    inline void _sendAudioFinished()
    {
        webSocket.sendTXT("{\"audio\":\"finished\"}");
    }

    // TODO: speaker. Once a speaker is attached, play the PCM here and only call this when playback
    // has really finished. For now it is called right away so the backend and mic keep working.
    inline void _finishDeviceSpeech()
    {
        deviceSpeaking = false;
        _sendAudioFinished();
    }

    // TODO: speaker. Incoming TTS audio (pcm_s16le, mono, sampleRate from "audioStart") is dropped.
    inline void _playDeviceAudio(uint8_t *payload, size_t length)
    {
        (void)payload;
        (void)length;
    }

    // TODO: speaker. Tone helpers for notification sounds; no-ops until there is a speaker.
    inline void playTone(uint32_t frequency, uint32_t durationMs)
    {
        (void)frequency;
        (void)durationMs;
    }

    inline void _startMic()
    {
        if (micReady)
            return;
        i2s.setPinsPdmRx(XiaoPins::kPdmClk, XiaoPins::kPdmData);
        micReady = i2s.begin(I2S_MODE_PDM_RX, kSampleRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
        Serial.println(micReady ? "[Mic] PDM microphone started." : "[Mic] Failed to start PDM microphone.");
    }

    inline void sendNotification(const String &name, const String &value)
    {
        JsonDocument doc;
        JsonObject notification = doc["notification"].to<JsonObject>();
        notification["name"] = name;
        notification["value"] = value;
        String payload;
        serializeJson(doc, payload);
        webSocket.sendTXT(payload);
        Serial.printf("[Notify] %s = %s\n", name.c_str(), value.c_str());
    }

    inline bool sendCameraFrame(uint8_t *buffer, size_t length, uint16_t width, uint16_t height)
    {
        if (!serverConnected || !buffer || length == 0)
            return false;

        JsonDocument doc;
        JsonObject image = doc["cameraImageStart"].to<JsonObject>();
        image["length"] = length;
        image["width"] = width;
        image["height"] = height;
        image["format"] = "rgb565";
        String payload;
        serializeJson(doc, payload);
        if (!webSocket.sendTXT(payload))
            return false;

        const size_t chunkSize = 4096;
        for (size_t offset = 0; offset < length; offset += chunkSize)
        {
            size_t currentSize = min(length - offset, chunkSize);
            if (!webSocket.sendBIN(buffer + offset, currentSize))
                return false;
            delay(1);
        }
        return true;
    }

    inline void sendCameraError(const String &message)
    {
        JsonDocument doc;
        doc["cameraImageError"] = message;
        String payload;
        serializeJson(doc, payload);
        webSocket.sendTXT(payload);
    }

    inline void _sendAudioMuteState()
    {
        JsonDocument doc;
        doc["audioState"]["micMuted"] = micMuted;
        doc["audioState"]["speakerMuted"] = speakerMuted;
        String payload;
        serializeJson(doc, payload);
        webSocket.sendTXT(payload);
    }

    inline void _applyAudioMuteState(bool muteMic, bool muteSpeaker, bool reportToBackend)
    {
        micMuted = muteMic;
        speakerMuted = muteSpeaker;
        Serial.printf("[Audio] mic %s, speaker %s\n", micMuted ? "muted" : "live", speakerMuted ? "muted" : "on");
        if (reportToBackend)
            _sendAudioMuteState();
    }

    // Uploads the persona + optional example history + this device's tool declarations so the
    // backend can install them as the live system prompt/history and function-calling schema.
    inline void _sendDeviceInfo()
    {
        JsonDocument doc;
        const PersonaEditor::Runtime &p = PersonaEditor::persona;
        doc["deviceInfo"]["deviceName"] = p.deviceName;
        doc["deviceInfo"]["persona"] = p.systemPrompt;
        JsonObject generation = doc["deviceInfo"]["generation"].to<JsonObject>();
        generation["model"] = p.model;
        generation["temperature"] = p.temperature;
        generation["top_p"] = p.topP;
        generation["top_k"] = p.topK;
        generation["max_tokens"] = p.maxTokens;
        generation["repeat_penalty"] = p.repeatPenalty;
        doc["deviceInfo"]["speech"]["voice"] = p.voice;
        JsonArray notifications = doc["deviceInfo"]["notificationGuidance"].to<JsonArray>();
        for (size_t i = 0; i < kNotificationGuidanceCount; i++)
        {
            JsonObject notification = notifications.add<JsonObject>();
            notification["name"] = kNotificationGuidance[i].name;
            notification["instruction"] = p.notifications[i];
        }
        JsonArray tools = doc["deviceInfo"]["tools"].to<JsonArray>();
        for (size_t i = 0; i < deviceToolCount; i++)
        {
            JsonObject tool = tools.add<JsonObject>();
            tool["name"] = deviceTools[i].name;
            tool["description"] = deviceTools[i].description;
            tool["dataType"] = deviceTools[i].dataType;
            tool["commType"] = deviceTools[i].commType;
            tool["responseType"] = deviceTools[i].responseType;
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
        Serial.printf("[Tool] %s = %s\n", name.c_str(), value.c_str());
        for (size_t i = 0; i < deviceToolCount; i++)
        {
            if (name == deviceTools[i].name)
            {
                deviceTools[i].handler(value);
                break;
            }
        }
    }

    inline void _handleConfigMessage(JsonObjectConst config)
    {
        clearRemoteTools();
        for (JsonObjectConst tool : config["tools"].as<JsonArrayConst>())
        {
            addRemoteTool(tool["name"].as<String>(), tool["deviceCommand"].as<String>(), tool["dataType"].as<String>());
        }
        Serial.printf("[Config] %d tools\n", remoteToolCount);
    }

    inline void _onWebSocketText(uint8_t *payload, size_t length)
    {
        JsonDocument doc;
        if (deserializeJson(doc, payload, length) != DeserializationError::Ok)
            return;

        if (doc["audioState"].is<JsonObject>())
        {
            JsonObjectConst audioState = doc["audioState"].as<JsonObjectConst>();
            bool muteMic = audioState["micMuted"] | micMuted;
            bool muteSpeaker = audioState["speakerMuted"] | speakerMuted;
            _applyAudioMuteState(muteMic, muteSpeaker, false);
        }
        else if (doc["config"].is<JsonObject>())
        {
            _handleConfigMessage(doc["config"].as<JsonObjectConst>());
        }
        else if (doc["toolCall"].is<JsonObject>())
        {
            JsonObjectConst toolCall = doc["toolCall"].as<JsonObjectConst>();
            _handleToolCall(toolCall["name"].as<String>(), toolCall["value"].as<String>());
        }
        else if (doc["assistantResponse"].is<const char *>())
        {
            // No screen or speaker yet, so the reply is only printed.
            Serial.printf("[AI] %s\n", doc["assistantResponse"].as<const char *>());
        }
        else if (doc["audioStart"].is<JsonObject>())
        {
            deviceSpeaking = true;
        }
        else if (!doc["audioEnd"].isNull())
        {
            _finishDeviceSpeech();
        }
    }

    inline void _onWebSocketEvent(WStype_t type, uint8_t *payload, size_t length)
    {
        switch (type)
        {
        case WStype_CONNECTED:
            serverConnected = true;
            Serial.println("[WS] Connected");
            _sendDeviceInfo();
            _sendAudioMuteState();
            break;
        case WStype_DISCONNECTED:
            serverConnected = false;
            deviceSpeaking = false;
            Serial.println("[WS] Disconnected");
            break;
        case WStype_ERROR:
            Serial.printf("[WS] Error: %s\n", payload ? (const char *)payload : "(no message)");
            break;
        case WStype_TEXT:
            _onWebSocketText(payload, length);
            break;
        case WStype_BIN:
            _playDeviceAudio(payload, length);
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

    // Lets a "<name>.local" backendHost (e.g. a Mac's Bonjour hostname) be resolved.
    inline void _startMDNS()
    {
        if (mdnsStarted)
            return;
        if (MDNS.begin("xiao-esp32s3-client"))
        {
            mdnsStarted = true;
            Serial.println("[mDNS] Started; .local backend hostnames can now be resolved.");
        }
        else
        {
            Serial.println("[mDNS] Failed to start; .local backend hostnames will not resolve.");
        }
    }

    inline void _connectWifi()
    {
        WiFi.mode(WIFI_STA);
        WiFi.setSleep(false);

        const unsigned long kConnectionTimeoutMs = 20000;
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
                continue;

            Serial.printf("[WiFi] Attempt %u/%u using SSID '%s'.\n",
                          static_cast<unsigned int>(index + 1), static_cast<unsigned int>(credentialCount), ssid);
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
                _startMDNS();
                PersonaEditor::startServer();
                Serial.printf("\n[WiFi] Connected. IP: %s, RSSI: %d dBm.\n",
                              WiFi.localIP().toString().c_str(), WiFi.RSSI());
                return;
            }
            Serial.printf("\n[WiFi] Attempt failed; status code: %d.\n", static_cast<int>(WiFi.status()));
        }
        Serial.println("[WiFi] No configured network could be reached.");
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
        if (!micReady)
            return;

        // Always read so the DMA buffer never goes stale while muted or while the device is speaking.
        size_t bytesRead = i2s.readBytes(reinterpret_cast<char *>(audioBuffer), sizeof(audioBuffer));
        if (bytesRead != sizeof(audioBuffer))
            return;
        if (micMuted || deviceSpeaking || !serverConnected)
            return;

        long sumSquares = 0;
        for (size_t i = 0; i < kAudioChunkSamples; i++)
        {
            int32_t sample = (int32_t)audioBuffer[i] << kMicGainShift;
            audioBuffer[i] = (int16_t)constrain(sample, -32768, 32767);
            sumSquares += (long)audioBuffer[i] * (long)audioBuffer[i];
        }

        static unsigned long lastLevelLog = 0;
        if (millis() - lastLevelLog >= 2000)
        {
            Serial.printf("[Mic] RMS %.0f\n", sqrt((float)sumSquares / kAudioChunkSamples));
            lastLevelLog = millis();
        }

        webSocket.sendBIN(reinterpret_cast<uint8_t *>(audioBuffer), sizeof(audioBuffer));
    }

    // The BOOT button toggles the microphone mute.
    inline void _checkMuteControl()
    {
        static bool wasDown = false;
        bool down = digitalRead(XiaoPins::kBootButton) == LOW;
        if (down && !wasDown)
            _applyAudioMuteState(!micMuted, speakerMuted, true);
        wasDown = down;
    }

    // Call once from setup(): brings up the mic, WiFi, and the backend WebSocket.
    inline void begin()
    {
        Serial.begin(115200);
        pinMode(XiaoPins::kBootButton, INPUT_PULLUP);
        _startMic();

        _loadBootstrapConfig();
        PersonaEditor::load();
        backendHost = PersonaEditor::backend.host;
        backendPort = PersonaEditor::backend.port;
        backendPath = PersonaEditor::backend.path;
        Serial.printf("[Config] Target backend: ws://%s:%u%s\n", backendHost.c_str(), backendPort, backendPath.c_str());

        _connectWifi();
        if (WiFi.status() == WL_CONNECTED)
            _startWebSocket();
        else
            nextWifiRetryAt = millis() + 10000;
    }

    inline void loop()
    {
        _checkMuteControl();
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
        PersonaEditor::handleClient();
        if (PersonaEditor::backendChanged)
        {
            PersonaEditor::backendChanged = false;
            PersonaEditor::changed = false;
            backendHost = PersonaEditor::backend.host;
            backendPort = PersonaEditor::backend.port;
            backendPath = PersonaEditor::backend.path;
            Serial.printf("[Config] Target backend: ws://%s:%u%s\n", backendHost.c_str(), backendPort, backendPath.c_str());
            webSocket.disconnect();
            webSocket.begin(backendHost.c_str(), backendPort, backendPath.c_str());
        }
        else if (PersonaEditor::changed)
        {
            // Dropping the connection makes the WebSocket client reconnect and re-send deviceInfo.
            PersonaEditor::changed = false;
            webSocket.disconnect();
        }
        webSocket.loop();
        _streamMicAudio();
    }

} // namespace BackendComm
