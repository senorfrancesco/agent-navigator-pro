let runtimePaths = {
  native: {
    key: "native",
    name: "Нативный запуск",
    status: "available",
    description: "Локальный developer-runtime с orchestration через launcher, прямым контролем путей моделей и видимостью сервисов.",
    launchSource: "scripts/launcher.sh --target native --profile adaptive",
    configSources: [
      { path: "backend/.env", role: "канонический env runtime и сервисов", freshness: "present" },
      { path: "backend/.env.native", role: "переопределения для нативного пути запуска", freshness: "present" },
      { path: "backend/.env.runtime", role: "сгенерированный применённый план runtime", freshness: "generated" },
      { path: "backend/.env.hardware.override", role: "постоянные overrides размещения", freshness: "present" },
    ],
    summary: [
      { label: "Профили запуска", value: "4 готовы", tone: "lime" },
      { label: "Цепочка env", value: "разрешена", tone: "cyan" },
      { label: "Состояние runtime", value: "стабильно", tone: "lime" },
    ],
    profiles: [
      { title: "Только backend", status: "available", body: "Минимальный путь API и orchestration для проверок workflow и endpoint'ов." },
      { title: "Chainlit dev", status: "available", body: "Путь для operator UI с backend orchestration и локальным session state." },
      { title: "Нативный runtime", status: "available", body: "Полный native launch flow с планом, учитывающим железо." },
      { title: "Полный локальный стек", status: "partial", body: "Запускается, но legal server всё ещё прогревается дольше одного цикла проверки." },
    ],
    whyUnavailable: {
      title: "Доступность нативного запуска",
      checks: [
        ["Python toolchain", "present"],
        ["launcher.sh", "present"],
        ["runtime_preflight.py", "present"],
        ["backend/.env chain", "configured"],
        ["пути model artifact", "configured"],
        ["tmux session support", "present"],
        ["generated runtime plan", "generated"],
      ],
      blockers: [],
      remediation: "Нативный путь запуска доступен. Открой «Конфиг», чтобы посмотреть приоритет источников, или перейди в «Запуск» и выбери профиль.",
    },
  },
  container: {
    key: "container",
    name: "Offline Bundle / контейнерный запуск",
    status: "partial",
    description: "Путь через offline bundle с загрузкой образов, деплоем, проверками parity и artifact-based запуском на сервере.",
    launchSource: "deploy/offline_bundle/scripts/run_offline_bundle.sh",
    configSources: [
      { path: "deploy/offline_bundle/env.bundle", role: "контракт env для bundle", freshness: "present" },
      { path: "deploy/offline_bundle/compose.offline.yaml", role: "топология контейнеров", freshness: "present" },
      { path: "deploy/offline_bundle/manifest.json", role: "manifest артефактов", freshness: "present" },
      { path: "deploy/offline_bundle/runtime_report.json", role: "последний импортированный runtime report", freshness: "stale" },
    ],
    summary: [
      { label: "Профили запуска", value: "1 заблокирован / 2 доступны для проверки", tone: "orange" },
      { label: "Файлы bundle", value: "есть", tone: "lime" },
      { label: "Состояние Docker engine", value: "заблокировано", tone: "orange" },
    ],
    profiles: [
      { title: "Загрузка образов bundle", status: "unavailable", body: "Архивы образов можно загрузить в Docker только когда доступны engine и socket." },
      { title: "Offline bundle runtime", status: "partial", body: "Основной пользовательский путь идёт через run_offline_bundle.sh и deploy.sh, а не через dev compose launcher checkout-репозитория." },
      { title: "Импорт и parity-проверка", status: "partial", body: "Manifest и env bundle доступны для чтения; проверки parity и deploy surface можно изучать ещё до старта runtime." },
    ],
    whyUnavailable: {
      title: "Состояние offline bundle / контейнерного запуска",
      checks: [
        ["deploy/offline_bundle/", "present"],
        ["compose.offline.yaml", "present"],
        ["env.bundle", "present"],
        ["manifest.json", "present"],
        ["runtime report import", "stale"],
        ["Docker engine", "missing"],
        ["Docker socket access", "missing"],
      ],
      blockers: [
        "Bundle-файлы есть, но активный Docker engine не обнаружен.",
        "Профили, которым нужен compose, останутся отключёнными, пока не пройдут проверки сокета и Docker engine.",
      ],
      remediation: "Этот путь запуска ведёт в deploy/offline_bundle/scripts: сначала можно загрузить образы bundle, затем выполнить deploy или run offline bundle без dev-сборки checkout-репозитория.",
    },
  },
};

let hardwareMetrics = [
  { label: "Определённая ОС", value: "Ожидаем состояние оператора", note: "Факты о железе приходят из backend." },
  { label: "Видимость GPU", value: "Неизвестно", note: "Host probe ещё не завершился." },
  { label: "CPU / память", value: "Неизвестно", note: "Host probe ещё не завершился." },
  { label: "Предлагаемый runtime profile", value: "Неизвестно", note: "Рекомендованные значения появятся после загрузки backend state." },
  { label: "Предлагаемый контекст", value: "Неизвестно", note: "Рекомендованные значения появятся после загрузки backend state." },
  { label: "Целевой архив", value: "Неизвестно", note: "Имя архива приходит из backend." },
];

let warnings = [];

let serviceRows = [];

let configState = {
  native: {
    sourceFiles: runtimePaths.native.configSources,
    groups: [
      {
        title: "Режим запуска",
        fields: [
          { key: "UMS_RUNTIME_PROFILE", label: "Профиль runtime", value: "adaptive", suggested: "adaptive", applied: "adaptive", source: "backend/.env.runtime" },
          { key: "DEVICE_MODE", label: "Режим устройства", value: "cpu+gpu hybrid", suggested: "cpu+gpu hybrid", applied: "cpu+gpu hybrid", source: "backend/.env.runtime" },
          { key: "BACKEND_MODE", label: "Режим backend", value: "llama-cpp-python", suggested: "llama-cpp-python", applied: "llama-cpp-python", source: "backend/.env" },
        ],
      },
      {
        title: "Модели и пути",
        fields: [
          { key: "MODEL_REGISTRY_CONFIG_PATH", label: "Конфиг registry моделей", value: "backend/config/models.yaml", suggested: "backend/config/models.yaml", applied: "backend/config/models.yaml", source: "backend/.env" },
          { key: "MODEL_PATH_LLM", label: "Путь LLM артефакта", value: "/models/qwen14b.gguf", suggested: "/models/qwen14b.gguf", applied: "/models/qwen14b.gguf", source: "backend/.env.native" },
          { key: "MODEL_PATH_VLM", label: "Путь VLM артефакта", value: "/models/qwenvl.gguf", suggested: "/models/qwenvl.gguf", applied: "/models/qwenvl.gguf", source: "backend/.env.native" },
          { key: "MODEL_PATH_EMBEDDING_INTENT", label: "Путь embedder для intent", value: "/models/qwen3-embedding-06b", suggested: "/models/qwen3-embedding-06b", applied: "/models/qwen3-embedding-06b", source: "backend/.env.native" },
          { key: "MODEL_PATH_EMBEDDING_RETRIEVAL", label: "Путь embedder для retrieval", value: "/models/labse", suggested: "/models/labse", applied: "/models/labse", source: "backend/.env.native" },
        ],
      },
      {
        title: "Контекст и бюджет",
        fields: [
          { key: "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", label: "Эффективный контекст", value: "16384", suggested: "16384", applied: "16384", source: "backend/.env.runtime" },
          { key: "UMS_RETRIEVED_CONTEXT_RATIO", label: "Доля retrieved context", value: "0.55", suggested: "0.55", applied: "0.55", source: "backend/.env.runtime" },
          { key: "UMS_GENERATION_TOKENS_RESERVE", label: "Резерв generation tokens", value: "1536", suggested: "1536", applied: "1536", source: "backend/.env.runtime" },
        ],
      },
      {
        title: "Размещение и hardware overrides",
        fields: [
          { key: "UMS_LLM_GPU_INDICES", label: "GPU indices для LLM", value: "0", suggested: "0", applied: "0", source: "backend/.env.hardware.override" },
          { key: "UMS_LLM_MIN_FREE_VRAM_GB", label: "Минимум свободной VRAM", value: "10", suggested: "10", applied: "10", source: "backend/.env.hardware.override" },
          { key: "UMS_LLM_MIN_BALANCE_RATIO", label: "Коэффициент balance", value: "0.7", suggested: "0.7", applied: "0.7", source: "backend/.env.hardware.override" },
          { key: "UMS_EMBEDDING_GPU_INDEX", label: "GPU index для embeddings", value: "cpu-fallback", suggested: "cpu-fallback", applied: "cpu-fallback", source: "backend/.env.hardware.override" },
        ],
      },
      {
        title: "Порты и URL",
        fields: [
          { key: "CHAINLIT_PORT", label: "Порт Chainlit", value: "3000", suggested: "3000", applied: "3000", source: "backend/.env" },
          { key: "UMS_PORT", label: "Порт UMS", value: "8090", suggested: "8090", applied: "8090", source: "backend/.env" },
          { key: "DOCUMENT_SERVER_URL", label: "URL document server", value: "http://127.0.0.1:8001", suggested: "http://127.0.0.1:8001", applied: "http://127.0.0.1:8001", source: "backend/.env" },
          { key: "LEGAL_SERVER_URL", label: "URL legal server", value: "http://127.0.0.1:8002", suggested: "http://127.0.0.1:8002", applied: "http://127.0.0.1:8002", source: "backend/.env" },
        ],
      },
      {
        title: "Профили Chainlit",
        fields: [
          { key: "CHAINLIT_DEFAULT_MODEL_PROFILE", label: "Профиль чата по умолчанию", value: "default-chat", suggested: "default-chat", applied: "default-chat", source: "backend/.env" },
          { key: "CHAINLIT_LEGAL_COMPARE_MODEL_PROFILE", label: "Профиль legal compare", value: "legal-compare", suggested: "legal-compare", applied: "legal-compare", source: "backend/.env" },
          { key: "CHAINLIT_LOW_VRAM_MODEL_PROFILE", label: "Профиль для low VRAM", value: "low-vram", suggested: "low-vram", applied: "low-vram", source: "backend/.env" },
        ],
      },
      {
        title: "Сравнение и парсинг",
        fields: [
          { key: "COMPARE_ANALYSIS_MAX_TOKENS", label: "Максимум tokens для compare", value: "1024", suggested: "1024", applied: "1024", source: "backend/.env" },
          { key: "COMPARE_SINGLE_ITEM_STRICT_JSON", label: "Strict JSON для single item", value: "true", suggested: "true", applied: "true", source: "backend/.env" },
          { key: "COMPARE_SINGLE_ITEM_RETRY_COUNT", label: "Повторы для single item", value: "1", suggested: "1", applied: "1", source: "backend/.env" },
        ],
      },
    ],
  },
  container: {
    sourceFiles: runtimePaths.container.configSources,
    groups: [
      {
        title: "Bundle runtime",
        fields: [
          { key: "BUNDLE_RUNTIME_PROFILE", label: "Профиль bundle", value: "default", suggested: "default", applied: "default", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_DEPLOY_MODE", label: "Режим deploy", value: "offline", suggested: "offline", applied: "offline", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_IMPORT_SOURCE", label: "Источник runtime report", value: "runtime_report.json", suggested: "runtime_report.json", applied: "runtime_report.json", source: "deploy/offline_bundle/runtime_report.json" },
        ],
      },
      {
        title: "Compose и topology",
        fields: [
          { key: "COMPOSE_FILE", label: "Файл compose topology", value: "compose.offline.yaml", suggested: "compose.offline.yaml", applied: "compose.offline.yaml", source: "deploy/offline_bundle/compose.offline.yaml" },
          { key: "CHAINLIT_SERVICE_ENABLED", label: "Сервис Chainlit", value: "true", suggested: "true", applied: "true", source: "deploy/offline_bundle/manifest.json" },
          { key: "UMS_SERVICE_ENABLED", label: "Сервис UMS", value: "true", suggested: "true", applied: "true", source: "deploy/offline_bundle/manifest.json" },
        ],
      },
      {
        title: "Порты и внутренние URL",
        fields: [
          { key: "CHAINLIT_PORT", label: "Порт Chainlit", value: "3000", suggested: "3000", applied: "3000", source: "deploy/offline_bundle/env.bundle" },
          { key: "UMS_PORT", label: "Порт UMS", value: "8090", suggested: "8090", applied: "8090", source: "deploy/offline_bundle/env.bundle" },
          { key: "AGENT_API_PORT", label: "Порт Agent API", value: "8000", suggested: "8000", applied: "8000", source: "deploy/offline_bundle/env.bundle" },
        ],
      },
      {
        title: "Точки монтирования артефактов",
        fields: [
          { key: "BUNDLE_MODEL_ROOT", label: "Корень моделей bundle", value: "/opt/agent-nav/models", suggested: "/opt/agent-nav/models", applied: "/opt/agent-nav/models", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_UPLOADS_ROOT", label: "Корень uploads", value: "/opt/agent-nav/uploads", suggested: "/opt/agent-nav/uploads", applied: "/opt/agent-nav/uploads", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_REPORTS_ROOT", label: "Корень reports", value: "/opt/agent-nav/reports", suggested: "/opt/agent-nav/reports", applied: "/opt/agent-nav/reports", source: "deploy/offline_bundle/env.bundle" },
        ],
      },
      {
        title: "Offline validation и parser-контроль",
        fields: [
          { key: "OFFLINE_COMPARE_STRICT_JSON", label: "Strict JSON для offline", value: "true", suggested: "true", applied: "true", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_PREFLIGHT_REQUIRED", label: "Preflight gate", value: "true", suggested: "true", applied: "true", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_PARITY_SMOKE_REQUIRED", label: "Требуется parity smoke", value: "true", suggested: "true", applied: "true", source: "deploy/offline_bundle/env.bundle" },
        ],
      },
    ],
  },
};

let diagnostics = [];

let logLines = [];

let metricsSummary = {
  overview: [],
  services: [],
  deploy: [],
};

let grafanaLinks = {
  base_url: "http://127.0.0.1:3002",
  prometheus_url: "http://127.0.0.1:9090",
  links: [],
};

let deploySurface = {
  build: {
    label: "Сборка bundle",
    summaryTitle: "Состояние сборки bundle",
    stagesTitle: "Стадии сборки",
    actionsTitle: "Действия сборки",
    artifactsTitle: "Выходные артефакты и упаковка архива",
    logsTitle: "Лог сборки и экспорта",
    summary: [
      { label: "Корень bundle", value: "deploy/offline_bundle", note: "Канонический workspace для подключённой сборки.", tone: "cyan" },
      { label: "Целевой архив", value: "agent-nav-offline-bundle_v1.0.tar.gz", note: "Единый переносимый артефакт для передачи.", tone: "lime" },
      { label: "Матрица host apt", value: "ubuntu-24.04 готов", note: "Собрано через локальный apt bundle flow.", tone: "lime" },
      { label: "Состояние manifest", value: "проверен", note: "Выход готов к deploy и проходит bundle validation.", tone: "lime" },
    ],
    sources: [
      { path: "deploy/offline_bundle/scripts/build_bundle.sh", role: "верхнеуровневый оркестратор сборки и экспорта", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/build_host_apt_bundle.sh", role: "сборка host APT bundle", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/export_images.sh", role: "сборка и экспорт Docker images", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/generate_manifest.py", role: "генерация manifest", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/validate_bundle.py", role: "проверка готовности к deploy", freshness: "present" },
    ],
    stages: [
      { title: "Wheelhouse", status: "running", body: "Подготовить offline Python wheelhouse перед экспортом images.", script: "build_wheelhouse.sh" },
      { title: "Образы", status: "running", body: "Собрать backend/UMS/Chainlit images и экспортировать их в tar-архивы.", script: "export_images.sh" },
      { title: "Модели", status: "running", body: "Экспортировать layout моделей для env.bundle и manifest.", script: "export_models.sh" },
      { title: "Состояние", status: "running", body: "Экспортировать runtime env и persisted state в bundle/state.", script: "export_state.sh" },
      { title: "Host packages", status: "running", body: "Собрать apt bundle с точными версиями для целевого Ubuntu.", script: "build_host_apt_bundle.sh" },
      { title: "Упаковка архива", status: "partial", body: "Упаковать весь deploy/offline_bundle в единый tar.gz артефакт.", script: "tar czf ..." },
    ],
    actions: [
      { title: "Собрать bundle", body: "Запустить каноническую orchestration сборки и экспорта.", action: "Собрать bundle" },
      { title: "Собрать host APT bundle", body: "Подготовить пакеты Ubuntu и versions lock для offline install.", action: "Собрать host APT bundle" },
      { title: "Экспортировать images", body: "Собрать offline images и сохранить их как image-архивы.", action: "Экспортировать images" },
      { title: "Сгенерировать manifest", body: "Сгенерировать manifest и проверить полноту готовности к deploy.", action: "Сгенерировать manifest" },
      { title: "Упаковать tar.gz", body: "Создать единый переносимый архив из deploy/offline_bundle.", action: "Упаковать tar.gz" },
      { title: "Посмотреть логи сборки", body: "Открыть stage-oriented output сборки и экспорта.", action: "Посмотреть логи сборки" },
    ],
    artifacts: [
      { title: "Архив bundle", body: "agent-nav-offline-bundle_v1.0.tar.gz · 18.6 GB · готов к передаче", badge: "ready" },
      { title: "Архивы образов", body: "backend-app, ums, chainlit, vllm сохранены в deploy/offline_bundle/images", badge: "ready" },
      { title: "Пакет host packages", body: "ubuntu-24.04 pool + Packages.gz + versions.lock.json", badge: "ready" },
      { title: "Manifest", body: "manifest.json содержит checksums для images, state, models, wheelhouse, host_packages", badge: "ready" },
    ],
  },
  import: {
    label: "Импорт / деплой",
    summaryTitle: "Состояние импорта / деплоя",
    stagesTitle: "Стадии на целевом host",
    actionsTitle: "Действия импорта и деплоя",
    artifactsTitle: "Приём архива и распакованное состояние bundle",
    logsTitle: "Лог импорта / деплоя",
    summary: [
      { label: "Приём архива", value: "выбран", note: "Один tar.gz принимается как канонический переносимый артефакт.", tone: "lime" },
      { label: "Корень распаковки", value: "/opt/agent-nav/offline_bundle", note: "Bundle распакован в целевой runtime root.", tone: "cyan" },
      { label: "Состояние deploy", value: "частично", note: "Bundle проходит проверку, но host runtime ещё ждёт install пакетов и загрузку images.", tone: "orange" },
      { label: "Канонический entrypoint", value: "deploy.sh", note: "run_offline_bundle.sh остаётся верхнеуровневой обёрткой для удобного запуска.", tone: "cyan" },
    ],
    sources: [
      { path: "deploy/offline_bundle/scripts/install_host_apt_bundle.sh", role: "offline install и проверка host packages", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/deploy.sh", role: "каноническая deploy orchestration", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/load_images.sh", role: "импорт Docker images", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/restore_state.sh", role: "восстановление state bundle", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/run_offline_bundle.sh", role: "обёртка верхнего уровня для удобного запуска", freshness: "present" },
    ],
    stages: [
      { title: "Приём архива", status: "running", body: "Принять tar.gz артефакт и зарегистрировать метаданные импорта.", script: "UI archive selector" },
      { title: "Распаковка", status: "running", body: "Распаковать bundle в целевой offline bundle root.", script: "tar xzf ..." },
      { title: "Проверка bundle", status: "running", body: "Проверить env, models, manifest, images и host packages.", script: "validate_bundle.py --mode deploy" },
      { title: "Установка host apt bundle", status: "partial", body: "Установить или проверить offline host packages для обнаруженного distro.", script: "install_host_apt_bundle.sh" },
      { title: "Загрузка images", status: "partial", body: "Загрузить docker images из bundle images/*.tar архивов.", script: "load_images.sh" },
      { title: "Деплой и проверка", status: "partial", body: "Восстановить state, запустить deploy.sh и затем проверить runtime.", script: "deploy.sh + verify_runtime.sh" },
    ],
    actions: [
      { title: "Выбрать архив", body: "Выбрать один offline bundle tar.gz артефакт.", action: "Выбрать архив" },
      { title: "Распаковать bundle", body: "Распаковать архив и подготовить целевой bundle root.", action: "Распаковать bundle" },
      { title: "Проверить host packages", body: "Запустить проверку host packages перед установкой.", action: "Проверить host packages" },
      { title: "Установить host APT bundle", body: "Установить offline host packages точной версии и runtime configuration.", action: "Установить host APT bundle" },
      { title: "Деплоить runtime", body: "Запустить канонический deploy flow: image load, restore state, compose up и verification.", action: "Деплоить runtime" },
      { title: "Запустить offline bundle", body: "Использовать верхнеуровневый launcher после прохождения prerequisite deploy.", action: "Запустить offline bundle" },
    ],
    artifacts: [
      { title: "Приём архива", body: "agent-nav-offline-bundle_v1.0.tar.gz получен с build-машины", badge: "ready" },
      { title: "Распакованный bundle", body: "compose.offline.yaml, env.bundle, manifest.json, images/, models/, state/ доступны", badge: "ready" },
      { title: "Установка host package", body: "versions.lock.json совпадает с target distro, install ещё ждёт runtime configure", badge: "partial" },
      { title: "Runtime deploy", body: "Ожидается image load и выполнение deploy.sh на целевом host", badge: "partial" },
    ],
  },
};

let deployLogLines = [
  { mode: "build", stage: "build", text: "[10:04:03] build_bundle: starting export orchestration in deploy/offline_bundle" },
  { mode: "build", stage: "wheelhouse", text: "[10:04:18] build_wheelhouse: ok wheelhouse prepared for offline image builds" },
  { mode: "build", stage: "images", text: "[10:06:10] export_images: saved agent-nav-backend-app-offline_v1.0.tar" },
  { mode: "build", stage: "host", text: "[10:07:42] build_host_apt_bundle: host-apt-bundle:ok distro=ubuntu-24.04" },
  { mode: "build", stage: "manifest", text: "[10:08:05] generate_manifest: manifest.json refreshed with checksums and build metadata" },
  { mode: "build", stage: "pack", text: "[10:08:34] archive: created agent-nav-offline-bundle_v1.0.tar.gz from deploy/offline_bundle" },
  { mode: "import", stage: "archive", text: "[11:12:01] intake: selected agent-nav-offline-bundle_v1.0.tar.gz for import" },
  { mode: "import", stage: "archive", text: "[11:12:08] unpack: extracted archive into /opt/agent-nav/offline_bundle" },
  { mode: "import", stage: "validate", text: "[11:12:25] validate_bundle: mode=deploy passed required files, images, models, host_packages" },
  { mode: "import", stage: "host", text: "[11:13:10] install_host_apt_bundle: host-apt-install-check:ok ubuntu-24.04" },
  { mode: "import", stage: "images", text: "[11:14:42] load_images: loaded backend-app, ums, chainlit archives into local docker image store" },
  { mode: "import", stage: "deploy", text: "[11:15:26] deploy.sh: check_host -> validate_bundle -> preflight_runtime -> load_images -> restore_state -> compose up -> verify_runtime" },
];

let maintenanceActions = [
  { title: "Запустить smoke", body: "Выполнить разрешённые smoke-checks для выбранного пути запуска и обновить summary состояния.", action: "Запустить smoke" },
  { title: "Перезагрузить источники конфига", body: "Перечитать mapped env/config файлы и пересчитать effective values для текущего пути.", action: "Перезагрузить источники конфига" },
  { title: "Пересчитать пути запуска", body: "Снова собрать availability для native/container из Python operator API.", action: "Пересчитать пути запуска" },
  { title: "Перезагрузить deploy surface", body: "Перечитать build/import/deploy surface из Python operator API.", action: "Перезагрузить deploy surface" },
  { title: "Показать parser issues", body: "Открыть parser diagnostics и summary по strict JSON failure для текущего пути.", action: "Показать parser issues" },
  { title: "Очистка / repair", body: "Запустить безопасные repair helpers для caches, stale state и generated summary files.", action: "Очистка / repair" },
];

let blockers = [];

let activityFeed = [];

let activeSection = "overview";
let selectedConfigPath = "native";
let selectedServicesPath = "native";
let selectedLaunchPath = "native";
let selectedDeployMode = "build";
let launchMenuOpen = false;
let dirtyFields = new Set();
let stagedFieldValues = new Map();
let revealedSecretFields = new Set();
let hiddenSecretFields = new Set();
let selectedConfigVariantByPath = {
  native: "runtime",
  container: "local_safe_ports",
};
let actionCatalog = [];
let actionCatalogById = {};
let actionCatalogByTitle = {};
let jobPollTimers = new Map();
let jobLogOffsets = new Map();
let toastTimeouts = new Map();
let currentLanguage = localStorage.getItem("operatorUiLanguage") || "ru";
let uiSettings = {
  showLiteralEnvKeys: localStorage.getItem("operatorUiShowLiteralEnvKeys") !== "false",
  showSourceFiles: localStorage.getItem("operatorUiShowSourceFiles") !== "false",
  showSecretValues: false,
  metricsDensity: localStorage.getItem("operatorUiMetricsDensity") || "compact",
};
let controlPlaneOnline = false;
let runningJobs = new Map();
let lastJobSummary = null;
let runtimeHealthByPath = {};

const sectionButtons = [...document.querySelectorAll(".nav-item")];
const sections = {
  overview: document.querySelector("#section-overview"),
  launch: document.querySelector("#section-launch"),
  config: document.querySelector("#section-config"),
  services: document.querySelector("#section-services"),
  deploy: document.querySelector("#section-deploy"),
  maintenance: document.querySelector("#section-maintenance"),
};

function chipClass(status) {
  if (["available", "running", "present", "configured", "generated", "ready", "passed"].includes(status)) return "lime";
  if (["partial", "degraded", "stale", "readable", "warmup"].includes(status)) return "cyan";
  if (["blocked", "unavailable", "missing"].includes(status)) return "orange";
  return "neutral";
}

function cap(value) {
  const translated = currentLanguage === "en" ? {
    available: "Ready",
    running: "Running",
    present: "Present",
    configured: "Configured",
    generated: "Generated",
    ready: "Ready",
    passed: "Passed",
    partial: "Partial",
    degraded: "Degraded",
    stale: "Stale",
    readable: "Readable",
    warmup: "Warmup",
    blocked: "Blocked",
    unavailable: "Unavailable",
    missing: "Missing",
    healthy: "Healthy",
    watch: "Watch",
    informational: "Info",
    attention: "Attention",
    info: "Info",
    needs: "Needs attention",
    completed: "Completed",
    failed: "Failed",
    done: "Done",
  } : {
    available: "Готово",
    running: "В работе",
    present: "Есть",
    configured: "Настроено",
    generated: "Сгенерировано",
    ready: "Готово",
    passed: "Пройдено",
    partial: "Частично",
    degraded: "Снижено",
    stale: "Устарело",
    readable: "Доступно",
    warmup: "Прогрев",
    blocked: "Заблокировано",
    unavailable: "Недоступно",
    missing: "Отсутствует",
    healthy: "Хорошо",
    watch: "Наблюдение",
    informational: "К сведению",
    attention: "Внимание",
    info: "К сведению",
    needs: "Требует внимания",
    completed: "Завершено",
    failed: "Ошибка",
    done: "Готово",
  };
  if (translated[String(value)]) {
    return translated[String(value)];
  }
  if (String(value) === "Unknown") {
    return currentLanguage === "en" ? "Unknown" : "Неизвестно";
  }
  return String(value)
    .split(/[\s_-]+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function pathTitle(path) {
  if (!path) return currentLanguage === "en" ? "Runtime Path" : "Путь запуска";
  if (currentLanguage === "en" && path.nameEn) return path.nameEn;
  if (path.key === "native") return currentLanguage === "en" ? "Native Runtime" : "Нативный запуск";
  if (path.key === "container") return currentLanguage === "en" ? "Offline Bundle / Containers" : "Offline Bundle / Контейнеры";
  return path.name;
}

function localizedField(item, key) {
  if (!item) return "";
  if (currentLanguage === "en" && item[`${key}En`]) return item[`${key}En`];
  return item[key];
}

function displayLabel(label) {
  const labels = currentLanguage === "en" ? {
    "Detected host OS": "Detected Host OS",
    "Определённая ОС": "Detected Host OS",
    "GPU visibility": "GPU Visibility",
    "Видимость GPU": "GPU Visibility",
    "GPU inventory": "GPU Inventory",
    "Инвентарь GPU": "GPU Inventory",
    "CPU / memory": "CPU / Memory",
    "CPU / память": "CPU / Memory",
    "Suggested runtime profile": "Suggested Runtime Profile",
    "Предлагаемый профиль runtime": "Suggested Runtime Profile",
    "Suggested context budget": "Suggested Context Budget",
    "Предлагаемый бюджет контекста": "Suggested Context Budget",
    "Bundle archive target": "Bundle Archive Target",
    "Целевой архив": "Bundle Archive Target",
    "Runtime Preflight": "Runtime Preflight",
    "Launcher": "Launcher",
    "Bundle Manifest": "Bundle Manifest",
    "Manifest bundle": "Bundle Manifest",
    "Docker Socket": "Docker Socket",
    "Docker socket": "Docker Socket",
    "Local Safe Ports": "Local Safe Ports",
    "Target Default Ports": "Target Default Ports",
    "Adaptive recommended": "Adaptive Recommended",
    "CPU fallback": "CPU Fallback",
    "Single GPU": "Single GPU",
    "Multi GPU": "Multi GPU",
    "Parser safe mode": "Parser Safe Mode",
    "Parser strict mode": "Parser Strict Mode",
    "Профили запуска": "Launch Profiles",
    "Цепочка env": "env Chain",
    "Состояние runtime": "Runtime State",
    "Файлы bundle": "Bundle Files",
    "Состояние Docker engine": "Docker Engine State",
    "Сборка bundle": "Build Bundle",
    "Импорт / деплой": "Import / Deploy",
    "Действия сборки": "Build Actions",
    "Действия импорта и деплоя": "Import and Deploy Actions",
    "Стадии сборки": "Build Stages",
    "Стадии на целевом host": "Target Host Stages",
    "Лог сборки и экспорта": "Build and Export Log",
    "Лог импорта / деплоя": "Import and Deploy Log",
    "Порт Chainlit": "Chainlit Port",
    "Порт UMS": "UMS Port",
    "Порт Agent API": "Agent API Port",
    "Порт Prometheus": "Prometheus Port",
    "Порт Grafana": "Grafana Port",
    "Bundle model root": "Bundle Model Root",
    "Uploads root": "Uploads Root",
    "Reports root": "Reports Root",
    "Grafana admin user": "Grafana Admin User",
    "Grafana admin password": "Grafana Admin Password",
    "Offline strict JSON": "Offline Strict JSON",
    "Preflight required": "Preflight Required",
    "Parity smoke required": "Parity Smoke Required",
  } : {
    "Detected host OS": "Определённая ОС",
    "GPU visibility": "Видимость GPU",
    "GPU inventory": "Инвентарь GPU",
    "CPU / memory": "CPU / память",
    "Suggested runtime profile": "Предлагаемый профиль runtime",
    "Suggested context budget": "Предлагаемый бюджет контекста",
    "Bundle archive target": "Целевой архив",
    "Runtime Preflight": "Предпроверка runtime",
    "Launcher": "Launcher",
    "Bundle Manifest": "Manifest bundle",
    "Docker Socket": "Docker socket",
    "Runtime profile": "Профиль runtime",
    "Device mode": "Режим устройства",
    "Backend mode": "Режим backend",
    "Model registry config": "Конфиг registry моделей",
    "LLM artifact path": "Путь LLM артефакта",
    "VLM artifact path": "Путь VLM артефакта",
    "Intent embedder path": "Путь intent-embedder",
    "Retrieval embedder path": "Путь retrieval-embedder",
    "Effective context tokens": "Эффективный контекст",
    "Retrieved context ratio": "Доля retrieved context",
    "Generation reserve": "Резерв generation tokens",
    "LLM GPU indices": "GPU indices для LLM",
    "Minimum free VRAM": "Минимум свободной VRAM",
    "Balance ratio": "Коэффициент balance",
    "Embedding GPU index": "GPU index для embeddings",
    "Chainlit port": "Порт Chainlit",
    "UMS port": "Порт UMS",
    "Document server URL": "URL document server",
    "Legal server URL": "URL legal server",
    "Default chat profile": "Профиль чата по умолчанию",
    "Legal compare profile": "Профиль legal compare",
    "Low VRAM profile": "Профиль low VRAM",
    "Compare analysis max tokens": "Максимум tokens для compare",
    "Single-item strict JSON": "Strict JSON для single item",
    "Single-item retry count": "Повторы для single item",
    "Runtime / Profile": "Режим запуска",
    "Model Registry & Paths": "Модели и пути",
    "GPU / Placement": "GPU / размещение",
    "Ports & URLs": "Порты и URL",
    "Chainlit Profiles": "Профили Chainlit",
    "Parsing / Compare": "Парсинг / compare",
    "Local Safe Ports": "Безопасные локальные порты",
    "Target Default Ports": "Целевые порты",
    "Monitoring": "Мониторинг",
    "Artifact Mounts": "Артефакты и пути",
    "Agent API port": "Порт Agent API",
    "Prometheus port": "Порт Prometheus",
    "Grafana port": "Порт Grafana",
    "Grafana admin user": "Пользователь Grafana",
    "Grafana admin password": "Пароль Grafana",
    "Offline strict JSON": "Offline strict JSON",
    "Preflight required": "Обязательный preflight",
    "Parity smoke required": "Обязательный parity smoke",
    "Adaptive recommended": "Адаптивный режим",
    "CPU fallback": "CPU fallback",
    "Single GPU": "Одна GPU",
    "Multi GPU": "Несколько GPU",
    "Parser safe mode": "Безопасный парсинг",
    "Parser strict mode": "Строгий парсинг",
    "Launch Profiles": "Профили запуска",
    "env Chain": "Цепочка env",
    "Runtime State": "Состояние runtime",
    "Bundle Files": "Файлы bundle",
    "Docker Engine State": "Состояние Docker engine",
    "Build Bundle": "Сборка bundle",
    "Import / Deploy": "Импорт / деплой",
    "Build Actions": "Действия сборки",
    "Import and Deploy Actions": "Действия импорта и деплоя",
    "Build Stages": "Стадии сборки",
    "Target Host Stages": "Стадии на целевом host",
    "Build and Export Log": "Лог сборки и экспорта",
    "Import and Deploy Log": "Лог импорта / деплоя",
  };
  return labels[label] || label;
}

function displayText(text) {
  const texts = currentLanguage === "en" ? {
    "канонический env runtime и сервисов": "canonical env for runtime and services",
    "переопределения для нативного пути запуска": "overrides for the native runtime path",
    "сгенерированный применённый план runtime": "generated applied runtime plan",
    "постоянные overrides размещения": "persistent placement overrides",
    "контракт env для bundle": "env contract for the bundle",
    "топология контейнеров": "container topology",
    "manifest артефактов": "artifact manifest",
    "экспортированное env-состояние runtime": "exported runtime env state",
    "разрешена": "resolved",
    "стабильно": "stable",
    "есть": "present",
    "доступен": "available",
    "отсутствует": "missing",
    "заблокирован": "blocked",
    "Только backend": "Backend only",
    "Нативный runtime": "Native runtime",
    "Полный локальный стек": "Full local stack",
    "Загрузка образов bundle": "Bundle image loading",
    "Импорт и parity-проверка": "Import and parity validation",
    "Минимальный API + orchestration flow для workflow и endpoint checks.": "Minimal API and orchestration flow for workflow and endpoint checks.",
    "Путь operator UI с backend orchestration и локальным session state.": "Operator UI path with backend orchestration and local session state.",
    "Полный native launcher flow с hardware-aware планом.": "Full native launcher flow with a hardware-aware plan.",
    "Запускается, но warmup и готовность сервисов могут отставать на один цикл проверки.": "Can start, but warmup and service readiness may lag by one probe cycle.",
    "Канонический вход для preflight и planning нативного запуска.": "Canonical entrypoint for native runtime preflight and planning.",
    "Канонический launcher для нативного и контейнерного путей запуска.": "Canonical launcher for native and container runtime paths.",
    "Manifest артефактов для deploy/offline_bundle.": "Artifact manifest for deploy/offline_bundle.",
    "Запуск контейнеров и загрузка images зависят от доступа к Docker socket на host.": "Container startup and image loading depend on Docker socket access on the host.",
    "проверка файловой системы": "filesystem probe",
    "Готово": "Ready",
    "Заблокировано": "Blocked",
    "Читается": "Readable",
    "Отсутствует": "Missing",
    "Состояние bundle видно сразу, а запуск контейнеров зависит от Docker engine и доступа к socket.": "Bundle state is visible immediately, while container startup still depends on Docker engine and socket access.",
    "Проверки native и host probes берутся из репозитория.": "Native checks and host probes are derived from repository state.",
    "Нет предупреждений": "No warnings",
    "Backend не сообщил о дополнительных блокерах runtime сверх текущего состояния доступности.": "Backend has not reported additional runtime blockers beyond the current availability state.",
    "Пока нет активности оператора": "No operator activity yet",
    "Здесь появятся действия, применение конфига и завершённые jobs.": "Actions, config applies, and completed jobs will appear here.",
    "Факты о железе собираются на стороне backend.": "Host facts are gathered server-side.",
    "Используется для объяснения возможностей runtime и предлагаемых значений.": "Used to explain runtime capabilities and suggested defaults.",
    "Все обнаруженные NVIDIA GPU перечислены для multi-GPU planning.": "All detected NVIDIA GPUs are listed for multi-GPU aware planning.",
    "Нужно для оценки размеров runtime и bundle.": "Used for runtime and bundle sizing context.",
    "Соответствует каноническому flow запуска для применённого плана.": "Matches the canonical launcher flow for the applied plan.",
    "Отражает текущее применённое или рекомендованное значение оператора.": "Mirrors the current applied or suggested operator value.",
    "Единый переносимый tar.gz остаётся предпочтительным артефактом.": "A single portable tar.gz remains the preferred artifact.",
    "Ссылки на Grafana и Prometheus появятся после загрузки backend-сводки наблюдаемости.": "Grafana and Prometheus links appear after backend observability state loads.",
    "Сводка Prometheus для `agent_api` и `UMS` появится после загрузки backend state.": "Prometheus summary for `agent_api` and `UMS` appears after backend state loads.",
    "Панель наблюдаемости для сборки и деплоя появится после загрузки backend-сводки.": "Observability for build and deploy appears after backend summary loads.",
    "Для этого пути запуска ещё нет загруженных логов. Сначала проверь диагностику и состояние сервисов выше.": "No logs have been loaded for this runtime path yet. Check diagnostics and service status first.",
    "Backend ещё не опубликовал проверки для этого пути запуска.": "Backend has not published service checks for this runtime path yet.",
    "Backend ещё не сообщил блокеры парсинга или запуска для этого пути.": "Backend has not reported parser or runtime blockers for this path yet.",
    "Все статусы, логи и диагностика ниже относятся к этому пути запуска.": "All statuses, logs, and diagnostics below apply to this runtime path.",
    "Grafana и Prometheus доступны рядом с метриками, без ухода в отдельный раздел.": "Grafana and Prometheus stay next to metrics instead of hiding in a separate area.",
    "Ниже показаны только этапы, действия и артефакты текущего режима.": "Only the current mode's stages, actions, and artifacts are shown below.",
    "Сначала проверяй сводку и этапы, затем уже отдельные действия и лог.": "Check summary and stages first, then move to actions and logs.",
    "Кнопки режима привязаны к allowlisted operator-командам и Python job model.": "Mode actions map to allowlisted operator commands and the Python job model.",
    "Открывай Grafana или Prometheus прямо из режима сборки и деплоя.": "Open Grafana or Prometheus directly from build and deploy mode.",
    "Локальный developer-runtime с запуском через launcher, управлением путями моделей и видимостью сервисов.": "Local developer runtime launched through the canonical launcher with model-path control and service visibility.",
    "Локальный developer-runtime с orchestration через launcher, прямым контролем путей моделей и видимостью сервисов.": "Local developer runtime with launcher orchestration, direct model-path control, and visible service state.",
    "Путь запуска через bundle и compose для offline rollout, проверки parity и artifact-based deployment.": "Runtime path through bundle and compose for offline rollout, parity checks, and artifact-based deployment.",
    "Путь через offline bundle с загрузкой образов, деплоем, проверками parity и artifact-based запуском на сервере.": "Offline bundle path for image loading, deploy, parity checks, and artifact-based server startup.",
    "Нативный путь запуска доступен. Открой «Конфиг», чтобы посмотреть приоритет источников, или перейди в «Запуск» и выбери профиль.": "Native runtime is available. Open Config to inspect source precedence or go to Launch and choose a profile.",
    "Профиль для локального запуска рядом с текущим operator UI без конфликта published ports.": "Profile for local bundle execution next to the current operator UI without published-port conflicts.",
    "Подставляет безопасные локальные порты для запуска рядом с текущим operator UI.": "Stages safe local ports for running the bundle next to the current operator UI.",
    "Рекомендуется для локального теста bundle на той же машине, где уже работает operator UI.": "Recommended when testing the bundle on the same machine where the operator UI already runs.",
    "Контейнерный деплой делит порт с текущим operator UI": "Container deploy shares a port with the current operator UI",
    "Если запускать bundle из этого же локального backend на `8000`, контейнерный `agent-api` попытается занять тот же порт. Для безопасного запуска нужен другой порт или отдельное окружение.": "If the bundle starts from this same local backend on `8000`, its container `agent-api` will try to take the same port. Use a different port profile or a separate environment.",
    "Профиль запуска, backend mode и общий режим устройства.": "Launch profile, backend mode, and overall device mode.",
    "Пути к моделям и registry-конфигу. Здесь обычно правки ручные, но рекомендации видны рядом.": "Model paths and registry config. These fields are usually edited manually, with recommendations shown next to each field.",
    "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.": "Model placement and GPU limits. Some values come from presets, others remain manual.",
    "Порты локального native path и service URLs.": "Ports for the local native path and service URLs.",
    "Профили Chainlit. Обычно редактируются вручную по конкретному workflow.": "Chainlit profiles. These are usually edited manually for a specific workflow.",
    "Параметры strict JSON и compare path. Здесь полезны safe/strict пресеты.": "Strict JSON and compare-path settings. Safe and strict presets are useful here.",
    "Канонический портовый профиль для target-host и offline release contract.": "Canonical port profile for the target host and offline release contract.",
    "Настройки observability для Prometheus и Grafana внутри offline bundle.": "Observability settings for Prometheus and Grafana inside the offline bundle.",
    "Пути к моделям, uploads и reports внутри offline bundle layout.": "Paths for models, uploads, and reports inside the offline bundle layout.",
    "Offline parser/compare contract внутри env.bundle.": "Offline parser and compare contract inside env.bundle.",
    "Обычно должен указывать на repo-local models.yaml.": "Usually points to the repository-local models.yaml.",
    "Укажи основной LLM artifact для runtime path.": "Set the main LLM artifact for this runtime path.",
    "Заполняется только если multimodal path реально используется.": "Fill this only when the multimodal path is actually used.",
    "Должен указывать на intent embedder текущего runtime path.": "Should point to the intent embedder used by the current runtime path.",
    "Должен указывать на retrieval embedder текущего runtime path.": "Should point to the retrieval embedder used by the current runtime path.",
    "Путь должен совпадать с runtime layout bundle.": "This path must stay aligned with the bundle runtime layout.",
    "Основной пользовательский путь идёт через run_offline_bundle.sh и deploy.sh, а не через dev compose launcher checkout-репозитория.": "The main user path goes through run_offline_bundle.sh and deploy.sh, not through the dev compose launcher for the checkout repository.",
    "Архивы образов можно загружать в Docker только когда доступны engine и socket.": "Bundle image archives can be loaded into Docker only when the engine and socket are available.",
    "Manifest, env.bundle и deploy surface читаются даже когда сам runtime ещё не поднят.": "Manifest, env.bundle, and the deploy surface remain readable even before the runtime is up.",
    "Контейнерный путь запуска ведёт в deploy/offline_bundle/scripts: сначала можно загрузить образы bundle, затем выполнить deploy или run offline bundle без dev-сборки checkout-репозитория.": "The container runtime path points to deploy/offline_bundle/scripts: first load bundle images, then deploy or run the offline bundle without a dev build from the checkout repository.",
    "Resolved host path exists:": "Resolved host path exists:",
    "Resolved host path does not exist yet:": "Resolved host path does not exist yet:",
    "This value is interpreted inside the offline bundle/container layout.": "This value is interpreted inside the offline bundle/container layout.",
    "Value is empty and must be set explicitly.": "Value is empty and must be set explicitly.",
  } : {
    "Host facts are gathered server-side for the operator UI.": "Факты о железе собираются на стороне backend.",
    "Used to explain runtime capabilities and suggested defaults.": "Используется для объяснения возможностей runtime и предлагаемых значений.",
    "All detected NVIDIA devices are listed for multi-GPU aware planning.": "Все обнаруженные NVIDIA GPU перечислены для multi-GPU planning.",
    "Used for runtime and bundle sizing context.": "Нужно для оценки размеров runtime и bundle.",
    "Matches the canonical launcher flow for generated applied plans.": "Соответствует каноническому flow запуска для применённого плана.",
    "Mirrors the current applied or suggested operator value.": "Отражает текущее применённое или рекомендованное значение оператора.",
    "Single portable tar.gz is the preferred build artifact.": "Единый переносимый tar.gz остаётся предпочтительным артефактом.",
    "Canonical native/runtime planning entrypoint exposed through operator summary.": "Канонический вход планирования native runtime, видимый в сводке оператора.",
    "Canonical user-facing launcher for native and container paths.": "Канонический launcher для нативного и контейнерного пути.",
    "Artifact manifest for deploy/offline_bundle.": "Manifest артефактов для deploy/offline_bundle.",
    "Container launch and image load depend on host docker socket access.": "Запуск контейнеров и загрузка images зависят от доступа к Docker socket.",
    "canonical env for runtime and services": "канонический env runtime и сервисов",
    "overrides for the native runtime path": "переопределения для нативного пути запуска",
    "generated applied runtime plan": "сгенерированный применённый план runtime",
    "persistent placement overrides": "постоянные overrides размещения",
    "env contract for the bundle": "контракт env для bundle",
    "container topology": "топология контейнеров",
    "artifact manifest": "manifest артефактов",
    "exported runtime env state": "экспортированное env-состояние runtime",
    "resolved": "разрешена",
    "stable": "стабильно",
    "present": "есть",
    "available": "доступен",
    "missing": "отсутствует",
    "blocked": "заблокирован",
    "Backend only": "Только backend",
    "Native runtime": "Нативный runtime",
    "Full local stack": "Полный локальный стек",
    "Bundle image loading": "Загрузка образов bundle",
    "Import and parity validation": "Импорт и parity-проверка",
    "Minimal API and orchestration flow for workflow and endpoint checks.": "Минимальный API + orchestration flow для workflow и endpoint checks.",
    "Operator UI path with backend orchestration and local session state.": "Путь operator UI с backend orchestration и локальным session state.",
    "Full native launcher flow with a hardware-aware plan.": "Полный native launcher flow с hardware-aware планом.",
    "Can start, but warmup and service readiness may lag by one probe cycle.": "Запускается, но warmup и готовность сервисов могут отставать на один цикл проверки.",
    "Canonical entrypoint for native runtime preflight and planning.": "Канонический вход для preflight и planning нативного запуска.",
    "Canonical launcher for native and container runtime paths.": "Канонический launcher для нативного и контейнерного путей запуска.",
    "Artifact manifest for deploy/offline_bundle.": "Manifest артефактов для deploy/offline_bundle.",
    "filesystem probe": "проверка файловой системы",
    "Readable": "Читается",
    "Missing": "Отсутствует",
    "Bundle state is visible immediately, while container startup still depends on Docker engine and socket access.": "Состояние bundle видно сразу, а запуск контейнеров зависит от Docker engine и доступа к socket.",
    "Native checks and host probes are derived from repository state.": "Проверки native и host probes берутся из репозитория.",
    "No warnings": "Нет предупреждений",
    "Backend has not reported additional runtime blockers beyond the current availability state.": "Backend не сообщил о дополнительных блокерах runtime сверх текущего состояния доступности.",
    "No operator activity yet": "Пока нет активности оператора",
    "Actions, config applies, and completed jobs will appear here.": "Здесь появятся действия, применение конфига и завершённые jobs.",
    "Local developer runtime launched through the canonical launcher with model-path control and service visibility.": "Локальный developer-runtime с запуском через launcher, управлением путями моделей и видимостью сервисов.",
    "Local developer runtime with launcher orchestration, direct model-path control, and visible service state.": "Локальный developer-runtime с orchestration через launcher, прямым контролем путей моделей и видимостью сервисов.",
    "Runtime path through bundle and compose for offline rollout, parity checks, and artifact-based deployment.": "Путь запуска через bundle и compose для offline rollout, проверки parity и artifact-based deployment.",
    "Native runtime is available. Open Config to inspect source precedence or go to Launch and choose a profile.": "Нативный путь запуска доступен. Открой «Конфиг», чтобы посмотреть приоритет источников, или перейди в «Запуск» и выбери профиль.",
    "Profile for local bundle execution next to the current operator UI without published-port conflicts.": "Профиль для локального запуска рядом с текущим operator UI без конфликта published ports.",
    "Stages safe local ports for running the bundle next to the current operator UI.": "Подставляет безопасные локальные порты для запуска рядом с текущим operator UI.",
    "Recommended when testing the bundle on the same machine where the operator UI already runs.": "Рекомендуется для локального теста bundle на той же машине, где уже работает operator UI.",
    "Container deploy shares a port with the current operator UI": "Контейнерный деплой делит порт с текущим operator UI",
    "If the bundle starts from this same local backend on `8000`, its container `agent-api` will try to take the same port. Use a different port profile or a separate environment.": "Если запускать bundle из этого же локального backend на `8000`, контейнерный `agent-api` попытается занять тот же порт. Для безопасного запуска нужен другой порт или отдельное окружение.",
    "Профиль запуска, backend mode и общий режим устройства.": "Профиль запуска, backend mode и общий режим устройства.",
    "Пути к моделям и registry-конфигу. Здесь обычно правки ручные, но рекомендации видны рядом.": "Пути к моделям и registry-конфигу. Здесь обычно правки ручные, но рекомендации видны рядом.",
    "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.": "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.",
    "Порты локального native path и service URLs.": "Порты локального native path и service URLs.",
    "Профили Chainlit. Обычно редактируются вручную по конкретному workflow.": "Профили Chainlit. Обычно редактируются вручную по конкретному workflow.",
    "Параметры strict JSON и compare path. Здесь полезны safe/strict пресеты.": "Параметры strict JSON и compare path. Здесь полезны safe/strict пресеты.",
    "Профиль для локального запуска рядом с текущим operator UI без конфликта published ports.": "Профиль для локального запуска рядом с текущим operator UI без конфликта published ports.",
    "Канонический портовый профиль для target-host и offline release contract.": "Канонический портовый профиль для target-host и offline release contract.",
    "Настройки observability для Prometheus и Grafana внутри offline bundle.": "Настройки observability для Prometheus и Grafana внутри offline bundle.",
    "Пути к моделям, uploads и reports внутри offline bundle layout.": "Пути к моделям, uploads и reports внутри offline bundle layout.",
    "Offline parser/compare contract внутри env.bundle.": "Offline parser/compare contract внутри env.bundle.",
    "Offline bundle path for image loading, deploy, parity checks, and artifact-based server startup.": "Путь через offline bundle с загрузкой образов, деплоем, проверками parity и artifact-based запуском на сервере.",
    "Launch profile, backend mode, and overall device mode.": "Профиль запуска, backend mode и общий режим устройства.",
    "Model paths and registry config. These fields are usually edited manually, with recommendations shown next to each field.": "Пути к моделям и registry-конфигу. Здесь обычно правки ручные, но рекомендации видны рядом.",
    "Model placement and GPU limits. Some values come from presets, others remain manual.": "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.",
    "Ports for the local native path and service URLs.": "Порты локального native path и service URLs.",
    "Chainlit profiles. These are usually edited manually for a specific workflow.": "Профили Chainlit. Обычно редактируются вручную по конкретному workflow.",
    "Strict JSON and compare-path settings. Safe and strict presets are useful here.": "Параметры strict JSON и compare path. Здесь полезны safe/strict пресеты.",
    "Canonical port profile for the target host and offline release contract.": "Канонический портовый профиль для target-host и offline release contract.",
    "Observability settings for Prometheus and Grafana inside the offline bundle.": "Настройки observability для Prometheus и Grafana внутри offline bundle.",
    "Paths for models, uploads, and reports inside the offline bundle layout.": "Пути к моделям, uploads и reports внутри offline bundle layout.",
    "Usually points to the repository-local models.yaml.": "Обычно должен указывать на repo-local models.yaml.",
    "Set the main LLM artifact for this runtime path.": "Укажи основной LLM artifact для runtime path.",
    "Fill this only when the multimodal path is actually used.": "Заполняется только если multimodal path реально используется.",
    "Should point to the intent embedder used by the current runtime path.": "Должен указывать на intent embedder текущего runtime path.",
    "Should point to the retrieval embedder used by the current runtime path.": "Должен указывать на retrieval embedder текущего runtime path.",
    "This path must stay aligned with the bundle runtime layout.": "Путь должен совпадать с runtime layout bundle.",
    "The main user path goes through run_offline_bundle.sh and deploy.sh, not through the dev compose launcher for the checkout repository.": "Основной пользовательский путь идёт через run_offline_bundle.sh и deploy.sh, а не через dev compose launcher checkout-репозитория.",
    "Bundle image archives can be loaded into Docker only when the engine and socket are available.": "Архивы образов можно загружать в Docker только когда доступны engine и socket.",
    "Manifest, env.bundle, and the deploy surface remain readable even before the runtime is up.": "Manifest, env.bundle и deploy surface читаются даже когда сам runtime ещё не поднят.",
    "The container runtime path points to deploy/offline_bundle/scripts: first load bundle images, then deploy or run the offline bundle without a dev build from the checkout repository.": "Контейнерный путь запуска ведёт в deploy/offline_bundle/scripts: сначала можно загрузить образы bundle, затем выполнить deploy или run offline bundle без dev-сборки checkout-репозитория.",
    "This value is interpreted inside the offline bundle/container layout.": "Это значение интерпретируется внутри layout offline bundle / контейнера.",
    "Value is empty and must be set explicitly.": "Значение пустое и должно быть задано явно.",
  };
  return texts[text] || text;
}

function displayValidationMessage(message) {
  if (!message) return "";
  if (currentLanguage === "en") return message;
  if (message.startsWith("Resolved host path exists:")) {
    return `Разрешённый host path существует: ${message.slice("Resolved host path exists:".length).trim()}`;
  }
  if (message.startsWith("Resolved host path does not exist yet:")) {
    return `Разрешённый host path пока не существует: ${message.slice("Resolved host path does not exist yet:".length).trim()}`;
  }
  return displayText(message);
}

function translateSeedLogLine(message) {
  if (!message || currentLanguage !== "en") return message;
  const replacements = [
    [
      "[operator][repository-derived] обнаружены env-источники: backend/.env, backend/.env.native, backend/.env.runtime",
      "[operator][repository-derived] detected env sources: backend/.env, backend/.env.native, backend/.env.runtime",
    ],
    [
      "[launcher][repository-derived] scripts/launcher.sh найден в корне репозитория",
      "[launcher][repository-derived] scripts/launcher.sh detected at the repository root",
    ],
    [
      "[bundle][repository-derived] обнаружены manifest и env-источники deploy/offline_bundle",
      "[bundle][repository-derived] manifest and env sources detected in deploy/offline_bundle",
    ],
    [
      "[docker][host-probe] socket available at /var/run/docker.sock",
      "[docker][host-probe] socket available at /var/run/docker.sock",
    ],
    [
      "[docker][host-probe] socket missing at /var/run/docker.sock",
      "[docker][host-probe] socket missing at /var/run/docker.sock",
    ],
  ];
  let translated = message;
  replacements.forEach(([ru, en]) => {
    translated = translated.replace(ru, en);
  });
  return translated;
}

function translatePathPolicy(policy) {
  if (!policy) return "";
  if (currentLanguage === "en") {
    if (policy === "host_path_flexible") return "Host path: absolute paths outside the repo are allowed";
    if (policy === "bundle_internal_path") return "Bundle path: must stay inside the bundle/container layout";
  }
  if (policy === "host_path_flexible") return "Host path: можно указывать абсолютные пути вне репозитория";
  if (policy === "bundle_internal_path") return "Bundle path: путь должен оставаться внутри layout bundle / контейнера";
  return policy;
}

function persistUiSettings() {
  localStorage.setItem("operatorUiShowLiteralEnvKeys", String(uiSettings.showLiteralEnvKeys));
  localStorage.setItem("operatorUiShowSourceFiles", String(uiSettings.showSourceFiles));
  localStorage.setItem("operatorUiMetricsDensity", uiSettings.metricsDensity);
}

function sliceMetricItems(items) {
  return uiSettings.metricsDensity === "compact" ? (items || []).slice(0, 4) : (items || []);
}

function hasBundlePortConflict() {
  return blockers.some((item) => item.title?.includes("делит порт"));
}

function localizeStaticShell() {
  document.documentElement.lang = currentLanguage;
  document.title = currentLanguage === "en" ? "Agent Navigator Pro | Operator Console" : "Agent Navigator Pro | Операторская панель";

  const textMap = currentLanguage === "en" ? [
    [".topbar-copy .eyebrow", "Operator Console"],
    [".topbar-copy h1", "System Launch and Control Panel"],
    [".brand-subtitle", "Operator Launch Console"],
    ["#sidebar-nav-label", "Sections"],
    ['.nav-item[data-target="overview"]', "Overview"],
    ['.nav-item[data-target="launch"]', "Launch"],
    ['.nav-item[data-target="config"]', "Config"],
    ['.nav-item[data-target="services"]', "Services"],
    ['.nav-item[data-target="deploy"]', "Build / Deploy"],
    ['.nav-item[data-target="maintenance"]', "Actions"],
    ["#ui-settings-button-label", "UI Settings"],
    ["#ui-settings-button-note", "Interface language, metrics density, env keys, and secret visibility."],
    ["#topbar-start-button-label", "Launch"],
    ["#sidebar-hardware-label", "Hardware Environment"],
    ["#sidebar-summary-label", "Status Summary"],
    ["#sidebar-available-paths-label", "Available Paths"],
    ["#sidebar-healthy-services-label", "Healthy Services"],
    ["#sidebar-dirty-config-label", "Unsaved Changes"],
    ["#section-overview .eyebrow", "Overview"],
    ["#section-overview h2", "Launch availability, hardware, service state, and parser status"],
    ["#section-overview .support-copy", "This workspace shows what can run now and where to inspect the system."],
    ["#overview-metrics-eyebrow", "Metrics"],
    ["#overview-metrics-title", "Key Prometheus signals for the operator summary"],
    ["#overview-metrics-pill", "Observability"],
    ["#overview-hardware-eyebrow", "Hardware"],
    ["#section-launch .eyebrow", "Launch"],
    ["#section-launch h2", "Runtime cards with explicit source and availability"],
    ["#section-launch .support-copy", "The main workflow starts with a visible runtime path, not hidden assumptions."],
    ["#section-launch .panel:nth-of-type(1) .eyebrow", "Run Profiles"],
    ["#section-launch .panel:nth-of-type(1) h3", "Runtime status, active job, and health of the selected path"],
    ["#section-launch .panel:nth-of-type(2) .eyebrow", "Launch Log"],
    ["#section-launch .panel:nth-of-type(2) h3", "Current or latest execution log"],
    ["#launch-next-eyebrow", "Next Step"],
    ["#launch-next-title", "What to do after launch"],
    ["#section-config .eyebrow", "Config"],
    ["#section-config h2", "env editor with runtime-path awareness and explicit apply"],
    ["#section-config .support-copy", "Changes write to real env/config sources and are never auto-saved."],
    ["#section-services .eyebrow", "Services and Metrics"],
    ["#section-services h2", "State, readiness, logs, and parser diagnostics"],
    ["#section-services .support-copy", "Logs and metrics are part of the operator decision loop."],
    ["#section-deploy .eyebrow", "Build / Deploy"],
    ["#section-deploy h2", "Build a portable bundle and import it back into the runtime"],
    ["#section-deploy .support-copy", "This workspace covers build, export, pack, unpack, validate, install, deploy, and verify."],
    ["#section-maintenance .eyebrow", "Actions"],
    ["#section-maintenance h2", "Only allowlisted maintenance scenarios"],
    ["#section-maintenance .support-copy", "Safe operator actions stay visible, typed, and bound to the selected runtime path."],
    ["#sidebar-system-label", "System"],
    ["#overview-hardware-title", "Detected capabilities and suggested env defaults"],
    ["#overview-hardware-pill", "Informational only"],
    ["#overview-warnings-eyebrow", "Warnings"],
    ["#overview-warnings-title", "Parser and runtime blockers"],
    ["#overview-warnings-pill", "Needs attention"],
    ["#overview-health-eyebrow", "Service Health"],
    ["#overview-health-title", "Readiness of the active path"],
    ["#overview-activity-eyebrow", "Recent Activity"],
    ["#overview-activity-title", "Recent runtime and config events"],
    ["#section-config .panel:nth-of-type(1) .eyebrow", "Config Sources"],
    ["#section-config .panel:nth-of-type(1) h3", "Source of the selected runtime path"],
    ["#section-config .panel:nth-of-type(2) .eyebrow", "Variants"],
    ["#section-config .panel:nth-of-type(2) h3", "Variants and recommended presets"],
    ["#section-config .panel:nth-of-type(3) .eyebrow", "Editable Fields"],
    ["#section-config .panel:nth-of-type(3) h3", "Grouped runtime and parsing settings"],
    ["#apply-config-button", "Apply changes"],
    ["#services-support-copy", "Logs here support operator decisions instead of just sitting at the bottom of the page."],
    ["#services-status-eyebrow", "Services"],
    ["#services-status-title", "Status of the selected path"],
    ["#services-diagnostics-eyebrow", "Parser Diagnostics"],
    ["#services-diagnostics-title", "Checks for the selected path"],
    ["#services-observability-eyebrow", "Metrics and Grafana"],
    ["#services-observability-title", "Evidence for runtime health, latency, and observability"],
    ["#services-logs-eyebrow", "Logs"],
    ["#services-logs-title", "Combined event and log stream"],
    ["#deploy-summary-eyebrow", "Mode Summary"],
    ["#deploy-sources-title", "Canonical scripts and artifact roots"],
    ["#deploy-sources-eyebrow", "Sources"],
    ["#deploy-observability-eyebrow", "Observability"],
    ["#deploy-observability-title", "Metric summary and Grafana links"],
    ["#deploy-stages-eyebrow", "Flow Stages"],
    ["#deploy-actions-eyebrow", "Actions"],
    ["#deploy-artifacts-eyebrow", "Artifacts"],
    ["#deploy-logs-eyebrow", "Logs"],
    ["#maintenance-actions-eyebrow", "Actions"],
    ["#maintenance-actions-title", "Controlled Operator Commands"],
    ["#maintenance-blockers-eyebrow", "Current Blockers"],
    ["#maintenance-blockers-title", "What Blocks Full Runtime Parity"],
    ["#ui-settings-modal-eyebrow", "Interface settings"],
    ["#ui-settings-modal-title", "UI Settings"],
    ["#ui-settings-modal-close", "Close"],
  ] : [
    [".topbar-copy .eyebrow", "Панель управления оператором"],
    [".topbar-copy h1", "Панель запуска и управления системой"],
    [".brand-subtitle", "Операторская панель запуска"],
    ["#sidebar-nav-label", "Разделы"],
    ['.nav-item[data-target="overview"]', "Обзор"],
    ['.nav-item[data-target="launch"]', "Запуск"],
    ['.nav-item[data-target="config"]', "Конфиг"],
    ['.nav-item[data-target="services"]', "Сервисы"],
    ['.nav-item[data-target="deploy"]', "Сборка / Деплой"],
    ['.nav-item[data-target="maintenance"]', "Действия"],
    ["#ui-settings-button-label", "Настройки UI"],
    ["#ui-settings-button-note", "Язык интерфейса, метрики, env keys и показ секретов."],
    ["#topbar-start-button-label", "Запуск"],
    ["#sidebar-hardware-label", "Аппаратная среда"],
    ["#sidebar-summary-label", "Сводка состояния"],
    ["#sidebar-available-paths-label", "Доступные пути"],
    ["#sidebar-healthy-services-label", "Здоровые сервисы"],
    ["#sidebar-dirty-config-label", "Незасейвленные правки"],
    ["#section-overview .eyebrow", "Обзор"],
    ["#section-overview h2", "Доступность запуска, железо, состояние сервисов и статус парсинга"],
    ["#section-overview .support-copy", "Эта панель показывает, что можно запустить сейчас, и где смотреть состояние системы."],
    ["#overview-metrics-eyebrow", "Метрики"],
    ["#overview-metrics-title", "Ключевые сигналы Prometheus для сводки оператора"],
    ["#overview-metrics-pill", "Наблюдаемость"],
    ["#overview-hardware-eyebrow", "Железо"],
    ["#section-launch .eyebrow", "Запуск"],
    ["#section-launch h2", "Карточки запуска с явным источником и доступностью"],
    ["#section-launch .support-copy", "Основной сценарий начинается с выбора доступного пути запуска, а не со скрытых допущений."],
    ["#section-launch .panel:nth-of-type(1) .eyebrow", "Лог запуска"],
    ["#section-launch .panel:nth-of-type(1) h3", "Ход текущей или последней задачи"],
    ["#launch-next-eyebrow", "Следующий шаг"],
    ["#launch-next-title", "Что делать после запуска"],
    ["#section-config .eyebrow", "Конфиг"],
    ["#section-config h2", "Редактор env с учётом выбранного пути запуска и явным применением"],
    ["#section-config .support-copy", "Изменения пишутся в реальные env/config источники и не сохраняются автоматически."],
    ["#section-services .eyebrow", "Сервисы и метрики"],
    ["#section-services h2", "Состояние, готовность, логи и диагностика парсинга"],
    ["#section-services .support-copy", "Логи и метрики входят в операторский цикл принятия решения."],
    ["#section-deploy .eyebrow", "Сборка / Деплой"],
    ["#section-deploy h2", "Сборка переносимого bundle и обратный импорт в рабочую систему"],
    ["#section-deploy .support-copy", "Панель покрывает сборку, экспорт, упаковку, распаковку, проверку, установку, деплой и верификацию."],
    ["#section-maintenance .eyebrow", "Действия"],
    ["#section-maintenance h2", "Только разрешённые maintenance-сценарии"],
    ["#section-maintenance .support-copy", "Безопасные действия оператора видны, типизированы и привязаны к выбранному пути запуска."],
    ["#overview-hardware-title", "Обнаруженные возможности и базовые env-настройки"],
    ["#overview-hardware-pill", "Только справка"],
    ["#overview-warnings-eyebrow", "Предупреждения"],
    ["#overview-warnings-title", "Блокеры парсинга и runtime"],
    ["#overview-warnings-pill", "Требует внимания"],
    ["#overview-health-eyebrow", "Здоровье сервисов"],
    ["#overview-health-title", "Готовность активного пути"],
    ["#overview-activity-eyebrow", "Последние действия"],
    ["#overview-activity-title", "Недавние runtime и config события"],
    ["#section-launch .panel .eyebrow", "Профили"],
    ["#section-launch .panel h3", "Профили запуска для выбранного пути"],
    ["#section-config .panel:nth-of-type(1) .eyebrow", "Источники конфига"],
    ["#section-config .panel:nth-of-type(1) h3", "Происхождение выбранного пути запуска"],
    ["#section-config .panel:nth-of-type(2) .eyebrow", "Варианты"],
    ["#section-config .panel:nth-of-type(2) h3", "Варианты и рекомендуемые пресеты"],
    ["#section-config .panel:nth-of-type(3) .eyebrow", "Редактируемые поля"],
    ["#section-config .panel:nth-of-type(3) h3", "Сгруппированные настройки запуска и парсинга"],
    ["#apply-config-button", "Применить изменения"],
    ["#services-support-copy", "Логи здесь помогают принимать решение, а не просто лежат внизу страницы."],
    ["#services-status-eyebrow", "Сервисы"],
    ["#services-status-title", "Статус выбранного пути"],
    ["#services-diagnostics-eyebrow", "Диагностика парсера"],
    ["#services-diagnostics-title", "Проверки для выбранного пути"],
    ["#services-observability-eyebrow", "Метрики и Grafana"],
    ["#services-observability-title", "Evidence для health, latency и наблюдаемости runtime"],
    ["#services-logs-eyebrow", "Логи"],
    ["#services-logs-title", "Сводный поток событий и логов"],
    ["#deploy-summary-eyebrow", "Сводка режима"],
    ["#deploy-sources-title", "Канонические scripts и корни артефактов"],
    ["#deploy-sources-eyebrow", "Источники"],
    ["#deploy-observability-eyebrow", "Наблюдаемость"],
    ["#deploy-observability-title", "Сводка метрик и переходы в Grafana"],
    ["#deploy-stages-eyebrow", "Этапы потока"],
    ["#deploy-actions-eyebrow", "Действия"],
    ["#deploy-artifacts-eyebrow", "Артефакты"],
    ["#deploy-logs-eyebrow", "Логи"],
    ["#maintenance-actions-eyebrow", "Действия"],
    ["#maintenance-actions-title", "Контролируемые команды оператора"],
    ["#maintenance-blockers-eyebrow", "Текущие блокеры"],
    ["#maintenance-blockers-title", "Что мешает полной runtime-паритетности"],
    ["#section-services .panel:nth-of-type(1) .eyebrow", "Сервисы"],
    ["#section-services .panel:nth-of-type(1) h3", "Статус выбранного пути запуска"],
    ["#section-services .panel:nth-of-type(2) .eyebrow", "Диагностика парсинга"],
    ["#section-services .panel:nth-of-type(2) h3", "Диагностика по выбранному пути запуска"],
    ["#section-services .panel:nth-of-type(3) .eyebrow", "Наблюдаемость"],
    ["#section-services .panel:nth-of-type(3) h3", "Метрики и переходы в Grafana"],
    ["#section-services .panel:nth-of-type(4) .eyebrow", "Логи"],
    ["#section-services .panel:nth-of-type(4) h3", "Сводный операционный поток"],
    ["#section-maintenance .panel:nth-of-type(1) .eyebrow", "Действия"],
    ["#section-maintenance .panel:nth-of-type(1) h3", "Контролируемые команды оператора"],
    ["#section-maintenance .panel:nth-of-type(2) .eyebrow", "Текущие блокеры"],
    ["#section-maintenance .panel:nth-of-type(2) h3", "Что мешает полной паритетности"],
    ["#reason-modal-eyebrow", "Почему недоступно?"],
    ["#reason-modal-title", "Детали runtime"],
    ["#reason-modal-close", "Закрыть"],
    ["#ui-settings-modal-eyebrow", "Настройки интерфейса"],
    ["#ui-settings-modal-title", "Настройки UI"],
    ["#ui-settings-modal-close", "Закрыть"],
  ];

  textMap.forEach(([selector, text]) => {
    const node = document.querySelector(selector);
    if (node) {
      node.textContent = text;
    }
  });

  const launchButton = document.querySelector("#topbar-start-button");
  if (launchButton) {
    const label = currentLanguage === "en" ? "Choose runtime path" : "Выбрать путь запуска";
    launchButton.setAttribute("aria-label", label);
    launchButton.setAttribute("title", label);
  }
}

function setLanguage(nextLanguage) {
  currentLanguage = nextLanguage === "en" ? "en" : "ru";
  localStorage.setItem("operatorUiLanguage", currentLanguage);
  localizeStaticShell();
  syncUiSettingsControls();
  rerenderAll();
}

function syncUiSettingsControls() {
  const shellSelect = document.querySelector("#language-select");
  const settingsSelect = document.querySelector("#settings-language-select");
  if (shellSelect) shellSelect.value = currentLanguage;
  if (settingsSelect) settingsSelect.value = currentLanguage;
  const showKeys = document.querySelector("#settings-show-keys");
  const showSources = document.querySelector("#settings-show-sources");
  const showSecrets = document.querySelector("#settings-show-secrets");
  const metricsDensity = document.querySelector("#settings-metrics-density");
  if (showKeys) showKeys.checked = uiSettings.showLiteralEnvKeys;
  if (showSources) showSources.checked = uiSettings.showSourceFiles;
  if (showSecrets) showSecrets.checked = uiSettings.showSecretValues;
  if (metricsDensity) metricsDensity.value = uiSettings.metricsDensity;

  if (showKeys?.previousElementSibling) {
    showKeys.previousElementSibling.textContent = currentLanguage === "en" ? "Show env keys" : "Показывать env keys";
  }
  if (showSources?.previousElementSibling) {
    showSources.previousElementSibling.textContent = currentLanguage === "en" ? "Show source files" : "Показывать source files";
  }
  if (showSecrets?.previousElementSibling) {
    showSecrets.previousElementSibling.textContent = currentLanguage === "en" ? "Show secret values" : "Показывать значения секретов";
  }
  if (metricsDensity?.previousElementSibling) {
    metricsDensity.previousElementSibling.textContent = currentLanguage === "en" ? "Metrics mode" : "Режим метрик";
  }
  if (settingsSelect?.previousElementSibling) {
    settingsSelect.previousElementSibling.textContent = currentLanguage === "en" ? "Interface language" : "Язык интерфейса";
  }
  if (settingsSelect) {
    settingsSelect.options[0].textContent = currentLanguage === "en" ? "Russian" : "Русский";
    settingsSelect.options[1].textContent = "English";
  }
  if (metricsDensity) {
    metricsDensity.options[0].textContent = currentLanguage === "en" ? "Compact" : "Компактно";
    metricsDensity.options[1].textContent = currentLanguage === "en" ? "Detailed" : "Подробно";
  }
}

function isLaunchable(pathKey) {
  return runtimePaths[pathKey].status !== "unavailable";
}

function setLaunchPath(pathKey) {
  selectedLaunchPath = pathKey;
  rerenderAll();
}

function rerenderAll() {
  renderSidebarState();
  renderSystemStatus();
  renderOverviewHero();
  renderOverviewPathCards();
  renderOverviewMetrics();
  renderHardware();
  renderWarnings();
  renderServiceOverview();
  renderActivity();
  renderLaunchStrip();
  renderProfiles();
  renderLaunchLogs();
  renderConfig();
  renderServicesStrip();
  renderServices();
  renderDeployStrip();
  renderDeploy();
  renderMaintenance();
  updateDirtyState();
  updateCounters();
  updateTopbarAction();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatHardwareMetricValue(metric) {
  const value = localizedField(metric, "value");
  const label = displayLabel(localizedField(metric, "label"));
  if (label === "GPU Inventory" || label === "Инвентарь GPU") {
    return escapeHtml(String(value)).replaceAll("; ", "<br>");
  }
  return escapeHtml(String(value));
}

function getMetricValue(label) {
  return hardwareMetrics.find((metric) => metric.label === label)?.value || "Неизвестно";
}

function renderSidebarState() {
  const hostSummary = getMetricValue("CPU / memory");
  const gpuSummary = getMetricValue("GPU visibility");
  const availablePaths = Object.values(runtimePaths).filter((path) => path.status === "available").length;
  const partialPaths = Object.values(runtimePaths).filter((path) => path.status === "partial").length;

  document.querySelector("#sidebar-host-summary").textContent = hostSummary || "Неизвестно";
  document.querySelector("#sidebar-runtime-summary").textContent =
    currentLanguage === "en"
      ? `${gpuSummary}. Ready: ${availablePaths}, partial: ${partialPaths}, warnings: ${warnings.length}.`
      : `${gpuSummary}. Доступно: ${availablePaths}, частично: ${partialPaths}, предупреждений: ${warnings.length}.`;
}

function runtimeHealthTone(status) {
  if (["running", "healthy"].includes(status)) return "lime";
  if (["building", "deploying", "degraded", "unknown", "not_started"].includes(status)) return "cyan";
  return "orange";
}

function runtimeHealthLabel(status) {
  const map = currentLanguage === "en"
    ? {
        healthy: "Runtime healthy",
        running: "Runtime healthy",
        building: "Build in progress",
        deploying: "Deploy in progress",
        failed: "Runtime failed",
        blocked: "Blocked",
        degraded: "Degraded",
        unknown: "Unknown",
        not_started: "Not started",
      }
    : {
        healthy: "Runtime healthy",
        running: "Система работает",
        building: "Идёт сборка",
        deploying: "Идёт деплой",
        failed: "Запуск завершился ошибкой",
        blocked: "Заблокировано",
        degraded: "Снижено",
        unknown: "Неизвестно",
        not_started: "Не запущено",
      };
  return map[status] || cap(status);
}

function deriveLaunchRuntimeState(pathKey) {
  const runtimeHealth = runtimeHealthByPath[pathKey];
  const runtimeJob = [...runningJobs.values()].find((job) => job.pathKey === pathKey);
  const pathLogs = logLines.filter((line) => line.path === pathKey);
  const latestLog = pathLogs[pathLogs.length - 1]?.text || "";

  if (runtimeJob?.status === "queued" || runtimeJob?.status === "running") {
    return {
      status: pathKey === "container" ? "building" : "deploying",
      detail: runtimeJob.detail,
    };
  }
  if (lastJobSummary?.pathKey === pathKey && lastJobSummary.outcome === "failed") {
    return {
      status: "failed",
      detail: lastJobSummary.detail,
    };
  }
  if (runtimeHealth?.status && runtimeHealth.status !== "unknown") {
    return {
      status: runtimeHealth.status,
      detail: currentLanguage === "en"
        ? `${runtimeHealth.runningChecks || 0}/${runtimeHealth.checkCount || 0} checks healthy`
        : `${runtimeHealth.runningChecks || 0}/${runtimeHealth.checkCount || 0} проверок healthy`,
    };
  }
  if (latestLog) {
    return {
      status: "not_started",
      detail: latestLog,
    };
  }
  return {
    status: "unknown",
    detail: currentLanguage === "en" ? "No runtime signal yet" : "Пока нет сигнала от runtime",
  };
}

function renderSystemStatus() {
  const selectedRows = serviceRows.filter((row) => row.path === selectedServicesPath);
  const runningCount = selectedRows.filter((row) => row.status === "running").length;
  const activeJobCount = [...runningJobs.values()].filter((job) => job.status === "queued" || job.status === "running").length;

  let pillClass = "neutral";
  let pillText = currentLanguage === "en" ? "Control plane pending" : "Контур не подтверждён";
  let sideTitle = currentLanguage === "en" ? "Waiting for backend" : "Ожидание backend";
  let sideBody = currentLanguage === "en"
    ? "Current job state and a confirmation that the control plane is alive appear here."
    : "Здесь появится статус текущей задачи и подтверждение, что контур работает.";

  if (controlPlaneOnline) {
    pillClass = "cyan";
    pillText = currentLanguage === "en" ? "Control plane online" : "Контур online";
    sideTitle = currentLanguage === "en" ? "Control plane online" : "Контур online";
    sideBody = currentLanguage === "en"
      ? "Operator backend responds and the shell is hydrated from live state."
      : "Operator backend отвечает, а shell гидратирован из live state.";
  }

  if (runningCount > 0) {
    pillClass = "lime";
    pillText = currentLanguage === "en" ? "System running" : "Система работает";
    sideTitle = currentLanguage === "en" ? "System running" : "Система работает";
    sideBody = currentLanguage === "en"
      ? `${runningCount} published checks are currently healthy for the active runtime path.`
      : `${runningCount} опубликованных проверки сейчас выглядят здоровыми для активного пути запуска.`;
  }

  if (activeJobCount > 0) {
    pillClass = "cyan";
    pillText = currentLanguage === "en" ? `Job running: ${activeJobCount}` : `Идёт задача: ${activeJobCount}`;
    sideTitle = runningJobs.values().next().value?.label || sideTitle;
    sideBody = runningJobs.values().next().value?.detail || sideBody;
  } else if (lastJobSummary) {
    sideTitle = lastJobSummary.label;
    sideBody = lastJobSummary.detail;
  }

  const activeJob = [...runningJobs.values()].find((job) => job.status === "queued" || job.status === "running");
  const jobPillText = activeJob
    ? (currentLanguage === "en" ? `${activeJob.label}: ${activeJob.detail}` : `${activeJob.label}: ${activeJob.detail}`)
    : (lastJobSummary?.label
      ? `${lastJobSummary.label}: ${lastJobSummary.detail}`
      : (currentLanguage === "en" ? "No active job" : "Нет активной задачи"));

  const systemPill = document.querySelector("#system-status-pill");
  const systemButton = document.querySelector("#system-status-button");
  const systemDot = document.querySelector("#system-status-dot");
  const jobPill = document.querySelector("#job-status-pill");
  const jobButton = document.querySelector("#job-status-button");
  const jobDot = document.querySelector("#job-status-dot");

  systemPill.textContent = pillText;
  if (systemButton) {
    systemButton.className = `sidebar-action sidebar-status-card ${pillClass}`;
  }
  if (systemDot) {
    systemDot.className = `sidebar-action-dot ${pillClass}`;
  }

  jobPill.textContent = jobPillText;
  const jobTone = activeJob ? "cyan" : (lastJobSummary ? "lime" : "neutral");
  if (jobButton) {
    jobButton.className = `sidebar-action sidebar-status-card ${jobTone}`;
  }
  if (jobDot) {
    jobDot.className = `sidebar-action-dot ${jobTone}`;
  }
  document.querySelector("#sidebar-system-status").textContent = sideTitle;
  document.querySelector("#sidebar-job-status").textContent = sideBody;
}

function renderOverviewSummaryCard(path) {
  const card = document.createElement("article");
  card.className = "service-card";
  card.innerHTML = `
    <div class="service-row">
      <div>
        <div class="source-label">${pathTitle(path)}</div>
        <h4>${cap(path.status)}</h4>
        <p>${displayText(localizedField(path, "description"))}</p>
        <div class="service-meta-list">
          <span class="service-meta-pill">${path.launchSource}</span>
          <span class="service-meta-pill">${currentLanguage === "en" ? "Sources" : "Источников"}: ${path.configSources.length}</span>
        </div>
      </div>
      <span class="status-pill ${chipClass(path.status)}">${cap(path.status)}</span>
    </div>
  `;
  return card;
}

function renderOverviewHero() {
  const pathValues = Object.values(runtimePaths);
  const availablePaths = pathValues.filter((path) => path.status === "available");
  const blockedPaths = pathValues.filter((path) => path.status === "unavailable");
  const configSources = configState[selectedConfigPath]?.sourceFiles?.length || 0;

  document.querySelector("#overview-hero-primary").textContent = availablePaths.length
    ? (currentLanguage === "en"
      ? `Available paths: ${availablePaths.map((path) => pathTitle(path)).join(", ")}`
      : `Доступные пути: ${availablePaths.map((path) => pathTitle(path)).join(", ")}`)
    : (currentLanguage === "en" ? "No runnable path detected" : "Не найдено ни одного запускаемого пути");
  document.querySelector("#overview-hero-secondary").textContent = currentLanguage === "en"
    ? `Visible config sources: ${configSources}`
    : `Видимых источников конфига: ${configSources}`;
  document.querySelector("#overview-hero-tertiary").textContent = blockedPaths.length
    ? (currentLanguage === "en"
      ? `Blocked paths: ${blockedPaths.length}`
      : `${blockedPaths.length} пут${blockedPaths.length > 1 ? "ей" : "ь"} заблокировано`)
    : (currentLanguage === "en"
      ? `Warnings: ${warnings.length}`
      : `${warnings.length} предупреждени${warnings.length === 1 ? "е" : "я"}`);
}

function getActionIdForPath(pathKey) {
  if (pathKey === "native") return "runtime.native.launch";
  if (pathKey === "container") return "deploy.runtime.run";
  return null;
}

function launchPrimaryLabel(path) {
  return currentLanguage === "en"
    ? `Launch ${pathTitle(path)}`
    : `Запустить ${pathTitle(path)}`;
}

async function triggerLaunchForPath(pathKey) {
  const path = runtimePaths[pathKey];
  if (!path) return;
  selectedLaunchPath = pathKey;
  rerenderAll();
  if (!isLaunchable(pathKey)) {
    switchSection("launch");
    openReasonModal(pathKey);
    return;
  }
  const actionId = getActionIdForPath(pathKey);
  if (!actionId) {
    activityFeed.unshift(`${currentLanguage === "en" ? "No mapped launch action for" : "Действие запуска не сопоставлено для"} ${pathTitle(path)}`);
    renderActivity();
    return;
  }
  await runOperatorAction(actionId, {
    surface: "runtime",
    pathKey,
    label: pathTitle(path),
  });
}

function getActionIdForDeployLabel(label) {
  const overrides = {
    "Собрать bundle": "deploy.bundle.build",
    "Собрать host APT bundle": "deploy.host_packages.build",
    "Экспортировать images": "deploy.images.export",
    "Сгенерировать manifest": "deploy.bundle.manifest",
    "Pack tar.gz": "deploy.bundle.pack",
    "Упаковать tar.gz": "deploy.bundle.pack",
    "Проверить host packages": "deploy.host.check",
    "Установить host APT bundle": "deploy.host.install",
    "Деплоить runtime": "deploy.runtime.deploy",
    "Запустить offline bundle": "deploy.runtime.run",
    "Проверить runtime": "deploy.runtime.verify",
  };
  return overrides[label] || actionCatalogByTitle[label]?.action_id || null;
}

function formatConfigSources(sources) {
  return sources
    .map((source) => `${source.path} · ${displayText(localizedField(source, "role"))}`)
    .join("<br>");
}

function renderPathCard(path, includeProfiles = true) {
  const showSafePortsButton = path.key === "container" && hasBundlePortConflict();
  const primaryLabel = path.key === "container"
    ? (currentLanguage === "en" ? "Run Bundle" : "Запустить bundle")
    : (currentLanguage === "en" ? "Launch" : "Запустить");
  const blockedLabel = path.key === "container"
    ? (currentLanguage === "en" ? "Bundle Run Blocked" : "Запуск bundle заблокирован")
    : (currentLanguage === "en" ? "Launch Blocked" : "Запуск заблокирован");
  const card = document.createElement("article");
  card.className = `runtime-card ${path.status === "unavailable" ? "unavailable" : ""}`;
  card.innerHTML = `
    <div class="path-card-header">
      <div>
        <div class="eyebrow">${currentLanguage === "en" ? "Launch Path" : "Путь запуска"}</div>
        <h3>${pathTitle(path)}</h3>
        <p>${displayText(localizedField(path, "description"))}</p>
      </div>
      <span class="status-pill ${chipClass(path.status)}">${cap(path.status)}</span>
    </div>
    <div class="path-summary-grid">
      ${path.summary.map((item) => `
        <div class="metric-card">
          <div class="metric-label">${displayLabel(localizedField(item, "label"))}</div>
          <strong>${localizedField(item, "value")}</strong>
          <span class="status-pill ${item.tone}">${cap(item.tone === "lime" ? "healthy" : item.tone === "cyan" ? "watch" : "blocked")}</span>
        </div>
      `).join("")}
    </div>
    <div class="source-block">
      <div>
        <div class="source-label">${currentLanguage === "en" ? "Launch Source" : "Источник запуска"}</div>
        <div class="source-value">${path.launchSource}</div>
      </div>
      <div>
        <div class="source-label">${currentLanguage === "en" ? "Config Source" : "Источник конфига"}</div>
        <div class="source-value">${formatConfigSources(path.configSources)}</div>
      </div>
    </div>
    ${includeProfiles ? `
      <div>
        <div class="source-label">${currentLanguage === "en" ? "Path Profiles" : "Профили пути"}</div>
        <div class="button-row">${path.profiles.map((profile) => `<span class="profile-pill ${chipClass(profile.status)}">${displayLabel(localizedField(profile, "title"))} · ${cap(profile.status)}</span>`).join("")}</div>
      </div>
    ` : ""}
    <div class="button-row">
      <button class="primary-button launch-button" data-path="${path.key}" ${!isLaunchable(path.key) ? "disabled" : ""}>
        ${!isLaunchable(path.key) ? blockedLabel : primaryLabel}
      </button>
      ${showSafePortsButton ? `
        <button class="ghost-button safe-ports-button" data-path="${path.key}">
          ${currentLanguage === "en" ? "Stage safe local ports" : "Подставить safe local ports"}
        </button>
      ` : ""}
      <button class="ghost-button why-button" data-path="${path.key}">
        ${(path.status === "available") ? (currentLanguage === "en" ? "Show Checks" : "Показать проверки") : (currentLanguage === "en" ? "Why Unavailable?" : "Почему недоступно?")}
      </button>
    </div>
  `;
  return card;
}

function renderOverviewPathCards() {
  const launchGrid = document.querySelector("#launch-path-cards");
  const overviewGrid = document.querySelector("#overview-path-cards");
  launchGrid.innerHTML = "";
  overviewGrid.innerHTML = "";
  Object.values(runtimePaths).forEach((path) => {
    launchGrid.appendChild(renderPathCard(path, true));
    overviewGrid.appendChild(renderOverviewSummaryCard(path));
  });
  wireRuntimeButtons();
}

function renderHardware() {
  const container = document.querySelector("#hardware-summary");
  container.innerHTML = hardwareMetrics.map((metric) => `
      <div class="metric-card">
      <div class="metric-label">${displayLabel(metric.label)}</div>
      <strong class="metric-strong-multiline">${formatHardwareMetricValue(metric)}</strong>
      <p>${displayText(metric.note)}</p>
    </div>
  `).join("");
}

function renderMetricCards(targetId, items, emptyTitle, emptyBody) {
  const container = document.querySelector(targetId);
  if (!container) return;
  const visibleItems = sliceMetricItems(items);
  if (!visibleItems.length) {
    container.innerHTML = `
      <div class="metric-card">
        <div class="source-label">${emptyTitle}</div>
        <p>${emptyBody}</p>
      </div>
    `;
    return;
  }
  container.innerHTML = visibleItems.map((item) => `
    <div class="metric-card">
      <div class="metric-label">${displayLabel(localizedField(item, "label"))}</div>
      <strong>${localizedField(item, "value")}</strong>
      <p>${displayText(localizedField(item, "note"))}</p>
      <span class="status-pill ${item.tone || "neutral"}">${cap(item.tone === "lime" ? "ready" : item.tone === "orange" ? "attention" : item.tone === "cyan" ? "informational" : "info")}</span>
    </div>
  `).join("");
}

function renderObservabilityLinks(targetId, surface) {
  const container = document.querySelector(targetId);
  if (!container) return;
  const links = collectObservabilityLinks(surface);
  if (!links.length) {
    container.innerHTML = `
      <div class="metric-card">
        <div class="source-label">${currentLanguage === "en" ? "Links Not Configured" : "Переходы не настроены"}</div>
        <p>${currentLanguage === "en" ? "Grafana and Prometheus links appear after the backend observability summary loads." : "Ссылки на Grafana и Prometheus появятся после загрузки backend-сводки наблюдаемости."}</p>
      </div>
    `;
    return;
  }
  container.innerHTML = links.map((item) => `
    <div class="metric-card">
      <div class="source-label">${displayLabel(localizedField(item, "title"))}</div>
      <strong>${displayText(localizedField(item, "description"))}</strong>
      <p class="mono-line">${item.url}</p>
      <div class="button-row" style="margin-top: 12px;">
        <a class="ghost-button" href="${item.url}" target="_blank" rel="noreferrer">${observabilityButtonLabel(item)}</a>
      </div>
    </div>
  `).join("");
}

function collectObservabilityLinks(surface) {
  const surfaces = Array.isArray(surface) ? surface : [surface];
  return (grafanaLinks.links || []).filter((item) => surfaces.includes(item.surface));
}

function observabilityButtonLabel(item) {
  if (item.url?.startsWith(grafanaLinks.prometheus_url)) {
    return currentLanguage === "en" ? "Open in Prometheus" : "Открыть в Prometheus";
  }
  if (item.url?.startsWith(grafanaLinks.base_url)) {
    return currentLanguage === "en" ? "Open in Grafana" : "Открыть в Grafana";
  }
  return currentLanguage === "en" ? "Open Link" : "Открыть ссылку";
}

function renderServicesStrip() {
  const container = document.querySelector("#services-strip");
  if (!container) return;

  const rows = serviceRows.filter((row) => row.path === selectedServicesPath);
  const running = rows.filter((row) => row.status === "running").length;
  const blocked = rows.filter((row) => row.status === "blocked").length;
  const pathDiagnostics = diagnostics.filter((item) => item.path === selectedServicesPath);
  const serviceLinks = collectObservabilityLinks("services");

  container.innerHTML = `
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Active Path" : "Активный путь"}</div>
      <strong>${pathTitle(runtimePaths[selectedServicesPath])}</strong>
      <p>${displayText("Все статусы, логи и диагностика ниже относятся к этому пути запуска.")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Service Checks" : "Проверки сервисов"}</div>
      <strong>${currentLanguage === "en" ? `${running} of ${rows.length || 0} healthy` : `${running} из ${rows.length || 0} готовы`}</strong>
      <p>${blocked ? (currentLanguage === "en" ? `Blocked: ${blocked}.` : `Заблокировано: ${blocked}.`) : displayText("Явных блокировок в опубликованных проверках нет.")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Diagnostics" : "Диагностика"}</div>
      <strong>${currentLanguage === "en" ? `${pathDiagnostics.length || 0} signals` : `${pathDiagnostics.length || 0} сигналов`}</strong>
      <p>${pathDiagnostics.length ? (currentLanguage === "en" ? "Check diagnostics first, then metrics and logs below." : "Сначала смотри диагностику, затем метрики и лог ниже.") : (currentLanguage === "en" ? "Backend has not sent additional parser/runtime signals yet." : "Backend пока не прислал дополнительных parser/runtime сигналов.")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Observability" : "Наблюдаемость"}</div>
      <strong>${currentLanguage === "en" ? `${serviceLinks.length || 0} quick links` : `${serviceLinks.length || 0} быстрых перехода`}</strong>
      <p>${displayText("Grafana и Prometheus доступны рядом с метриками, без ухода в отдельный раздел.")}</p>
    </div>
  `;
}

function renderDeployStrip() {
  const container = document.querySelector("#deploy-strip");
  if (!container) return;

  const mode = deploySurface[selectedDeployMode];
  const runningStages = mode.stages.filter((item) => item.status === "running").length;
  const blockedStages = mode.stages.filter((item) => item.status === "blocked").length;
  const links = collectObservabilityLinks(selectedDeployMode === "build" ? ["deploy", "overview"] : "deploy");

  container.innerHTML = `
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Active Mode" : "Активный режим"}</div>
      <strong>${displayLabel(mode.label)}</strong>
      <p>${displayText("Ниже показаны только этапы, действия и артефакты текущего режима.")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Flow Stages" : "Этапы потока"}</div>
      <strong>${currentLanguage === "en" ? `${runningStages} running / ${blockedStages} blocked` : `${runningStages} в работе / ${blockedStages} заблокированы`}</strong>
      <p>${displayText("Сначала проверяй сводку и этапы, затем уже отдельные действия и лог.")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Actions" : "Действия"}</div>
      <strong>${currentLanguage === "en" ? `${mode.actions.length} available actions` : `${mode.actions.length} доступных кнопок`}</strong>
      <p>${displayText("Кнопки режима привязаны к allowlisted operator-командам и Python job model.")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Observability" : "Наблюдаемость"}</div>
      <strong>${currentLanguage === "en" ? `${links.length} links` : `${links.length} перехода`}</strong>
      <p>${displayText("Открывай Grafana или Prometheus прямо из режима сборки и деплоя.")}</p>
    </div>
  `;
}

function renderOverviewMetrics() {
  renderMetricCards(
    "#overview-metrics-grid",
    metricsSummary.overview || [],
    "Метрики ещё не загружены",
    "Backend ещё не опубликовал summary-сигналы из observability слоя.",
  );
}

function renderWarnings() {
  const container = document.querySelector("#warning-list");
  if (!warnings.length) {
    container.innerHTML = `
      <div class="metric-card">
        <div class="source-label">${currentLanguage === "en" ? "No Warnings" : "Нет предупреждений"}</div>
        <p>${displayText("Backend не сообщил о дополнительных блокерах runtime сверх текущего состояния доступности.")}</p>
      </div>
    `;
    return;
  }
  container.innerHTML = warnings.map((warning) => `
    <div class="warning-card">
      <h4>${displayLabel(localizedField(warning, "title"))}</h4>
      <p>${displayText(localizedField(warning, "body"))}</p>
    </div>
  `).join("");
}

function renderServiceOverview() {
  const container = document.querySelector("#service-overview-list");
  container.innerHTML = Object.values(runtimePaths).map((path) => {
    const rows = serviceRows.filter((row) => row.path === path.key);
    const running = rows.filter((row) => row.status === "running").length;
    const degraded = rows.filter((row) => row.status === "degraded").length;
    const blocked = rows.filter((row) => row.status === "blocked").length;
    return `
      <div class="service-card">
        <div class="service-row">
          <div>
            <div class="source-label">${pathTitle(path)}</div>
            <h4>${currentLanguage === "en" ? `${running}/${rows.length} healthy, ${degraded} degraded, ${blocked} blocked` : `${running}/${rows.length} готовы, ${degraded} снижены, ${blocked} заблокированы`}</h4>
            <p>${rows.length ? displayText(path.key === "native" ? "Проверки native и host probes берутся из репозитория." : "Состояние bundle видно сразу, а запуск контейнеров зависит от Docker engine и доступа к socket.") : (currentLanguage === "en" ? "No published service probes for this runtime path yet." : "Для этого пути запуска ещё нет опубликованных service probes.")}</p>
          </div>
          <span class="status-pill ${chipClass(path.status)}">${cap(path.status)}</span>
        </div>
      </div>
    `;
  }).join("");
}

function renderActivity() {
  const container = document.querySelector("#recent-activity");
  if (!activityFeed.length) {
    container.innerHTML = `
      <div class="metric-card">
        <div class="activity-row">
          <div>
            <div class="source-label">${currentLanguage === "en" ? "No Operator Activity Yet" : "Пока нет активности оператора"}</div>
            <strong>${displayText("Здесь появятся действия, применение конфига и завершённые jobs.")}</strong>
          </div>
        </div>
      </div>
    `;
    return;
  }
  container.innerHTML = activityFeed.map((item) => `
    <div class="metric-card">
      <div class="activity-row">
        <div>
          <div class="source-label">${currentLanguage === "en" ? "Latest Event" : "Последнее событие"}</div>
          <strong>${displayText(translateSeedLogLine(item))}</strong>
        </div>
      </div>
    </div>
  `).join("");
}

function renderProfiles() {
  const container = document.querySelector("#profile-grid");
  const profiles = runtimePaths[selectedLaunchPath].profiles;
  container.innerHTML = profiles.map((profile) => `
    <div class="profile-card">
      <div class="service-row">
        <div>
          <div class="source-label">${pathTitle(runtimePaths[selectedLaunchPath])}</div>
          <h4>${displayLabel(localizedField(profile, "title"))}</h4>
          <p>${displayText(localizedField(profile, "body"))}</p>
        </div>
        <span class="status-pill ${chipClass(profile.status)}">${cap(profile.status)}</span>
      </div>
    </div>
  `).join("");
}

function renderLaunchStrip() {
  const container = document.querySelector("#launch-strip");
  if (!container) return;
  const path = runtimePaths[selectedLaunchPath];
  const state = deriveLaunchRuntimeState(selectedLaunchPath);
  const runtimeJob = [...runningJobs.values()].find((job) => job.pathKey === selectedLaunchPath);
  const nextAction = selectedLaunchPath === "container"
    ? (hasBundlePortConflict()
      ? (currentLanguage === "en" ? "Stage safe local ports before deploy" : "Сначала подставь safe local ports")
      : (currentLanguage === "en" ? "Open build/deploy after launch" : "После запуска перейди в Сборка / Деплой"))
    : (currentLanguage === "en" ? "Verify services and logs after start" : "После запуска проверь сервисы и логи");

  container.innerHTML = `
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Selected path" : "Выбранный путь"}</div>
      <strong>${pathTitle(path)}</strong>
      <p>${displayText(localizedField(path, "description"))}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Runtime state" : "Состояние runtime"}</div>
      <strong>${runtimeHealthLabel(state.status)}</strong>
      <p>${state.detail}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Active job" : "Активная задача"}</div>
      <strong>${runtimeJob ? runtimeJob.label : (currentLanguage === "en" ? "No active job" : "Нет активной задачи")}</strong>
      <p>${runtimeJob ? runtimeJob.detail : (lastJobSummary?.pathKey === selectedLaunchPath ? lastJobSummary.detail : (currentLanguage === "en" ? "Launch actions will appear here." : "Здесь появится ход текущего запуска."))}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Next step" : "Следующий шаг"}</div>
      <strong>${currentLanguage === "en" ? "Inspect after launch" : "Проверка после запуска"}</strong>
      <p>${nextAction}</p>
    </div>
  `;
}

function renderLaunchLogs() {
  const runtimeState = deriveLaunchRuntimeState(selectedLaunchPath);
  const logConsole = document.querySelector("#launch-log-console");
  const runtimePill = document.querySelector("#launch-runtime-pill");
  const lines = logLines
    .filter((line) => line.path === selectedLaunchPath)
    .map((line) => translateSeedLogLine(line.text));

  runtimePill.className = `status-pill ${runtimeHealthTone(runtimeState.status)}`;
  runtimePill.textContent = runtimeHealthLabel(runtimeState.status);
  logConsole.textContent = lines.length
    ? lines.join("\n")
    : (currentLanguage === "en"
      ? "No launch log for this runtime path yet."
      : "Для этого пути запуска пока нет launch-лога.");

  const nextSteps = document.querySelector("#launch-next-steps");
  const items = selectedLaunchPath === "container"
    ? [
        currentLanguage === "en" ? "Check Build / Deploy and the container health cards." : "Проверь Сборка / Деплой и health-карточки контейнерного пути.",
        hasBundlePortConflict()
          ? (currentLanguage === "en" ? "Apply safe local ports before retrying deploy." : "Перед повторным деплоем подставь safe local ports.")
          : (currentLanguage === "en" ? "If the build fails, inspect registry/network access in the launch log." : "Если сборка падает, смотри доступ к registry/network в launch-логе."),
      ]
    : [
        currentLanguage === "en" ? "Open Services to verify endpoints and logs." : "Открой Сервисы и проверь endpoints и логи.",
        currentLanguage === "en" ? "Open Config if runtime profile or device mode needs adjustment." : "Открой Конфиг, если нужно поправить runtime profile или device mode.",
      ];
  nextSteps.innerHTML = items.map((item) => `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Operator hint" : "Подсказка оператору"}</div>
      <p>${item}</p>
    </div>
  `).join("");
}

function renderPathSwitcher(targetId, selectedKey, onChange) {
  const node = document.querySelector(targetId);
  node.innerHTML = Object.values(runtimePaths).map((path) => `
    <button class="path-toggle ${path.key === selectedKey ? "active" : ""}" data-path="${path.key}">
      ${pathTitle(path)}
    </button>
  `).join("");
  [...node.querySelectorAll(".path-toggle")].forEach((button) => {
    button.addEventListener("click", () => onChange(button.dataset.path));
  });
}

function setConfigPath(pathKey) {
  selectedConfigPath = pathKey;
  renderPathSwitcher("#config-path-switcher", selectedConfigPath, setConfigPath);
  renderConfig();
}

function getConfigPathState(pathKey) {
  return configState[pathKey] || { sourceFiles: [], variants: [], defaultVariantId: null };
}

function getSelectedConfigVariant(pathKey) {
  const pathState = getConfigPathState(pathKey);
  const variants = pathState.variants || [];
  const selected = selectedConfigVariantByPath[pathKey];
  if (variants.some((variant) => variant.variantId === selected)) {
    return selected;
  }
  return pathState.defaultVariantId || variants[0]?.variantId || null;
}

function getConfigVariant(pathKey, variantId) {
  return (getConfigPathState(pathKey).variants || []).find((variant) => variant.variantId === variantId) || null;
}

function getFieldValue(pathKey, field) {
  const staged = stagedFieldValues.get(`${pathKey}:${field.key}`);
  return staged ?? field.applied;
}

function maskSecretValue(value) {
  const raw = String(value ?? "");
  if (!raw) {
    return currentLanguage === "en" ? "(empty)" : "(пусто)";
  }
  return "••••••••";
}

function displayFieldValue(field, value) {
  return field?.secret && !uiSettings.showSecretValues ? maskSecretValue(value) : value;
}

function secretFieldMapKey(pathKey, fieldKey) {
  return `${pathKey}:${fieldKey}`;
}

function isSecretFieldVisible(pathKey, field) {
  if (!field?.secret) return true;
  const mapKey = secretFieldMapKey(pathKey, field.key);
  if (hiddenSecretFields.has(mapKey)) return false;
  if (uiSettings.showSecretValues) return true;
  return revealedSecretFields.has(mapKey);
}

function toggleSecretFieldVisibility(pathKey, fieldKey) {
  const mapKey = secretFieldMapKey(pathKey, fieldKey);
  if (uiSettings.showSecretValues) {
    if (hiddenSecretFields.has(mapKey)) {
      hiddenSecretFields.delete(mapKey);
    } else {
      hiddenSecretFields.add(mapKey);
    }
    return;
  }
  if (revealedSecretFields.has(mapKey)) {
    revealedSecretFields.delete(mapKey);
  } else {
    revealedSecretFields.add(mapKey);
  }
  hiddenSecretFields.delete(mapKey);
}

function randomToken(length, alphabet) {
  const chars = alphabet || "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let output = "";
  for (let index = 0; index < length; index += 1) {
    output += chars[bytes[index] % chars.length];
  }
  return output;
}

function generateSecretValue(field) {
  if (field.key === "CHAINLIT_AUTH_SECRET") {
    return randomToken(48, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_");
  }
  if (field.key === "VLLM_API_KEY") {
    return `vllm_${randomToken(32, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")}`;
  }
  return randomToken(20, "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*");
}

async function copyToClipboard(value) {
  await navigator.clipboard.writeText(String(value ?? ""));
}

function showToast(title, body, tone = "success") {
  const stack = document.querySelector("#toast-stack");
  if (!stack) return;
  const toast = document.createElement("div");
  const totalLifetime = 2400;
  const hoverResumeLifetime = 900;
  const toastId = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  let remaining = totalLifetime;
  let startedAt = 0;
  const startProgress = (duration) => {
    const progress = toast.querySelector(".toast-progress-bar");
    if (!progress) return;
    const ratio = Math.max(0, Math.min(1, duration / totalLifetime));
    progress.style.transition = "none";
    progress.style.width = `${ratio * 100}%`;
    void progress.offsetWidth;
    progress.style.transition = `width ${duration}ms linear`;
    progress.style.width = "0%";
    startedAt = Date.now();
  };
  const pauseProgress = () => {
    const progress = toast.querySelector(".toast-progress-bar");
    if (!progress) return;
    const elapsed = startedAt ? Math.max(0, Date.now() - startedAt) : 0;
    remaining = Math.max(0, remaining - elapsed);
    const ratio = Math.max(0, Math.min(1, remaining / totalLifetime));
    progress.style.transition = "none";
    progress.style.width = `${ratio * 100}%`;
  };
  const dismissToast = () => {
    toast.classList.remove("visible");
    toast.classList.add("hiding");
    window.setTimeout(() => {
      toast.remove();
      toastTimeouts.delete(toastId);
    }, 220);
  };
  const scheduleDismiss = (delay) => {
    const existing = toastTimeouts.get(toastId);
    if (existing) {
      window.clearTimeout(existing);
    }
    const timeout = window.setTimeout(dismissToast, delay);
    toastTimeouts.set(toastId, timeout);
  };
  toast.className = `toast ${tone}`;
  toast.dataset.toastId = toastId;
  toast.innerHTML = `
    <strong>${title}</strong>
    <span>${body}</span>
    <div class="toast-progress"><div class="toast-progress-bar"></div></div>
  `;
  stack.appendChild(toast);
  window.setTimeout(() => {
    toast.classList.add("visible");
  }, 18);
  toast.addEventListener("mouseenter", () => {
    const existing = toastTimeouts.get(toastId);
    if (existing) {
      window.clearTimeout(existing);
      toastTimeouts.delete(toastId);
    }
    pauseProgress();
  });
  toast.addEventListener("mouseleave", () => {
    remaining = Math.min(remaining || hoverResumeLifetime, hoverResumeLifetime);
    startProgress(remaining);
    scheduleDismiss(remaining);
  });
  startProgress(totalLifetime);
  scheduleDismiss(totalLifetime);
}

function renderFieldControl(pathKey, field) {
  const value = getFieldValue(pathKey, field);
  const isVisible = isSecretFieldVisible(pathKey, field);
  if (field.control === "select" && Array.isArray(field.options) && field.options.length) {
    return `
      <select ${field.editable === false ? "disabled" : ""}>
        ${field.options.map((option) => `
          <option value="${option.value}" ${String(option.value) === String(value) ? "selected" : ""}>${displayLabel(localizedField(option, "label"))}</option>
        `).join("")}
      </select>
    `;
  }
  const input = `<input type="${field.secret && !isVisible ? "password" : "text"}" value="${value}" ${field.editable === false ? "disabled" : ""} />`;
  if (field.secret && field.editable !== false) {
    return `
      <div class="config-input-row">
        ${input}
        <button
          type="button"
          class="secret-visibility-button"
          data-toggle-secret="${field.key}"
          title="${isVisible ? (currentLanguage === "en" ? "Hide value" : "Скрыть значение") : (currentLanguage === "en" ? "Show value" : "Показать значение")}"
          aria-label="${isVisible ? (currentLanguage === "en" ? "Hide value" : "Скрыть значение") : (currentLanguage === "en" ? "Show value" : "Показать значение")}"
        >
          ${isVisible ? `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m3 3 18 18" />
              <path d="M10.6 10.7a2 2 0 0 0 2.8 2.8" />
              <path d="M9.4 5.5A10.7 10.7 0 0 1 12 5c5 0 9 4 10 7-0.45 1.35-1.37 2.8-2.7 4.03" />
              <path d="M6.6 6.7C4.58 8.02 3.3 9.9 2 12c1 3 5 7 10 7 1.73 0 3.33-.48 4.7-1.3" />
            </svg>
          ` : `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          `}
        </button>
        <button
          type="button"
          class="secret-copy-button"
          data-copy-secret="${field.key}"
          title="${currentLanguage === "en" ? "Copy value" : "Скопировать значение"}"
          aria-label="${currentLanguage === "en" ? "Copy value" : "Скопировать значение"}"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="9" y="9" width="10" height="10" rx="2" />
            <path d="M15 9V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" />
          </svg>
        </button>
        <button
          type="button"
          class="secret-generate-button"
          data-generate-secret="${field.key}"
          title="${currentLanguage === "en" ? "Generate value" : "Сгенерировать значение"}"
          aria-label="${currentLanguage === "en" ? "Generate value" : "Сгенерировать значение"}"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M5 3v4" />
            <path d="M3 5h4" />
            <path d="M18 2l.9 2.1L21 5l-2.1.9L18 8l-.9-2.1L15 5l2.1-.9L18 2Z" />
            <path d="M8.5 9.5 15 16" />
            <path d="m14 8 2 2" />
            <path d="M7 17l-1 4 4-1 9.6-9.6a1.5 1.5 0 0 0 0-2.1l-1.9-1.9a1.5 1.5 0 0 0-2.1 0Z" />
          </svg>
        </button>
      </div>
    `;
  }
  return input;
}

function setStagedFieldValue(pathKey, fieldKey, value) {
  const field = findField(pathKey, fieldKey);
  if (!field) return;
  const mapKey = `${pathKey}:${fieldKey}`;
  if (String(value) !== String(field.applied)) {
    stagedFieldValues.set(mapKey, String(value));
    dirtyFields.add(mapKey);
  } else {
    stagedFieldValues.delete(mapKey);
    dirtyFields.delete(mapKey);
  }
}

function renderConfig() {
  const path = runtimePaths[selectedConfigPath];
  const pathState = getConfigPathState(selectedConfigPath);
  const selectedVariantId = getSelectedConfigVariant(selectedConfigPath);
  selectedConfigVariantByPath[selectedConfigPath] = selectedVariantId;
  const selectedVariant = getConfigVariant(selectedConfigPath, selectedVariantId);
  document.querySelector("#config-selected-path-label").textContent = pathTitle(path);
  document.querySelector("#config-source-list").closest(".panel").style.display = uiSettings.showSourceFiles ? "" : "none";
  document.querySelector("#config-source-list").innerHTML = pathState.sourceFiles.map((item) => `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Source File" : "Файл-источник"}</div>
      <strong class="mono-line">${item.path}</strong>
      <p>${displayText(localizedField(item, "role"))}</p>
      <span class="status-pill ${chipClass(item.freshness)}">${cap(item.freshness)}</span>
    </div>
  `).join("");

  const variantSwitcher = document.querySelector("#config-variant-switcher");
  variantSwitcher.innerHTML = (pathState.variants || []).map((variant) => `
    <button class="path-toggle ${variant.variantId === selectedVariantId ? "active" : ""}" data-variant="${variant.variantId}">
      ${displayLabel(localizedField(variant, "title"))}
    </button>
  `).join("");
  [...variantSwitcher.querySelectorAll("[data-variant]")].forEach((button) => {
    button.addEventListener("click", () => {
      selectedConfigVariantByPath[selectedConfigPath] = button.dataset.variant;
      renderConfig();
    });
  });
  document.querySelector("#config-variant-title").textContent = displayLabel(localizedField(selectedVariant || {}, "title") || "Config Variants");
  document.querySelector("#config-variant-description").textContent = displayText(localizedField(selectedVariant || {}, "description") || (currentLanguage === "en" ? "Variant not selected" : "Вариант не выбран"));

  const presetList = document.querySelector("#config-preset-list");
  const presets = selectedVariant?.presets || [];
  presetList.innerHTML = presets.length ? presets.map((preset) => `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Preset" : "Пресет"}</div>
      <strong>${displayLabel(localizedField(preset, "title"))}</strong>
      <p>${displayText(localizedField(preset, "description"))}</p>
      <div class="button-row" style="margin-top: 12px;">
        <button class="ghost-button config-preset-button" data-preset-id="${preset.presetId}">${currentLanguage === "en" ? "Stage preset values" : "Подставить в staged"}</button>
      </div>
    </div>
  `).join("") : `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Manual editing" : "Ручное редактирование"}</div>
      <p>${currentLanguage === "en" ? "This variant has no meaningful preset. Edit the fields below directly." : "У этого варианта нет осмысленного пресета. Изменяй поля ниже вручную."}</p>
    </div>
  `;

  const groups = document.querySelector("#config-groups");
  groups.innerHTML = (selectedVariant?.groups || []).map((group) => `
    <section class="config-group">
      <div class="config-group-copy">
        <div class="eyebrow">${pathTitle(path)}</div>
        <h4>${displayLabel(localizedField(group, "title"))}</h4>
        <p class="config-group-note">${currentLanguage === "en"
          ? `${group.fields.length} editable fields in this slice. Review values on the right and apply changes explicitly.`
          : `${group.fields.length} редактируемых поля в этом срезе. Проверь значения справа и применяй изменения явно.`}</p>
      </div>
      <div class="config-field-grid">
        ${group.fields.map((field) => `
          <label class="config-field" data-key="${field.key}">
            <div class="config-label-row">
              <span class="config-key">${displayLabel(localizedField(field, "label"))}</span>
              ${uiSettings.showLiteralEnvKeys ? `<span class="config-key-hint">${field.key}</span>` : ""}
            </div>
            <p class="config-help">${displayText(localizedField(field, "description") || "")}</p>
            ${renderFieldControl(selectedConfigPath, field)}
            <div class="config-meta">
              <span>${currentLanguage === "en" ? "Applied" : "Применено"}: <span class="mono-line">${field.secret && !isSecretFieldVisible(selectedConfigPath, field) ? maskSecretValue(field.applied) : field.applied}</span></span>
              ${uiSettings.showSourceFiles ? `<span>${currentLanguage === "en" ? "Source" : "Источник"}: <span class="mono-line">${field.source}</span></span>` : ""}
              ${field.pathPolicy ? `<span>${currentLanguage === "en" ? "Path policy" : "Политика пути"}: ${translatePathPolicy(field.pathPolicy)}</span>` : ""}
              ${field.pathExample ? `<span>${currentLanguage === "en" ? "Example" : "Пример"}: <span class="mono-line">${field.pathExample}</span></span>` : ""}
              ${field.validation ? `<span>${currentLanguage === "en" ? "Validation" : "Проверка"}: ${displayValidationMessage(field.validation.message || "")}</span>` : ""}
            </div>
          </label>
        `).join("")}
      </div>
    </section>
  `).join("");

  groups.querySelectorAll("input, select").forEach((control) => {
    control.addEventListener(control.tagName === "SELECT" ? "change" : "input", () => {
      const fieldNode = control.closest(".config-field");
      setStagedFieldValue(selectedConfigPath, fieldNode.dataset.key, control.value);
      updateDirtyState();
    });
  });

  presetList.querySelectorAll(".config-preset-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await previewConfigPreset(selectedConfigPath, button.dataset.presetId);
    });
  });

  groups.querySelectorAll("[data-generate-secret]").forEach((button) => {
    button.addEventListener("click", () => {
      const fieldNode = button.closest(".config-field");
      const field = findField(selectedConfigPath, button.dataset.generateSecret);
      if (!fieldNode || !field) return;
      const input = fieldNode.querySelector("input");
      if (!input) return;
      input.value = generateSecretValue(field);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      showToast(
        currentLanguage === "en" ? "Value generated" : "Значение сгенерировано",
        displayLabel(localizedField(field, "label")),
      );
      activityFeed.unshift(
        currentLanguage === "en"
          ? `Generated value for ${displayLabel(localizedField(field, "label"))}`
          : `Сгенерировано значение для ${displayLabel(localizedField(field, "label"))}`,
      );
      renderActivity();
    });
  });

  groups.querySelectorAll("[data-toggle-secret]").forEach((button) => {
    button.addEventListener("click", () => {
      const field = findField(selectedConfigPath, button.dataset.toggleSecret);
      if (!field) return;
      toggleSecretFieldVisibility(selectedConfigPath, field.key);
      renderConfig();
    });
  });

  groups.querySelectorAll("[data-copy-secret]").forEach((button) => {
    button.addEventListener("click", async () => {
      const fieldNode = button.closest(".config-field");
      const field = findField(selectedConfigPath, button.dataset.copySecret);
      if (!fieldNode || !field) return;
      const input = fieldNode.querySelector("input");
      if (!input) return;
      try {
        await copyToClipboard(input.value);
        showToast(
          currentLanguage === "en" ? "Value copied" : "Значение скопировано",
          displayLabel(localizedField(field, "label")),
        );
        activityFeed.unshift(
          currentLanguage === "en"
            ? `Copied value for ${displayLabel(localizedField(field, "label"))}`
            : `Скопировано значение для ${displayLabel(localizedField(field, "label"))}`,
        );
      } catch (_error) {
        showToast(
          currentLanguage === "en" ? "Copy failed" : "Не удалось скопировать",
          displayLabel(localizedField(field, "label")),
          "error",
        );
        activityFeed.unshift(
          currentLanguage === "en"
            ? `Could not copy value for ${displayLabel(localizedField(field, "label"))}`
            : `Не удалось скопировать значение для ${displayLabel(localizedField(field, "label"))}`,
        );
      }
      renderActivity();
    });
  });

  groups.querySelectorAll("input, select").forEach((control) => {
    const fieldNode = control.closest(".config-field");
    const field = findField(selectedConfigPath, fieldNode.dataset.key);
    if (String(control.value) !== String(field?.applied)) {
      control.classList.add("dirty");
    }
  });
}

async function previewConfigPreset(pathKey, presetId) {
  try {
    const response = await fetch(`/operator/config/${pathKey}/preset/preview`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ preset_id: presetId }),
    });
    if (!response.ok) {
      throw new Error(await responseDetail(response));
    }
    const payload = await response.json();
    selectedConfigVariantByPath[pathKey] = payload.variantId;
    Object.entries(payload.updates || {}).forEach(([key, value]) => {
      setStagedFieldValue(pathKey, key, value);
    });
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Preset staged: ${localizedField(payload, "title") || payload.title}`
        : `Пресет подготовлен: ${payload.title}`,
    );
    renderActivity();
    renderConfig();
    updateDirtyState();
  } catch (error) {
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Could not preview preset (${error.message || "internal error"})`
        : `Не удалось подготовить пресет (${error.message || "внутренняя ошибка"})`,
    );
    renderActivity();
  }
}

function findField(pathKey, fieldKey) {
  return (configState[pathKey]?.variants || [])
    .flatMap((variant) => variant.groups || [])
    .flatMap((group) => group.fields || [])
    .find((field) => field.key === fieldKey);
}

async function applyConfigChanges() {
  const updates = {};
  [...stagedFieldValues.entries()]
    .filter(([mapKey]) => mapKey.startsWith(`${selectedConfigPath}:`))
    .forEach(([mapKey, value]) => {
      updates[mapKey.split(":")[1]] = value;
    });
  if (!Object.keys(updates).length) {
    activityFeed.unshift(
      currentLanguage === "en"
        ? `No config changes to apply for ${pathTitle(runtimePaths[selectedConfigPath])}`
        : `Нет изменений конфига для применения в ${pathTitle(runtimePaths[selectedConfigPath])}`,
    );
    renderActivity();
    return;
  }
  try {
    const response = await fetch(`/operator/config/${selectedConfigPath}/apply`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ updates }),
    });
    if (!response.ok) {
      throw new Error(await responseDetail(response));
    }
    const payload = await response.json();
    configState[selectedConfigPath] = payload.config;
    [...stagedFieldValues.keys()]
      .filter((key) => key.startsWith(`${selectedConfigPath}:`))
      .forEach((key) => stagedFieldValues.delete(key));
    dirtyFields = new Set([...dirtyFields].filter((key) => !key.startsWith(`${selectedConfigPath}:`)));
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Applied ${payload.updatedKeys.length} config changes for ${pathTitle(runtimePaths[selectedConfigPath])}`
        : `Применено ${payload.updatedKeys.length} изменений конфига для ${pathTitle(runtimePaths[selectedConfigPath])}`,
    );
    rerenderAll();
  } catch (error) {
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Could not apply config for ${pathTitle(runtimePaths[selectedConfigPath])} (${error.message || "internal error"})`
        : `Не удалось применить конфиг для ${pathTitle(runtimePaths[selectedConfigPath])} (${error.message || "внутренняя ошибка"})`,
    );
    renderActivity();
  }
}

function updateDirtyState() {
  document.querySelector("#dirty-config-count").textContent = String(dirtyFields.size);
  document.querySelector("#config-dirty-indicator").textContent = dirtyFields.size
    ? (currentLanguage === "en" ? `Unsaved changes: ${dirtyFields.size}` : `Изменений: ${dirtyFields.size}`)
    : (currentLanguage === "en" ? "No unsaved changes" : "Нет несохранённых изменений");
}

function renderServices() {
  const grid = document.querySelector("#services-grid");
  const rows = serviceRows.filter((row) => row.path === selectedServicesPath);
  grid.innerHTML = rows.length ? rows.map((row) => `
    <div class="service-card">
      <div class="service-row">
          <div>
            <div class="source-label">${pathTitle(runtimePaths[row.path])}</div>
            <h4>${displayLabel(row.name)}</h4>
            <p>${displayText(localizedField(row, "note"))}</p>
            <div class="service-meta-list">
              <span class="service-meta-pill">${displayText(localizedField(row, "stage"))}</span>
              <span class="service-meta-pill mono-line">${row.endpoint}</span>
              <span class="service-meta-pill">${displayText(localizedField(row, "freshness"))}</span>
            </div>
          </div>
          <span class="status-pill ${chipClass(row.status)}">${cap(row.status)}</span>
      </div>
    </div>
  `).join("") : `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "No Service Checks Yet" : "Пока нет проверок сервисов"}</div>
      <p>${displayText("Backend ещё не опубликовал проверки для этого пути запуска.")}</p>
    </div>
  `;

  const pathDiagnostics = diagnostics.filter((item) => item.path === selectedServicesPath);
  document.querySelector("#diagnostics-status-pill").textContent = pathDiagnostics.length
    ? (currentLanguage === "en" ? `Diagnostics: ${pathDiagnostics.length}` : `Диагностика: ${pathDiagnostics.length}`)
    : (currentLanguage === "en" ? "No runtime diagnostics" : "Нет runtime-диагностики");
  document.querySelector("#diagnostics-list").innerHTML = pathDiagnostics.length ? pathDiagnostics.map((item) => `
      <div class="diagnostic-card">
        <div class="diagnostic-row">
          <div>
            <h4>${displayLabel(item.title)}</h4>
            <p>${displayText(item.body)}</p>
          </div>
          <span class="status-pill ${item.tone === "orange" ? "orange" : "cyan"}">${item.tone === "orange" ? (currentLanguage === "en" ? "Attention" : "Внимание") : (currentLanguage === "en" ? "Info" : "Информация")}</span>
        </div>
      </div>
    `).join("") : `
      <div class="metric-card">
        <div class="source-label">${currentLanguage === "en" ? "No Diagnostics" : "Нет диагностики"}</div>
        <p>${displayText("Backend ещё не сообщил блокеры парсинга или запуска для этого пути.")}</p>
      </div>
    `;

  renderMetricCards(
    "#services-metrics-grid",
    metricsSummary.services || [],
    "Сервисные метрики ещё не загружены",
    "Сводка Prometheus для `agent_api` и `UMS` появится после загрузки backend state.",
  );
  renderObservabilityLinks("#services-links-grid", "services");
  refreshLogFilterOptions();
  renderLogs(document.querySelector("#log-filter").value || "all");
}

function refreshLogFilterOptions() {
  const select = document.querySelector("#log-filter");
  const services = [...new Set(logLines.filter((line) => line.path === selectedServicesPath).map((line) => line.service))];
  const previous = select.value;
  select.innerHTML = `
    <option value="all">${currentLanguage === "en" ? `All logs for ${pathTitle(runtimePaths[selectedServicesPath])}` : `Все логи ${pathTitle(runtimePaths[selectedServicesPath])}`}</option>
    ${services.map((service) => `<option value="${service}">${cap(service)}</option>`).join("")}
  `;
  if (services.includes(previous)) {
    select.value = previous;
  } else {
    select.value = "all";
  }
}

function renderLogs(filter) {
  const logConsole = document.querySelector("#log-console");
  const visibleLines = logLines
    .filter((line) => line.path === selectedServicesPath)
    .filter((line) => filter === "all" || line.service === filter)
    .map((line) => translateSeedLogLine(line.text));
  logConsole.textContent = visibleLines.length ? visibleLines.join("\n") : displayText("Для этого пути запуска ещё нет загруженных логов. Сначала проверь диагностику и состояние сервисов выше.");
}

function renderDeploy() {
  const mode = deploySurface[selectedDeployMode];
  document.querySelector("#deploy-summary-title").textContent = displayLabel(localizedField(mode, "summaryTitle"));
  document.querySelector("#deploy-summary-pill").textContent = displayLabel(localizedField(mode, "label"));
  document.querySelector("#deploy-stages-title").textContent = displayLabel(localizedField(mode, "stagesTitle"));
  document.querySelector("#deploy-actions-title").textContent = displayLabel(localizedField(mode, "actionsTitle"));
  document.querySelector("#deploy-artifacts-title").textContent = displayLabel(localizedField(mode, "artifactsTitle"));
  document.querySelector("#deploy-logs-title").textContent = displayLabel(localizedField(mode, "logsTitle"));

  document.querySelector("#deploy-summary-grid").innerHTML = mode.summary.map((item) => `
    <div class="metric-card">
      <div class="metric-label">${displayLabel(localizedField(item, "label"))}</div>
      <strong>${localizedField(item, "value")}</strong>
      <p>${displayText(localizedField(item, "note"))}</p>
      <span class="status-pill ${item.tone}">${cap(item.tone === "lime" ? "ready" : item.tone === "cyan" ? "informational" : "needs attention")}</span>
    </div>
  `).join("");

  document.querySelector("#deploy-source-list").innerHTML = mode.sources.map((item) => `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Data Source" : "Источник данных"}</div>
      <strong class="mono-line">${item.path}</strong>
      <p>${displayText(localizedField(item, "role"))}</p>
      <span class="status-pill ${chipClass(item.freshness)}">${cap(item.freshness)}</span>
    </div>
  `).join("");

  document.querySelector("#deploy-stage-grid").innerHTML = mode.stages.map((item) => `
    <div class="service-card">
      <div class="service-row">
        <div>
          <div class="source-label">${item.script}</div>
          <h4>${displayLabel(localizedField(item, "title"))}</h4>
          <p>${displayText(localizedField(item, "body"))}</p>
        </div>
        <span class="status-pill ${chipClass(item.status)}">${cap(item.status)}</span>
      </div>
    </div>
  `).join("");

  document.querySelector("#deploy-action-grid").innerHTML = mode.actions.map((item) => `
    <div class="action-card">
      <h4>${displayLabel(localizedField(item, "title"))}</h4>
      <p>${displayText(localizedField(item, "body"))}</p>
      <div class="button-row" style="margin-top: 14px;">
        <button class="primary-button deploy-action-button" data-action="${item.action}" ${!getActionIdForDeployLabel(item.action) ? "disabled" : ""}>${displayLabel(localizedField(item, "action"))}</button>
      </div>
    </div>
  `).join("");

  document.querySelector("#deploy-artifact-list").innerHTML = mode.artifacts.map((item) => `
    <div class="artifact-card">
      <div class="service-row">
        <div>
          <h4>${displayLabel(localizedField(item, "title"))}</h4>
          <p>${displayText(localizedField(item, "body"))}</p>
        </div>
        <span class="status-pill ${chipClass(item.badge)}">${cap(item.badge)}</span>
      </div>
    </div>
  `).join("");

  document.querySelectorAll(".deploy-action-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const actionId = getActionIdForDeployLabel(button.dataset.action);
      if (!actionId) {
        activityFeed.unshift(`Действие ${button.dataset.action} ещё не подключено к исполняемой operator-команде`);
        renderActivity();
        return;
      }
      await runOperatorAction(actionId, {
        surface: "deploy",
        mode: selectedDeployMode,
        label: button.dataset.action,
      });
    });
  });

  renderMetricCards(
    "#deploy-metrics-grid",
    metricsSummary.deploy || [],
    "Deploy-метрики ещё не загружены",
    "Панель наблюдаемости для сборки и деплоя появится после загрузки backend-сводки.",
  );
  renderObservabilityLinks("#deploy-links-grid", selectedDeployMode === "build" ? ["deploy", "overview"] : "deploy");
  refreshDeployLogFilterOptions();
  renderDeployLogs(document.querySelector("#deploy-log-filter").value || "all");
}

function refreshDeployLogFilterOptions() {
  const select = document.querySelector("#deploy-log-filter");
  const stages = [...new Set(deployLogLines.filter((line) => line.mode === selectedDeployMode).map((line) => line.stage))];
  const previous = select.value;
  select.innerHTML = `
    <option value="all">${currentLanguage === "en" ? `All logs ${localizedField(deploySurface[selectedDeployMode], "label")}` : `Все логи ${deploySurface[selectedDeployMode].label}`}</option>
    ${stages.map((stage) => `<option value="${stage}">${cap(stage)}</option>`).join("")}
  `;
  if (stages.includes(previous)) {
    select.value = previous;
  } else {
    select.value = "all";
  }
}

function renderDeployLogs(filter) {
  const logConsole = document.querySelector("#deploy-log-console");
  logConsole.textContent = deployLogLines
    .filter((line) => line.mode === selectedDeployMode)
    .filter((line) => filter === "all" || line.stage === filter)
    .map((line) => line.text)
    .join("\n");
}

function renderMaintenance() {
  document.querySelector("#action-grid").innerHTML = maintenanceActions.map((item) => `
    <div class="action-card">
      <h4>${displayLabel(localizedField(item, "title"))}</h4>
      <p>${displayText(localizedField(item, "body"))}</p>
      <div class="button-row" style="margin-top: 14px;">
        <button class="primary-button action-button" data-action="${item.action}">${displayLabel(localizedField(item, "action"))}</button>
      </div>
    </div>
  `).join("");

  document.querySelector("#blocker-list").innerHTML = blockers.length ? blockers.map((item) => `
    <div class="blocker-card">
      <h4>${displayLabel(localizedField(item, "title"))}</h4>
      <p>${displayText(localizedField(item, "body"))}</p>
    </div>
  `).join("") : `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "No Blockers" : "Нет блокеров"}</div>
      <p>${currentLanguage === "en" ? "The backend has not published any maintenance blockers yet." : "Backend пока не опубликовал maintenance-блокеры."}</p>
    </div>
  `;

  [...document.querySelectorAll(".action-button")].forEach((button) => {
    button.addEventListener("click", async () => {
      await runMaintenanceAction(button.dataset.action);
    });
  });
}

function openReasonModal(pathKey) {
  const runtime = runtimePaths[pathKey];
  const modal = document.querySelector("#reason-modal");
  document.querySelector("#reason-modal-title").textContent = displayLabel(localizedField(runtime.whyUnavailable, "title"));
  document.querySelector("#reason-modal-content").innerHTML = `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Launch Command" : "Команда запуска"}</div>
      <strong class="mono-line">${runtime.launchSource}</strong>
    </div>
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Config Sources" : "Источники конфига"}</div>
      <strong class="mono-line">${formatConfigSources(runtime.configSources)}</strong>
    </div>
    <div class="config-group">
      <h4>${currentLanguage === "en" ? "Checks" : "Проверки"}</h4>
      ${runtime.whyUnavailable.checks.map(([label, state]) => `
        <div class="source-row">
          <span>${displayText(label)}</span>
          <span class="status-pill ${chipClass(state)}">${cap(state)}</span>
        </div>
      `).join("")}
    </div>
    <div class="config-group">
      <h4>${currentLanguage === "en" ? "What to Do" : "Что сделать"}</h4>
      <p>${displayText(localizedField(runtime.whyUnavailable, "remediation"))}</p>
      ${(currentLanguage === "en" ? (runtime.whyUnavailable.blockersEn || runtime.whyUnavailable.blockers) : runtime.whyUnavailable.blockers).length ? (currentLanguage === "en" ? (runtime.whyUnavailable.blockersEn || runtime.whyUnavailable.blockers) : runtime.whyUnavailable.blockers).map((blocker) => `<div class="warning-card"><p>${displayText(blocker)}</p></div>`).join("") : ""}
    </div>
  `;
  modal.showModal();
}

function wireRuntimeButtons() {
  document.querySelectorAll(".why-button").forEach((button) => {
    button.addEventListener("click", () => openReasonModal(button.dataset.path));
  });
  document.querySelectorAll(".launch-button").forEach((button) => {
    button.addEventListener("click", async () => {
      await triggerLaunchForPath(button.dataset.path);
    });
  });
  document.querySelectorAll(".safe-ports-button").forEach((button) => {
    button.addEventListener("click", async () => {
      setConfigPath(button.dataset.path);
      switchSection("config");
      await previewConfigPreset("container", "bundle-local-safe-ports");
    });
  });
}

function switchSection(sectionId) {
  activeSection = sectionId;
  sectionButtons.forEach((button) => button.classList.toggle("active", button.dataset.target === sectionId));
  Object.entries(sections).forEach(([key, section]) => section.classList.toggle("active", key === sectionId));
}

function initNavigation() {
  sectionButtons.forEach((button) => {
    button.addEventListener("click", () => switchSection(button.dataset.target));
  });
}

function initLogFilter() {
  const select = document.querySelector("#log-filter");
  select.addEventListener("change", () => renderLogs(select.value));
  const deploySelect = document.querySelector("#deploy-log-filter");
  deploySelect.addEventListener("change", () => renderDeployLogs(deploySelect.value));
}

function updateTopbarAction() {
  const button = document.querySelector("#topbar-start-button");
  if (!button) return;
  button.setAttribute("aria-expanded", launchMenuOpen ? "true" : "false");
  renderTopbarLaunchMenu();
}

function renderTopbarLaunchMenu() {
  const menu = document.querySelector("#topbar-launch-menu");
  if (!menu) return;
  const orderedPaths = ["native", "container"]
    .map((key) => runtimePaths[key])
    .filter(Boolean);
  menu.hidden = !launchMenuOpen;
  menu.innerHTML = orderedPaths.map((path) => `
    <button
      class="launch-menu-item ${path.key === selectedLaunchPath ? "active" : ""}"
      type="button"
      role="menuitem"
      data-path="${path.key}"
      ${!isLaunchable(path.key) ? "disabled" : ""}
    >
      <span class="launch-menu-copy">
        <strong>${pathTitle(path)}</strong>
        <span>${displayText(localizedField(path, "description"))}</span>
      </span>
      <span class="status-pill ${chipClass(path.status)}">${cap(path.status)}</span>
    </button>
  `).join("");
  [...menu.querySelectorAll(".launch-menu-item")].forEach((button) => {
    button.addEventListener("click", async () => {
      const { path } = button.dataset;
      launchMenuOpen = false;
      await triggerLaunchForPath(path);
    });
  });
}

function initTopbarAction() {
  const primaryButton = document.querySelector("#topbar-start-button");
  const menu = document.querySelector("#topbar-launch-menu");
  primaryButton.addEventListener("click", (event) => {
    event.stopPropagation();
    launchMenuOpen = !launchMenuOpen;
    updateTopbarAction();
  });
  document.addEventListener("click", (event) => {
    const group = document.querySelector("#topbar-launch-group");
    if (!group?.contains(event.target) && launchMenuOpen) {
      launchMenuOpen = false;
      updateTopbarAction();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && launchMenuOpen) {
      launchMenuOpen = false;
      updateTopbarAction();
    }
  });
  menu.addEventListener("click", (event) => event.stopPropagation());
  updateTopbarAction();
}

function initUiSettings() {
  syncUiSettingsControls();
  document.querySelector("#ui-settings-button").addEventListener("click", () => {
    document.querySelector("#ui-settings-modal").showModal();
  });
  document.querySelector("#ui-settings-modal-close").addEventListener("click", () => {
    document.querySelector("#ui-settings-modal").close();
  });
  document.querySelector("#settings-language-select").addEventListener("change", (event) => {
    setLanguage(event.target.value);
  });
  document.querySelector("#settings-show-keys").addEventListener("change", (event) => {
    uiSettings.showLiteralEnvKeys = event.target.checked;
    persistUiSettings();
    rerenderAll();
  });
  document.querySelector("#settings-show-sources").addEventListener("change", (event) => {
    uiSettings.showSourceFiles = event.target.checked;
    persistUiSettings();
    rerenderAll();
  });
  document.querySelector("#settings-show-secrets").addEventListener("change", (event) => {
    uiSettings.showSecretValues = event.target.checked;
    persistUiSettings();
    rerenderAll();
  });
  document.querySelector("#settings-metrics-density").addEventListener("change", (event) => {
    uiSettings.metricsDensity = event.target.value === "detailed" ? "detailed" : "compact";
    persistUiSettings();
    rerenderAll();
  });
}

function initConfigFlow() {
  renderPathSwitcher("#config-path-switcher", selectedConfigPath, setConfigPath);
  document.querySelector("#apply-config-button").addEventListener("click", applyConfigChanges);
}

function initServicesFlow() {
  const onServicesPathChange = (pathKey) => {
    selectedServicesPath = pathKey;
    renderPathSwitcher("#services-path-switcher", selectedServicesPath, onServicesPathChange);
    renderServices();
  };
  renderPathSwitcher("#services-path-switcher", selectedServicesPath, onServicesPathChange);
}

function initDeployFlow() {
  const modes = {
    build: { key: "build", name: currentLanguage === "en" ? "Build Bundle" : "Сборка bundle" },
    import: { key: "import", name: currentLanguage === "en" ? "Import / Deploy" : "Импорт / деплой" },
  };
  const onModeChange = (modeKey) => {
    selectedDeployMode = modeKey;
    const node = document.querySelector("#deploy-mode-switcher");
    node.innerHTML = Object.values(modes).map((mode) => `
      <button class="path-toggle ${mode.key === selectedDeployMode ? "active" : ""}" data-mode="${mode.key}">
        ${mode.name}
      </button>
    `).join("");
    [...node.querySelectorAll("[data-mode]")].forEach((button) => {
      button.addEventListener("click", () => onModeChange(button.dataset.mode));
    });
    renderDeploy();
  };
  onModeChange(selectedDeployMode);
}

function updateCounters() {
  const availablePaths = Object.values(runtimePaths).filter((path) => path.status === "available" || path.status === "partial").length;
  const healthyServices = serviceRows.filter((row) => row.status === "running").length;
  document.querySelector("#available-paths-count").textContent = `${availablePaths} / ${Object.keys(runtimePaths).length}`;
  document.querySelector("#healthy-services-count").textContent = serviceRows.length ? `${healthyServices} / ${serviceRows.length}` : "0 / 0";
}

async function responseDetail(response) {
  try {
    const payload = await response.json();
    const detail = payload.detail || payload.message || `http-${response.status}`;
    if (String(detail).startsWith("port-conflict:")) {
      const [, service, port, ...rest] = String(detail).split(":");
      return rest.join(":") || `${service} conflicts on port ${port}`;
    }
    return detail;
  } catch (_error) {
    return `http-${response.status}`;
  }
}

async function hydrateOperatorState() {
  try {
    const response = await fetch("/operator/state", {
      headers: {
        Accept: "application/json",
      },
    });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    runtimePaths = payload.runtimePaths || runtimePaths;
    hardwareMetrics = payload.hardwareMetrics || hardwareMetrics;
    warnings = payload.warnings || warnings;
    serviceRows = payload.serviceRows || serviceRows;
    configState = payload.configState || configState;
    diagnostics = payload.diagnostics || diagnostics;
    logLines = payload.logLines || logLines;
    maintenanceActions = payload.maintenanceActions || maintenanceActions;
    blockers = payload.blockers || blockers;
    activityFeed = payload.activityFeed || activityFeed;
    deploySurface = payload.deploySurface || deploySurface;
    deployLogLines = payload.deployLogLines || deployLogLines;
    metricsSummary = payload.metricsSummary || metricsSummary;
    grafanaLinks = payload.grafanaLinks || grafanaLinks;
    controlPlaneOnline = true;
  } catch (_error) {
    controlPlaneOnline = false;
    activityFeed.unshift(currentLanguage === "en" ? "Could not fetch backend state; showing the local fallback shell." : "Не удалось получить backend state; показываем локальный резервный shell.");
  }
}

async function hydrateActionCatalog() {
  try {
    const response = await fetch("/operator/actions/catalog", {
      headers: {
        Accept: "application/json",
      },
    });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    actionCatalog = payload.actions || [];
    actionCatalogById = Object.fromEntries(actionCatalog.map((item) => [item.action_id, item]));
    actionCatalogByTitle = Object.fromEntries(actionCatalog.map((item) => [item.title, item]));
  } catch (_error) {
    activityFeed.unshift(currentLanguage === "en" ? "Could not load the action catalog" : "Не удалось загрузить каталог действий");
  }
}

async function hydrateRuntimeHealth() {
  const entries = await Promise.all(
    Object.keys(runtimePaths).map(async (pathKey) => {
      try {
        const response = await fetch(`/operator/runtime/health/${pathKey}`, {
          headers: {
            Accept: "application/json",
          },
        });
        if (!response.ok) {
          return [pathKey, { status: "unknown", checkCount: 0, runningChecks: 0 }];
        }
        const payload = await response.json();
        return [pathKey, payload];
      } catch (_error) {
        return [pathKey, { status: "unknown", checkCount: 0, runningChecks: 0 }];
      }
    }),
  );
  runtimeHealthByPath = Object.fromEntries(entries);
}

function appendJobLogs(job, context) {
  const currentOffset = jobLogOffsets.get(job.job_id) || 0;
  const entries = (job.logs || []).slice(currentOffset);
  if (!entries.length) {
    return;
  }
  jobLogOffsets.set(job.job_id, job.logs.length);

  if (context.surface === "deploy") {
    entries.forEach((entry) => {
      deployLogLines.push({
        mode: context.mode || selectedDeployMode,
        stage: entry.stage || "process",
        text: `[${entry.timestamp}] ${entry.stream}: ${entry.message}`,
      });
    });
    renderDeployLogs(document.querySelector("#deploy-log-filter").value || "all");
    return;
  }

  entries.forEach((entry) => {
    logLines.push({
      path: context.pathKey || selectedLaunchPath,
      service: "operator",
      text: `[${entry.timestamp}] ${entry.stage}/${entry.stream}: ${entry.message}`,
    });
  });
  renderLogs(document.querySelector("#log-filter").value || "all");
}

async function pollJob(jobId, context) {
  try {
    const response = await fetch(`/operator/jobs/${jobId}`, {
      headers: {
        Accept: "application/json",
      },
    });
    if (!response.ok) {
      throw new Error(`job-poll-failed:${response.status}`);
    }
    const job = await response.json();
    runningJobs.set(jobId, {
      label: context.label,
      status: job.status,
      detail: job.current_stage || (currentLanguage === "en" ? "processing" : "выполнение"),
    });
    appendJobLogs(job, context);
    renderSystemStatus();

    if (job.status === "completed" || job.status === "failed") {
      clearInterval(jobPollTimers.get(jobId));
      jobPollTimers.delete(jobId);
      runningJobs.delete(jobId);
      lastJobSummary = {
        label: context.label,
        pathKey: context.pathKey || selectedLaunchPath,
        outcome: job.status,
        detail: job.status === "completed"
          ? (currentLanguage === "en" ? `completed (${job.current_stage || "done"})` : `завершено (${job.current_stage || "готово"})`)
          : `${currentLanguage === "en" ? `failed (${job.current_stage || "error"})` : `ошибка (${job.current_stage || "ошибка"})`}: ${job.logs?.at(-1)?.message || ""}`,
      };
      activityFeed.unshift(
        currentLanguage === "en"
          ? `${context.label} ${job.status === "completed" ? "completed" : "failed"} (${job.current_stage || "done"})`
          : `${context.label} ${job.status === "completed" ? "завершён" : "завершился с ошибкой"} (${job.current_stage || "done"})`,
      );
      await hydrateOperatorState();
      await hydrateRuntimeHealth();
      rerenderAll();
    }
  } catch (_error) {
      clearInterval(jobPollTimers.get(jobId));
      jobPollTimers.delete(jobId);
      runningJobs.delete(jobId);
      lastJobSummary = {
        label: context.label,
        pathKey: context.pathKey || selectedLaunchPath,
        outcome: "failed",
        detail: currentLanguage === "en" ? "job polling failed" : "не удалось опросить задачу",
      };
    activityFeed.unshift(currentLanguage === "en" ? `Could not poll job for ${context.label}` : `Не удалось опросить job для ${context.label}`);
    renderActivity();
    renderSystemStatus();
  }
}

async function runOperatorAction(actionId, context) {
  const spec = actionCatalogById[actionId];
  const allowPrivileged = Boolean(spec?.privileged);
  try {
    const response = await fetch("/operator/actions/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        action_id: actionId,
        allow_privileged: allowPrivileged,
      }),
    });
    if (!response.ok) {
      throw new Error(await responseDetail(response));
    }
    const job = await response.json();
    runningJobs.set(job.job_id, {
      label: context.label,
      pathKey: context.pathKey || selectedLaunchPath,
      status: "queued",
      detail: currentLanguage === "en" ? "queued" : "в очереди",
    });
    activityFeed.unshift(currentLanguage === "en" ? `${context.label} queued via ${actionId}` : `${context.label} поставлен в очередь через ${actionId}`);
    renderActivity();
    renderSystemStatus();
    jobLogOffsets.set(job.job_id, 0);
    const timerId = setInterval(() => {
      pollJob(job.job_id, context);
    }, 1000);
    jobPollTimers.set(job.job_id, timerId);
    await pollJob(job.job_id, context);
  } catch (error) {
    lastJobSummary = {
      label: context.label,
      pathKey: context.pathKey || selectedLaunchPath,
      outcome: "failed",
      detail: error.message || (currentLanguage === "en" ? "internal error" : "внутренняя ошибка"),
    };
    activityFeed.unshift(currentLanguage === "en" ? `Could not start action: ${context.label} (${error.message || "internal error"})` : `Не удалось запустить действие: ${context.label} (${error.message || "внутренняя ошибка"})`);
    renderActivity();
    renderSystemStatus();
  }
}

async function runMaintenanceAction(actionLabel) {
  try {
    if (actionLabel === "Перезагрузить источники конфига" || actionLabel === "Reload Config Sources") {
      await hydrateOperatorState();
      await hydrateRuntimeHealth();
      rerenderAll();
      activityFeed.unshift(currentLanguage === "en" ? "Config sources reloaded from Python operator state" : "Источники конфига перезагружены из Python operator state");
      renderActivity();
      return;
    }
    if (actionLabel === "Пересчитать пути запуска" || actionLabel === "Recompute Runtime Paths") {
      const response = await fetch("/operator/runtime/paths", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("runtime-paths-failed");
      }
      const payload = await response.json();
      runtimePaths = payload.runtimePaths || runtimePaths;
      await hydrateRuntimeHealth();
      rerenderAll();
      activityFeed.unshift(currentLanguage === "en" ? "Runtime paths recomputed from Python operator API" : "Пути запуска пересчитаны из Python operator API");
      renderActivity();
      return;
    }
    if (actionLabel === "Перезагрузить deploy surface" || actionLabel === "Reload Deploy Surface") {
      const response = await fetch(`/operator/deploy/${selectedDeployMode}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("deploy-review-failed");
      }
      const payload = await response.json();
      deploySurface[selectedDeployMode] = payload;
      renderDeploy();
      activityFeed.unshift(
        currentLanguage === "en"
          ? `Reloaded ${displayLabel(deploySurface[selectedDeployMode].label)} from Python operator API`
          : `Перезагружено ${displayLabel(deploySurface[selectedDeployMode].label)} из Python operator API`,
      );
      renderActivity();
      return;
    }
    activityFeed.unshift(
      currentLanguage === "en"
        ? `No dedicated Python maintenance action is wired for ${displayLabel(actionLabel)} yet`
        : `Для действия ${displayLabel(actionLabel)} пока нет отдельного Python действия обслуживания`,
    );
    renderActivity();
  } catch (_error) {
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Could not execute maintenance action: ${displayLabel(actionLabel)}`
        : `Не удалось выполнить действие обслуживания: ${displayLabel(actionLabel)}`,
    );
    renderActivity();
  }
}

async function init() {
  await hydrateOperatorState();
  await hydrateRuntimeHealth();
  await hydrateActionCatalog();
  localizeStaticShell();
  syncUiSettingsControls();
  initNavigation();
  rerenderAll();
  initLogFilter();
  initTopbarAction();
  initUiSettings();
  initConfigFlow();
  initServicesFlow();
  initDeployFlow();
  updateDirtyState();
  updateCounters();

  document.querySelector("#reason-modal-close").addEventListener("click", () => {
    document.querySelector("#reason-modal").close();
  });
}

init();
