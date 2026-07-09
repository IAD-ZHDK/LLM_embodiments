#!/usr/bin/env bash

set -euo pipefail

# Cross-platform setup script for Debian-based Linux (incl. Raspberry Pi) and macOS
# It installs dependencies, sets up venv, npm packages and configures autostart.

if [ "${USER:-}" = "root" ]; then
  echo "Please run this script as a regular user (not root). If you used sudo, re-run without it."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

OS_NAME="$(uname -s)"

install_on_debian() {
  echo "Updating APT repositories..."
  sudo apt update && sudo apt upgrade -y

  echo "Installing required packages (node, chromium, git, libusb, build deps)..."
  # Node 18 from nodesource
  curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
  # Try modern chromium package name first, fall back to chromium-browser
  if ! sudo apt install -y nodejs git libusb-1.0-0-dev build-essential python3-venv python3-dev libffi-dev portaudio19-dev; then
    echo "apt install failed; please check your package sources"
  fi
  if ! sudo apt install -y chromium git; then
    sudo apt install -y chromium-browser || true
  fi
  
  # Install Python 3.13.3 from deadsnakes PPA if not already installed
  if ! command -v python3.13 >/dev/null 2>&1; then
    echo "Installing Python 3.13.3 from deadsnakes PPA..."
    sudo apt install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt update
    sudo apt install -y python3.13 python3.13-venv python3.13-dev
  fi
}

install_on_macos() {
  echo "Detected macOS. Installing Homebrew packages (node, git, etc.)..."
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found. Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for this session (Apple Silicon vs Intel handling)
    if [ -d "/opt/homebrew/bin" ]; then
      export PATH="/opt/homebrew/bin:$PATH"
    elif [ -d "/usr/local/bin" ]; then
      export PATH="/usr/local/bin:$PATH"
    fi
  fi

  brew update || true
  brew install node git portaudio libusb || true
  
  # Install Python 3.13.3 specifically
  echo "Installing Python 3.13.3..."
  brew install python@3.13 || brew upgrade python@3.13 || true
  # Create symlink to ensure we can use python3.13 command
  brew link --overwrite python@3.13 || true
  # Skipping Chromium installation on macOS per project preference
}

is_wsl() {
  # Return 0 if running inside WSL
  if [ -r /proc/version ]; then
    if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}
install_on_windows() {
  echo "Running on native Windows (not WSL)."
  echo "This script will attempt to install Python 3.13.3 specifically, which is required for this project."
  echo
  # Detect package managers
  if command -v winget >/dev/null 2>&1; then
    echo "winget detected. I can try to install Node, Python 3.13.3, Git and other dependencies via winget."
    read -r -p "Proceed with winget installs? [y/N]: " resp || true
    if [[ "$resp" =~ ^[Yy] ]]; then
      echo "Installing packages with winget (may require elevated privileges)..."
      # Install Python 3.13.3 specifically if available
      winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements || true
      # If specific 3.13 version fails, try generic Python 3
      if ! command -v python3.13 >/dev/null 2>&1 && ! command -v py -3.13 >/dev/null 2>&1; then
        echo "Python 3.13 specific version not found via winget, trying generic Python 3..."
        winget install --id Python.Python.3 -e --accept-source-agreements --accept-package-agreements || true
      fi
      # Install other dependencies
      winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements || true
      winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements || true
      # Skipping Chromium installation on native Windows per project preference
    else
      echo "Skipping winget installs."
    fi
  elif command -v choco >/dev/null 2>&1; then
    echo "Chocolatey detected. I can try to install Python 3.13.3 and other dependencies via choco."
    read -r -p "Proceed with choco installs? [y/N]: " resp || true
    if [[ "$resp" =~ ^[Yy] ]]; then
      # Try to install Python 3.13.3 specifically
      choco install -y python3 --version=3.13.3 || true
      # Install other dependencies
      choco install -y nodejs-lts git || true
    else
      echo "Skipping choco installs."
    fi
  else
    cat <<EOF
Native Windows automatic installation not available.
Please install the following manually and then re-run this script from a Bash shell (Git Bash) or WSL:

- Python 3.13.3 specifically (https://www.python.org/downloads/release/python-3133/)
- Node.js (LTS)
- Git

IMPORTANT: When installing Python 3.13.3, make sure to check "Add Python to PATH" in the installer.

Recommended options:
- WSL2 (preferred): install Ubuntu from the Microsoft Store and run this script inside WSL.
- Git for Windows (Git Bash): open Git Bash and run this script from there after installing Node/Python.

EOF
  fi

  echo
  echo "Checking Python installation..."
  if command -v python3.13 >/dev/null 2>&1; then
    echo "Python 3.13 found in PATH as python3.13"
  elif command -v py -3.13 >/dev/null 2>&1; then
    echo "Python 3.13 found as py launcher"
    echo "You may need to use 'py -3.13' instead of 'python' or 'python3'"
  elif command -v python >/dev/null 2>&1; then
    PY_VERSION=$(python --version 2>&1)
    echo "Python found: $PY_VERSION"
    echo "Note: This may not be version 3.13.3, which is recommended for this project"
  else
    echo "WARNING: Python was not found in PATH. Please ensure Python 3.13.3 is installed and in your PATH."
    echo "You may need to restart your terminal or system after installation."
  fi

  # Create a helper script in the project directory instead of auto-starting
  echo "Creating a helper script to run the application manually..."
  WIN_HELPER="$SCRIPT_DIR/run_windows.bat"
  cat > "$WIN_HELPER" <<BAT
@echo off
REM Helper to start the project on Windows. This attempts to use WSL or Git Bash to run the project's run.sh
setlocal
where wsl.exe >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via WSL...
  wsl -e bash -lc "cd \"$(wslpath '"$SCRIPT_DIR"')\" && ./run.sh"
  exit /b 0
)
where bash >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via Git Bash...
  bash -lc "cd \"$SCRIPT_DIR\" && ./run.sh"
  exit /b 0
)
echo No WSL or Bash found in PATH. Please run the project from WSL or Git Bash manually.
pause
BAT
  echo "Created $WIN_HELPER"
  echo "To run the application, double-click run_windows.bat in the project directory."
  echo "Note: Auto-start on login has been disabled as requested."
}

setup_node_and_install() {
  echo "Installing npm dependencies for the workspace..."
  npm install
}

setup_python_venv() {
  echo "Setting up Python virtual environment and installing pip packages..."
  
  # Determine Python command to use (prefer 3.13.3)
  PYTHON_CMD=""
  if command -v python3.13 >/dev/null 2>&1; then
    PYTHON_CMD="python3.13"
  elif [[ "$OS_NAME" == "Darwin"* ]] && [ -f "/opt/homebrew/opt/python@3.13/bin/python3.13" ]; then
    PYTHON_CMD="/opt/homebrew/opt/python@3.13/bin/python3.13"
  elif [[ "$OS_NAME" == "Darwin"* ]] && [ -f "/usr/local/opt/python@3.13/bin/python3.13" ]; then
    PYTHON_CMD="/usr/local/opt/python@3.13/bin/python3.13"
  else
    echo "Warning: Python 3.13.3 not found. Using default Python version."
    PYTHON_CMD="python3"
  fi
  
  echo "Using Python command: $PYTHON_CMD"
  $PYTHON_CMD --version || true
  
  # Create venv only if missing to make the script idempotent
  if [ ! -d "python/venv" ]; then
    echo "Creating virtual environment with $PYTHON_CMD..."
    $PYTHON_CMD -m venv python/venv
  fi
  
  # shellcheck source=/dev/null
  source python/venv/bin/activate

  # Diagnostics: show which python/pip we're using
  echo "Python executable: $(python -c 'import sys; print(sys.executable)')"
  python -V || true
  python -m pip --version || true

  # Use the venv's python -m pip to avoid ambiguity about which pip is used
  python -m pip install --upgrade pip wheel setuptools || true
  python -m pip install cffi srt tqdm websockets || true
  
  # Always install onnxruntime regardless of Python version
  python -m pip install onnxruntime || true
  
  if [ -f python/requirements.txt ]; then
    # If the requirements file isn't readable, inform the user rather than attempting
    # to change ownership or permissions automatically (which can fail under some setups).
    if [ ! -r python/requirements.txt ]; then
      echo "python/requirements.txt exists but is not readable by this user."
      echo "Please run: chmod a+r python/requirements.txt or adjust permissions and re-run this script."
    fi

    # Try installing requirements without filtering
    echo "Installing Python requirements from python/requirements.txt (this may take a while)..."
    if python -m pip install --no-cache-dir -v -r python/requirements.txt; then
      echo "Python requirements installed successfully."
    else
      echo "Warning: Some Python packages failed to install from python/requirements.txt."
      echo "See pip output above for details. Useful debug commands:"
      echo "  source python/venv/bin/activate"
      echo "  python -m pip debug --verbose"
      echo "  python -m pip install --no-cache-dir -v <package>"
    fi
  fi
  
  # Ensure numpy is present (requirements.txt already lists it, but be defensive)
  python -m pip install numpy || true
  
  # Verify onnxruntime installation
  if python -c "import onnxruntime" 2>/dev/null; then
    echo "onnxruntime is installed successfully."
  else
    echo "WARNING: onnxruntime could not be imported. Attempting direct installation..."
    python -m pip install --no-cache-dir -v onnxruntime
  fi
}

create_helper_scripts() {
  echo "Creating helper scripts (no auto-start will be configured)..."
  # Create a small helper to run the project on Windows if useful (keeps existing behavior)
  WIN_HELPER="$SCRIPT_DIR/run_windows.bat"
  if [ ! -f "$WIN_HELPER" ]; then
    cat > "$WIN_HELPER" <<BAT
@echo off
REM Helper to start the project on Windows. This attempts to use WSL or Git Bash to run the project's run.sh
setlocal
where wsl.exe >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via WSL...
  wsl -e bash -lc "cd \"$(wslpath '"$SCRIPT_DIR"')\" && ./run.sh"
  exit /b 0
)
where bash >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via Git Bash...
  bash -lc "cd \"$SCRIPT_DIR\" && ./run.sh"
  exit /b 0
)
echo No WSL or Bash found in PATH. Please run the project from WSL or Git Bash manually.
pause
BAT
    chmod 644 "$WIN_HELPER" || true
    echo "Created $WIN_HELPER"
  fi

  # Create a simple run helper for POSIX users
  POSIX_HELPER="$SCRIPT_DIR/run_local.sh"
  if [ ! -f "$POSIX_HELPER" ]; then
    cat > "$POSIX_HELPER" <<'SH'
#!/usr/bin/env bash
cd "$(cd "$(dirname "$0")" && pwd)"
./run.sh
SH
    chmod +x "$POSIX_HELPER" || true
    echo "Created $POSIX_HELPER"
  fi
}


main() {
  case "$OS_NAME" in
    Linux*)
      echo "Detected Linux"
      install_on_debian
      ;;
    Darwin*)
      echo "Detected macOS"
      install_on_macos
      ;;
    CYGWIN*|MINGW*|MSYS*|Windows_NT*)
      echo "Detected Windows-like environment: $OS_NAME"
      if is_wsl; then
        echo "Running inside WSL — delegating to Debian installer."
        install_on_debian
      else
        install_on_windows
      fi
      ;;
    *)
      echo "Unsupported OS: $OS_NAME"
      exit 1
      ;;
  esac

  # Common steps: npm, python, permissions
  setup_node_and_install
  setup_python_venv

  # Make run.sh executable
  chmod +x run.sh

  # Install wscat globally if npm is available
  if command -v npm >/dev/null 2>&1; then
    npm install -g wscat || true
  fi

  # We intentionally do NOT configure auto-start on any platform. Instead create helper scripts
  # so users can start the app manually when they want.
  create_helper_scripts

  # Create .env if missing
  if [ ! -f .env ]; then
    echo "Enter your OpenAI API Key (or press Enter to skip):"
    read -s api_key || true
    if [ -n "$api_key" ]; then
      echo "OPENAI_API_KEY='$api_key'" > .env
      echo ".env file created"
    else
      echo "Skipping .env creation (no key provided)"
    fi
  fi

  echo "Setup complete. Start the app with ./run.sh"
}

main "$@"