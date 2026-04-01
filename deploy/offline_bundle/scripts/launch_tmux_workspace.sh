#!/usr/bin/env bash

set -euo pipefail

SESSION_NAME="${1:-agent-nav-offline}"

if ! command -v tmux >/dev/null 2>&1; then
  echo "missing:tmux" >&2
  exit 1
fi

if ! command -v btop >/dev/null 2>&1; then
  echo "missing:btop" >&2
  exit 1
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "tmux-session-exists:$SESSION_NAME"
  echo "attach with: tmux attach -t $SESSION_NAME"
  exit 0
fi

tmux new-session -d -s "$SESSION_NAME" -n shell
tmux new-window -t "$SESSION_NAME" -n monitor "btop"
tmux select-window -t "$SESSION_NAME:1"

echo "tmux-workspace:ok session=$SESSION_NAME"
echo "attach with: tmux attach -t $SESSION_NAME"
