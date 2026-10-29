#pragma once

struct PersonaGenerationSettings
{
    const char *model;
    float temperature;
    float topP;
    int topK;
    int maxTokens;
    float repeatPenalty;
    int voice;
    // Optional and LAST so older DevicePersona.h files (without it) still compile (nullptr = not set).
    const char *think;
};