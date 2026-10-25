#!/bin/sh
# Creates "Piano MIDI Cleanup.app" in the repository root. The app opens the
# post-processing panel without a Terminal window. It stores absolute paths,
# so it can be dragged to the Dock or /Applications; run this again after
# moving the repository or rebuilding the Python environment.

cd "$(dirname "$0")/.." || exit 1
REPO_DIR="$(pwd)"
APP_PATH="$REPO_DIR/Piano MIDI Cleanup.app"

PYTHON_BIN=""

for candidate in \
  "$HOME/.local/share/piano-midi-cleanup-env/bin/python3" \
  "./.venv/bin/python3" \
  "/opt/homebrew/bin/python3" \
  "/usr/local/bin/python3" \
  "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" \
  "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3" \
  "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3"
do
  if [ -x "$candidate" ]; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  cat <<'MSG'
Python 3 is not installed in a usable location.

Set up the Python environment first, then double-click this file again.
MSG
  read -r -p "Press Return to close..."
  exit 1
fi

case "$PYTHON_BIN" in
  /*) ;;
  *) PYTHON_BIN="$REPO_DIR/${PYTHON_BIN#./}" ;;
esac

if ! "$PYTHON_BIN" -c "import sys; sys.path.insert(0, 'src'); import postprocess_panel"; then
  echo "The selected Python environment cannot load the panel."
  echo "Install tkinter and the project dependencies, then run this tool again."
  echo "Python: $PYTHON_BIN"
  read -r -p "Press Return to close..."
  exit 1
fi

# Escape backslashes and double quotes for AppleScript string literals.
as_string() {
  printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

SCRIPT_FILE="$(mktemp -t piano-midi-cleanup-app)" || exit 1
cat > "$SCRIPT_FILE" <<SCRIPT
set repoDir to $(as_string "$REPO_DIR")
set pythonBin to $(as_string "$PYTHON_BIN")
try
  do shell script "test -x " & quoted form of pythonBin & " && test -f " & quoted form of (repoDir & "/src/postprocess_panel.py")
on error
  display dialog "Python or the repository was moved. Run macos/create_app.command again to recreate this app." buttons {"OK"} default button 1 with icon caution
  return
end try
set logDir to (POSIX path of (path to library folder from user domain)) & "Logs/Piano MIDI Cleanup"
set logFile to logDir & "/panel.log"
try
  do shell script "mkdir -p " & quoted form of logDir
  set launchCommand to "cd " & quoted form of repoDir & " && PYTHONUTF8=1 " & quoted form of pythonBin & " src/postprocess_panel.py >> " & quoted form of logFile & " 2>&1"
  set errorMessage to "Piano MIDI Cleanup could not run. Check the Python environment and dependencies. Error details: " & logFile
  set errorScript to "on run argv" & linefeed & "display dialog (item 1 of argv) buttons {\"OK\"} default button 1 with icon caution" & linefeed & "end run"
  set notifyCommand to "/usr/bin/osascript -e " & quoted form of errorScript & " " & quoted form of errorMessage
  set watcher to launchCommand & "; code=\$?; if [ \"\$code\" -ne 0 ]; then " & notifyCommand & "; fi"
  do shell script "/usr/bin/nohup /bin/sh -c " & quoted form of watcher & " > /dev/null 2>&1 < /dev/null &"
on error
  display dialog "Piano MIDI Cleanup could not run. Check the Python environment and dependencies. Error details: " & logFile buttons {"OK"} default button 1 with icon caution
end try
SCRIPT

rm -rf "$APP_PATH"
if osacompile -o "$APP_PATH" "$SCRIPT_FILE"; then
  rm -f "$SCRIPT_FILE"
  echo "Created: $APP_PATH"
  echo "Python:  $PYTHON_BIN"
  echo
  echo "Double-click it to open the panel. You can also drag it to the Dock or /Applications."
else
  rm -f "$SCRIPT_FILE"
  echo "osacompile failed; the app was not created."
fi

read -r -p "Press Return to close..."
