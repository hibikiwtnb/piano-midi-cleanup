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
do shell script "cd " & quoted form of repoDir & " && PYTHONUTF8=1 nohup " & quoted form of pythonBin & " src/postprocess_panel.py > /dev/null 2>&1 < /dev/null &"
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
