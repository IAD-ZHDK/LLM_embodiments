#pragma once

// Runtime-editable persona. Values from DevicePersona.h are only the defaults; anything saved
// through the web page (http://<device-ip>/) is stored in flash (NVS) and survives re-flashing
// the sketch. "Reset to defaults" on the page erases the saved values.
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

#include "../DevicePersona.h"

namespace PersonaEditor
{
    struct Runtime
    {
        String deviceName;
        String systemPrompt;
        String model;
        float temperature;
        float topP;
        int topK;
        int maxTokens;
        float repeatPenalty;
        int voice;
        String notifications[kNotificationGuidanceCount > 0 ? kNotificationGuidanceCount : 1];
    };

    static Runtime persona;
    static WebServer server(80);
    static bool serverStarted = false;
    static bool changed = false; // set after a save; BackendComm reconnects so the backend gets the new persona

    inline void load()
    {
        Preferences prefs;
        prefs.begin("persona", true);
        persona.deviceName = prefs.getString("name", kDeviceName);
        persona.systemPrompt = prefs.getString("prompt", kSystemPrompt);
        persona.model = prefs.getString("model", kGenerationSettings.model);
        persona.temperature = prefs.getFloat("temp", kGenerationSettings.temperature);
        persona.topP = prefs.getFloat("topP", kGenerationSettings.topP);
        persona.topK = prefs.getInt("topK", kGenerationSettings.topK);
        persona.maxTokens = prefs.getInt("maxTok", kGenerationSettings.maxTokens);
        persona.repeatPenalty = prefs.getFloat("repeat", kGenerationSettings.repeatPenalty);
        persona.voice = prefs.getInt("voice", kGenerationSettings.voice);
        for (size_t i = 0; i < kNotificationGuidanceCount; i++)
            persona.notifications[i] = prefs.getString(("n" + String(i)).c_str(), kNotificationGuidance[i].instruction);
        prefs.end();
    }

    inline String _escape(const String &in)
    {
        String out;
        out.reserve(in.length() + 16);
        for (size_t i = 0; i < in.length(); i++)
        {
            char c = in[i];
            if (c == '&')
                out += "&amp;";
            else if (c == '<')
                out += "&lt;";
            else if (c == '>')
                out += "&gt;";
            else if (c == '"')
                out += "&quot;";
            else
                out += c;
        }
        return out;
    }

    inline void _field(String &html, const char *label, const char *name, const String &value)
    {
        html += "<label>" + String(label) + "<input name='" + name + "' value=\"" + _escape(value) + "\"></label>";
    }

    inline void _handleRoot()
    {
        String html;
        html.reserve(4096);
        html += "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>Device persona</title><style>"
                "body{font-family:sans-serif;max-width:640px;margin:1em auto;padding:0 1em}"
                "label{display:block;margin:.8em 0 .2em;font-weight:bold}"
                "input,textarea{width:100%;box-sizing:border-box;font:inherit;padding:.4em}"
                "textarea{height:12em}button{margin:1em .5em 0 0;padding:.6em 1.2em}"
                "</style></head><body><h2>Device persona</h2>";
        if (server.hasArg("saved"))
            html += "<p><b>Saved. The device is reconnecting with the new persona.</b></p>";
        html += "<form method='POST' action='/save'>";
        _field(html, "Device name", "name", persona.deviceName);
        html += "<label>System prompt<textarea name='prompt'>" + _escape(persona.systemPrompt) + "</textarea></label>";
        _field(html, "Model (empty = config.toml)", "model", persona.model);
        _field(html, "Temperature", "temp", String(persona.temperature));
        _field(html, "Top-p", "topP", String(persona.topP));
        _field(html, "Top-k", "topK", String(persona.topK));
        _field(html, "Max tokens", "maxTok", String(persona.maxTokens));
        _field(html, "Repeat penalty", "repeat", String(persona.repeatPenalty));
        _field(html, "Voice (1-6)", "voice", String(persona.voice));
        for (size_t i = 0; i < kNotificationGuidanceCount; i++)
        {
            html += "<label>Notification '" + _escape(kNotificationGuidance[i].name) + "'<textarea style='height:5em' name='n" + String(i) + "'>" +
                    _escape(persona.notifications[i]) + "</textarea></label>";
        }
        html += "<button type='submit'>Save</button></form>"
                "<form method='POST' action='/reset' onsubmit=\"return confirm('Erase saved values and use the flashed defaults?')\">"
                "<button type='submit'>Reset to defaults</button></form></body></html>";
        server.send(200, "text/html", html);
    }

    inline void _handleSave()
    {
        Preferences prefs;
        prefs.begin("persona", false);
        prefs.putString("name", server.arg("name"));
        prefs.putString("prompt", server.arg("prompt"));
        prefs.putString("model", server.arg("model"));
        prefs.putFloat("temp", server.arg("temp").toFloat());
        prefs.putFloat("topP", server.arg("topP").toFloat());
        prefs.putInt("topK", server.arg("topK").toInt());
        prefs.putInt("maxTok", server.arg("maxTok").toInt());
        prefs.putFloat("repeat", server.arg("repeat").toFloat());
        prefs.putInt("voice", server.arg("voice").toInt());
        for (size_t i = 0; i < kNotificationGuidanceCount; i++)
            prefs.putString(("n" + String(i)).c_str(), server.arg("n" + String(i)));
        prefs.end();
        load();
        changed = true;
        server.sendHeader("Location", "/?saved=1");
        server.send(303);
    }

    inline void _handleReset()
    {
        Preferences prefs;
        prefs.begin("persona", false);
        prefs.clear();
        prefs.end();
        load();
        changed = true;
        server.sendHeader("Location", "/?saved=1");
        server.send(303);
    }

    // Call once WiFi is connected.
    inline void startServer()
    {
        if (serverStarted)
            return;
        server.on("/", HTTP_GET, _handleRoot);
        server.on("/save", HTTP_POST, _handleSave);
        server.on("/reset", HTTP_POST, _handleReset);
        server.begin();
        serverStarted = true;
        Serial.printf("[Persona] Editor at http://%s/\n", WiFi.localIP().toString().c_str());
    }

    inline void handleClient()
    {
        if (serverStarted)
            server.handleClient();
    }
} // namespace PersonaEditor
