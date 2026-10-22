#!/bin/sh

cd "$(dirname "$0")/.." || exit 1

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
  cat <<'EOF'
Python 3 is not installed in a usable location.

Set up .venv in this folder, then double-click this file again.
EOF
  read -r -p "Press Return to close..."
  exit 1
fi

"$PYTHON_BIN" src/midi_workbench.py

read -r -p "Press Return to close..."
