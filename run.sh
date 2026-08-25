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

# Function for logging - writes to both the terminal and the log file directly, rather than
# piping the whole script's stdout/stderr through tee. The backend drives a full-screen Textual
# UI that needs a real tty: it sends terminal-capability queries and blocks waiting for a reply,
# which never arrives if stdout is a pipe (this caused it to hang right at "Waiting for
# application startup" with zero further output - looked exactly like a stuck process).
log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
  echo "$msg"
  echo "$msg" >> "$LOG_FILE"
}

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

# Activate Python virtual environment
if [ -f "$SCRIPT_DIR/backend/venv/bin/activate" ]; then
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/backend/venv/bin/activate"
else
  log "⚠️ Python venv not found at backend/venv - continuing without venv activation."
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

# Detects the LLM_EMBODIMENT_PROFILE (config.<profile>.toml overlay) if not already set.
detect_profile() {
  if [[ -n "${LLM_EMBODIMENT_PROFILE:-}" ]]; then
    return
  fi
  case "$(uname -s)" in
    Darwin) export LLM_EMBODIMENT_PROFILE="mac" ;;
    Linux) export LLM_EMBODIMENT_PROFILE="linux" ;;
  esac
  if [[ -n "${LLM_EMBODIMENT_PROFILE:-}" ]]; then
    log "Using config profile: ${LLM_EMBODIMENT_PROFILE}"
  fi
}

# Main runtime function: starts services
run_once() {
  detect_profile

  # Clear anything left listening on port 3000 before starting a fresh backend.
  lsof -ti tcp:3000 | xargs -r kill -9 2>/dev/null || true

  # Start USB watcher in background (it never touches the terminal, safe to background under job control)
  watch_for_usb "$$" &
  WATCHER_PID=$!

  # Run the backend in the foreground (not backgrounded): it drives a full-screen Textual UI that
  # reads the controlling terminal directly. Under `set -m` job control, a *backgrounded* process
  # doing that gets sent SIGTTIN by the kernel and is silently suspended - indistinguishable from a
  # hang (no more output, ever). Running it in the foreground avoids that entirely.
  log "Starting Python backend server..."
  python3 -m backend.server
  log "Backend process exited."

  kill "$WATCHER_PID" 2>/dev/null || true
  wait "$WATCHER_PID" 2>/dev/null || true
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