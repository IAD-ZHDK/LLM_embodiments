#  LLM_Embodiements

This project makes it easy to connect physical devices to a large language model, for prototyping so called "Large Language Objects". The project is essentially a voice assistant optimised for running on a pc or server, with an arduino or similar device connecting via WiFi. The code has been tested on Linux and macOS. 

---

### **Clone the Repository**
```bash
git clone https://github.com/IAD-ZHDK/LLM_embodiments.git
cd LLM_Embodiements
```   

### **Get latest version after installing**

Navigate to the path of the project and run this line
```bash
git pull
```  

## Quick start

You can attempt to do the setup with the setup shell script. If this fails, then attempt the manuel process 

```bash
chmod +x setup.sh
./setup.sh
```

If the setup is successful, you can run: 

```bash
chmod +x run.sh
./run.sh
```

### Windows

Open PowerShell in the project folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

The setup script creates `backend\venv`, installs `backend\requirements.txt`, and checks for Ollama. Install Ollama separately from https://ollama.com/download/windows, then pull the configured model, for example `ollama pull qwen3:14b`.

### Model Installation (LLM + STT + TTS)

This project supports local LLMs with Ollama, Vosk for speech-to-text, and Piper for text-to-speech.

#### 1) Install LLM models (Ollama)

Install Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Install at least one model (pick one):

```bash
ollama pull llama3.2:3b

# DeepSeek-R1-Distill
ollama pull deepseek-r1:1.5b

# Qwen2 family
ollama pull qwen2:7b
ollama pull qwen2.5:3b
ollama pull qwen3:14b

# Lightweight tool-calling model 
ollama pull hf.co/LiquidAI/LFM2-1.2B-Tool-GGUF:Q4_K_M
```

Set the model in `llmSettings.model` i n [config.toml](config.toml), for example:

```toml
[llmSettings]
provider = "ollama"
model = "hf.co/LiquidAI/LFM2-1.2B-Tool-GGUF:Q4_K_M"
url = "http://127.0.0.1:11434/api/chat"
```

Tool-calling compatibility note:
- The LiquidAI model above was verified against `/api/chat` with `tools` enabled.
- It returns Ollama-native `message.tool_calls` entries (for example `set_LED` with `arguments.value = 1`).
- This matches the current Python backend parser in `backend/llm_api.py`, so no adapter is needed.

Suggested local models:
- Raspberry Pi-class devices: `hf.co/LiquidAI/LFM2-1.2B-Tool-GGUF:Q4_K_M` for lightweight tool calling.
- Apple Silicon or PCs with 16 GB RAM: `qwen3:8b` for balanced conversation and tool calling; `lfm2.5:8b` for faster intent classification.
- Apple Silicon or PCs with 32 GB RAM: `qwen3:14b` for stronger conversation and reliable tool calls.
- Apple Silicon or PCs with 32 GB+ RAM, where response speed is less important: `qwen3.8:27b` for the strongest local conversations.

To switch back to OpenAI, set `provider: "openai"`, a valid OpenAI model, and the OpenAI API URL.

#### 2) Install STT models (Vosk or Whisper)

Two STT backends are supported, set via `speech.sttBackend` in [config.toml](config.toml):

- `"vosk"` (default) - lightweight, CPU-only, streams partial results as you speak.
- `"whisper"` - [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2). More accurate, supports
  CUDA GPUs, and is the better choice if you plan to run on multiple graphics cards or (in a future version) run
  several simultaneous transcription sessions in parallel, since each session loads its own model instance and can
  be pinned to its own GPU via `speech.whisper.deviceIndex`. It has no streaming partials: it transcribes each
  utterance shortly after you stop speaking, using the same voice-activity detection as the Vosk path.

Install whichever backend(s) you plan to use:

```bash
# Vosk (already a default dependency)
python -m pip install vosk

# Whisper
python -m pip install faster-whisper
```

The repository already contains multiple Vosk models under `backend/STTmodels/`.
If you want to add another one manually:

```bash
cd backend/STTmodels
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
rm vosk-model-small-en-us-0.15.zip
```

Whisper models are downloaded automatically by faster-whisper on first use (no manual step needed). Suggested
models, set as `speechToTextModel` in [config.toml](config.toml):

- English only, fastest/smallest: `tiny.en`, `base.en`, `small.en`, `medium.en`
- Multilingual (needed for German, or English + German with a single model): `tiny`, `base`, `small`, `medium`, `large-v3`
- Raspberry Pi / CPU-only: `tiny` or `tiny.en`
- Single consumer GPU (~6-8 GB VRAM): `small` or `medium`
- Higher-end GPU (~10 GB+ VRAM): `large-v3` for the best accuracy

Set the STT model name in [config.toml](config.toml) under the active language profile (folder name for Vosk, model name for Whisper).

#### 3) Install TTS models (Piper)

Place both `.onnx` and matching `.onnx.json` files in `backend/TTSmodels/`.
Example (English voice):

```bash
cd backend/TTSmodels
wget https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/low/en_GB-alan-low.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/low/en_GB-alan-low.onnx.json
```

Set the TTS model file name in [config.toml](config.toml) under the active language profile.

#### 4) Select language profile

To switch language for STT and TTS together, change `activeLanguage` in [config.toml](config.toml) and restart:

```toml
activeLanguage = "en"  # or "de"

[speech]
sttBackend = "vosk"  # or "whisper"

[speech.languageProfiles.en]
speechToTextModel = "vosk-model-small-en-us-0.15"
textToSpeechModel = "en_GB-alan-low.onnx"

[speech.languageProfiles.de]
speechToTextModel = "vosk-model-small-de-0.15"
textToSpeechModel = "de_DE-thorsten-medium.onnx"
```

Use model names directly (no numeric indexing).

## Manual Setup

### 1. **Install Dependencies**
- Update the system and install Python and system libraries:
  ```bash
  sudo apt update && sudo apt upgrade -y
  sudo apt-get install libusb-1.0-0-dev
  sudo apt install portaudio19-dev
  sudo apt install fswebcam
  ```

On macOS:

```bash
brew install git
brew install libusb
```

### 2. Create and activate a Python virtual environment and install packages

This project requires Python 3.13.3 (please do not use a newer Python version, until onyxruntime is supported). The instructions below assume the Python 3.13 executable is available as `python3.13`.

```bash
# create venv with Python 3.13.3
python3.13 -m venv backend/venv
source backend/venv/bin/activate

# use the venv's python to install packages
python -m pip install --upgrade pip wheel setuptools
python -m pip install vosk numpy piper pyusb sounddevice requests
python -m pip install --no-deps -r backend/requirements.txt
python -m pip install onnxruntime pyaudio webrtcvad
```

Short notes on obtaining Python 3.13.3:

- Debian/Ubuntu: use the deadsnakes PPA

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3.13-dev
```

- macOS (Homebrew):

```bash
brew update
brew install python@3.13
brew link --overwrite --force python@3.13
```

- Windows: download and install Python 3.13.3 from the official Python website and check "Add Python to PATH" during installation:

https://www.python.org/downloads/release/python-3133/

After installation verify the binary:

```bash
python3.13 --version
# expected: Python 3.13.3
```
### 3. setup .env file (only for OpenAI provider)

```bash
nano .env
```
and replace the API Key with your own. 
 ```bash
OPENAI_API_KEY='******************************' 
  ```

### 4. **Start the Application**

- Make sure python virtual environment is started:

```bash
  source backend/venv/bin/activate
```
- Start backend (API on port 3000):
```bash
  python3 -m backend.server
```

### Python Backend

Run backend directly:

```bash
python3 -m backend.server
```

Notes:
- `run.sh` activates `backend/venv` when present and also runs this for you.
- It also clears port 3000 before startup to prevent `address already in use` errors.

Current Python backend scope:
- Local/Web API LLM calls (Ollama/OpenAI) using `llmSettings`
- STT/TTS worker orchestration via existing Python scripts
- Serial/BLE/WiFi communication with the same function-call flow

### 5. **Run**

```bash
chmod +x run.sh
./run.sh
```

### Debugging with terminal 

- Open a websocket connection
```bash
  wscat -c ws://localhost:3000
```

- Type a command to pause speech detection, or send text directly to the LLM
```bash
{"command":"protocol"}
{"command":"sendMessage","message":"Hello from the terminal!"}
```

###  Todo

- Auto.restart when Arduino disconnected 
- Recent changes to LLM API for images: fix needed
- add physical button to restart whole application 
- BLE integration 


