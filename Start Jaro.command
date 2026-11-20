#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
export PATH="/opt/homebrew/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  echo "Run Setup Jaro.command once to prepare Jaro."
  exit 1
fi
if [[ $# -eq 0 ]]; then
  set -- --background
fi
exec .venv/bin/python server.py "$@"
