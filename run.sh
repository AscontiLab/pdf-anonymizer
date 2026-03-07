#!/bin/bash
# DSGVO PDF-Anonymizer — Startscript
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:14b}"
export PORT="${PORT:-5050}"

exec /usr/bin/python3 "$SCRIPT_DIR/app.py" >> "$LOG_DIR/anonymizer.log" 2>&1
