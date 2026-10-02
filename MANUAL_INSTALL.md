# Manual Installation

These steps are for installing dependencies without the setup script. The scripts prefer Python 3.13 when available; use that version where possible. The Python environment must be created from the same interpreter used to install the packages.

## System dependencies

On Debian or Ubuntu:

```bash
sudo apt update
sudo apt install -y git libusb-1.0-0-dev build-essential python3-venv python3-dev libffi-dev portaudio19-dev
```

The setup script installs Python 3.13 if it is not available from the system packages. On Ubuntu, the equivalent manual steps use the deadsnakes PPA:

```bash
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3.13-dev
```

On other distributions, install Python 3.13 from a trusted package source for that distribution.

On macOS with Homebrew:

```bash
brew install git libusb portaudio python@3.13
brew link --overwrite python@3.13
```

## Python environment

From the repository root, create and activate the environment, then install the complete requirements file:

```bash
python3.13 -m venv backend/venv
source backend/venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r backend/requirements.txt
```

If your Python 3.13 executable has a different name, substitute it in the `venv` command. On Windows PowerShell, use:

```powershell
py -3.13 -m venv backend\venv
.\backend\venv\Scripts\python.exe -m pip install --upgrade pip wheel setuptools
.\backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

## Models and provider

The default LLM provider is Ollama. Install Ollama separately, then fetch the model selected in [config.toml](config.toml):

```bash
ollama pull gemma4:e4b
```

The model can call `take_picture` to capture a still from the backend computer's camera; change `[camera].deviceIndex` in `config.toml` if needed. The M5Stack CoreS3 example also exposes its built-in camera through the same tool. Its firmware requires M5Stack Board Manager 3.2.2 or newer and M5Unified 0.2.11 or newer. Captured photos appear in the device tab's **Latest photo** section in the terminal UI. The configured model must support image input; if it rejects the photo, the backend reports that instead of silently failing.

The default speech-to-text backend is Whisper with the `small.en` model. `faster-whisper` downloads that model on first use. To use Vosk instead, change `speech.sttBackend` in `config.toml` and set `speech.speechToTextModel` to a model-folder name under `backend/STTmodels/`.

Text-to-speech uses the Piper voice named by `speech.textToSpeechModel`. The current config expects `en_GB-northern_english_male-medium.onnx`. Put that `.onnx` file and its matching `.onnx.json` file in `backend/TTSmodels/`; if you choose another voice from the [Piper voices collection](https://huggingface.co/rhasspy/piper-voices), update the config value too.

For OpenAI, set `llmSettings.provider`, `llmSettings.model`, and `llmSettings.url` in `config.toml`, and set `OPENAI_API_KEY` in a `.env` file at the repository root.

## Run

On macOS or Linux, activate the environment and start the backend:

```bash
source backend/venv/bin/activate
./run.sh
```

On Windows, run `powershell -ExecutionPolicy Bypass -File .\run.ps1` from PowerShell. The backend listens on port 3000.