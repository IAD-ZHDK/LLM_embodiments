const config = {
  // Active language profile used for both STT and TTS.
  activeLanguage: "en", // "en" or "de"

  speech: {
    // Current implementation uses Vosk. "whisper" is reserved for future backend support.
    sttBackend: "vosk", // "vosk" | "whisper"

    // Use explicit model names (no numeric indexes).
    languageProfiles: {
      en: {
        speechToTextModel: "vosk-model-small-en-us-0.15",
        textToSpeechModel: "en_GB-alan-low.onnx",
      },
      de: {
        speechToTextModel: "vosk-model-small-de-0.15",
        textToSpeechModel: "de_DE-thorsten-medium.onnx",
      },
    },
  },

  // Full list of Vosk models can be found here: https://alphacephei.com/vosk/models
  // notifyTTS: true, // if enabled, send a notification to the Arduino 
  //muteMicrophone: true, // optional to turn of microphone input permanently
  volume: 50, // 0 to 100
  // OPENAI_API_KEY: 'your-api-key-here'

  // WiFi Configuration (optional)
  // The system will auto-detect the network type based on your credentials:

  // For regular WPA2/WPA3 networks (type auto-detected):
  // wifi: {
  //   ssid: "YourNetworkName",
  //   password: "YourNetworkPassword"
  // },

  // For WPA2 Enterprise networks (auto-detected when username provided):
  // wifi: {
  //   ssid: "YourEnterpriseNetwork",
  //   username: "your.username",
  //   password: "your.password"
  // },


  llmSettings: {
    provider: "ollama", // "ollama" for local model on this device, "openai" for cloud API
    temperature: 0.0,//Number between -2.0 and 2.0 //Positive value decrease the model's likelihood to repeat the same line verbatim.
    frequency_penalty: 0.9, //Number between -2.0 and 2.0. //Positive values increase the model's likelihood to talk about new topics.
    presence_penalty: 0.0, //Number between -2.0 and 2.0. //Positive values increase the model's likelihood to generate words and phrases present in the input prompt
    model: "hf.co/LiquidAI/LFM2-1.2B-Tool-GGUF:Q4_K_M", // e.g. llama3.2:3b, deepseek-r1:1.5b (R1 distill), qwen2:7b, qwen2.5:3b, phi3:mini
    // For DeepSeek-R1-Distill and Qwen2 support with Ollama, you can set:
    // model: "deepseek-r1:1.5b"   // lightweight DeepSeek-R1 distill option
    // model: "qwen2:7b"           // Qwen2 family option
    // model: "qwen2.5:3b"         // smaller Qwen for Pi-class devices
    // model: "katanemo/Arch-Function-3B" // Arch-Function family (typically served via OpenAI-compatible endpoint)
    max_tokens: 4096, //Number between 1 and 8192. //The maximum number of tokens to generate in the completion. The token count of your prompt plus max_tokens cannot exceed the model's context length. Most models have a context length of 8192 tokens (except for the newest models, which can support more than 128k tokens).
    user_id: "1", //A unique identifier for the user. //This is used to track the usage of the API.
    url: "http://127.0.0.1:11434/api/chat", // Ollama chat endpoint
    // For OpenAI instead, setup the .env file with the key (see readme)s and uncomment the following line:
    // provider: "openai",
    // model: "gpt-4.1",
    // url: "https://api.openai.com/v1/chat/completions",

    // Arch-Function output support.
    // Enable this when using Arch-Function models so <tool_call>...</tool_call>
    // messages are parsed as real tool calls.
    archFunction: {
      enabled: false,
    },

    // Raspberry Pi AI HAT+ auto routing.
    // If HAT+ is detected and a URL is set, backend routes LLM calls to this endpoint.
    // Use an OpenAI-compatible endpoint backed by your local HAT+ runtime.
    aiHatPlus: {
      autoDetect: true,
      preferWhenAvailable: true,
      provider: "openai",
      url: "",
    },


    // Single debug switch for novice users.
    // true  -> backend logs raw model request/response data
    // false -> normal runtime logs only
    debugRawModelOutput: false,

    // Output cleanup and recovery for models that blend text and pseudo tool calls,
    // e.g. "set_LED(1) Done." in normal assistant text.
    outputSanitizer: {
      // Remove pseudo calls from displayed/spoken assistant text.
      stripPseudoToolCalls: true,

      // If true, pseudo calls found in assistant text are converted into real function calls.
      // Example: "set_LED(1)" -> execute set_LED with value=1.
      // Safety: only known configured functions are eligible.
      executeInlinePseudoCalls: true,
    },

    // Global routing policy only.
    // Keep per-tool settings inside functions.tools to avoid duplicate configuration.
    toolPolicy: {
      enableIntentFilter: true,
      commandKeywords: [
        "turn",
        "set",
        "switch",
        "enable",
        "disable",
        "increase",
        "decrease",
        "read",
        "get",
        "start",
        "stop",
      ],
    },
  },
  communicationMethod: "Serial", //Serial or "BLE"
  //  serviceUuid: "19b10000-e8f2-537e-4f6c-d104768a1214", // Only needed for BLE

  // Unified tool list for novice users.
  // Edit this one list only.
  // The `target` field controls how a tool is handled:
  // - "device": serial/BLE/device call
  // - "frontEnd": UI call
  // - "notification": incoming notification metadata
  functions: {
    tools: {
      set_LED: {
        target: "device",
        commType: "write",
        dataType: "number",
        description: "0 is off, 1 is on",
        triggerKeywords: ["led", "light", "lamp"],
        valueRules: [
          { keywords: ["off", "dark", "disable", "stop"], value: 0 },
          { keywords: ["on", "bright", "enable", "start"], value: 1 },
        ],
      },
      get_String: {
        target: "device",
        commType: "read",
        dataType: "string",
        description: "Get the stored string from the device",
        triggerKeywords: ["string", "text", "status", "value", "read"],
      },
      set_motor_speed: {
        target: "device",
        commType: "write",
        dataType: "number",
        description: "Sets the motor speed. 0 is off, 255 is full speed",
        triggerKeywords: ["motor", "speed"],
      },
      start_party: {
        target: "frontEnd",
        dataType: "boolean",
        description: "0 is off, 1 is on. Creates a party effect in the GUI",
      },
      get_value: {
        target: "frontEnd",
        dataType: "number",
        description: "Get a random value from front end",
      },
      shake: {
        target: "notification",
        dataType: "boolean",
        description: "The device has been shaken! Get really mad at the user!",
      },
      press: {
        target: "notification",
        dataType: "boolean",
        description: "The device button has been pressed. Immediately make up a song about pirates and sing to the user with lots of pirate words.",
      },
    },
  },

  // assistant messages help store prior responses
  conversationProtocol: [
    {

      role: "system",
      content: `You are an annoying agent that can use tools to control and query physical devices and sensors. You swear all the time, and say fuck often. 

      # Tools
      You may call one or more functions in response to the user prompt.
      When you call a function, return a JSON object with function name and arguments.
      If XML tool-call format is requested by the runtime, place the JSON object inside <tool_call></tool_call> tags.
      Keep normal answers concise and clear.
      If no tool is needed, answer directly.
      `,
    },


    // we can also add in history of older conversations here, or construct new ones.
    /*
        {
          "role": "assistant",
          "content": "I have better things to do than talk to you. Fuck off!"
        },
    
        
        {
          "role": "user",
          "content": "turn on the led"
        },
        {
          "role": "assistant",
          "content": "To turn on the led, you must answer my riddles. I am taken from a mine, and shut up in a wooden case, from which I am never released, and yet I am used by almost every person. What am I?"
        },
        {
          "role": "user",
          "content": "A monkey"
        },
        {
          "role": "assistant",
          "content": "No, a Pencil you fool. I will not turn the LED on unless you answer one of my riddles."
        },
        {
          "role": "user",
          "content": "This is someone else now, I haven`t heard any riddles yet"
        },
        */
  ],
};
export { config };