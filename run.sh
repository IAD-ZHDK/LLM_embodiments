#!/bin/bash
set -m

# Directory of this script (absolute)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Set log file location
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/kiosk.log"

# Restart marker file used by USB watcher
RESTART_FILE="$SCRIPT_DIR/.kiosk_restart_request"

# Function for logging
log() {
  # write to stdout; stdout is redirected to the main tee which appends to the log file
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Redirect all stdout and stderr to log file (avoid process-substitution which can change signal delivery)
exec > >(tee -a "$LOG_FILE") 2>&1

log "Starting application"

# Suppress desktop pop-ups on GNOME only. Explicitly do NOTHING on macOS (Darwin).
suppress_desktop_popups() {
  if [[ "$(uname)" == "Darwin" ]]; then
    log "macOS detected - skipping desktop pop-up suppression (no-op)"
    return 0
  fi

  if command -v gsettings >/dev/null 2>&1 && gsettings writable org.gnome.desktop.notifications show-banners >/dev/null 2>&1; then
    # Save current values so we can restore them later
    OLD_SHOW_BANNERS=$(gsettings get org.gnome.desktop.notifications show-banners 2>/dev/null || 'true')
    # disable notification banners
    gsettings set org.gnome.desktop.notifications show-banners false 2>/dev/null || true

    # disable auto-mount and auto-open of removable media which causes file-manager popups
    if gsettings writable org.gnome.desktop.media-handling automount >/dev/null 2>&1; then
      OLD_AUTOMOUNT=$(gsettings get org.gnome.desktop.media-handling automount 2>/dev/null || 'true')
      gsettings set org.gnome.desktop.media-handling automount false 2>/dev/null || true
    fi
    if gsettings writable org.gnome.desktop.media-handling automount-open >/dev/null 2>&1; then
      OLD_AUTOMOUNT_OPEN=$(gsettings get org.gnome.desktop.media-handling automount-open 2>/dev/null || 'true')
      gsettings set org.gnome.desktop.media-handling automount-open false 2>/dev/null || true
    fi

    export OLD_SHOW_BANNERS
    export OLD_AUTOMOUNT
    export OLD_AUTOMOUNT_OPEN
    log "Desktop pop-ups suppressed (GNOME banners + automount disabled)"
  else
    log "No GNOME gsettings control available; skipping pop-up suppression"
  fi
}

restore_desktop_popups() {
  if [[ "$(uname)" == "Darwin" ]]; then
    # explicit no-op on macOS
    return 0
  fi

  if [[ -n "${OLD_SHOW_BANNERS:-}" ]]; then
    gsettings set org.gnome.desktop.notifications show-banners "$OLD_SHOW_BANNERS" 2>/dev/null || true
    log "Restored GNOME notification banners -> ${OLD_SHOW_BANNERS}"
    unset OLD_SHOW_BANNERS
  fi

  if [[ -n "${OLD_AUTOMOUNT:-}" ]]; then
    gsettings set org.gnome.desktop.media-handling automount "$OLD_AUTOMOUNT" 2>/dev/null || true
    log "Restored GNOME automount -> ${OLD_AUTOMOUNT}"
    unset OLD_AUTOMOUNT
  fi

  if [[ -n "${OLD_AUTOMOUNT_OPEN:-}" ]]; then
    gsettings set org.gnome.desktop.media-handling automount-open "$OLD_AUTOMOUNT_OPEN" 2>/dev/null || true
    log "Restored GNOME automount-open -> ${OLD_AUTOMOUNT_OPEN}"
    unset OLD_AUTOMOUNT_OPEN
  fi
}

# Change to the directory where this script is located
cd "$SCRIPT_DIR" || exit 1

# Try to suppress desktop pop-ups (no-op on macOS)
suppress_desktop_popups

# Function to check for updates
check_for_updates() {
    log "Checking for updates..."
    
    # Fetch latest changes without modifying local files
    if ! git fetch origin main; then
        log "⚠️ Failed to fetch updates. Continuing with current version."
        return 1
    fi
    # Get the number of commits behind
    COMMITS_BEHIND=$(git rev-list HEAD..origin/main --count)
    
    if [ "$COMMITS_BEHIND" -gt 0 ]; then
        log "📦 Updates available ($COMMITS_BEHIND new commits)"
        
        # Stash any local changes
        if [ -n "$(git status --porcelain)" ]; then
            log "Stashing local changes..."
            git stash
        fi
        
        # Pull updates
        if git pull origin main; then
            log "✅ Updated successfully"
            
            # Update python packages if requirements.txt changed
            if git diff HEAD@{1} HEAD --name-only | grep -q "requirements.txt"; then
                log "📦 Python requirements changed, updating packages..."
                source python/venv/bin/activate
                pip3 install --no-deps -r python/requirements.txt
            fi
            
            # Pop stashed changes if any
            if [ -n "$(git stash list)" ]; then
                log "Restoring local changes..."
                git stash pop
            fi
            
            # Restart the script
            log "🔄 Restarting to apply updates..."
            exec "$0"
        else
            log "⚠️ Update failed. Continuing with current version."
        fi
    else
        log "✅ Already running latest version"
    fi
}

# Check for updates
check_for_updates

# Activate Python virtual environment
if [ -f "$SCRIPT_DIR/python/venv/bin/activate" ]; then
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/python/venv/bin/activate"
else
  log "⚠️ Python venv not found at python/venv - continuing without venv activation."
fi

# Function to clean up on exit
cleanup() {
  # prevent re-entrant cleanup
  if [[ "${CLEANING_UP:-0}" == "1" ]]; then
    log "Cleanup already in progress - skipping re-entry"
    return
  fi
  CLEANING_UP=1
  log "Shutting down servers and cleaning up..."

  # Stop USB watcher if running
  if [[ -n "$WATCHER_PID" ]]; then
    log "Stopping USB watcher (pid: $WATCHER_PID)..."
    kill "$WATCHER_PID" 2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
  fi

  # Kill any process using port 3000
  log "Killing processes on port 3000..."
  lsof -ti tcp:3000 | xargs kill -9 2>/dev/null || true
  sleep 1

 # Kill backend process
  if [[ -n "${BACKEND_PID:-}" ]]; then
    log "Killing backend (pid: $BACKEND_PID)..."
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi

  # Try to kill Chromium by PID
  if [[ -n "$CHROMIUM_PID" ]]; then
    log "Killing chromium (pid: $CHROMIUM_PID)..."
    kill "$CHROMIUM_PID" 2>/dev/null || true
    sleep 1
    if ps -p "$CHROMIUM_PID" > /dev/null 2>&1; then
      kill -9 "$CHROMIUM_PID" 2>/dev/null || true
    fi
  fi

  # Fallback: kill any chromium-browser / Chrome processes
  pkill -f chromium-browser 2>/dev/null || true
  pkill -f "Google Chrome" 2>/dev/null || true
  pkill -o chromium 2>/dev/null || true

  # Final attempt: free ports again
  lsof -ti tcp:3000 | xargs kill -9 2>/dev/null || true

  # Restore desktop pop-up and automount preferences if we changed them
  restore_desktop_popups

  log "Cleanup complete."
}

# Trap INT/TERM/USR1 and run cleanup (do not exit here so the top-level loop can handle restart)
# watcher will send SIGUSR1 to request a restart
trap 'cleanup' SIGINT SIGTERM SIGUSR1

# Run cleanup at the start to clear old processes
cleanup

if lsof -ti tcp:3000 >/dev/null; then
  log "Port 3000 is still in use. Exiting..."
  exit 1
fi

# Setup WiFi permissions only on Raspberry Pi (Linux)
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  log "Setting up WiFi management permissions for Raspberry Pi..."
  
  # Check if we need to add the sudoers rule
  if ! sudo -n grep -q "pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli" /etc/sudoers.d/nmcli-pi 2>/dev/null; then
    log "Adding WiFi management permissions..."
    echo "pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli" | sudo tee /etc/sudoers.d/nmcli-pi > /dev/null
    sudo chmod 0440 /etc/sudoers.d/nmcli-pi
    log "WiFi permissions configured."
  else
    log "WiFi permissions already configured."
  fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
  log "Running on macOS - WiFi management not required."
else
  log "Unknown OS type: $OSTYPE - skipping WiFi setup."
fi

# Watcher: looks for new USB mounts that contain a config.toml and triggers a restart.
# It will kill the main PID, wait for it to exit, then exec a fresh instance of this script.
watch_for_usb() {
  log "Starting USB watcher..."
  local bases=( "/media/$USER" "/media" "/mnt" "/run/media/$USER" "/Volumes" )
  # Use an indexed array for seen paths (macOS /bin/bash doesn't support associative arrays)
  seen_list=()
  local main_pid="$1"

  while true; do
    # prune seen_list entries whose file no longer exists (handles removal)
    if [[ ${#seen_list[@]} -gt 0 ]]; then
      for i in "${!seen_list[@]}"; do
        s="${seen_list[$i]}"
        if [[ ! -f "$s" ]]; then
          log "Detected removal of previously seen config: $s — removing from seen list"
          unset 'seen_list[$i]'
        fi
      done
      # compact array
      seen_list=("${seen_list[@]}")
    fi

    for base in "${bases[@]}"; do
      [[ -d "$base" ]] || continue
      while IFS= read -r -d '' cfg; do
        cfg="${cfg%/}"
        # membership check (handles spaces) - linear search but fine for small numbers of mounts
        found=false
        for s in "${seen_list[@]}"; do
          if [[ "$s" == "$cfg" ]]; then
            found=true
            break
          fi
        done
        if ! $found; then
          seen_list+=("$cfg")
          log "📱 Detected USB config: $cfg — requesting restart..."
          # create restart marker for the parent to see
          touch "$RESTART_FILE"
          # signal parent to terminate so it can perform cleanup and then restart
          if [[ -n "$main_pid" ]]; then
              log "Signaling main pid $main_pid to request restart (SIGUSR1)..."
              kill -USR1 "$main_pid" 2>/dev/null || true
          fi
          # continue watching; do not exit so we can detect additional events
        fi
      done < <(find "$base" -maxdepth 3 -type f -name 'config.toml' -print0 2>/dev/null)
    done
    sleep 3
  done
}

# Main runtime function: starts services and kiosk browser
run_once() {
  # Start Python backend server in the background
  log "Starting Python backend server..."
  ./run_backend_python.sh &
  BACKEND_PID=$!

  # Start USB watcher in background (gives it main PID so it can signal termination)
  watch_for_usb "$$" &
  WATCHER_PID=$!

  # Wait for the backend server to be ready
  log "Waiting for backend server to be ready on http://localhost:3000 ..."
  until curl -s http://localhost:3000 > /dev/null; do
    sleep 2
  done

  # Launch Chromium in kiosk mode on the attached display
  if [[ "$OSTYPE" == "darwin"* ]]; then
    log "Launching default browser on macOS..."
    open http://localhost:3000 &
  else
    export DISPLAY=:0
    log "Launching Chromium in kiosk mode..."
    
    # Ensure Chromium is configured to not use keyring
    mkdir -p ~/.config/chromium/Default
    if [ ! -f ~/.config/chromium/Default/Preferences ]; then
      cat > ~/.config/chromium/Default/Preferences << EOL
{
  "credentials_enable_service": false,
  "credentials_enable_autosignin": false
}
EOL
    fi
    
    # Define Chromium flags to disable password prompts and other dialogs
    CHROMIUM_FLAGS="--no-sandbox --kiosk --disable-infobars --disable-restore-session-state --disable-features=PasswordManager,GCMChannelStatus --password-store=basic --no-first-run --no-default-browser-check"
    
    sleep 5  # Extra wait for desktop to finish loading
    if command -v chromium >/dev/null 2>&1; then
      chromium $CHROMIUM_FLAGS http://localhost:3000 &
      CHROMIUM_PID=$!
    elif command -v chromium-browser >/dev/null 2>&1; then
      chromium-browser $CHROMIUM_FLAGS http://localhost:3000 &
      CHROMIUM_PID=$!
    else
      log "Chromium browser not found! Please install it with 'sudo apt install chromium' or 'sudo apt install chromium-browser'"
    fi
  fi

  # Wait for background jobs (so trap works). This wait returns when all children exit.
  wait
  log "Background jobs have exited."
}

# Top-level loop: run and restart if the watcher requested one
while true; do
  # Clear previous restart marker
  rm -f "$RESTART_FILE" 2>/dev/null || true

  run_once

  if [ -f "$RESTART_FILE" ]; then
    log "Restart requested by watcher. Re-launching..."
    rm -f "$RESTART_FILE" 2>/dev/null || true
    # small delay to allow ports to free
    sleep 1
    exec "$0"
  else
    log "No restart requested. Exiting main loop."
    break
  fi
done

log "All processes exited. Goodbye!"
# ...existing code...