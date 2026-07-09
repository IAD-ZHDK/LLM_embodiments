#  LLM_Embodiements

This project makes it easy to connect physical devices to a large language model, for prototyping so called "Large Language Objects". The project is essentially a voice assistant optimised for running on a raspberry pi with an attached Arduino. The code has been tested on Linux and Mac OS, and is optimised for Raspbery PI. 

---

## 🚀 Setting Up on a New Raspberry Pi

### 1. **Prepare the SD Card**
- Flash the latest Raspberry Pi OS (Desktop) to your SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
- **Enable SSH in imager**  

### 2. **First Boot**
- Insert the SD card into the Raspberry Pi and power it on.
- Connect via SSH:  
  ```bash
  ssh <username>@<devicename>.local
  ```
 
  
IMPORTANT: Enable Serial Interface

  ```bash
  sudo raspi-config
  ```

In config select "Interfacing Options" > "Serial". 

"Would you like a login shell to be accessible over serial?" > NO
"Would you like the serial port hardware to be enabled?" > Yes



### **Clone the Repository**
```bash
git clone https://github.com/IAD-ZHDK/LLM_Embodiements.git
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

### Model Installation (LLM + STT + TTS)

This project supports local LLMs with Ollama, Vosk for speech-to-text, and Piper for text-to-speech.

#### 1) Install LLM models (Ollama)

Install Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Install at least one model (pick one):

```bash
# Good default for Raspberry Pi 5
ollama pull llama3.2:3b

# DeepSeek-R1-Distill
ollama pull deepseek-r1:1.5b

# Qwen2 family
ollama pull qwen2:7b
ollama pull qwen2.5:3b
```

Set the model in `llmSettings.model` in [config.js](config.js), for example:

```js
llmSettings: {
  provider: "ollama",
  model: "llama3.2:3b",
  url: "http://127.0.0.1:11434/api/chat",
}
```

To switch back to OpenAI, set `provider: "openai"`, a valid OpenAI model, and the OpenAI API URL.

#### 2) Install STT models (Vosk)

The repository already contains multiple Vosk models under `python/STTmodels/`.
If you want to add another one manually:

```bash
cd python/STTmodels
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
rm vosk-model-small-en-us-0.15.zip
```

Set the STT model name in [config.js](config.js) under the active language profile (folder name, not a number).

#### 3) Install TTS models (Piper)

Place both `.onnx` and matching `.onnx.json` files in `python/TTSmodels/`.
Example (English voice):

```bash
cd python/TTSmodels
wget https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/low/en_GB-alan-low.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/low/en_GB-alan-low.onnx.json
```

Set the TTS model file name in [config.js](config.js) under the active language profile.

#### 4) Select language profile

To switch language for STT and TTS together, change `activeLanguage` in [config.js](config.js) and restart:

```js
activeLanguage: "en", // or "de"
speech: {
  sttBackend: "vosk",
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
}
```

Use model names directly (no numeric indexing).

## Manual Setup

### 1. **Install Dependencies**
- Update the system and install Node.js, npm, and Chromium:
  ```bash
  sudo apt update && sudo apt upgrade -y
  curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
  # For Raspberry Pi OS Bookworm (newer)
  sudo apt install -y nodejs chromium git
  # For Raspberry Pi OS Bullseye and earlier
  sudo apt install -y nodejs chromium-browser git
  sudo apt-get install libusb-1.0-0-dev
  sudo apt install portaudio19-dev
  sudo apt install fswebcam
  ```

On macOS:
  brew install nodejs
  brew install libusb
  ```

### 2. **Install Project Dependencies**
```bash
cd LLM_Embodiements
npm install
```

### 3. Create and activate a Python virtual environment and install packages

This project requires Python 3.13.3 (please do not use a newer Python version, until onyxruntime is supported). The instructions below assume the Python 3.13 executable is available as `python3.13`.

```bash
# create venv with Python 3.13.3
python3.13 -m venv python/venv
source python/venv/bin/activate

# use the venv's python to install packages
python -m pip install --upgrade pip wheel setuptools
python -m pip install vosk numpy piper pyusb sounddevice requests
python -m pip install --no-deps -r python/requirements.txt
python -m pip install onnxruntime pyaudio webrtcvad
```

Short notes on obtaining Python 3.13.3:

- Debian/Ubuntu (including Raspberry Pi OS): use the deadsnakes PPA

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
### 4. setup .env file (only for OpenAI provider)

```bash
nano .env
```
and replace the API Key with your own. 
 ```bash
OPENAI_API_KEY='******************************' 
  ```

### 5. **Start the Application**

- Make sure python virtual environment is started:

```bash
  source python/venv/bin/activate
```
- To start both backend and frontend together:
```bash
  npm start
```
or for development:

```bash
  npm run dev
```

- The backend will run on port 3000, and the frontend on port 5173.

### 6. **Set Up Kiosk Mode and autostart**

```bash
chmod +x run.sh
./run.sh
```


### Debuging with terminal 

- Install wscat for terminal websocket connections
```bash
  npm install -g wscat
```
- Open a websocket connection
```bash
  wscat -c ws://localhost:3000
```

- Type a command to pause speech detection, or send text directly to the LLM
```bash
{"command":"protocol"}
{"command":"sendMessage","message":"Hello from the terminal!"}
```

###  AutoStart

Add  /.config/autostart/llm-embodiments.desktop with the following content:

```bash
  [Desktop Entry]
  Type=Application
  Name=LLM_Embodiments
  Comment=Start LLM_Embodiments Kiosk
  Exec=/home/pi/LLM_Embodiments/run.sh
  Path=/home/pi/LLM_Embodiments/
  Icon=utilities-terminal
  Terminal=false
```

### setup wifi WPA2 enterprise
```bash
   sudo nmcli connection add con-name "wlan-ZHDK" type wifi ifname wlan0 ssid "YOUR_SSID" wifi-sec.key-mgmt wpa-eap 802-1x.eap peap 802-1x.phase2-auth mschapv2 802-1x.identity "YOUR_USERNAME" 802-1x.password "YOUR_PASSWORD" ipv4.method auto connection.autoconnect yes
  
   sudo nmcli connection up "wlan-ZHDK"
  
   nmcli connection show
```
###  Todo

- Auto.restart when Arduino disconnected 
- Recent changes to LLM API for images: fix needed
- add physical button to restart whole application 
- BLE integration 
