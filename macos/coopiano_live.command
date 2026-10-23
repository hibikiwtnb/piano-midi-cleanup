#!/bin/sh
set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
COOPIANO_DIR="$PROJECT_DIR/sound_sources/coopiano"

cd "$COOPIANO_DIR"
if [ ! -x ./coopiano ]; then
    make
fi

exec ./coopiano live
