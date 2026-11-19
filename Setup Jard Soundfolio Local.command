#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.bun/bin:$PATH"

echo "One-time setup prepares Python, builds the browser interface, and downloads the local models."
echo "For everyday use, double-click Start Jard Soundfolio Local or Quit Jard Soundfolio Local."

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "Install FFmpeg with brew install ffmpeg, then run this setup again."
  exit 1
fi

for tool in uv bun; do
  if ! command -v "$tool" >/dev/null; then
    echo "Install $tool, then run Setup Jard Soundfolio Local again."
    exit 1
  fi
done

uv sync --locked --python 3.13
bun install --frozen-lockfile
JARD_WELCOME_PAGE="$(pwd -P)/README.html" bun run build
.venv/bin/python server.py --setup-model
.venv/bin/python -c "from local_qwen import setup; setup()"
echo "Ready. Open Start Jard Soundfolio Local.command."
