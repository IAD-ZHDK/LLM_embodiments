#pragma once

struct PersonaGenerationSettings
{
    const char *model;
    float temperature;
    float topP;
    int topK;
    int maxTokens;
    float repeatPenalty;
};