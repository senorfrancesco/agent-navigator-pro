# Сборка И Экспорт

Этот документ описывает все скачивания и сборки, которые оператор выполняет **вручную** на connected build-машине.

Важно:
- `manifest.template.json` относится к source-slice и может храниться в git;
- generated `manifest.json` относится к build/export output и обычно не коммитится;
- `host_packages/`, image tar-архивы, exported env/state и локальный `vendor/llama.cpp/`
  считаются build-side артефактами и не должны автоматически попадать в source commit.

## 1. Подготовка env

```bash
cd deploy/offline_bundle
cp env.bundle.example env.bundle
```

Отредактировать `env.bundle` под нужные порты и секреты.

Важно:
- `env.bundle` не должен оставаться минимальной заглушкой;
- в нём должны быть указаны container-side обязательные `MODEL_PATH_*`;
- для текущего `v1.0` `MODEL_PATH_VLM` и `MMPROJ_PATH` остаются закомментированными;
- container-side пути должны соответствовать фактическому содержимому `deploy/offline_bundle/models/`.

## 1.1. Зафиксировать источник `llama.cpp` для UMS image

Для текущего source-slice `UMS` image тянет `llama.cpp` из официального репозитория
через build args:

```bash
--build-arg LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp.git
--build-arg LLAMA_CPP_REF=master
```

Если нужен fork или pin на конкретный commit/tag, переопределите эти аргументы
явно при `docker build`.

Важно:
- для текущего сервера с NVIDIA A4000 в `UMS` image используется `CMAKE_CUDA_ARCHITECTURES="86"`;
- сборка `llama.cpp` в `UMS` image идёт с `-DCMAKE_BUILD_TYPE=Release` и `cmake --build ... --config Release`;
- multi-GPU runtime остаётся под контролем `UMS` через его placement logic, а не через статический `docker run`.

## 1.2. Tmux configs

Bundle уже содержит локальную копию tmux-конфигов:
- `tmux/tmux.conf`
- `tmux/tmux.conf.local`

Их не нужно генерировать. На оффлайн-сервере оператор просто копирует их в:

```bash
mkdir -p ~/.config/tmux
cp tmux/tmux.conf ~/.config/tmux/tmux.conf
cp tmux/tmux.conf.local ~/.config/tmux/tmux.conf.local
```

## 2. Собрать backend-app, UMS и Chainlit образы

Из корня репозитория:

```bash
docker build \
  -f deploy/offline_bundle/Dockerfile.backend.offline \
  -t agent-nav-backend-app-offline:v1.0 \
  .

docker build \
  -f deploy/offline_bundle/Dockerfile.ums.offline \
  --build-arg LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp.git \
  --build-arg LLAMA_CPP_REF=master \
  -t agent-nav-ums-offline:v1.0 \
  .

docker build \
  -f deploy/offline_bundle/Dockerfile.chainlit.offline \
  -t agent-nav-chainlit-offline:v1.0 \
  .
```

Если вы запускаете сборку не из корня репозитория, а из `deploy/offline_bundle/`, build context всё равно должен оставаться корнем репозитория:

```bash
docker build -f Dockerfile.backend.offline -t agent-nav-backend-app-offline:v1.0 ../..
docker build -f Dockerfile.ums.offline \
  --build-arg LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp.git \
  --build-arg LLAMA_CPP_REF=master \
  -t agent-nav-ums-offline:v1.0 ../..
docker build -f Dockerfile.chainlit.offline -t agent-nav-chainlit-offline:v1.0 ../..
```

Если нужен `vllm` image для offline path, подготовить его отдельно и протегировать как:

```bash
docker tag <existing-vllm-image> agent-nav-vllm-offline:v1.0
```

Важно:
- `backend-app` и `chainlit` используют `python:3.11-slim-bookworm`;
- `UMS` собирается отдельно на `nvidia/cuda:12.8.1-*` и включает собранный `llama-server`;
- внутри `UMS` `torch==2.8.0` ставится отдельно из `https://download.pytorch.org/whl/cu128`;
- если `docker build` падает ещё до `COPY` на pull базового образа с `network is unreachable`, это проблема Docker builder/host networking.
- для такого сбоя не нужно менять `COPY` или build context, пока не починится `docker pull` базового образа.
- если `UMS` build падает на `git clone`/checkout `llama.cpp`, нужно проверить доступность репозитория, корректность `LLAMA_CPP_REPO` и `LLAMA_CPP_REF`.
- если сервер использует несколько A4000, runtime-ограничение heavy path по картам задаётся уже не в Dockerfile, а через `UMS_LLM_GPU_INDICES` в `env.bundle`.

## 3. Экспорт контейнерных образов в bundle

```bash
bash deploy/offline_bundle/scripts/export_images.sh --skip-build
```

Если хотите, чтобы script сначала сам пересобрал backend-app, UMS и Chainlit images:

```bash
bash deploy/offline_bundle/scripts/export_images.sh
```

Ожидаемые артефакты:
- `images/agent-nav-backend-app-offline_v1.0.tar`
- `images/agent-nav-ums-offline_v1.0.tar`
- `images/agent-nav-chainlit-offline_v1.0.tar`
- optional `images/agent-nav-vllm-offline_v1.0.tar`

После экспорта образов bundle можно проверить уже в deploy-режиме:

```bash
python3 deploy/offline_bundle/scripts/validate_bundle.py --mode deploy
```

## 4. Экспорт моделей

По умолчанию bundle копирует модели из `backend/models/`:

```bash
bash deploy/offline_bundle/scripts/export_models.sh
```

После этого оператор обязан сверить, что пути в `env.bundle` реально соответствуют экспортированному layout, например:

```bash
find deploy/offline_bundle/models -maxdepth 4 | sort
```

Ожидаемая логика:
- `deploy/offline_bundle/models/gguf/...` -> внутри контейнера `/app/backend/models/gguf/...`
- `deploy/offline_bundle/models/st/...` -> внутри контейнера `/app/backend/models/st/...`

Если модели лежат в другом checkout root:

```bash
bash deploy/offline_bundle/scripts/export_models.sh --source-root /path/to/repo
```

Если фактические веса лежат вне репозитория и `backend/models/` не используется как source of truth:

```bash
bash deploy/offline_bundle/scripts/export_models.sh --models-root /absolute/path/to/models
```

Скрипт использует staging-directory и меняет `models/` только после успешного копирования и валидации.
Если копирование прервать, текущий bundle layout не должен остаться полуочищенным.

После копирования bundle валидирует, что пути в `env.bundle` реально указывают на существующие
артефакты внутри `deploy/offline_bundle/models/`.

Важно:
- `MODEL_PATH_LLM`, `MODEL_PATH_EMBEDDING_INTENT`, `MODEL_PATH_EMBEDDING_RETRIEVAL` считаются обязательными;
- `MODEL_PATH_VLM` и `MMPROJ_PATH` для текущего `v1.0` остаются закомментированными, потому что VLM пока считается runtime-заглушкой;
- `MODEL_PATH_E5_LEGAL` и `MODEL_PATH_RUBERT` можно оставить пустыми, если вы не переносите legacy/auxiliary embeddings.

## 5. Экспорт state и env

Перед export state нужно остановить runtime или как минимум привести SQLite в консистентное состояние.
Для release-артефакта не считать допустимым snapshot с работающих контейнеров/процессов.

```bash
bash deploy/offline_bundle/scripts/export_state.sh
```

Скрипт копирует:
- `backend/.data/*`
- `backend/open_webui_uploads/`
- `backend/orchestrator/.files/` в `state/legacy-orchestrator-files/`, если каталог существует
- `backend/.env`
- `backend/.env.runtime`
- `backend/.env.hardware.override` при наличии

Если какого-то state-файла нет, скрипт не скрывает это, а пишет `skip-missing`.

Важно:
- обязательным live-state считаются каталоги `state/backend-data`, `state/chainlit-data`, `state/uploads` и `state/exported-env`;
- `backend/orchestrator/.files` считается legacy archive-set, а не primary runtime storage;
- текущий runtime хранит Chainlit file elements в `open_webui_uploads/chainlit-elements/`.
- если нужен новый чистый инстанс без старых данных, каталоги `state/backend-data/`, `state/chainlit-data/` и `state/uploads/` можно оставить пустыми;
- в этом режиме старые `chainlit.db`, `orchestrator_kb.db`, uploads и legacy `.files` не требуются.

Это reference snapshot, а не runtime env для контейнеров.
Фактический offline runtime использует `deploy/offline_bundle/env.bundle`.

## 6. Wheelhouse

Если нужен host-side Python wheelhouse:

```bash
bash deploy/offline_bundle/scripts/build_wheelhouse.sh
```

Если на этом этапе нужен только каркас bundle без скачивания wheel-файлов:

```bash
bash deploy/offline_bundle/scripts/build_wheelhouse.sh --skip-download
```

Что ещё оператор должен подготовить для полноценной оффлайн-сборки и запуска:
- доступный `llama.cpp` repo/ref для build args `LLAMA_CPP_REPO` и `LLAMA_CPP_REF`
- базовые Docker image, которые должны успешно `pull/build` на build-машине
- Python-зависимости из lock-файлов bundle
- веса моделей в `models/`
- runtime state в `state/`
- host apt bundle в `host_packages/ubuntu-24.04/`

Что уже покрыто текущими lock-файлами по реальным runtime-imports:
- `SQLAlchemy` добавлен в Chainlit path для persistence layer
- `sentence-transformers` остаётся в `UMS` path для embedding/ST runtime
- `torch==2.8.0` ставится отдельно в `UMS` image из `cu128` wheel index
- `uvicorn[standard]` зафиксирован для `backend-app` и `UMS`
- `aiofiles` и `asyncpg` добавлены как практический runtime/persistence запас для Chainlit и backend path
- `huggingface_hub` добавлен в `backend-app` и `UMS` как безопасный companion для `transformers` / `sentence-transformers`

Что не включено в обязательный `v1.0` runtime, потому что по основному runtime-коду сейчас не требуется:
- `langchain-mcp-adapters`
- `mcp`
- `onnxscript`
- `pytest`
- `llama-cpp-python[server]`

Если позже серверный runtime будет расширен и начнёт реально импортировать эти пакеты в основном path, их нужно будет добавить в соответствующий lock-файл bundle.

Что скачивать **обязательно** для текущего `v1.0`:
- Python-пакеты из `requirements.backend.lock.txt`
- Python-пакеты из `requirements.ums.lock.txt`
- `torch==2.8.0` из `https://download.pytorch.org/whl/cu128`
- Python-пакеты из `requirements.chainlit.lock.txt`
- базовые Docker image для `backend-app`, `UMS` и `Chainlit`
- host `.deb` пакеты для Ubuntu 22.04 и/или 24.04 через `build_host_apt_bundle.sh`
- доступный `llama.cpp` repo/ref для `UMS` build
- обязательные веса моделей из `env.bundle`
- каталоги runtime state из `state/`

Что сейчас считается **опциональным** и отдельно не требуется для штатного server-runtime:
- `pytest` — только для разработки и верификации
- `llama-cpp-python[server]` — не нужен, потому что heavy GGUF path идёт через `llama-server`
- `onnxscript` — нужен только для export/tooling path, а не для обычного запуска bundle
- `langchain-mcp-adapters` и `mcp` — в текущем основном runtime-path не импортируются

## 6.1. Host apt bundle для Ubuntu 22.04 / 24.04

Для оффлайн-сервера build-машина должна отдельно подготовить локальный apt bundle:

```bash
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh
```

Если нужен bundle под Ubuntu 22.04:

```bash
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh --distro ubuntu-22.04
```

Что жёстко фиксируем для host package slice:
- пакетный baseline Docker: `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, `docker-compose-plugin`
- пакетный baseline NVIDIA Container Toolkit:
  - `nvidia-container-toolkit=1.19.0-1`
  - `nvidia-container-toolkit-base=1.19.0-1`
  - `libnvidia-container-tools=1.19.0-1`
  - `libnvidia-container1=1.19.0-1`
- ручной baseline NVIDIA driver для CUDA 12.8:
  - Data Center Driver `590.48.01` под Ubuntu 22.04
  - Data Center Driver `590.48.01` под Ubuntu 24.04

Важно:
- `build_host_apt_bundle.sh` собирает локальный apt bundle для пакетной установки;
- ручной NVIDIA driver `590.48.01` при необходимости скачивается оператором отдельно под конкретную версию Ubuntu;
- если вы используете пакетный вариант драйвера через локальный apt bundle, остаётся `nvidia-driver-550-server` по умолчанию или `nvidia-driver-550` как override.

Когда нужны `--extra-package`:
- если вы планируете ставить драйвер **через локальный apt bundle**, на target-host могут понадобиться kernel-specific пакеты;
- типичные кандидаты: `linux-headers-<uname -r>` и связанные metapackage для ветки драйвера;
- точный набор заранее не универсален и определяется только на конкретном сервере по:
  - `uname -r`
  - `dpkg -l | grep -E 'linux-(image|headers)|nvidia'`
- если вы идёте по **ручному пути** с `NVIDIA Data Center Driver 590.48.01`, `--extra-package` обычно не нужен для driver slice bundle.

Минимальная памятка: какие данные снять с target-сервера, чтобы добрать kernel-specific пакеты:

```bash
cat /etc/os-release
uname -r
uname -m
dpkg --print-architecture
dpkg -l | grep -E 'linux-(image|headers|modules)|nvidia|dkms' || true
apt-cache policy nvidia-driver-550 nvidia-driver-550-server || true
apt-cache search "^linux-modules-nvidia" || true
apt-cache search "^linux-headers-$(uname -r)$" || true
nvidia-smi || true
```

По этим данным выбирается:
- какая пакетная матрица нужна: `ubuntu-22.04` или `ubuntu-24.04`;
- нужен ли вообще пакетный `nvidia-driver-*` или остаётся manual-driver path;
- какие `linux-headers-*` / `linux-modules-nvidia-*` надо добавить в bundle через `--extra-package`.

Пример, если нужен пакетный driver path и известна версия ядра target-хоста:

```bash
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh \
  --distro ubuntu-22.04 \
  --extra-package linux-headers-<kernel-version>
```

Если нужен bundle под Ubuntu 24.04:

```bash
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh --distro ubuntu-24.04
```

По умолчанию скрипт фиксирует пакетный baseline:
- `docker-ce`
- `docker-ce-cli`
- `containerd.io`
- `docker-buildx-plugin`
- `docker-compose-plugin`
- `nvidia-driver-550-server`
- `nvidia-container-toolkit`
- `python3`
- `curl`
- `tmux`
- `btop`

Если нужен desktop-вариант драйвера:

```bash
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh --driver-package nvidia-driver-550
```

Полезные режимы:

```bash
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh --check-only
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh --dry-run
```

Флаги `build_host_apt_bundle.sh`:
- `--distro <name>`: выбрать целевую пакетную матрицу. Допустимые значения: `ubuntu-22.04`, `ubuntu-24.04`. По умолчанию используется `ubuntu-24.04`.
- `--driver-package <name>`: выбрать пакетный драйверный baseline для apt-path. Допустимые значения: `nvidia-driver-550-server` и `nvidia-driver-550`.
- `--output-root <path>`: переопределить каталог, в который будет собран host apt bundle вместо стандартного `host_packages/<distro>/`.
- `--extra-package <name>`: явно добавить пакет в bundle. Флаг можно повторять несколько раз. Нужен в первую очередь для kernel-specific пакетов при пакетной установке драйвера.
- `--check-only`: не скачивать `.deb`, а только проверить prerequisites, доступность repo и разрешение exact package candidates.
- `--dry-run`: показать, что именно планируется скачать, без фактического скачивания пакетов.

Практические примеры:

```bash
bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh \
  --distro ubuntu-22.04 \
  --driver-package nvidia-driver-550

bash deploy/offline_bundle/scripts/build_host_apt_bundle.sh \
  --distro ubuntu-24.04 \
  --extra-package linux-headers-6.8.0-57-generic
```

Source of truth для exact версий:

```text
deploy/offline_bundle/host_packages/<ubuntu-22.04|ubuntu-24.04>/versions.lock.json
```

На оффлайн-сервере эти пакеты должны ставиться только из локального bundle.
При успешной сборке рядом с `pool/` должны существовать также:
- `Packages.gz`
- `Release`

Именно их `install_host_apt_bundle.sh --check-only` теперь тоже валидирует, чтобы audit отвечал не только на вопрос про пакеты на хосте, но и на вопрос про целостность самого локального apt bundle.

## 7. Полная сборка bundle

Минимальный безопасный проход без автоматической пересборки images и без wheel download:

```bash
bash deploy/offline_bundle/scripts/build_bundle.sh
```

После этого проверить:

```bash
python3 deploy/offline_bundle/scripts/generate_manifest.py
python3 deploy/offline_bundle/scripts/validate_bundle.py --mode build
python3 deploy/offline_bundle/scripts/preflight_runtime.py --report-only
docker compose -f deploy/offline_bundle/compose.offline.yaml --env-file deploy/offline_bundle/env.bundle config
pytest deploy/offline_bundle/tests -q
```

Если images уже экспортированы в `images/`, дополнительно прогнать:

```bash
python3 deploy/offline_bundle/scripts/validate_bundle.py --mode deploy
```

## 8. Monitoring profile

Bundle уже содержит:
- `monitoring/prometheus.yml`
- Grafana provisioning
- baseline dashboard

Ничего отдельно скачивать для этих конфигов не нужно. Они поедут вместе с bundle.
