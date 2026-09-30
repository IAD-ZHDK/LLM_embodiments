#pragma once

#include <Arduino.h>

// One entry per function the model can call on this device — MCP-style: a name, a natural
// language description of what it does (shown to the model so it knows when/how to call it),
// a dataType ("bool" | "int" | "float" | "string" | "none"), a communication type
// ("write" for commands and "read" for queries), and the handler function to run.
struct DeviceTool
{
    const char *name;
    const char *description;
    const char *dataType;
    const char *commType;
    void (*handler)(const String &value);
};

// Define the actual table in the main .ino (it references your own handler functions).
extern DeviceTool deviceTools[];
extern const size_t deviceToolCount;
