# Руководство По Оффлайн-Деплою

Этот документ описывает только действия на оффлайн-сервере.
Все скачивания и сборки должны быть выполнены заранее на build-машине по `BUILD_AND_EXPORT.md`.

## 0. Server Runbook

Короткий маршрут без пояснений для целевого оффлайн-сервера:

```bash
tar -xzf llm-tools-platform-offline-bundle-v1.0.tar.gz
cd deploy/offline_bundle

sudo bash scripts/install_host_apt_bundle.sh --manual-driver

cp env.bundle.example env.bundle

python3 scripts/validate_bundle.py --mode deploy
python3 scripts/preflight_runtime.py

bash scripts/run_offline_bundle.sh

docker compose -f compose.offline.yaml ps
tmux list-sessions
```

Если driver path пакетный, а не manual-driver:

```bash
sudo bash scripts/install_host_apt_bundle.sh
```

## 1. Host requirements

На сервере заранее должны быть установлены:
- Docker
- Docker Compose plugin
- NVIDIA driver, совместимый с CUDA 12.8
- NVIDIA Container Toolkit версии `1.19.0-1`
- `python3`
- `curl`
- tmux
- btop

Рекомендуемый состав пакетов/компонентов на хосте:
- `docker-ce`
- `docker-ce-cli`
- `containerd.io`
- `docker-buildx-plugin`
- `docker-compose-plugin`
- `nvidia-driver-590` или `nvidia-driver-590-server` при пакетной установке из локального apt-bundle
- либо ручной NVIDIA Data Center Driver `590.48.01` под конкретную версию Ubuntu
- `nvidia-container-toolkit=1.19.0-1`
- `nvidia-container-toolkit-base=1.19.0-1`
- `libnvidia-container-tools=1.19.0-1`
- `libnvidia-container1=1.19.0-1`
- `python3`
- `curl`
- `tmux`
- `btop`

Что важно по GPU-стеку:
- для запуска bundle нужен именно **NVIDIA driver** на хосте;
- для проброса GPU в контейнеры нужен **NVIDIA Container Toolkit**;
- `UMS` использует CUDA runtime внутри контейнера, поэтому хост должен корректно отдавать GPU через Docker;
- для текущего `v1.0` целевой сервер ориентирован на NVIDIA RTX A4000;
- для ручной установки драйвера жёстко фиксируем baseline как **NVIDIA Data Center Driver `590.48.01`**;
- `590.48.01` подходит для `CUDA 12.8` и RTX A4000;
- для пакетной оффлайн-установки из bundle допускается distro-пакет `nvidia-driver-590` / `nvidia-driver-590-server`;
- для текущего `v1.0` жёстко фиксируем container-runtime baseline как **`nvidia-container-toolkit 1.19.0-1`** и связанные пакеты той же версии;
- нижнюю границу совместимости CUDA 12.8 стоит считать не целью, а только минимумом.

Что **не требуется** только для запуска bundle:
- отдельный `CUDA Toolkit` на хосте;
- локальная установка Python-зависимостей проекта на сервере;
- сборка или скачивание Python wheelhouse на сервере: bundle уже должен содержать
  локально подготовленный `deploy/offline_bundle/wheelhouse/`;
- native runtime репозитория (`run_native.sh`, `conda`, host Python path).

Проверка:

```bash
bash scripts/check_host.sh
```

Если чего-то не хватает, bundle остановится и сообщит, что именно отсутствует.

Рекомендуемый post-install sequence для сервера:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
nvidia-smi
docker info | grep -i nvidia
```

Именно этот набор считаем каноническим host baseline для `release/v1.0`.

Если драйвер ставится вручную, скачивайте пакет **под точную версию Ubuntu**:
- `Ubuntu 22.04` -> Data Center Driver `590.48.01` для Ubuntu 22.04
- `Ubuntu 24.04` -> Data Center Driver `590.48.01` для Ubuntu 24.04

Не смешивайте пакет для `22.04` и `24.04`, даже если номер драйвера одинаковый.

Как выбрать между ручным и пакетным driver path:
- если хотите самый предсказуемый путь под `CUDA 12.8`, используйте ручной `Data Center Driver 590.48.01`;
- если хотите полностью оффлайн-пакетную установку из bundle, используйте локальный apt bundle с `nvidia-driver-590-server` / `nvidia-driver-590`;
- для пакетного пути на некоторых серверах могут понадобиться kernel-specific пакеты, и тогда build-side bundle надо пересобрать с `--extra-package`;
- для ручного `590.48.01` этот шаг обычно не нужен.

NVIDIA runtime обязателен прежде всего для `UMS`.
`agent-api`, `document-server`, `legal-server` и `chainlit` используют свои отдельные non-CUDA runtime images.
Для текущего `v1.0` heavy GGUF path ожидает `BACKEND_MODE=llama-server`.

## 2. Подготовка bundle

На build-машине bundle обычно передают на сервер как один `tar.gz` архив:

```bash
tar -czf llm-tools-platform-offline-bundle-v1.0.tar.gz \
  --exclude='deploy/offline_bundle/wheelhouse' \
  --exclude='deploy/offline_bundle/vendor/llama.cpp' \
  deploy/offline_bundle
```

На сервере:

```bash
tar -xzf llm-tools-platform-offline-bundle-v1.0.tar.gz
cd deploy/offline_bundle
```

Для deploy-side достаточно, чтобы в распакованном bundle были:
- `compose.offline.yaml`
- `env.bundle`
- `scripts/`
- `docs/`
- `images/*.tar`
- `models/`
- `state/`
- `host_packages/`
- generated `manifest.json`
- `tmux/`
- `monitoring/`

Что обычно не нужно переносить на сервер:
- `wheelhouse/`
- `vendor/llama.cpp/`
- исходники всего репозитория вне `deploy/offline_bundle/`

Если на сервере ещё не установлены нужные host packages из bundle:

```bash
sudo bash scripts/install_host_apt_bundle.sh
```

Если NVIDIA driver уже установлен **вручную** и пакетный `nvidia-driver-*` ставить не нужно:

```bash
sudo bash scripts/install_host_apt_bundle.sh --manual-driver
```

Скрипт перед установкой:
- выводит информацию о системе (`/etc/os-release`, архитектуру, ядро);
- предлагает интерактивно выбрать пакетную матрицу `ubuntu-22.04` или `ubuntu-24.04`;
- не позволит молча поставить bundle для другой версии Ubuntu, чем реально обнаружена на хосте.
- в режиме `--manual-driver` не будет навязывать пакетный `nvidia-driver-*` и вместо этого будет считать source of truth ручную установку драйвера;
- выбор `--manual-driver` фиксируется marker-файлом внутри bundle, чтобы последующие проверки и `deploy.sh` тоже не требовали пакетный `nvidia-driver-*`.

Что хранится в `state/host-install/`:
- `.gitkeep` остаётся пустым и нужен только для того, чтобы Git не потерял каталог в репозитории;
- `manual-driver` создаётся самим installer уже на сервере, если запуск был с `--manual-driver`;
- вручную заранее заполнять `state/host-install/` не нужно.

Если нужен только audit без установки:

```bash
python3 scripts/check_host_packages.py --distro auto
python3 scripts/verify_host_runtime.py
sudo bash scripts/install_host_apt_bundle.sh --check-only
```

Для ручного driver path:

```bash
python3 scripts/check_host_packages.py --distro auto --manual-driver
python3 scripts/verify_host_runtime.py
sudo bash scripts/install_host_apt_bundle.sh --manual-driver --check-only
```

Логика audit теперь разделена:
- `check_host_packages.py` проверяет только exact package matrix из `versions.lock.json`;
- `verify_host_runtime.py` отдельно проверяет готовность Docker/NVIDIA runtime;
- это позволяет не путать проблемы package drift и проблемы runtime configuration.

Флаги `install_host_apt_bundle.sh`:
- `--distro <name>`: принудительно выбрать пакетную матрицу `ubuntu-22.04` или `ubuntu-24.04` без интерактивного выбора.
- `--manual-driver`: не ставить и не требовать пакетный `nvidia-driver-*`. Использовать, если NVIDIA driver уже установлен вручную, например `590.48.01`.
- `--check-only`: выполнить только audit exact версий и runtime contract без установки пакетов.
- `--skip-runtime-configure`: не вызывать `nvidia-ctk runtime configure --runtime=docker`. Полезно, если Docker runtime уже настроен заранее и вы не хотите, чтобы bundle installer его трогал.

Флаги `check_host_packages.py`:
- `--lock-file <path>`: явно указать путь к `versions.lock.json`, если нужен нестандартный источник host package matrix.
- `--distro <name>`: выбрать `auto`, `ubuntu-22.04` или `ubuntu-24.04` для поиска нужного lock-файла.
- `--manual-driver`: не считать пакетный `nvidia-driver-*` обязательным при package-audit, если драйвер установлен вручную.
- `--json`: вывести machine-readable JSON-report вместо обычного текстового вывода.

Флаги `verify_host_runtime.py`:
- `--json`: вывести machine-readable JSON-report по проверкам `docker`, `docker compose`, `nvidia-smi` и `docker info`.

```bash
cp env.bundle.example env.bundle
```

Отредактировать секреты и порты в `env.bundle`.

Отдельно проверить, что в `env.bundle` корректно заданы container-side пути к весам:

```bash
MODEL_PATH_LLM=/app/backend/models/...
MODEL_PATH_EMBEDDING_INTENT=/app/backend/models/...
MODEL_PATH_EMBEDDING_RETRIEVAL=/app/backend/models/...
```

Для текущего `release/v1.0` `MODEL_PATH_VLM` и `MMPROJ_PATH` должны оставаться закомментированными.
Это не активный server-runtime path, а задел под будущий multimodal slice.

Если на сервере несколько A4000 и heavy path нужно ограничить конкретными картами,
это задаётся в `env.bundle` через:

```bash
UMS_LLM_GPU_INDICES=0,1
```

Настройка tmux:

```bash
mkdir -p ~/.config/tmux
cp tmux/tmux.conf ~/.config/tmux/tmux.conf
cp tmux/tmux.conf.local ~/.config/tmux/tmux.conf.local
```

Проверка, что папка bundle самодостаточна как deployment artifact:

```bash
python3 scripts/validate_bundle.py --mode build
python3 scripts/preflight_runtime.py
```

`--mode deploy` имеет смысл только когда в `images/` уже лежат экспортированные tar-архивы образов.

## 2.0.1. Короткий Порядок Действий На Сервере

После распаковки bundle канонический путь такой:

```bash
cd deploy/offline_bundle
sudo bash scripts/install_host_apt_bundle.sh --manual-driver
cp env.bundle.example env.bundle
python3 scripts/validate_bundle.py --mode deploy
python3 scripts/preflight_runtime.py
bash scripts/run_offline_bundle.sh
```

Если driver path пакетный, а не manual-driver:

```bash
sudo bash scripts/install_host_apt_bundle.sh
```

После старта полезно проверить:

```bash
docker compose -f compose.offline.yaml ps
tmux list-sessions
```

## 2.1. Реестр Флагов Deploy-Side Скриптов

Ниже перечислены все user-facing entrypoint scripts, которые оператор запускает уже
на сервере или при финальной deploy-подготовке bundle.

`check_host.sh`
- Отдельных флагов сейчас нет.
- Назначение: проверить обязательные host prerequisites и наличие `env.bundle`.

`install_host_apt_bundle.sh`
- `--distro <name>`: выбрать `ubuntu-22.04` или `ubuntu-24.04` без интерактивного меню.
- `--manual-driver`: не требовать пакетный `nvidia-driver-*`, считать драйвер установленным вручную.
- `--check-only`: выполнить только audit без установки пакетов.
- `--skip-runtime-configure`: не вызывать `nvidia-ctk runtime configure --runtime=docker`.

`check_host_packages.py`
- `--lock-file <path>`: использовать нестандартный `versions.lock.json`.
- `--distro <name>`: выбрать `auto`, `ubuntu-22.04` или `ubuntu-24.04`.
- `--manual-driver`: ослабить проверку пакетного `nvidia-driver-*` для manual-driver path.
- `--json`: вывести machine-readable JSON-report.

`verify_host_runtime.py`
- `--json`: вывести machine-readable JSON-report по Docker/NVIDIA runtime.

`validate_bundle.py`
- `--mode {build,deploy}`: `build` проверяет bundle после export; `deploy` дополнительно требует image archives.
- `--skip-manifest`: пропустить manifest-based validation и проверить только bundle files/env/models.
- `--env-file <path>`: указать явный env-файл вместо `env.bundle`.
- `--models-dir <path>`: указать явный каталог моделей вместо `deploy/offline_bundle/models`.

`preflight_runtime.py`
- `--env-file <path>`: проверить указанный env-файл вместо `env.bundle`.
- `--report-only`: вывести summary/warnings без жёсткого fail по validation errors.

`load_images.sh`
- Отдельных флагов сейчас нет.
- Назначение: загрузить `*.tar` и `*.tar.gz` из `images/` в local Docker daemon.

`restore_state.sh`
- `--allow-missing-manifest`: разрешить запуск без `manifest.json`.

`deploy.sh`
- По умолчанию не вызывает `load_images.sh`; offline deploy использует уже загруженные local images.
- `--ensure-image-load`: явно импортировать `*.tar` и `*.tar.gz` из `images/` перед `compose up`.
- `--skip-image-load`: compatibility alias; оставлен для старых вызовов, но ничего дополнительно не меняет.
- `--skip-host-check`: не вызывать `check_host.sh`.

`run_offline_bundle.sh`
- `--with-monitoring`: поднять compose profile `monitoring`.
- `--no-tmux`: не создавать tmux workspace.
- `--attach-tmux`: после старта сразу подключиться к tmux-сессии.
- По умолчанию не вызывает `load_images.sh`; runtime стартует из уже присутствующих local images.
- `--ensure-image-load`: явно импортировать `*.tar` и `*.tar.gz` из `images/` перед стартом.
- `--skip-image-load`: compatibility alias; оставлен для старых вызовов, но ничего дополнительно не меняет.
- `--skip-host-check`: не вызывать `check_host.sh`.
- `--tmux-session <name>`: задать имя tmux-сессии.
- `-h`, `--help`: показать справку.

`launch_tmux_workspace.sh`
- Позиционный аргумент `<session-name>`: имя tmux-сессии, по умолчанию `llm-tools-platform-offline`.
- Отдельных флагов сейчас нет.

`stop_offline_bundle.sh`
- Позиционный аргумент `<session-name>`: имя tmux-сессии, если отличается от стандартного.
- Отдельных флагов сейчас нет.

`verify_runtime.sh`
- `--timeout <seconds>`: максимальное время ожидания на группу сервисов, по умолчанию `120`.

## 3. Импорт образов

```bash
bash scripts/load_images.sh
```

Перед этим в `images/` уже должны лежать минимум:
- `llm-tools-platform-backend-app-offline_v1.0.tar`
- `llm-tools-platform-ums-offline_v1.0.tar`
- `llm-tools-platform-chainlit-offline_v1.0.tar`

Короткая памятка для обновления уже запущенного локального стека из новых tar-архивов:

```bash
bash scripts/load_images.sh
docker compose -f compose.offline.yaml --env-file env.bundle up -d --force-recreate
docker compose -f compose.offline.yaml --env-file env.bundle ps
docker compose -f compose.offline.yaml --env-file env.bundle logs --tail=120
```

Почему нужен именно `--force-recreate`:
- `docker load` обновляет теги образов в локальном Docker daemon;
- уже запущенные контейнеры сами по себе на новый image не переключаются;
- `up -d --force-recreate` пересоздаёт контейнеры и поднимает их уже из импортированных образов.

## 4. Модели

Каталог `models/` должен уже быть заполнен на build-машине до переноса bundle.

## 5. Восстановление state

```bash
bash scripts/restore_state.sh
```

Скрипт валидирует bundle по `manifest.json`:
- проверяет наличие обязательных state-каталогов;
- проверяет наличие обязательных state-файлов;
- для обязательных файлов сверяет checksum, если он указан в manifest.

`state/legacy-orchestrator-files/` не обязателен для штатного запуска `v1.0`.
Это архивный слой для старого `backend/orchestrator/.files`, а не live mount нового runtime.

## 6. Запуск

```bash
bash scripts/deploy.sh
```

Скрипт выполняет:
- `check_host.sh`
- `validate_bundle.py`
- `preflight_runtime.py`
- `load_images.sh`
- `restore_state.sh`
- `docker compose up -d`
- post-start verification

Если нужен monitoring profile:

```bash
docker compose -f compose.offline.yaml --env-file env.bundle --profile monitoring up -d
```

Рекомендуемый operator path:

```bash
bash scripts/run_offline_bundle.sh --with-monitoring
```

Если нужно сразу подключиться к tmux:

```bash
bash scripts/run_offline_bundle.sh --with-monitoring --attach-tmux
```

## 7. Проверка

```bash
docker compose -f compose.offline.yaml ps
curl -sf http://localhost:8090/health
curl -sf http://localhost:8000/health
curl -I http://localhost:3000
```

Повторная штатная проверка из bundle:

```bash
bash scripts/verify_runtime.sh
```

Полная валидация bundle как самостоятельной runtime-папки:

```bash
python3 scripts/validate_bundle.py --mode deploy
python3 scripts/preflight_runtime.py
python3 scripts/check_host_packages.py --distro auto
python3 scripts/verify_host_runtime.py
```

Что считается корректным persistence layout:
- `state/backend-data/` содержит backend DB/runtime metadata;
- `state/chainlit-data/` может содержать `chainlit.db`, но для чистого старта каталог может быть пустым;
- `state/uploads/` может содержать uploads, reports и `chainlit-elements/`, но для нового инстанса тоже может быть пустым;
- `models/` содержит экспортированные веса;
- `state/legacy-orchestrator-files/` может присутствовать как archive-only набор, но не нужен для обычного старта.

Для нового чистого развёртывания допустим такой минимальный state:
- пустой `state/backend-data/`
- пустой `state/chainlit-data/`
- пустой `state/uploads/`
- пустой `state/legacy-orchestrator-files/`

При первом старте сервисы создадут новые рабочие DB-файлы сами.

Локальная terminal-панель через tmux + btop:

```bash
bash scripts/launch_tmux_workspace.sh
```

Остановка:

```bash
bash scripts/stop_offline_bundle.sh
```

## 8. Как работать с monitoring

### Grafana

Открыть:

```text
http://<server-ip>:3002
```

Что смотреть:
- статус сервисов;
- базовые HTTP latency / error trends;
- наличие scrape targets от Prometheus.

### Prometheus

Открыть:

```text
http://<server-ip>:9090
```

Используется как источник сырых метрик и для проверки, что scrape действительно идёт.

### tmux + btop

`launch_tmux_workspace.sh` создаёт tmux-сессию с отдельным окном `monitor`, где запущен `btop`.

Это нужно для live-наблюдения на самом сервере:
- CPU / RAM / процессы;
- общий runtime picture без браузера;
- быстрая диагностика при проблемах старта.
