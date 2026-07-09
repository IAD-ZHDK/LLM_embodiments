import fetch from 'node-fetch';
import 'dotenv/config';
import fs from 'fs';
import path from 'path';

/**
 * LLMAPI class
 * Handles communication with OpenAI or a local Ollama server.
 */
class LLMAPI {
  constructor(config, functionHandler) {
    this.config = config;
    this.functionHandler = functionHandler;

    const settings = config.llmSettings || {};
    this.provider = (settings.provider || 'openai').toLowerCase();

    this.Url = settings.url || this.getDefaultUrl(this.provider);
    this.Model = settings.model;
    this.MaxTokens = settings.max_tokens;
    this.UserId = settings.user_id;
    this.apiKey = this.provider === 'openai' ? this.getApiKey(this.config) : null;
  }

  getDefaultUrl(provider) {
    if (provider === 'ollama' || provider === 'local') {
      return 'http://127.0.0.1:11434/api/chat';
    }
    return 'https://api.openai.com/v1/chat/completions';
  }

  // Function to get API key from either .env or config.js
  getApiKey(config) {
    try {
      if (config.OPENAI_API_KEY) {
        console.log("Using API key from config.js");
        // Write to .env if not already present or if different
        const envPath = path.resolve(process.cwd(), '.env');
        let shouldWrite = true;
        if (fs.existsSync(envPath)) {
          const envContent = fs.readFileSync(envPath, 'utf-8');
          if (envContent.includes(`OPENAI_API_KEY='${config.OPENAI_API_KEY}'`)) {
            shouldWrite = false;
          }
        }
        if (shouldWrite) {
          fs.writeFileSync(envPath, `OPENAI_API_KEY='${config.OPENAI_API_KEY}'\n`, { flag: 'w' });
          console.log(".env file created/updated with OpenAI API key.");
        }
        return config.OPENAI_API_KEY;
      } else {
        if (process.env.OPENAI_API_KEY) {
          console.log("Using API key from .env file");
          return process.env.OPENAI_API_KEY;
        }
      }
    } catch (err) {
      console.error("Error reading config file or writing .env:", err);
    }

    // If not found anywhere, throw error
    throw new Error("OpenAI API key not found. Please provide it in .env file or config.js");
  }


  /**
   * Get the current model name
   */
  getModel() {
    return this.Model;
  }

  normalizeRoleForOllama(role) {
    if (role === 'function') return 'tool';
    if (role === 'notification') return 'system';
    if (role === 'assistant' || role === 'user' || role === 'system' || role === 'tool') {
      return role;
    }
    return 'user';
  }

  buildMessages(sQuestion, role, functionName, isImageData, imageData) {
    if (isImageData && imageData) {
      let base64Data = imageData;
      if (imageData.startsWith('data:image/')) {
        base64Data = imageData.replace(/^data:image\/\w+;base64,/, '');
      }

      if (!base64Data || base64Data.length < 100) {
        throw new Error('Invalid or empty image data');
      }

      if (this.provider === 'openai') {
        return [
          ...this.config.conversationProtocol,
          {
            role,
            content: [
              {
                type: 'image_url',
                image_url: {
                  url: `data:image/jpeg;base64,${base64Data}`,
                },
              },
            ],
          },
        ];
      }

      return [
        ...this.config.conversationProtocol,
        {
          role: this.normalizeRoleForOllama(role),
          content: 'Please describe what you see in this image.',
          images: [base64Data],
        },
      ];
    }

    const messages = [...this.config.conversationProtocol];
    const message = functionName
      ? { role, name: functionName, content: sQuestion }
      : { role, content: sQuestion };

    messages.push(message);
    this.config.conversationProtocol.push(message);

    return messages;
  }

  buildOpenAIRequest(messages, isImageData) {
    const data = {
      model: this.Model,
      user: this.UserId,
      messages,
    };

    if (typeof this.Model === 'string' && this.Model.startsWith('gpt-5')) {
      data.max_completion_tokens = this.MaxTokens;
    } else {
      data.max_tokens = this.MaxTokens;
      data.temperature = this.config.llmSettings.temperature;
      data.frequency_penalty = this.config.llmSettings.frequency_penalty;
      data.presence_penalty = this.config.llmSettings.presence_penalty;
      data.stop = ['#', 'ƒ'];
    }

    if (!isImageData) {
      data.functions = this.functionHandler.getAllFunctions();
    }

    return data;
  }

  buildOllamaRequest(messages, isImageData) {
    const data = {
      model: this.Model,
      stream: false,
      messages: messages.map((msg) => ({
        ...msg,
        role: this.normalizeRoleForOllama(msg.role),
      })),
      options: {
        temperature: this.config.llmSettings.temperature,
        num_predict: this.MaxTokens,
      },
    };

    if (!isImageData) {
      data.tools = this.functionHandler.getAllFunctions().map((fn) => ({
        type: 'function',
        function: {
          name: fn.name,
          description: fn.description,
          parameters: fn.parameters,
        },
      }));
    }

    return data;
  }

  async handleFunctionCall(message, returnObject, resolve) {
    const result = await this.functionHandler.handleCall(message, returnObject);

    if (typeof result.value === 'string' && result.value.startsWith('{"Camera Image":')) {
      console.log('result from function call:', result.message);
    } else {
      console.log('result from function call:', result);
    }

    this.config.conversationProtocol.push({
      role: 'function',
      name: message.function_call.name,
      content: message.function_call.arguments,
    });

    if (result.description == 'response') {
      console.log('sending back to llm', result.value);
      resolve(this.send(result.description, 'function', result.value));
      return;
    }

    console.log('resolving function call result:', result);
    resolve(result);
  }

  extractAssistantMessage(providerResponse) {
    if (providerResponse?.choices?.[0]?.text) {
      return providerResponse.choices[0].text;
    }

    if (providerResponse?.choices?.[0]?.message?.content) {
      return providerResponse.choices[0].message.content;
    }

    if (providerResponse?.message?.content) {
      return providerResponse.message.content;
    }

    return '';
  }

  /**
   * Send a message to the configured provider and handle the response.
   * Optionally, handle function calls.
   */
  async send(sQuestion, role, functionName) {
    let timeStampMillis = Date.now();

    let isImageData = false;
    let imageData = null;

    if (typeof sQuestion === 'string' && sQuestion.startsWith('{"Camera Image":')) {
      console.log("📸 detected camera image data, parsing...");
      try {
        const parsedInput = JSON.parse(sQuestion);
        imageData = parsedInput["Camera Image"];
        isImageData = true;
        console.log('📸 sending image to llm');
      } catch (e) {
        console.error('Error parsing camera image data:', e);
        isImageData = false;
      }
    } else {
      console.log('send to llm:' + role + ' ' + sQuestion + ' function:' + functionName);
    }

    return new Promise((resolve) => {
      (async () => {
        let messages;
        let returnObject = {
          message: null,
          promise: null,
          role: 'assistant',
        };

        if (!sQuestion) {
          console.log('message content is empty!');
          return resolve(returnObject);
        }

        try {
          messages = this.buildMessages(sQuestion, role, functionName, isImageData, imageData);

          let data;
          let headers = {
            Accept: 'application/json',
            'Content-Type': 'application/json',
          };

          if (this.provider === 'ollama' || this.provider === 'local') {
            data = this.buildOllamaRequest(messages, isImageData);
          } else {
            data = this.buildOpenAIRequest(messages, isImageData);
            headers.Authorization = `Bearer ${this.apiKey}`;
          }

          console.log(`Send request to ${this.provider} provider`);
          const response = await fetch(this.Url, {
            method: 'POST',
            headers,
            body: JSON.stringify(data),
          });

          const duration = Date.now() - timeStampMillis;
          const providerResponse = await response.json();
          console.log(`✅ ${this.provider} response received in ${duration}ms`);
          console.log('Provider response:', providerResponse);

          if (providerResponse.error && providerResponse.error.message) {
            throw new Error(providerResponse.error.message);
          }

          const openAIFunctionCall = providerResponse?.choices?.[0]?.finish_reason === 'function_call'
            ? providerResponse?.choices?.[0]?.message
            : null;

          const ollamaToolCall = providerResponse?.message?.tool_calls?.[0];

          if (!isImageData && openAIFunctionCall?.function_call) {
            await this.handleFunctionCall(openAIFunctionCall, returnObject, resolve);
            return;
          }

          if (!isImageData && ollamaToolCall?.function?.name) {
            const functionArguments = typeof ollamaToolCall.function.arguments === 'string'
              ? ollamaToolCall.function.arguments
              : JSON.stringify(ollamaToolCall.function.arguments || {});

            const toolCallAsOpenAIMessage = {
              function_call: {
                name: ollamaToolCall.function.name,
                arguments: functionArguments,
              },
            };

            await this.handleFunctionCall(toolCallAsOpenAIMessage, returnObject, resolve);
            return;
          }

          console.log(isImageData ? 'image response' : 'normal response');
          const currentTimeMillis = Date.now();
          console.log('response time (ms):', currentTimeMillis - timeStampMillis);

          let sMessage = this.extractAssistantMessage(providerResponse);
          if (!sMessage) {
            console.log('no response from provider');
            sMessage = 'No response';
          }

          returnObject.message = sMessage;
          this.config.conversationProtocol.push({
            role: 'assistant',
            content: sMessage,
          });
          resolve(returnObject);
        } catch (e) {
          returnObject.message = `Error fetching ${this.Url}: ${e.message}`;
          returnObject.role = 'error';
          resolve(returnObject);
        }
      })();
    });
  }
}


export default LLMAPI;