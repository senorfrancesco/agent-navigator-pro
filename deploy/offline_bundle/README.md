# Оффлайн-бандл Agent Navigator v1.0

Этот каталог является оффлайн deployment slice для release-линии `release/v1.0`.

Внутри находятся:
- versioned manifest template и build/export contract;
- offline-only compose и Dockerfile для `backend-app`, `ums` и `chainlit`;
- build-side checkout `vendor/llama.cpp/` как фиксированная локальная зависимость для сборки `UMS` image;
- export/import/deploy scripts;
- host-install scripts и contract для локальных apt-bundle под Ubuntu 22.04 / 24.04;
- каталоги для image archives, локального wheelhouse, state и models.

Папка должна быть самодостаточной для серверного runtime:
- запуск и остановка идут только из неё;
- валидация комплектности идёт через `scripts/validate_bundle.py`;
- runtime sanity и writeability проверяются через `scripts/preflight_runtime.py`;
- root native/runtime scripts не являются обязательной частью server-deploy path.

Канонический persistence contract для `v1.0`:
- `state/backend-data`
- `state/chainlit-data`
- `state/uploads`
- `models`

Legacy `backend/orchestrator/.files` хранится отдельно как archive-only набор в
`state/legacy-orchestrator-files` и не монтируется в live runtime по умолчанию.

Для нового чистого инстанса допускается пустой state:
- каталоги `state/backend-data`, `state/chainlit-data` и `state/uploads` должны существовать;
- сами SQLite-файлы могут быть созданы приложением при первом запуске;
- содержимое `state/uploads` и `state/legacy-orchestrator-files` можно не переносить.

Правило scope для текущей задачи:
- изменения делаются только внутри `deploy/offline_bundle/`;
- любые правки вне этой папки требуют отдельного предупреждения пользователю.

Быстрый маршрут:
1. На build-хосте следовать `docs/BUILD_AND_EXPORT.md`.
2. На оффлайн-сервере следовать `docs/DEPLOY_OFFLINE.md`.

Коротко по упаковке:
- build-side `wheelhouse/` и `vendor/llama.cpp/` обычно не нужно переносить на оффлайн-сервер;
- на сервер обычно уезжает уже готовый bundle с `images/*.tar`, `host_packages/`, `models/`, `state/`, `scripts/`, `env.bundle` и generated `manifest.json`;
- рекомендуемый способ переноса: один `tar.gz` архив всей папки `deploy/offline_bundle/` без build-only каталогов.

Основной operator entrypoint:
- `scripts/run_offline_bundle.sh`

Число рабочих документов в bundle намеренно ограничено:
- `docs/BUILD_AND_EXPORT.md`
- `docs/DEPLOY_OFFLINE.md`

Что в git обычно не фиксируется:
- generated `manifest.json`;
- `host_packages/` после локальной сборки apt bundle;
- exported payload в `images/`, `models/`, `state/exported-env/`, `wheelhouse/`;
- локальный checkout `vendor/llama.cpp/`, который используется только на build-side.
