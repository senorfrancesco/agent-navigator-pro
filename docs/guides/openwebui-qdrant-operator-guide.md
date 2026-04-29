# Open WebUI + Qdrant Operator Guide

Этот документ фиксирует рабочий операторский путь для `Open WebUI-first` контура, где:

- `Open WebUI` остаётся основным пользовательским интерфейсом;
- `session RAG`, управляемый серверной частью, хранится в `Qdrant` в коллекции `rag_chunks_v1`;
- native `Open WebUI Knowledge` использует тот же сервер `Qdrant`, но отдельное пространство коллекций с префиксом `anp-openwebui`;
- `bootstrap_openwebui.py` настраивает только стабильный admin API-контур и не создаёт native `Knowledge` через недокументированные вызовы.

## 1. Границы ответственности

- `session RAG`, управляемый серверной частью:
  - источник истины: `document_bindings` и серверный поиск;
  - коллекция: `rag_chunks_v1`;
  - путь пользователя: загрузка файла прямо в чат и следующий вопрос по тому же чату.
- Native `Open WebUI Knowledge`:
  - источник истины: объекты знаний внутри `Open WebUI`;
  - пространство коллекций: префикс `anp-openwebui` при включённом `ENABLE_QDRANT_MULTITENANCY_MODE=true`;
  - путь пользователя: явная загрузка материала в раздел `Knowledge`.

Оба контура используют один сервер `Qdrant`, но не смешивают владельца данных и схему коллекций.

## 2. Подготовка `backend/.env`

Перед запуском проверьте минимум такие значения в [backend/.env](../../backend/.env):

```dotenv
WEBUI_ADMIN_EMAIL="admin@example.com"
WEBUI_ADMIN_PASSWORD="change-me-now"
OPENAPI_TOOL_SERVER_TOKEN="llm-tools-platform-tool-server-dev-token-change-me"

KB_BACKEND="qdrant"
QDRANT_URL="http://127.0.0.1:6333"
QDRANT_COLLECTION_NAME="rag_chunks_v1"
SESSION_RAG_TTL_HOURS="72"
```

Замечания:

- `QDRANT_URL` в `backend/.env` остаётся адресом хоста для `run_native`: `http://127.0.0.1:6333`.
- Контейнерные сервисы используют один Docker-адрес `http://qdrant:6333`; это относится и к `agent-api`, и к `Open WebUI`.
- Для `Open WebUI` параметры native `Knowledge` задаются через `docker-compose.yaml`, а не через bootstrap серверной части.

## 3. Запуск контура

Поднимите backend и `Qdrant`:

```bash
docker compose up -d qdrant agent-api document-server legal-server ums
```

Поднимите `Open WebUI`:

```bash
docker compose up -d open-webui
```

Проверки доступности:

```bash
curl -sf http://127.0.0.1:6333/collections
```

```bash
curl -sf http://127.0.0.1:8000/health
```

```bash
curl -sf http://127.0.0.1:8090/health
```

```bash
curl -sf http://127.0.0.1:3001/health | head
```

## 4. Bootstrap `Open WebUI`

Сначала выполните диагностический прогон:

```bash
python scripts/bootstrap_openwebui.py \
  --backend-base-url http://127.0.0.1:8000 \
  --openwebui-base-url http://127.0.0.1:3001 \
  --dry-run
```

Если `preflight` не показывает отсутствующие ключи, выполните реальный bootstrap:

```bash
python scripts/bootstrap_openwebui.py \
  --backend-base-url http://127.0.0.1:8000 \
  --openwebui-base-url http://127.0.0.1:3001
```

Что bootstrap настраивает автоматически:

- соединение `OpenAPI Tool Server`;
- `Workspace Tools`;
- `Action Functions`;
- `Workspace Prompts`;
- модель по умолчанию;
- `task config`, включая отключение генерации следующих вопросов.

Что bootstrap не настраивает автоматически:

- native `Knowledge`;
- переключение и миграция `Qdrant` внутри `Open WebUI`;
- переиндексация старых объектов знаний.

## 5. Ручная настройка native `Knowledge` на `Qdrant`

После запуска контейнера `open-webui` уже получает такие runtime-параметры через `docker-compose.yaml`:

- `VECTOR_DB=qdrant`
- `QDRANT_URI=http://qdrant:6333`
- `ENABLE_QDRANT_MULTITENANCY_MODE=true`
- `QDRANT_COLLECTION_PREFIX=anp-openwebui`
- `RAG_EMBEDDING_ENGINE=openai`
- `RAG_OPENAI_API_BASE_URL=http://host.docker.internal:8092/v1`
- `RAG_OPENAI_API_KEY=sk-dummy`
- `RAG_EMBEDDING_MODEL=qwen3-embedding-0.6b`

Ручные шаги администратора:

1. Войдите в `Open WebUI` под администратором.
2. Откройте `Admin Settings -> Documents`.
3. Убедитесь, что векторная база установлена в `Qdrant`.
4. Убедитесь, что используется `QDRANT_URI=http://qdrant:6333`.
5. Проверьте, что включён `Enable Qdrant Multitenancy Mode`.
6. Проверьте префикс коллекций `anp-openwebui`.
7. Проверьте, что движок эмбеддингов установлен в `openai`, а модель — `qwen3-embedding-0.6b`.
8. Если вы мигрируете со старой схемы `Qdrant`, заранее снимите резервную копию или снимок состояния, затем при необходимости выполните `Reindex Knowledge Base`.

Важно:

- `Open WebUI` считает `Qdrant` интеграцией, поддерживаемой сообществом. Перед обновлением `Open WebUI` или сменой режима multitenancy снимайте резервную копию и проверяйте схему коллекций.
- `Reindex Knowledge Base` переносит только native базу знаний. Он не мигрирует backend `session RAG`.
- `labse-embedding` остаётся отдельным специализированным профилем для legal/documentation сценариев; общий `Knowledge/RAG` не должен молча переключаться на него без переиндексации коллекций.
- Для контейнерного контура не используйте `host.docker.internal:6333` как адрес `Qdrant`; он остаётся только для хостовых сервисов, которым действительно нужен доступ к backend на хосте.

## 6. Ручная проверка backend `session RAG`

Ожидаемое поведение:

- пользователь загружает файл прямо в чат;
- backend создаёт `document_ref` и быстрый индекс в `rag_chunks_v1`;
- `Open WebUI` не создаёт native объект знаний автоматически;
- следующий вопрос по тому же чату ищет в области `session` внутри `Qdrant`.

Порядок проверки:

1. Откройте новый чат в `Open WebUI`.
2. Загрузите тестовый файл в окно чата.
3. Задайте вопрос по содержимому файла.
4. Задайте второй вопрос по тому же файлу в том же чате.
5. Убедитесь, что ответ опирается на контекст файла, а новый объект не появился в списке знаний.

Дополнительная проверка по `Qdrant`:

```bash
curl -sf http://127.0.0.1:6333/collections
```

После session-загрузки должна использоваться коллекция `rag_chunks_v1`, которой управляет серверная часть. Появление новых коллекций с префиксом `anp-openwebui` от одной только chat-загрузки считается ошибкой контура.

## 7. Ручная проверка native `Knowledge`

Ожидаемое поведение:

- материал загружается в раздел `Knowledge`;
- `Open WebUI` пишет свои вектора в `Qdrant` с префиксом `anp-openwebui`;
- backend `session RAG` и native `Knowledge` не смешивают коллекции.

Порядок проверки:

1. Откройте раздел `Knowledge` в `Open WebUI`.
2. Создайте новый объект знаний.
3. Загрузите туда документ.
4. Дождитесь завершения индексации.
5. При необходимости выполните `Reindex Knowledge Base`.
6. Вернитесь в чат и используйте этот объект знаний в обычном сценарии `Open WebUI`.

Дополнительная проверка по `Qdrant`:

```bash
curl -sf http://127.0.0.1:6333/collections
```

Для native `Knowledge` должны появляться коллекции с префиксом `anp-openwebui`. Они не должны совпадать с `rag_chunks_v1`, которым управляет серверная часть.

## 8. Признаки неправильной настройки

- Загрузка файла в чат сразу создаёт объект в `Knowledge`.
- Native `Knowledge` пишет в `rag_chunks_v1`.
- `Open WebUI` не может проиндексировать знания и показывает ошибку эмбеддингов.
- После включения multitenancy старые знания пропали, но `Reindex Knowledge Base` не запускался.
- После обновления `Open WebUI` префиксы коллекций изменились и данные перестали находиться.

## 9. Откат и осторожность при обновлениях

Перед любым изменением этих параметров снимайте резервную копию `Qdrant`:

- `ENABLE_QDRANT_MULTITENANCY_MODE`
- `QDRANT_COLLECTION_PREFIX`
- версия `Open WebUI`

Минимальное правило:

- сначала резервная копия или снимок состояния `Qdrant`;
- потом изменение конфигурации;
- затем `Reindex Knowledge Base`, если меняется схема native `Knowledge`;
- только после проверки удалять старые коллекции.
