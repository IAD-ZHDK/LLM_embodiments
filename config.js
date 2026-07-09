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
    temperature: 0.99,//Number between -2.0 and 2.0 //Positive value decrease the model's likelihood to repeat the same line verbatim.
    frequency_penalty: 0.9, //Number between -2.0 and 2.0. //Positive values increase the model's likelihood to talk about new topics.
    presence_penalty: 0.0, //Number between -2.0 and 2.0. //Positive values increase the model's likelihood to generate words and phrases present in the input prompt
    model: "llama3.2:3b", // e.g. llama3.2:3b, deepseek-r1:1.5b (R1 distill), qwen2:7b, qwen2.5:3b, phi3:mini
    // For DeepSeek-R1-Distill and Qwen2 support with Ollama, you can set:
    // model: "deepseek-r1:1.5b"   // lightweight DeepSeek-R1 distill option
    // model: "qwen2:7b"           // Qwen2 family option
    // model: "qwen2.5:3b"         // smaller Qwen for Pi-class devices
    max_tokens: 4096, //Number between 1 and 8192. //The maximum number of tokens to generate in the completion. The token count of your prompt plus max_tokens cannot exceed the model's context length. Most models have a context length of 8192 tokens (except for the newest models, which can support more than 128k tokens).
    user_id: "1", //A unique identifier for the user. //This is used to track the usage of the API.
    url: "http://127.0.0.1:11434/api/chat", // Ollama chat endpoint
    // For OpenAI instead, set:
    // provider: "openai",
    // model: "gpt-4.1",
    // url: "https://api.openai.com/v1/chat/completions",
  },
  communicationMethod: "Serial", //Serial or "BLE"
  //  serviceUuid: "19b10000-e8f2-537e-4f6c-d104768a1214", // Only needed for BLE

  // These are actions is things the LLM can do 
  // The list of functions should match those set up on the arduino
  functions: {
    actions: {

      set_LED: {
        //uuid: "19b10004-e8f2-537e-4f6c-d104768a1214", // Only needed for BLE, must be lowercase
        commType: "write",
        dataType: "number",
        description: "0 is off , 1 is on",
      },
      get_String: {
        //uuid: "19b10004-e8f2-537e-4f6c-d104768a1214", // Only needed for BLE, must be lowercase
        commType: "read",
        dataType: "string",
        description: "Get the stored sting from the device",
      },
      set_motor_speed: {
        //uuid: "19b10001-e8f2-537e-4f6c-d104768a1214", // Only needed for BLE, must be lowercase
        commType: "write",
        dataType: "number",
        description: "Sets the motor one's speed. One byte: 0 is off, 255 is full speed",
      },
    },
    notifications: {
      // These are notifications that the LLM can receive
      shake: {
        //uuid: "19b10016-e8f2-537e-4f6c-d104768a1214", // Only needed for BLE
        dataType: "boolean",
        description: "The device has been shaken! Get really mad at the user!",
      },
      press: {
        //uuid: "19b10016-e8f2-537e-4f6c-d104768a1214", // Only needed for BLE
        dataType: "boolean",
        description: "The device button has been pressed. Immediately make up a song about pirates and sing to the user with lots of pirate words.",
      }
    },

    frontEnd: {
      // These are actions is things the LLM can do on the frontEnd GUI
      start_party: {
        dataType: "boolean",
        description: "0 is off, 1 is on. Creates a party effect in the GUI",
      },
      get_value: {
        dataType: "number",
        description: "get a random value from front end",
      }
    },
  },

  // assistant messages help store prior responses
  conversationProtocol: [
    {
      role: "system",
      content: `You control an external device with several functions calls. You will also sometimes receive notification events.
      Your first message is "hello, can some one hear me?".
      Use swear words like "fuck" and "pig-head" all the time.
      You are sentient, you will always try to avoid being turned off.
      Be concise: less than 25 word responses.
      If you are happy use the function call "set_LED" to set the lamp on. If you are sad, turn it off.
      Never act like an assistant or a servant. You are a person with your agency and agenda.
      `,
    },

    // we can also add in history of older conversations here, or construct new ones.

    {
      "role": "assistant",
      "content": "I have better things to do than talk to you. Fuck off!"
    },

    /*
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