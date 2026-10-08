#pragma once

// Board-independent part of the backend WebSocket protocol (JSON text messages only).
// Builds the messages a device sends and dispatches the ones it receives, so it can be reused
// with any transport (WiFiNINA, ESP32 WiFi, Ethernet...). It needs only ArduinoJson and the
// two shared files DevicePersona.h / DeviceTools.h.

#include <ArduinoJson.h>

#include "../DevicePersona.h"
#include "DeviceTools.h"

namespace BackendProtocol
{
    inline String notificationMessage(const String &name, const String &value)
    {
        JsonDocument doc;
        doc["notification"]["name"] = name;
        doc["notification"]["value"] = value;
        String payload;
        serializeJson(doc, payload);
        return payload;
    }

    // Persona + generation settings + tool declarations, sent once after every (re)connect.
    inline String deviceInfoMessage()
    {
        JsonDocument doc;
        JsonObject info = doc["deviceInfo"].to<JsonObject>();
        info["deviceName"] = kDeviceName;
        info["persona"] = kSystemPrompt;
        JsonObject generation = info["generation"].to<JsonObject>();
        generation["model"] = kGenerationSettings.model;
        generation["temperature"] = kGenerationSettings.temperature;
        generation["top_p"] = kGenerationSettings.topP;
        generation["top_k"] = kGenerationSettings.topK;
        generation["max_tokens"] = kGenerationSettings.maxTokens;
        generation["repeat_penalty"] = kGenerationSettings.repeatPenalty;
        JsonArray notifications = info["notificationGuidance"].to<JsonArray>();
        for (size_t i = 0; i < kNotificationGuidanceCount; i++)
        {
            JsonObject entry = notifications.add<JsonObject>();
            entry["name"] = kNotificationGuidance[i].name;
            entry["instruction"] = kNotificationGuidance[i].instruction;
        }
        JsonArray tools = info["tools"].to<JsonArray>();
        for (size_t i = 0; i < deviceToolCount; i++)
        {
            JsonObject tool = tools.add<JsonObject>();
            tool["name"] = deviceTools[i].name;
            tool["description"] = deviceTools[i].description;
            tool["dataType"] = deviceTools[i].dataType;
            tool["commType"] = deviceTools[i].commType;
            tool["responseType"] = deviceTools[i].responseType;
        }
        JsonArray history = info["history"].to<JsonArray>();
        for (size_t i = 0; i < kPersonaHistoryCount; i++)
        {
            JsonObject turn = history.add<JsonObject>();
            turn["role"] = kPersonaHistory[i].role;
            turn["content"] = kPersonaHistory[i].content;
        }
        String payload;
        serializeJson(doc, payload);
        return payload;
    }

    // Handles one incoming text message. Only tool calls matter to a board without audio;
    // everything else (audio, config, display text) is ignored. Returns true if a tool ran.
    inline bool handleMessage(const uint8_t *payload, size_t length)
    {
        JsonDocument doc;
        if (deserializeJson(doc, payload, length) != DeserializationError::Ok)
            return false;
        if (!doc["toolCall"].is<JsonObject>())
            return false;

        String name = doc["toolCall"]["name"].as<String>();
        String value = doc["toolCall"]["value"].as<String>();
        for (size_t i = 0; i < deviceToolCount; i++)
        {
            if (name == deviceTools[i].name)
            {
                deviceTools[i].handler(value);
                return true;
            }
        }
        return false;
    }
} // namespace BackendProtocol
