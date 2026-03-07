#!/usr/bin/env bash
# HiFi TUI launcher
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$DIR/src:${PYTHONPATH}"
exec "$DIR/venv/bin/python" -m hifi_tui "$@"
