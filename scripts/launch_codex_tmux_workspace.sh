#!/usr/bin/env bash

set -euo pipefail

# Самый простой способ настройки: впишите нужные идентификаторы чатов ниже.
# Если строка пустая, в соответствующей панели запускается просто `codex`.
DEFAULT_SESSION_NAME="llm-tools-platform-codex"
DEFAULT_WINDOW_NAME="codex"
DEFAULT_CODEX_BIN="codex"
DEFAULT_SESSION_WIDTH="220"
DEFAULT_SESSION_HEIGHT="60"
DEFAULT_GRID_CHAT_1="main_worker_2"
DEFAULT_GRID_CHAT_2="main_worker_1"
DEFAULT_GRID_CHAT_3="QDRANT_RAG_IMPLEMENT"
DEFAULT_GRID_CHAT_4="repo_audit_dependency"
DEFAULT_HELPER_CHAT="repo_helper"

SESSION_NAME="${LLM_TOOLS_PLATFORM_CODEX_SESSION_NAME:-$DEFAULT_SESSION_NAME}"
WINDOW_NAME="${LLM_TOOLS_PLATFORM_CODEX_WINDOW_NAME:-$DEFAULT_WINDOW_NAME}"
CODEX_BIN="${LLM_TOOLS_PLATFORM_CODEX_BIN:-$DEFAULT_CODEX_BIN}"
SESSION_WIDTH="${LLM_TOOLS_PLATFORM_CODEX_SESSION_WIDTH:-$DEFAULT_SESSION_WIDTH}"
SESSION_HEIGHT="${LLM_TOOLS_PLATFORM_CODEX_SESSION_HEIGHT:-$DEFAULT_SESSION_HEIGHT}"
GRID_CHAT_1="${LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_1:-$DEFAULT_GRID_CHAT_1}"
GRID_CHAT_2="${LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_2:-$DEFAULT_GRID_CHAT_2}"
GRID_CHAT_3="${LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_3:-$DEFAULT_GRID_CHAT_3}"
GRID_CHAT_4="${LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_4:-$DEFAULT_GRID_CHAT_4}"
HELPER_CHAT="${LLM_TOOLS_PLATFORM_CODEX_HELPER_CHAT:-$DEFAULT_HELPER_CHAT}"
ATTACH_TMUX=true

print_help() {
  cat <<'EOF'
launch_codex_tmux_workspace.sh

Поднимает отдельную tmux-сессию для работы с Codex.

Раскладка по умолчанию:
  - окно `codex-grid`: 4 панели `codex` в сетке 2x2
  - окно `codex-helper`: 2 панели с вертикальным разделением:
      - слева обычный терминал
      - справа `codex` помощник
  - в `copy-mode` клавиша `y` копирует в системный буфер через `wl-copy`,
    `xclip` или `xsel`, если доступен один из этих инструментов

Использование:
  ./scripts/launch_codex_tmux_workspace.sh
  ./scripts/launch_codex_tmux_workspace.sh --no-attach
  ./scripts/launch_codex_tmux_workspace.sh --session my-codex
  ./scripts/launch_codex_tmux_workspace.sh --chat-1 abc --chat-2 def --helper-chat xyz

Быстрая настройка:
  Откройте этот файл и заполните `DEFAULT_GRID_CHAT_*` и `DEFAULT_HELPER_CHAT`.
  Тогда панели будут запускаться через `codex resume <чат>` без передачи флагов.

Флаги:
  --session NAME
      Явно задать имя tmux-сессии.
  --window NAME
      Явно задать имя окна.
  --codex-bin CMD
      Бинарник `codex`. По умолчанию `codex`.
  --chat-1 CHAT
      Чат для панели `codex-1`. Если не задан, запускается просто `codex`.
  --chat-2 CHAT
      Чат для панели `codex-2`. Если не задан, запускается просто `codex`.
  --chat-3 CHAT
      Чат для панели `codex-3`. Если не задан, запускается просто `codex`.
  --chat-4 CHAT
      Чат для панели `codex-4`. Если не задан, запускается просто `codex`.
  --helper-chat CHAT
      Чат для панели `codex-helper`. Если не задан, запускается просто `codex`.
  --no-attach
      Не подключаться к tmux после создания сессии.
  -h, --help
      Показать эту справку.

Переменные окружения:
  LLM_TOOLS_PLATFORM_CODEX_SESSION_NAME
  LLM_TOOLS_PLATFORM_CODEX_WINDOW_NAME
  LLM_TOOLS_PLATFORM_CODEX_BIN
  LLM_TOOLS_PLATFORM_CODEX_SESSION_WIDTH
  LLM_TOOLS_PLATFORM_CODEX_SESSION_HEIGHT
  LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_1
  LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_2
  LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_3
  LLM_TOOLS_PLATFORM_CODEX_GRID_CHAT_4
  LLM_TOOLS_PLATFORM_CODEX_HELPER_CHAT
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --session)
      SESSION_NAME="${2:?missing session name}"
      shift 2
      ;;
    --window)
      WINDOW_NAME="${2:?missing window name}"
      shift 2
      ;;
    --codex-bin)
      CODEX_BIN="${2:?missing codex binary}"
      shift 2
      ;;
    --chat-1)
      GRID_CHAT_1="${2:?missing chat id}"
      shift 2
      ;;
    --chat-2)
      GRID_CHAT_2="${2:?missing chat id}"
      shift 2
      ;;
    --chat-3)
      GRID_CHAT_3="${2:?missing chat id}"
      shift 2
      ;;
    --chat-4)
      GRID_CHAT_4="${2:?missing chat id}"
      shift 2
      ;;
    --helper-chat)
      HELPER_CHAT="${2:?missing helper chat id}"
      shift 2
      ;;
    --no-attach)
      ATTACH_TMUX=false
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "unknown-arg:$1" >&2
      print_help >&2
      exit 1
      ;;
  esac
done

if ! command -v tmux >/dev/null 2>&1; then
  echo "missing:tmux" >&2
  exit 1
fi

if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
  echo "missing:codex-command:$CODEX_BIN" >&2
  exit 1
fi

# Фиксируем абсолютный путь заранее: tmux-панели могут жить с другим PATH.
CODEX_BIN="$(command -v "$CODEX_BIN")"

build_codex_command() {
  local chat_id="$1"
  if [ -n "$chat_id" ]; then
    printf '%q resume %q' "$CODEX_BIN" "$chat_id"
  else
    printf '%q' "$CODEX_BIN"
  fi
}

detect_tmux_clipboard_command() {
  if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] && command -v wl-copy >/dev/null 2>&1; then
    printf '%s' 'wl-copy'
    return 0
  fi
  if command -v xclip >/dev/null 2>&1; then
    printf '%s' 'xclip -selection clipboard -in >/dev/null 2>&1'
    return 0
  fi
  if command -v xsel >/dev/null 2>&1; then
    printf '%s' 'xsel -i -b >/dev/null 2>&1'
    return 0
  fi
  return 1
}

configure_tmux_copy_mode() {
  local clipboard_cmd=""
  tmux set-option -g set-clipboard on
  tmux set-window-option -g mode-keys vi
  tmux bind-key [ copy-mode
  tmux bind-key -T copy-mode-vi v send -X begin-selection
  if clipboard_cmd="$(detect_tmux_clipboard_command)"; then
    tmux bind-key -T copy-mode-vi y send -X copy-pipe-and-cancel "$clipboard_cmd"
    echo "tmux-clipboard:system command=$clipboard_cmd"
  else
    tmux bind-key -T copy-mode-vi y send -X copy-selection-and-cancel
    echo "tmux-clipboard:tmux-buffer-only"
  fi
}

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  configure_tmux_copy_mode
  echo "tmux-session-exists:$SESSION_NAME"
  echo "attach with: tmux attach -t $SESSION_NAME"
  exit 0
fi

GRID_WINDOW_NAME="${WINDOW_NAME}-grid"
HELPER_WINDOW_NAME="${WINDOW_NAME}-helper"

tmux new-session -d -s "$SESSION_NAME" -n "$GRID_WINDOW_NAME" -x "$SESSION_WIDTH" -y "$SESSION_HEIGHT"

grid_root_pane="$(tmux display-message -p -t "$SESSION_NAME:$GRID_WINDOW_NAME" '#{pane_id}')"

# Окно 1: сетка 2x2 для четырёх основных сессий Codex.
tmux split-window -v -t "$grid_root_pane" -l 50%
grid_bottom_pane="$(tmux display-message -p -t "$SESSION_NAME:$GRID_WINDOW_NAME" '#{pane_id}')"
grid_top_pane="$grid_root_pane"

tmux split-window -h -t "$grid_top_pane" -l 50%
grid_top_right_pane="$(tmux display-message -p -t "$SESSION_NAME:$GRID_WINDOW_NAME" '#{pane_id}')"
grid_top_left_pane="$grid_top_pane"

tmux split-window -h -t "$grid_bottom_pane" -l 50%
grid_bottom_right_pane="$(tmux display-message -p -t "$SESSION_NAME:$GRID_WINDOW_NAME" '#{pane_id}')"
grid_bottom_left_pane="$grid_bottom_pane"

# Окно 2: обычный терминал и Codex-помощник, разделённые по вертикали.
tmux new-window -t "$SESSION_NAME" -n "$HELPER_WINDOW_NAME"
helper_root_pane="$(tmux display-message -p -t "$SESSION_NAME:$HELPER_WINDOW_NAME" '#{pane_id}')"
tmux split-window -h -t "$helper_root_pane" -l 50%
helper_right_pane="$(tmux display-message -p -t "$SESSION_NAME:$HELPER_WINDOW_NAME" '#{pane_id}')"
helper_left_pane="$helper_root_pane"

configure_tmux_copy_mode

tmux select-pane -t "$grid_top_left_pane" -T "codex-1"
tmux select-pane -t "$grid_top_right_pane" -T "codex-2"
tmux select-pane -t "$grid_bottom_left_pane" -T "codex-3"
tmux select-pane -t "$grid_bottom_right_pane" -T "codex-4"
tmux select-pane -t "$helper_left_pane" -T "terminal"
tmux select-pane -t "$helper_right_pane" -T "codex-helper"

tmux send-keys -t "$grid_top_left_pane" "$(build_codex_command "$GRID_CHAT_1")" Enter
tmux send-keys -t "$grid_top_right_pane" "$(build_codex_command "$GRID_CHAT_2")" Enter
tmux send-keys -t "$grid_bottom_left_pane" "$(build_codex_command "$GRID_CHAT_3")" Enter
tmux send-keys -t "$grid_bottom_right_pane" "$(build_codex_command "$GRID_CHAT_4")" Enter
tmux send-keys -t "$helper_right_pane" "$(build_codex_command "$HELPER_CHAT")" Enter

tmux select-window -t "$SESSION_NAME:$GRID_WINDOW_NAME"
tmux select-pane -t "$grid_top_left_pane"

echo "tmux-workspace:ok session=$SESSION_NAME grid_window=$GRID_WINDOW_NAME helper_window=$HELPER_WINDOW_NAME"
echo "layout:window1=4x codex grid window2=vertical terminal+assistant"
echo "codex-bin:$CODEX_BIN"
echo "grid chats: ${GRID_CHAT_1:-new}, ${GRID_CHAT_2:-new}, ${GRID_CHAT_3:-new}, ${GRID_CHAT_4:-new}"
echo "helper chat: ${HELPER_CHAT:-new}"
echo "attach with: tmux attach -t $SESSION_NAME"

if [ "$ATTACH_TMUX" = true ]; then
  tmux attach -t "$SESSION_NAME"
fi
