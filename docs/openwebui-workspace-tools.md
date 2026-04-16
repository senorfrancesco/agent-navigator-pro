# Open WebUI Workspace Tools

Этот guide фиксирует канонический authoring path для локальных `Workspace > Tools` в `Open WebUI`.

## Когда использовать этот путь

`Workspace Tool` подходит, когда инструмент:

- живёт прямо внутри `Open WebUI`;
- не требует нашего backend `OpenAPI Tool Server`;
- нужен как локальный utility tool или verification fixture;
- должен вызываться через native `tool_ids` в `POST /api/chat/completions`.

Не использовать этот путь для:

- `Action Functions` под сообщением;
- backend-owned specialized workflows (`analyze_*`, `compare_*`, `equipment_*`);
- long-running jobs с `job_id/status_url`;
- Knowledge / RAG / corpus lifecycle.

Для них у нас отдельные контуры:

- `Workspace Tools` = локальный Python tool внутри `Open WebUI`;
- `Action Functions` = follow-up UI actions;
- `OpenAPI Tool Server` = внешний backend tool path.

Текущий product policy:

- `Community Sum Tool` остаётся только verification fixture для native tool-calling smoke;
- production-like explicit tools для `Open WebUI` живут в backend-owned `OpenAPI Tool Server`;
- в текущем live contour как named tools materialize’ятся только `equipment` инструменты;
- document/compare tools остаются deferred до `M2.1/M2.2`, даже если backend schemas уже существуют.

## Минимальный контракт tool-файла

`Open WebUI` ожидает Python-файл с frontmatter и классом `Tools`.

Минимальный шаблон уже лежит здесь:

- [openwebui_workspace_tool_template.py](/home/seral/HDD/proj/agent-navigator-pro/scripts/templates/openwebui_workspace_tool_template.py)

Рабочий deterministic пример:

- [openwebui_community_sum_tool.py](/home/seral/HDD/proj/agent-navigator-pro/tests/harness/openwebui/openwebui_community_sum_tool.py)

Обязательные части:

- frontmatter с `title`, `author`, `version`;
- `class Tools`;
- `Valves` как `pydantic.BaseModel`;
- async method(s), которые становятся callable tool surface.

Минимальный shape:

```python
"""
title: Example Workspace Tool
author: llm-tools-platform
version: 1.0.0
requirements:
"""

from pydantic import BaseModel


class Tools:
    class Valves(BaseModel):
        priority: int = 5

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def example_action(self, value: str) -> str:
        return f"EXAMPLE_TOOL_OK:{value.strip()}"
```

Практические правила:

- делайте tool output детерминированным, если это smoke fixture;
- не завязывайте локальный tool на backend secrets без явной причины;
- держите один tool file = один понятный surface;
- если нужен structured workflow, лучше идти в backend tool server, а не наращивать локальный Python tool.

## Как мы создавали Community Sum Tool

`Community Sum Tool` создавался не через backend `tool-server`, а как локальный `Workspace Tool`.

Путь был таким:

1. Написали tool file:
   [openwebui_community_sum_tool.py](/home/seral/HDD/proj/agent-navigator-pro/tests/harness/openwebui/openwebui_community_sum_tool.py)
2. Через admin API импортировали его в `Open WebUI`.
3. В чате включили tool в picker.
4. Проверили, что запрос уходит с `tool_ids=["community_sum_tool"]`.

То есть фактически это:

- отдельный Python tool file;
- helper script, который делает `install/update/status/delete`;
- после импорта `Open WebUI` хранит этот tool у себя и показывает его в `Workspace > Tools`.

## CLI для import / update / delete

Канонический helper:

- [manage_openwebui_tool.py](/home/seral/HDD/proj/agent-navigator-pro/scripts/manage_openwebui_tool.py)

Основные команды:

```bash
python scripts/manage_openwebui_tool.py install \
  --tool-file scripts/my_tool.py \
  --tool-id my_tool \
  --tool-name "My Tool" \
  --description "Local Open WebUI tool"
```

```bash
python scripts/manage_openwebui_tool.py status --tool-id my_tool
```

```bash
python scripts/manage_openwebui_tool.py delete --tool-id my_tool
```

Параметры:

- `install|status|delete`
- `--tool-file`
- `--tool-id`
- `--tool-name`
- `--description`
- `--env-file`
- `--openwebui-base-url`

Для compatibility fixture остаётся thin wrapper:

- [manage_openwebui_community_tool.py](/home/seral/HDD/proj/agent-navigator-pro/tests/harness/openwebui/manage_openwebui_community_tool.py)

Он просто прокидывает дефолты в generic helper.

## Verification recipe

Минимальный smoke:

1. `install` tool через helper.
2. Проверить `status`.
3. Открыть `Open WebUI`.
4. Убедиться, что tool виден в `Workspace > Tools` / tool picker.
5. В новом чате включить tool.
6. Для raw-model contour проверить, что request в `/api/chat/completions` содержит нужный `tool_id`.
7. Если нужен native path, проверить `Controls > Вызов функции`.
8. Снять deterministic result или tool result card.
9. Выполнить `delete`.

Для follow-up диагностики второго хода есть отдельный harness:

- [openwebui_followup_payload_harness.py](/home/seral/HDD/proj/agent-navigator-pro/tests/harness/openwebui/openwebui_followup_payload_harness.py)

Он делает именно то, что нужно для разбора persistence/state bug:

1. открывает чистый temporary chat;
2. требует включить только один tool;
3. отправляет первый и второй prompt;
4. сохраняет первый и второй `POST /api/chat/completions`;
5. классифицирует результат как:
   - `success`
   - `state_bug`
   - `contamination`
   - `tool_selection_or_runtime`
   - `incomplete`

Пример запуска:

```bash
python tests/harness/openwebui/openwebui_followup_payload_harness.py \
  --tool-id community_sum_tool \
  --tool-name "Community Sum Tool" \
  --output /tmp/openwebui_followup_payload_harness.json
```

Правило для follow-up regression:

- source of truth здесь не текст ответа в чате, а **второй request body**;
- если во втором payload нет ожидаемого `tool_id`, это `state_bug`;
- если во втором payload появились лишние tools, это `contamination`, и smoke считается невалидным до изоляции tool catalog;
- `features.memory=true` само по себе не считается contamination; важны именно `tool_ids`/tool refs, реально переданные модели;
- если payload чистый, но deterministic result не появился, это уже `tool_selection_or_runtime`, а не state serialization.

## Follow-up diagnostic harness

Для воспроизводимой диагностики второго хода в одном чистом чате используется отдельный harness:

```bash
python tests/harness/openwebui/openwebui_followup_payload_harness.py
```

Что делает harness:

- открывает `Open WebUI` в текущем browser session;
- подтягивает admin token из `backend/.env` / `backend/.env.example` через `sign_in`;
- переводит браузер в `/?temporary-chat=true`;
- ждёт, пока оператор вручную оставит в текущем чате только `Community Sum Tool`;
- отправляет первый и второй промпт подряд;
- фиксирует первый и второй `POST /api/chat/completions`;
- сохраняет JSON-отчёт с `firstRequestBody`, `secondRequestBody`, `firstToolRefs`, `secondToolRefs` и итоговой классификацией.

Классификация в отчёте:

- `state_bug` — во втором запросе пропал ожидаемый `tool_id`;
- `contamination` — во втором запросе появился лишний tool ref;
- `tool_selection_or_runtime` — запрос чистый, но deterministic result не наблюдается;
- `success` — expected tool сохранился и deterministic result видим.

По умолчанию harness ждёт:

- первый ответ: `COMMUNITY_TOOL_OK:18`;
- второй ответ: `COMMUNITY_TOOL_OK:14`.

## Типовые сбои

- Tool виден, но не вызывается:
  обычно tool не включён в текущем чате или request ушёл без `tool_ids`.
- Tool включён, но модель не даёт результат:
  это уже runtime/model issue, а не import issue.
- Кажется, что tool “не работает”, но ответ приходит поздно:
  для CPU-backed raw model возможна заметная задержка.
- Ответ пустой или странный:
  сначала проверяйте network request shape, потом сам tool file.

## Что не входит в этот guide

- authoring `Action Functions`
- authoring `Workspace Prompts`
- backend `OpenAPI Tool Server`
- `Knowledge`, `Qdrant`, ingestion pipeline

Это отдельные слои и их нельзя смешивать с локальными `Workspace Tools`.
