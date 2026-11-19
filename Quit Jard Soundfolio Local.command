#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

# The running server holds this file open for the lifetime of its session library.
library="${RUMBLE_DATA_DIR:-$HOME/Library/Application Support/Rumble}"
lock="$library/.server.lock"
if [[ -f "$lock" ]]; then
  for pid in $(/usr/sbin/lsof -t -a -u "$(id -un)" -- "$lock" 2>/dev/null); do
    cwd=$(/usr/sbin/lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p') || continue
    [[ "$cwd" -ef "$PWD" ]] || continue
    command_line=$(ps -ww -p "$pid" -o command=) || continue
    case "$command_line" in
      *" server.py"|*" server.py "*|*" $cwd/server.py"|*" $cwd/server.py "*) ;;
      *) continue ;;
    esac

    echo "Quitting Jard Soundfolio Local…"
    if ! kill -TERM "$pid" 2>/dev/null && kill -0 "$pid" 2>/dev/null; then
      echo "Could not stop Jard Soundfolio Local." >&2
      exit 1
    fi
    while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
    echo "Jard Soundfolio Local has quit."
    exit 0
  done
fi
echo "Jard Soundfolio Local is already stopped."
