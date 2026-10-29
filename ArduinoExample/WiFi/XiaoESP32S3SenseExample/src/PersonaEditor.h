#pragma once

// Runtime-editable persona. Values from DevicePersona.h are only the defaults; anything saved
// through the web page (http://<device-ip>/) is stored in flash (NVS) and survives re-flashing
// the sketch. "Reset to defaults" on the page erases the saved values.
#include <LittleFS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

#include "DeviceConfig.h"
#include "../DevicePersona.h"

namespace PersonaEditor
{
    struct Backend
    {
        String host;
        uint16_t port;
        String path;
    };

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
    static Backend backend;
    static WebServer server(80);
    static bool serverStarted = false;
    static bool changed = false;        // set after a save; BackendComm reconnects so the backend gets the new persona
    static bool backendChanged = false; // set when the backend address changed; BackendComm reconnects to the new address

    static String saveError;

    constexpr size_t kChunkSize = 3900;
    constexpr int kMaxChunks = 64;
    constexpr size_t kMaxFieldBytes = kChunkSize * kMaxChunks;

    inline String _chunkKey(const char *key, int index)
    {
        return index == 0 ? String(key) : String(key) + "." + String(index);
    }

    inline String _getLong(Preferences &prefs, const char *key, const String &fallback)
    {
        if (!prefs.isKey(key))
            return fallback;
        String out = prefs.getString(key, "");
        for (int i = 1; i < kMaxChunks; i++)
        {
            String k = _chunkKey(key, i);
            if (!prefs.isKey(k.c_str()))
                break;
            out += prefs.getString(k.c_str(), "");
        }
        return out;
    }

    inline void _putLong(Preferences &prefs, const char *key, const String &value)
    {
        size_t len = value.length();
        int chunks = len == 0 ? 1 : (int)((len + kChunkSize - 1) / kChunkSize);
        bool ok = chunks <= kMaxChunks;
        for (int i = 0; ok && i < chunks; i++)
        {
            String part = value.substring(i * kChunkSize, min(len, (i + 1) * kChunkSize));
            String k = _chunkKey(key, i);
            if (prefs.putString(k.c_str(), part) == 0 && part.length() > 0)
                ok = false;
        }
        if (!ok)
        {
            bool tooLong = len > kMaxFieldBytes;
            Serial.printf("[Persona] Failed to save '%s' (%u bytes): %s\n", key, (unsigned)len,
                          tooLong ? "text exceeds the per-field limit." : "NVS write failed (partition full).");
            saveError += String(key) + " (submitted " + String(len) + " bytes; maximum " +
                         String(kMaxFieldBytes) + " bytes): " +
                         (tooLong ? "text exceeds the per-field limit. "
                                  : "the NVS storage area is full (only ~20 KB shared by all settings) and no filesystem partition is available. ");
            return;
        }
        for (int i = chunks; i < kMaxChunks; i++)
        {
            String k = _chunkKey(key, i);
            if (!prefs.isKey(k.c_str()))
                break;
            prefs.remove(k.c_str());
        }
    }

    constexpr const char *kPromptFile = "/prompt.txt";

    inline bool _fsReady()
    {
        static int state = -1;
        if (state < 0)
        {
            state = LittleFS.begin(true) ? 1 : 0;
            if (!state)
                Serial.println("[Persona] LittleFS mount failed (no filesystem partition?). Select a partition scheme with SPIFFS/FS.");
        }
        return state == 1;
    }

    inline String _loadPrompt(Preferences &prefs)
    {
        if (_fsReady() && LittleFS.exists(kPromptFile))
        {
            File f = LittleFS.open(kPromptFile, "r");
            if (f)
            {
                String s = f.readString();
                f.close();
                return s;
            }
        }
        return _getLong(prefs, "prompt", kSystemPrompt);
    }

    inline void _savePrompt(Preferences &prefs, const String &value)
    {
        if (_fsReady())
        {
            File f = LittleFS.open(kPromptFile, "w");
            if (f)
            {
                size_t written = f.print(value);
                f.close();
                if (written == value.length())
                {
                    _putLong(prefs, "prompt", "");
                    return;
                }
            }
            LittleFS.remove(kPromptFile);
            Serial.println("[Persona] Writing prompt file failed, falling back to NVS.");
        }
        _putLong(prefs, "prompt", value);
    }

    inline void load()
    {
        Preferences prefs;
        prefs.begin("persona", true);
        persona.deviceName = _getLong(prefs, "name", kDeviceName);
        persona.systemPrompt = _loadPrompt(prefs);
        persona.model = _getLong(prefs, "model", kGenerationSettings.model);
        persona.temperature = prefs.getFloat("temp", kGenerationSettings.temperature);
        persona.topP = prefs.getFloat("topP", kGenerationSettings.topP);
        persona.topK = prefs.getInt("topK", kGenerationSettings.topK);
        persona.maxTokens = prefs.getInt("maxTok", kGenerationSettings.maxTokens);
        persona.repeatPenalty = prefs.getFloat("repeat", kGenerationSettings.repeatPenalty);
        persona.voice = prefs.getInt("voice", kGenerationSettings.voice);
        for (size_t i = 0; i < kNotificationGuidanceCount; i++)
            persona.notifications[i] = _getLong(prefs, ("n" + String(i)).c_str(), kNotificationGuidance[i].instruction);
        prefs.end();

        prefs.begin("device-cfg", true);
        backend.host = prefs.getString("backendHost", DeviceConfig::kFallbackBackendHost);
        backend.port = prefs.getUShort("backendPort", DeviceConfig::kFallbackBackendPort);
        backend.path = prefs.getString("backendPath", DeviceConfig::kFallbackBackendPath);
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
        if (server.hasArg("failed"))
            html += "<p style='color:#b00'><b>Not saved: flash storage is insufficient. " + _escape(saveError) + "</b></p>";
        else if (server.hasArg("saved"))
            html += "<p><b>Saved. The device is reconnecting.</b></p>";
        // The prompt is uploaded in small pieces first: the ESP32 WebServer silently drops large POST bodies.
        html += "<form id='pf' method='POST' action='/save'>";
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
        html += "<h2>Backend connection</h2>";
        _field(html, "Backend host (IP or name.local)", "backendHost", backend.host);
        _field(html, "Backend port", "backendPort", String(backend.port));
        _field(html, "Backend path", "backendPath", backend.path);
        html += "<button type='submit'>Save</button></form>"
                "<script>(function(){var f=document.getElementById('pf');var s=false;"
                "f.addEventListener('submit',async function(e){if(s)return;e.preventDefault();"
                "var t=f.querySelector('textarea[name=prompt]');var v=t.value;var n=Math.max(1,Math.ceil(v.length/800));"
                "try{for(var i=0;i<n;i++){var r=await fetch('/part',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},"
                "body:'i='+i+'&t='+encodeURIComponent(v.substr(i*800,800))});"
                "if(!r.ok)throw new Error('HTTP '+r.status);}}"
                "catch(x){alert('Upload of the system prompt failed: '+x.message);return;}"
                "t.removeAttribute('name');var h=document.createElement('input');h.type='hidden';h.name='promptParts';h.value=n;"
                "f.appendChild(h);s=true;f.submit();});})();</script>"
                "<form method='POST' action='/reset' onsubmit=\"return confirm('Erase saved persona values and use the flashed defaults?')\">"
                "<button type='submit'>Reset persona to defaults</button></form>"
                "<form method='POST' action='/resetBackend' onsubmit=\"return confirm('Erase the saved backend address and use the flashed default?')\">"
                "<button type='submit'>Reset backend address</button></form></body></html>";
        server.send(200, "text/html", html);
    }

    static String promptBuf;
    static int promptPartsGot = 0;

    inline void _handlePart()
    {
        int i = server.arg("i").toInt();
        if (i == 0)
        {
            promptBuf = "";
            promptPartsGot = 0;
        }
        if (i != promptPartsGot)
        {
            Serial.printf("[Persona] Prompt piece %d out of order (expected %d)\n", i, promptPartsGot);
            server.send(409, "text/plain", "out of order");
            return;
        }
        promptBuf += server.arg("t");
        promptPartsGot++;
        server.send(200, "text/plain", "ok");
    }

    inline void _handleSave()
    {
        saveError = "";
        String prompt = server.arg("prompt");
        if (server.hasArg("promptParts"))
        {
            if (server.arg("promptParts").toInt() == promptPartsGot)
                prompt = promptBuf;
            else
                saveError += "prompt (upload incomplete) ";
        }
        Serial.printf("[Persona] Save request: prompt %u bytes, free heap %u\n", (unsigned)prompt.length(), (unsigned)ESP.getFreeHeap());
        Preferences prefs;
        prefs.begin("persona", false);
        _putLong(prefs, "name", server.arg("name"));
        if (!server.hasArg("promptParts") || server.arg("promptParts").toInt() == promptPartsGot)
            _savePrompt(prefs, prompt);
        _putLong(prefs, "model", server.arg("model"));
        prefs.putFloat("temp", server.arg("temp").toFloat());
        prefs.putFloat("topP", server.arg("topP").toFloat());
        prefs.putInt("topK", server.arg("topK").toInt());
        prefs.putInt("maxTok", server.arg("maxTok").toInt());
        prefs.putFloat("repeat", server.arg("repeat").toFloat());
        prefs.putInt("voice", server.arg("voice").toInt());
        for (size_t i = 0; i < kNotificationGuidanceCount; i++)
            _putLong(prefs, ("n" + String(i)).c_str(), server.arg("n" + String(i)));
        prefs.end();

        String host = server.arg("backendHost");
        host.trim();
        String path = server.arg("backendPath");
        path.trim();
        if (!path.startsWith("/"))
            path = "/" + path;
        long port = server.arg("backendPort").toInt();
        // An empty host or out-of-range port would leave the device unable to reach any backend, so keep the old address.
        if (host.length() && port > 0 && port <= 65535)
        {
            if (host != backend.host || port != backend.port || path != backend.path)
                backendChanged = true;
            Preferences cfg;
            cfg.begin("device-cfg", false);
            cfg.putString("backendHost", host);
            cfg.putUShort("backendPort", static_cast<uint16_t>(port));
            cfg.putString("backendPath", path);
            cfg.end();
        }
        load();
        changed = true;
        server.sendHeader("Location", saveError.length() ? "/?failed=1" : "/?saved=1");
        server.send(303);
    }

    inline void _handleReset()
    {
        Preferences prefs;
        prefs.begin("persona", false);
        prefs.clear();
        if (_fsReady())
            LittleFS.remove(kPromptFile);
        prefs.end();
        load();
        changed = true;
        server.sendHeader("Location", "/?saved=1");
        server.send(303);
    }

    inline void _handleResetBackend()
    {
        Preferences prefs;
        prefs.begin("device-cfg", false);
        prefs.remove("backendHost");
        prefs.remove("backendPort");
        prefs.remove("backendPath");
        prefs.end();
        load();
        backendChanged = true;
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
        server.on("/part", HTTP_POST, _handlePart);
        server.on("/reset", HTTP_POST, _handleReset);
        server.on("/resetBackend", HTTP_POST, _handleResetBackend);
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
