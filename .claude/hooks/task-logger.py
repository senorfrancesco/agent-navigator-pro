#!/usr/bin/env python3
"""
Task Logger Hook — логирует завершение задач в TASKS.md.

Срабатывает на PostToolUse для TaskUpdate когда status=completed.
Добавляет запись в раздел ## Session Log в TASKS.md.
"""

import json
import os
import sys
from datetime import datetime

def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_output = hook_input.get("tool_response", {})

    # Реагируем только на TaskUpdate с status=completed
    if tool_name != "TaskUpdate":
        sys.exit(0)

    status = tool_input.get("status", "")
    if status != "completed":
        sys.exit(0)

    task_id = tool_input.get("taskId", "?")
    subject = tool_input.get("subject", "")

    # Пытаемся получить subject из output если не передан
    if not subject and isinstance(tool_output, dict):
        result = tool_output.get("result", "")
        if "Task #" in result:
            # "Task #1 updated successfully: Название"
            parts = result.split(":", 1)
            if len(parts) > 1:
                subject = parts[1].strip()

    tasks_md = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "TASKS.md"
    )

    if not os.path.exists(tasks_md):
        sys.exit(0)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_entry = f"- [x] **[{timestamp}]** Task #{task_id}: {subject or '(без названия)'} — ✅ completed\n"

    with open(tasks_md, "r", encoding="utf-8") as f:
        content = f.read()

    # Ищем или создаём раздел Session Log
    session_log_header = "\n## Session Log\n"
    if session_log_header not in content:
        content += session_log_header
        content += "\n"

    # Вставляем запись после заголовка Session Log
    insert_pos = content.index(session_log_header) + len(session_log_header) + 1
    content = content[:insert_pos] + log_entry + content[insert_pos:]

    with open(tasks_md, "w", encoding="utf-8") as f:
        f.write(content)

    # Пишем в stderr — это видно в Claude Code но не прерывает выполнение
    print(f"[task-logger] Logged: Task #{task_id} completed → TASKS.md", file=sys.stderr)

if __name__ == "__main__":
    main()
