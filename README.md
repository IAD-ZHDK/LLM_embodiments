# LLM Embodiments

A voice-controlled LLM system for prototyping physical "Large Language Objects" with devices connected over Serial, BLE, or WiFi.

## Install

Clone the repository:

```bash
git clone https://github.com/IAD-ZHDK/LLM_embodiments.git
cd LLM_embodiments
```

### macOS and Linux

```bash
chmod +x setup.sh run.sh
./setup.sh
```

The setup script creates the Python environment and installs dependencies. It does not install Ollama; install it from [ollama.com](https://ollama.com/download), then download the default model:

```bash
ollama pull gemma4:e4b
./run.sh
```

If setup asks for an OpenAI API key and you use Ollama, press Enter to skip.

### Windows

Install Ollama from [ollama.com](https://ollama.com/download/windows), then open PowerShell in the repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
ollama pull gemma4:e4b
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

The configured provider and default model are `ollama` and `gemma4:e4b`. See [config.toml](config.toml) to change them.

For manual dependency installation, speech-model setup, or OpenAI configuration, see [Manual installation](MANUAL_INSTALL.md).

To update an existing checkout, run `git pull` from the repository folder.
