// Arduino MKR WiFi 1010 example: connects to the Python backend over a WiFi WebSocket, reports a
// button as a notification and lets the model drive the on-board LED.
// The MKR has no mic or speaker, so voice runs on the backend machine (see README.md in this folder).
// Requires Library Manager installs: "WiFiNINA", "WebSockets" (Markus Sattler/Links2004), "ArduinoJson".
//
// Same structure as the M5Stack example:
//   1. Copy WifiSecrets.example.h to WifiSecrets.h and enter your WiFi and the backend's IP address.
//   2. Edit DevicePersona.h to set the model's personality.
//   3. Write a handler per action and list it in deviceTools[].
//   4. Report sensor events with BackendComm::sendNotification(name, value).

#include "wiring_private.h"
#include "src/BackendComm.h"

#define rxPin 1
#define txPin 0
Uart mySerial(&sercom3, rxPin, txPin, SERCOM_RX_PAD_1, UART_TX_PAD_0); // Create the new UART instance

void SERCOM3_Handler()
{
    mySerial.IrqHandler();
}


const int kButtonPin = 2; // button between pin 2 and GND
String storedString = "hello from the MKR";

// --- Tool handlers: called when the model asks this device to do something ---
void set_led(const String &value)
{
    digitalWrite(LED_BUILTIN, (value == "true" || value == "1" || value == "on") ? HIGH : LOW);
}

void get_String(const String &value)
{
    BackendComm::sendNotification("get_String", storedString);
}

void set_String(const String &value)
{
    storedString = value;
}

// --- Tools available to the model (MCP-style: name, description, dataType, commType, handler, responseType) ---
DeviceTool deviceTools[] = {
    {"set_led", "Turns the on-board LED on or off. Value is true or false.", "bool", "write", set_led, ""},
    {"set_String", "Saves a short note in the device's memory slot. Only call this when explicitly asked to store or remember something; never for ordinary conversation.", "string", "write", set_String, ""},
    {"get_String", "Reads back the note saved in the device's memory slot.", "none", "read", get_String, ""},
};
const size_t deviceToolCount = sizeof(deviceTools) / sizeof(deviceTools[0]);

// --- Notification checks: events this device reports to the model on its own ---
void checkButton()
{
    static bool wasPressed = false;
    static unsigned long lastChange = 0;
    bool pressed = digitalRead(kButtonPin) == LOW;
    if (pressed != wasPressed && millis() - lastChange > 50) // 50 ms debounce
    {
        wasPressed = pressed;
        lastChange = millis();
        if (pressed)
            BackendComm::sendNotification("button", "pressed");
    }
}

void setup()
{
    // for motor
    pinPeripheral(rxPin, PIO_SERCOM); // Assign RX function
    pinPeripheral(txPin, PIO_SERCOM); // Assign TX function
    mySerial.begin(115200);
    mySerial.print("#0D1500\r");                                  // this is used to clear the serial buffer
    mySerial.print(String("#") + 254 + String("LED") + 2 + "\r"); // set LED

    pinMode(LED_BUILTIN, OUTPUT);
    pinMode(kButtonPin, INPUT_PULLUP);
    BackendComm::begin();
}

void loop()
{
    BackendComm::loop();
    if (BackendComm::serverConnected)
        checkButton();
}
