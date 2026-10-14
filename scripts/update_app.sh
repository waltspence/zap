#!/bin/sh
# Self-update for Frankly Zapped.app: pull the repo, rebuild, relaunch.
# Invoked by the app's "Check for Updates" (detached), or run it yourself.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/frankly-zapped-update.log"
{
  echo "== update $(date)"
  cd "$REPO"
  git pull --ff-only
  PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY=python3
  "$PY" scripts/make_macos_app.py
  sleep 1
  open "$HOME/Applications/Frankly Zapped.app"
  echo "== done"
} >>"$LOG" 2>&1
