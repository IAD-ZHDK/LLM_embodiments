// Add voice option in config

import express from 'express';
import cors from 'cors';
import http from 'http';
import dns from 'dns';
import { WebSocketServer, WebSocket } from 'ws';
import LLMAPI from './Components/LLMAPI.js';
// import config json file
import { loadConfig, loadFromUSB, getUSBDetector } from './Components/configHandler.js';
import SerialCommunication from './Components/SerialCommunication.js';
import ICommunicationMethod from './Components/ICommunicationMethod.js';
import FunctionHandler from './Components/FunctionHandler.js';
//import BLECommunication from './Components/BLECommunication.js';
import SpeechToText from './Components/SpeechToText.js';
import TextToSpeech from './Components/TextToSpeech.js';
import WiFiManager from './Components/WiFiManager.js';
import path from 'path';
import { fileURLToPath } from 'url';
//import USBConfigWatcher from './Components/USBConfigWatcher.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Instance tracking for restarts
let currentInstances = {
  server: null,
  wss: null,
  usbWatcher: null,
  wifiManager: null,
  communicationMethod: null,
  speechToText: null,
  app: null
};
let latestImagePath = null;
let isRestarting = false;
let config = null;
let ttsvolume = 50;

let existingConfig = null; // this is used for comparison on usb config change

const PORT = process.env.PORT || 3000;

function getActiveSpeechSettings(appConfig) {
  const legacyFallback = {
    speechToTextModel: appConfig.speechToTextModel || 'vosk-model-small-en-us-0.15',
    textToSpeechModel: appConfig.textToSpeechModel || 'en_GB-alan-low.onnx',
  };

  if (!appConfig.speech || !appConfig.speech.languageProfiles) {
    return {
      sttBackend: 'vosk',
      ...legacyFallback,
    };
  }

  const activeLanguage = appConfig.activeLanguage || 'en';
  const languageProfile = appConfig.speech.languageProfiles[activeLanguage] || appConfig.speech.languageProfiles.en;

  return {
    sttBackend: appConfig.speech.sttBackend || 'vosk',
    speechToTextModel: languageProfile?.speechToTextModel || legacyFallback.speechToTextModel,
    textToSpeechModel: languageProfile?.textToSpeechModel || legacyFallback.textToSpeechModel,
  };
}

async function main() {
  if (isRestarting) return; // Don't start if we're in the middle of restarting

  try {
    console.log('🚀 Starting LLM Arduino application...');

    // Clear any existing instances
    if (currentInstances.server || currentInstances.wss) {
      await cleanup(false);
    }

    // Create new Express app and HTTP server
    currentInstances.app = express();
    currentInstances.server = http.createServer(currentInstances.app);
    currentInstances.wss = new WebSocketServer({ server: currentInstances.server });

    // 0. Load configuration
    config = await loadConfig(existingConfig);
    console.log('✅ Configuration loaded');
    // 0.1. Initialize USB Config Watcher
    /*
    currentInstances.usbWatcher = getUSBDetector();
    // Start watching for USB config changes

    currentInstances.usbWatcher.start();
    // Handle config changes 
    currentInstances.usbWatcher.on('configFound', async (event) => {
      console.log(`🔄 USB config detected and loaded: ${event.configPath}`);
      let newConfig = await loadFromUSB(event.configPath);
      if (JSON.stringify(newConfig) !== JSON.stringify(config)) {
        console.log('🔃 Config change, reloading config and restarting application with new configuration...');
        existingConfig = newConfig;
        // Restart internally instead of restarting the process
        await cleanup(true);
      } else {
        //attempt to eject the USB drive
        let configPath = event.configPath
        console.log('⚠️  configPath:', configPath);
        //  currentInstances.usbWatcher.ejectUSBDrive(configPath);
      }
    });
*/


    //currentInstances.usbWatcher.eject()

    // 0.2. Initialize WiFi - always attempt to connect. If config.wifi is
    // provided it will be used; otherwise connectFromConfig will try saved
    // encrypted secrets and existing NetworkManager profiles.
    currentInstances.wifiManager = new WiFiManager();
    try {
      if (config.wifi) {
        console.log('📶 WiFi configuration found in config.js, attempting to connect...');
      } else {
        console.log('📶 No WiFi configuration in config.js — attempting saved secrets / existing profiles...');
      }

      const result = await currentInstances.wifiManager.connectFromConfig(config.wifi);
      if (result && result.success) {
        console.log('✅ WiFi connected successfully:', result.message);
        const info = await currentInstances.wifiManager.getConnectionInfo();
        console.log(`📡 Connected to: ${info.ssid}, IP: ${info.ip}`);
      } else {
        console.log('❌ WiFi connection failed:', result ? result.message : 'unknown error');
      }
    } catch (err) {
      console.error('❌ Error while attempting WiFi connection:', err.message || err);
    }

    testNetworkPerformance(config.llmSettings)

    const speechSettings = getActiveSpeechSettings(config);
    console.log('speech settings:', speechSettings);

    // set volume from config
    console.log("setting tts volume to config value:", config.volume);
    ttsvolume = config.volume || 50;

    // 1. Initialize communication method based on config
    console.log('📡 Initializing communication...');
    if (config.communicationMethod == "BLE") {
      console.log("BLE communication not yet implemented");
      currentInstances.communicationMethod = new ICommunicationMethod(comCallback, config);
    } else if (config.communicationMethod == "Serial") {
      currentInstances.communicationMethod = new SerialCommunication(comCallback, config);
    } else {
      currentInstances.communicationMethod = new ICommunicationMethod(comCallback, config);
    }



    // Setup function handler
    const functionHandler = new FunctionHandler(config, currentInstances.communicationMethod);

    // Setup LLM API
    console.log("model config:", config.llmSettings.model);

    let LLM_API = new LLMAPI(config, functionHandler);


    // Define callback functions first
    function comCallback(message) {
      console.log("com callback");
      console.log(message);
      // pass messages directly from the arduino to to LLM API
      LLM_API.send(message, "system").then((response) => {
        console.log("response from LLM API to serial information :", response);
        LLMresponseHandler(response);
      });
    }

    function callBackSpeechToText(msg) {
      let complete = false;
      if (msg.confirmedText) {
        console.log('stt:', msg.confirmedText);
        complete = true;
        msg.speech = msg.confirmedText
        // parse message to LLM API
        LLM_API.send(msg.confirmedText, "user").then((response) => {
          LLMresponseHandler(response);
        });
      } else if (msg.interimResult) {
        console.log('interim stt:', msg.interimResult);
        complete = false;
        msg.speech = msg.interimResult
      } else {
        msg.speech = "";
      }
      try {
        updateFrontend(msg.speech, "user", complete);
      } catch (e) {
        console.error('Error speech to text response', msg, e);
      }
    }


    // 2. Initialize speech to text
    console.log('🎤 Initializing speech to text...');

    if (config.muteMicrophone == true) {
      console.log('🔇 Microphone muted in config, skipping speech to text initialization');
      currentInstances.speechToText = null;
    } else {
      currentInstances.speechToText = new SpeechToText(
        callBackSpeechToText,
        speechSettings.speechToTextModel,
        speechSettings.sttBackend
      );
    }
    // 3. Setup Express middleware
    console.log('📦 Setting up Express middleware...');
    currentInstances.app.use(cors());
    console.log('✅ CORS middleware added');
    currentInstances.app.use(express.json());
    console.log('✅ JSON middleware added');

    // Debug middleware - log all requests
    currentInstances.app.use((req, res, next) => {
      // console.log(`📨 ${req.method} ${req.path}`);
      next();
    });


    currentInstances.app.get('/api/latest-image', (req, res) => {
      //    console.log("✓ GET /api/latest-image - returning:", latestImagePath);
      res.json({ image: latestImagePath });
    });
    currentInstances.app.post('/api/latest-image', (req, res) => {
      latestImagePath = req.body.image;
      //   console.log("📸 Latest image updated:", latestImagePath);
      res.json({ success: true, image: latestImagePath });
    });

    // Static files for scratch_files
    currentInstances.app.use('/scratch_files', express.static('scratch_files'));

    // Explicit 404 for any remaining API calls (not found)
    currentInstances.app.use(express.static('frontend', {
      index: ['index.html'],
      setHeaders: (res, path) => {
        if (!path.includes('.')) {
          g
          // No extension = likely an API route, don't serve index.html
          res.setHeader('Cache-Control', 'no-store');
        }
      }
    }));

    // Explicit 404 for any remaining API calls (not found)
    currentInstances.app.use('/api', (req, res) => {
      console.log("❌ Unhandled API route:", req.path);
      res.status(404).json({ error: 'API endpoint not found', path: req.path });
    });

    // Fallback: serve index.html for SPA routing (LAST)
    currentInstances.app.use((req, res) => {
      res.sendFile(path.join(__dirname, '../frontend/index.html'));
    });


    // 4. Setup WebSocket handling
    currentInstances.wss.on('connection', (ws, req) => {
      const ip = req.socket.remoteAddress;
      if (ip !== '127.0.0.1' && ip !== '::1' && ip !== '::ffff:127.0.0.1') {
        ws.close();
        console.log(`Rejected connection from non-local address: ${ip}`);
        return;
      }
      console.log(`Accepted WebSocket connection from ${ip}`);
      const lastAssistantMessage = config.conversationProtocol
        .filter(msg => msg.role === "assistant")
        .pop();

      // 
      if (lastAssistantMessage) {
        const initialState = {
          backEnd: {
            messageOut: lastAssistantMessage.content,
            messageInComplete: true  // Assume complete since it's history
          }
        };
        ws.send(JSON.stringify(initialState));
      }

      ws.on('message', async (message) => {
        try {

          // Try to parse as JSON, or treat as plain text
          let cmd;
          try {
            cmd = JSON.parse(message);
          } catch {
            cmd = { text: message.toString().trim() };
          }
          console.log('Received command via WebSocket:', cmd);

          if (cmd.command === 'pause' && currentInstances.speechToText) {
            currentInstances.speechToText.pause();
          } else if (cmd.command === 'resume' && currentInstances.speechToText) {
            currentInstances.speechToText.resume();
            ws.send('Sent resume command to Python');
          } else if (cmd.command === 'setVolume') {
            // convert string to number
            ttsvolume = parseInt(cmd.value, 10);
          } else if (cmd.command === 'restart-app') {
            console.log('🔄 Manual restart requested via WebSocket');
            ws.send(JSON.stringify({
              type: 'restart-initiated',
              message: 'Application restarting...'
            }));
            await cleanup(true);
          } else if (cmd.command === 'config-status') {
            ws.send(JSON.stringify({
              type: 'config-status',
              config: config,
              timestamp: new Date().toISOString()
            }));
          } else if (cmd.command === 'wifi-status') {
            // Get WiFi connection status
            const status = await currentInstances.wifiManager.getConnectionStatus();
            const info = await currentInstances.wifiManager.getConnectionInfo();
            ws.send(JSON.stringify({
              command: 'wifi-status',
              status: status,
              info: info
            }));
          } else if (cmd.command === 'wifi-scan') {
            // Scan for available networks  
            const networks = await currentInstances.wifiManager.scanNetworks();
            ws.send(JSON.stringify({
              command: 'wifi-scan',
              networks: networks
            }));
          } else if (cmd.command === 'wifi-connect') {
            // Connect to WiFi with provided credentials
            const result = await currentInstances.wifiManager.connectFromConfig(cmd.wifi);
            ws.send(JSON.stringify({
              command: 'wifi-connect',
              result: result
            }));
          } else if (cmd.text) {
            LLM_API.send(cmd.text, "user").then((response) => {
              LLMresponseHandler(response);
            });
            ws.send('Sent message to LLM API');
          } else if (cmd.command === 'protocol') {
            // Send the conversation protocol to the client
            ws.send(JSON.stringify(config.conversationProtocol))
          } else if (cmd.command === 'reload-config') {
            // Manually trigger config reload
            console.log('🔄 Manual config reload requested via WebSocket');
            await cleanup(true);
          } else {
            // ws.send('Unknown command');
          }
        } catch (err) {
          ws.send('Error handling command: ' + err.message);
        }
      });

      ws.on('close', () => {
        console.log('👋 WebSocket connection closed');
      });
    });

    // 5. Setup helper functions
    function broadcastUpdate(data) {
      // Check if WebSocket server exists and is available
      if (!currentInstances.wss || !currentInstances.wss.clients) {
        console.warn('WebSocket server not available, skipping broadcast');
        return;
      }

      // avoid sending image data around
      try {
        const dataObj = JSON.parse(data);
        if (dataObj.backEnd && dataObj.backEnd.message &&
          typeof dataObj.backEnd.message === 'string' &&
          dataObj.backEnd.message.startsWith('{"Camera Image":')) {
          dataObj.backEnd.message = "image";
          data = JSON.stringify(dataObj);
        }
      } catch (e) {
        // If JSON parsing fails, use original data
      }

      currentInstances.wss.clients.forEach(client => {
        if (client.readyState === WebSocket.OPEN) {
          client.send(data);
        }
      });
    }

    function updateFrontend(message, messageType, complete) {
      // Check if WebSocket server is available before broadcasting
      if (!currentInstances.wss || !currentInstances.wss.clients) {
        console.warn('WebSocket server not available, skipping frontend update');
        return;
      }

      const dataObj = {};
      dataObj.backEnd = {};
      if (typeof message !== 'undefined') dataObj.backEnd.message = message;
      if (typeof messageType !== 'undefined') dataObj.backEnd.messageType = messageType;
      if (typeof complete !== 'undefined') dataObj.backEnd.complete = complete;
      const data = JSON.stringify(dataObj);
      //console.log(data);
      broadcastUpdate(data);
    }

    function frontEndFunction(functionName, args) {
      console.log("frontEndFunction called with functionName:", functionName, "and args:", args);
      const dataObj = {};
      dataObj.backEnd = {};
      if (typeof functionName !== 'undefined') dataObj.backEnd.functionName = functionName;
      if (typeof args !== 'undefined') dataObj.backEnd.args = args;
      const data = JSON.stringify(dataObj);
      broadcastUpdate(data);
    }


    // test the LLM API
    /*
    LLM_API.send("Tell me the time", "user").then((response) => {
      LLMresponseHandler(response);
    })
    */

    function cleanAssistantMessage(rawMessage) {
      if (typeof rawMessage !== 'string') {
        return rawMessage;
      }

      let cleaned = rawMessage.trim();

      // Remove model reasoning blocks if present (DeepSeek-style tags).
      cleaned = cleaned.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();

      // Remove chat template markers seen in some local model outputs.
      cleaned = cleaned
        .replace(/<\|im_start\|>assistant\s*/gi, '')
        .replace(/<\|im_end\|>/gi, '')
        .replace(/<\|assistant\|>\s*/gi, '')
        .replace(/<\|user\|>\s*/gi, '')
        .trim();

      // Some local models prepend role labels; strip repeated prefixes.
      while (/^assistant\b[:\s\n]*/i.test(cleaned)) {
        cleaned = cleaned.replace(/^assistant\b[:\s\n]*/i, '').trim();
      }

      // Remove standalone pseudo tool-call lines such as: (set_LED) # ...
      cleaned = cleaned
        .split('\n')
        .filter(line => !/^\s*\([a-zA-Z_][a-zA-Z0-9_]*\)\s*(#.*)?\s*$/i.test(line))
        .join('\n')
        .trim();

      // Unwrap a fully quoted string response.
      if ((cleaned.startsWith('"') && cleaned.endsWith('"')) ||
        (cleaned.startsWith("'") && cleaned.endsWith("'"))) {
        cleaned = cleaned.slice(1, -1).trim();
      }

      return cleaned;
    }

    function tryParseFunctionReturnValue(value) {
      if (typeof value !== 'string') return null;
      try {
        return JSON.parse(value);
      } catch {
        return null;
      }
    }

    function parseAssistantFallbackToolCommand(message, availableFunctionNames) {
      if (typeof message !== 'string') return null;

      const trimmed = message.trim();
      if (!trimmed || trimmed.includes('\n')) return null;

      let name = null;
      let rawArgs = null;

      // Supports: set_LED(0)
      let match = trimmed.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)$/);
      if (match) {
        name = match[1];
        rawArgs = (match[2] || '').trim();
      } else {
        // Supports: set_LED 0
        match = trimmed.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\s+(.+)$/);
        if (match) {
          name = match[1];
          rawArgs = (match[2] || '').trim();
        }
      }

      if (!name || !availableFunctionNames.has(name)) return null;

      // Optional JSON object payload: set_LED {"value":0}
      if (rawArgs && rawArgs.startsWith('{') && rawArgs.endsWith('}')) {
        try {
          const parsed = JSON.parse(rawArgs);
          if (parsed && typeof parsed === 'object') {
            return { name, args: parsed };
          }
        } catch {
          // Fall through to scalar parsing.
        }
      }

      let value = rawArgs;
      if (/^(true|false)$/i.test(rawArgs)) {
        value = rawArgs.toLowerCase() === 'true';
      } else if (/^-?\d+(\.\d+)?$/.test(rawArgs)) {
        value = Number(rawArgs);
      } else if (
        (rawArgs.startsWith('"') && rawArgs.endsWith('"')) ||
        (rawArgs.startsWith("'") && rawArgs.endsWith("'"))
      ) {
        value = rawArgs.slice(1, -1);
      }

      return { name, args: { value } };
    }

    function tryExecuteAssistantFallbackToolCommand(message) {
      const availableFunctionNames = new Set(
        functionHandler.getAllFunctions().map((fn) => fn.name)
      );

      const parsedCommand = parseAssistantFallbackToolCommand(message, availableFunctionNames);
      if (!parsedCommand) return false;

      console.log('Executing assistant fallback tool command:', parsedCommand);

      const syntheticMessage = {
        function_call: {
          name: parsedCommand.name,
          arguments: JSON.stringify(parsedCommand.args),
        },
      };

      const returnObject = {
        message: null,
        promise: null,
        role: 'assistant',
      };

      functionHandler.handleCall(syntheticMessage, returnObject)
        .then((response) => {
          LLMresponseHandler(response);
        })
        .catch((error) => {
          console.error('Fallback tool command execution failed:', error);
          updateFrontend(`Error executing assistant command: ${error.message}`, 'error');
        });

      return true;
    }

    function LLMresponseHandler(returnObject) {
      console.log("LLM response handler called with returnObject:", returnObject);
      // TODO: add error handling
      // console.log(returnObject);
      if (returnObject.role == "assistant") {
        // convert the returnObject.message to string to avoid the class having access to the returnObject
        let message = cleanAssistantMessage(returnObject.message.toString());

        // Ignore/sanitize assistant echoes of low-level serial ack payloads.
        const assistantPayload = tryParseFunctionReturnValue(message);
        const assistantSerialWrite = assistantPayload && typeof assistantPayload["Writing to Serial"] === 'string'
          ? assistantPayload["Writing to Serial"]
          : null;
        if (assistantSerialWrite) {
          if (assistantSerialWrite.startsWith('Error:')) {
            updateFrontend(assistantSerialWrite, 'error');
          } else {
            updateFrontend(`Executed: ${assistantSerialWrite}`, 'system');
          }
          return;
        }

        // Fallback for models that emit plain-text tool command lines instead of JSON tool calls.
        if (tryExecuteAssistantFallbackToolCommand(message)) {
          return;
        }

        if (!message) {
          console.log("Empty assistant message after normalization; skipping frontend/TTS update.");
          return;
        }

        try {
          updateFrontend(message, "assistant");
          console.log("Text to speech volume: " + ttsvolume);
          textToSpeech.say(message, speechSettings.textToSpeechModel, ttsvolume);
        } catch (error) {
          console.log(error);
          updateFrontend(error, "error");
        }
      } else if (returnObject.role == "function") {
        // call the frontend function with the arguments
        const functionName = returnObject.message;
        const args = returnObject.arguments;
        frontEndFunction(functionName, args);
        updateFrontend(functionName, "system");
      } else if (returnObject.role == "functionReturnValue") {
        const val = returnObject.value;
        const parsedVal = tryParseFunctionReturnValue(val);
        const responseValue = parsedVal && typeof parsedVal.response === 'string'
          ? parsedVal.response
          : null;
        const serialWriteValue = parsedVal && typeof parsedVal["Writing to Serial"] === 'string'
          ? parsedVal["Writing to Serial"]
          : null;

        // Avoid noisy loops from nested payloads and low-level function errors.
        if (typeof val === "string" && val.trim().startsWith('{"response":{')) {
          console.log("Ignored functionReturnValue: nested response payload");
        } else if (serialWriteValue) {
          // Do not send serial write acknowledgements back to the LLM (prevents JSON echo loops).
          if (serialWriteValue.startsWith('Error:')) {
            console.log("Serial write returned error:", serialWriteValue);
            updateFrontend(serialWriteValue, "error");
          } else {
            console.log("Serial write acknowledged:", serialWriteValue);
            updateFrontend(`Executed: ${serialWriteValue}`, "system");
          }
        } else if (responseValue && responseValue.startsWith("Error:")) {
          console.log("Function call returned error; not sending payload back to LLM:", responseValue);
          updateFrontend(responseValue, "error");
        } else {
          console.log("sending value back to LLM:", val);
          LLM_API.send(val, "system").then((response) => {
            LLMresponseHandler(response);
          });
          updateFrontend(val, "system");
        }
      } else if (returnObject.role == "error") {
        updateFrontend(returnObject.message, "error");
      } else if (returnObject.role == "system") {
        // handle notifications from the device   
        updateFrontend(returnObject.message, "system");
      }
      if (returnObject.promise != null) {
        console.log("there is a promise")
        // there is another nested promise 
        // TODO: protect against endless recursion
        returnObject.promise.then((returnObject) => {
          console.log("nested LLM response handler called with returnObject:", returnObject);
          LLMresponseHandler(returnObject)
        })
      } else {
        endExchange()
      }
    }

    function endExchange() {
      // todo: setup timer for continous interaction 
    }

    // 8. Setup Text to Speech
    let textToSpeech = new TextToSpeech(callBackTextToSpeech);


    function callBackTextToSpeech(msg) {
      let data = {
        name: "TTS",
        value: "0"
      }

      if (msg.tts == "started" || msg.tts == "resumed") {
        console.log("⏸️ pausing speech to text");
        // attempt to send message to serial alerting about text to speech starting

        if (config.notifyTTS) {
          data.value = "1"
          currentInstances.communicationMethod.write(data);
        }
        if (currentInstances.speechToText) {
          currentInstances.speechToText.pause();
        }
      } else if (msg.tts == "stopped" || msg.tts == "paused") {
        console.log("🏁 resuming speech to text");
        if (config.notifyTTS) {
          // attempt to send message to serial alerting about text to speech stopping
          data.value = "0"
          currentInstances.communicationMethod.write(data);
        }
        if (currentInstances.speechToText) {
          currentInstances.speechToText.resume();
        }
      }
    }

    // 9. Start the server
    currentInstances.server.listen(PORT, () => {
      console.log(`🌐 Server running on http://localhost:${PORT}`);
      console.log('✅ Application started successfully');
    });

  } catch (error) {
    console.error('❌ incomplete start of application:', error);
    throw error;
  }
}

async function cleanup(restart = false) {
  if (isRestarting && restart) return; // Prevent multiple restarts

  console.log(`🧹 Cleaning up resources... (restart: ${restart})`);

  try {
    // Stop USB watcher
    if (currentInstances.usbWatcher) {
      console.log('🛑 Stopping USB config watcher...');

      // Remove all event listeners to prevent scope issues
      currentInstances.usbWatcher.removeAllListeners();

      // Stop the watcher
      currentInstances.usbWatcher.stop();
      currentInstances.usbWatcher = null;
    }

    // Stop speech to text
    if (currentInstances.speechToText) {
      console.log('🛑 Pausing speech to text...');
      currentInstances.speechToText.pause();
      currentInstances.speechToText = null;
    }

    // Close communication method
    if (currentInstances.communicationMethod) {
      console.log('🛑 Closing communication method...');
      await currentInstances.communicationMethod.close();
      currentInstances.communicationMethod = null;
    }

    // Close WebSocket server first (disconnect all clients)
    if (currentInstances.wss) {
      console.log('🛑 Closing WebSocket server...');

      // Disconnect all clients first
      currentInstances.wss.clients.forEach((ws) => {
        if (ws.readyState === ws.OPEN) {
          ws.close();
        }
      });

      // Close the WebSocket server
      currentInstances.wss.close();
      currentInstances.wss = null;
    }

    // Close HTTP server with better error handling
    if (currentInstances.server) {
      console.log('🛑 Closing HTTP server...');

      // Check if server is actually listening before trying to close
      if (currentInstances.server.listening) {
        await new Promise((resolve) => {
          const timeout = setTimeout(() => {
            console.log('⚠️ HTTP server close timeout, forcing shutdown...');
            if (currentInstances.server && currentInstances.server.destroy) {
              currentInstances.server.destroy();
            }
            resolve();
          }, 3000);

          currentInstances.server.close((err) => {
            clearTimeout(timeout);
            if (err) {
              console.log('⚠️ Error closing HTTP server:', err.message);
            } else {
              console.log('✅ HTTP server closed');
            }
            resolve();
          });
        });
      } else {
        console.log('ℹ️ HTTP server was not running');
      }

      currentInstances.server = null;
    }

    console.log('✅ Cleanup completed');

    if (restart) {
      isRestarting = true;
      console.log('🔄 Restarting application...');
      setTimeout(async () => {
        try {
          isRestarting = false;
          await main();
          console.log('✅ Application restarted successfully');
        } catch (error) {
          console.error('❌ Failed to restart application:', error);
          isRestarting = false;
        }
      }, 1000);
    }

  } catch (error) {
    console.error('❌ Error during cleanup:', error);
    if (!restart) {
      process.exit(1);
    } else {
      // Even if cleanup fails, try to restart
      isRestarting = true;
      setTimeout(async () => {
        try {
          await main();
          isRestarting = false;
          console.log('✅ Application restarted successfully after cleanup error');
        } catch (error) {
          console.error('❌ Failed to restart application after cleanup error:', error);
          isRestarting = false;
        }
      }, 2000);
    }
  }
}

// Start the application
main().catch((error) => {
  console.error('💥 Fatal error during startup:', error);
  process.exit(1);
});

// Signal handlers for graceful shutdown only (no restart)
process.on('SIGINT', async () => {
  console.log('\n🛑 Received SIGINT (Ctrl+C)...');
  await cleanup(false);
  process.exit(0);
});

process.on('SIGTERM', async () => {
  console.log('\n🛑 Received SIGTERM...');
  await cleanup(false);
  process.exit(0);
});

// Handle uncaught exceptions
process.on('uncaughtException', async (error) => {
  console.error('💥 Uncaught Exception:', error);
  await cleanup(false);
  process.exit(1);
});

process.on('unhandledRejection', async (reason, promise) => {
  console.error('💥 Unhandled Rejection at:', promise, 'reason:', reason);
  await cleanup(false);
  process.exit(1);
});


async function testNetworkPerformance(llmSettings = {}) {
  console.log('🔍 Testing network performance...');

  try {
    const provider = (llmSettings.provider || 'openai').toLowerCase();
    const endpoint = llmSettings.url || (provider === 'openai'
      ? 'https://api.openai.com/v1/chat/completions'
      : 'http://127.0.0.1:11434/api/chat');

    const endpointUrl = new URL(endpoint);
    const endpointHost = endpointUrl.hostname;

    // Test DNS resolution speed
    const dnsStart = Date.now();
    await dns.promises.lookup(endpointHost);
    const dnsTime = Date.now() - dnsStart;
    console.log(`DNS resolution: ${dnsTime}ms`);

    // Test HTTP request speed to a fast endpoint
    const httpStart = Date.now();
    const response = await fetch('https://httpbin.org/ip', {
      signal: AbortSignal.timeout(5000), // Use AbortSignal instead of timeout
      headers: { 'User-Agent': 'RaspberryPi-Test' }
    });
    await response.text();
    const httpTime = Date.now() - httpStart;
    console.log(`HTTP request: ${httpTime}ms`);

    // Test to configured provider endpoint
    const providerStart = Date.now();
    try {
      await fetch(endpoint, {
        signal: AbortSignal.timeout(10000),
        headers: { 'User-Agent': 'RaspberryPi-Test' }
      });
    } catch (e) {
      // Even if endpoint returns an error code, this timing still helps diagnose connectivity.
    }
    const providerTime = Date.now() - providerStart;
    console.log(`Provider endpoint (${provider}): ${providerTime}ms`);

    return {
      dns: dnsTime,
      http: httpTime,
      provider: providerTime
    };

  } catch (error) {
    console.error('Network test failed:', error.message);
    return null;
  }
}

