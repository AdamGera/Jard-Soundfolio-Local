#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
export PATH="/opt/homebrew/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  echo "Run Setup Jard Soundfolio Local.command once to prepare Jard Soundfolio Local."
  exit 1
fi
if [[ $# -eq 0 ]]; then
  set -- --background
fi
exec .venv/bin/python server.py "$@"
