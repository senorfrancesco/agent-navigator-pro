let runtimePaths = {
  native: {
    key: "native",
    name: "Нативный запуск",
    status: "available",
    description: "Локальный developer-runtime с orchestration через launcher, прямым контролем путей моделей и видимостью сервисов.",
    launchSource: "scripts/launcher.sh --target native --profile adaptive",
    configSources: [
      { path: "backend/.env", role: "канонический env runtime и сервисов", freshness: "present" },
      { path: "backend/.env.native", role: "переопределения для нативного пути запуска", freshness: "missing" },
      { path: "backend/.env.runtime", role: "сгенерированный применённый план runtime", freshness: "missing" },
      { path: "backend/.env.hardware.override", role: "постоянные overrides размещения", freshness: "present" },
    ],
    summary: [
      { label: "Профили запуска", value: "4 готовы", tone: "lime" },
      { label: "Цепочка env", value: "разрешена", tone: "cyan" },
      { label: "Состояние среды", value: "стабильно", tone: "lime" },
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
    description: "Путь через офлайн-бандл с загрузкой образов, деплоем, проверками согласованности и запуском артефактов на сервере.",
    launchSource: "deploy/offline_bundle/scripts/run_offline_bundle.sh",
    configSources: [
      { path: "deploy/offline_bundle/env.bundle", role: "контракт env для бандла", freshness: "present" },
      { path: "deploy/offline_bundle/compose.offline.yaml", role: "топология контейнеров", freshness: "present" },
      { path: "deploy/offline_bundle/manifest.json", role: "манифест артефактов", freshness: "present" },
      { path: "deploy/offline_bundle/runtime_report.json", role: "последний импортированный отчёт о среде", freshness: "stale" },
    ],
    summary: [
      { label: "Профили запуска", value: "1 заблокирован / 2 доступны для проверки", tone: "orange" },
      { label: "Файлы бандла", value: "есть", tone: "lime" },
      { label: "Состояние Docker engine", value: "заблокировано", tone: "orange" },
    ],
    profiles: [
      { title: "Загрузка архивов образов", status: "unavailable", body: "Архивы образов можно загрузить в Docker только когда доступны движок и сокет." },
      { title: "Запуск офлайн-бандла", status: "partial", body: "Основной пользовательский путь идёт через run_offline_bundle.sh и deploy.sh, а не через dev compose launcher checkout-репозитория." },
      { title: "Импорт и проверка согласованности", status: "partial", body: "Манифест и env.bundle доступны для чтения; поверхность деплоя можно изучать ещё до старта среды." },
    ],
    whyUnavailable: {
      title: "Состояние офлайн-бандла и контейнерного запуска",
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
      remediation: "Этот путь запуска ведёт в deploy/offline_bundle/scripts: сначала можно загрузить архивы образов, затем выполнить деплой или запуск офлайн-бандла без dev-сборки checkout-репозитория.",
    },
  },
};

let hardwareMetrics = [
  { label: "Определённая ОС", value: "Ожидаем состояние оператора", note: "Факты о железе приходят из backend." },
  { label: "Видимость GPU", value: "Неизвестно", note: "Host probe ещё не завершился." },
  { label: "CPU / память", value: "Неизвестно", note: "Host probe ещё не завершился." },
  { label: "Предлагаемый профиль среды", value: "Неизвестно", note: "Предлагаемые значения появятся после загрузки состояния backend." },
  { label: "Предлагаемый контекст", value: "Неизвестно", note: "Рекомендованные значения появятся после загрузки backend state." },
  { label: "Целевой архив", value: "Неизвестно", note: "Имя архива приходит из backend." },
];

let warnings = [];
const stagedPathValidations = new Map();
const pathValidationTimers = new Map();

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
          { key: "BUNDLE_MODEL_ROOT", label: "Корень моделей bundle", value: "/opt/llm-tools-platform/models", suggested: "/opt/llm-tools-platform/models", applied: "/opt/llm-tools-platform/models", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_UPLOADS_ROOT", label: "Корень uploads", value: "/opt/llm-tools-platform/uploads", suggested: "/opt/llm-tools-platform/uploads", applied: "/opt/llm-tools-platform/uploads", source: "deploy/offline_bundle/env.bundle" },
          { key: "BUNDLE_REPORTS_ROOT", label: "Корень reports", value: "/opt/llm-tools-platform/reports", suggested: "/opt/llm-tools-platform/reports", applied: "/opt/llm-tools-platform/reports", source: "deploy/offline_bundle/env.bundle" },
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
    label: "Сборка офлайн-бандла",
    summaryTitle: "Состояние сборки офлайн-бандла",
    stagesTitle: "Стадии сборки",
    actionsTitle: "Действия сборки",
    artifactsTitle: "Выходные артефакты и упаковка архива",
    logsTitle: "Лог сборки и экспорта",
    summary: [
      { label: "Корень бандла", value: "deploy/offline_bundle", note: "Канонический рабочий каталог сборки.", tone: "cyan" },
      { label: "Целевой архив", value: "llm-tools-platform-offline-bundle_v1.0.tar.gz", note: "Единый переносимый артефакт для передачи.", tone: "lime" },
      { label: "Матрица системных пакетов", value: "ubuntu-24.04 готов", note: "Собрано через локальный сценарий APT-бандла.", tone: "lime" },
      { label: "Состояние манифеста", value: "проверен", note: "Выход готов к деплою и проходит проверку бандла.", tone: "lime" },
    ],
    sources: [
      { path: "deploy/offline_bundle/scripts/build_bundle.sh", role: "верхнеуровневый оркестратор сборки и экспорта", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/build_host_apt_bundle.sh", role: "сборка APT-бандла системных пакетов", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/export_images.sh", role: "сборка и экспорт Docker-образов", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/generate_manifest.py", role: "генерация манифеста", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/validate_bundle.py", role: "проверка готовности к деплою", freshness: "present" },
    ],
    stages: [
      { title: "Wheelhouse", status: "running", body: "Подготовить offline Python wheelhouse перед экспортом images.", script: "build_wheelhouse.sh" },
      { title: "Образы", status: "running", body: "Собрать backend/UMS/Chainlit images и экспортировать их в tar-архивы.", script: "export_images.sh" },
      { title: "Модели", status: "running", body: "Экспортировать layout моделей для env.bundle и манифеста.", script: "export_models.sh" },
      { title: "Состояние", status: "running", body: "Экспортировать env среды и сохранённое состояние в bundle/state.", script: "export_state.sh" },
      { title: "Системные пакеты", status: "running", body: "Собрать APT-бандл с точными версиями для целевой Ubuntu.", script: "build_host_apt_bundle.sh" },
      { title: "Упаковка архива", status: "partial", body: "Упаковать весь deploy/offline_bundle в единый tar.gz артефакт.", script: "tar czf ..." },
    ],
    actions: [
      { title: "Собрать офлайн-бандл", body: "Запустить каноническую оркестрацию сборки и экспорта.", action: "Собрать офлайн-бандл" },
      { title: "Собрать APT-бандл", body: "Подготовить системные пакеты Ubuntu и lock-файл версий для офлайн-установки.", action: "Собрать APT-бандл" },
      { title: "Экспортировать образы", body: "Собрать офлайн-образы и сохранить их как архивы образов.", action: "Экспортировать образы" },
      { title: "Сгенерировать манифест", body: "Сгенерировать манифест и проверить полноту готовности к деплою.", action: "Сгенерировать манифест" },
      { title: "Упаковать tar.gz", body: "Создать единый переносимый архив из deploy/offline_bundle.", action: "Упаковать tar.gz" },
      { title: "Посмотреть логи сборки", body: "Открыть stage-oriented output сборки и экспорта.", action: "Посмотреть логи сборки" },
    ],
    artifacts: [
      { title: "Архив бандла", body: "llm-tools-platform-offline-bundle_v1.0.tar.gz · 18.6 GB · готов к передаче", badge: "ready" },
      { title: "Архивы образов", body: "backend-app, ums, chainlit, vllm сохранены в deploy/offline_bundle/images", badge: "ready" },
      { title: "Пакет системных пакетов", body: "ubuntu-24.04 pool + Packages.gz + versions.lock.json", badge: "ready" },
      { title: "Манифест", body: "manifest.json содержит контрольные суммы для images, state, models, wheelhouse и host_packages", badge: "ready" },
    ],
  },
  import: {
    label: "Импорт / деплой",
    summaryTitle: "Состояние импорта / деплоя",
    stagesTitle: "Стадии на целевом хосте",
    actionsTitle: "Действия импорта и деплоя",
    artifactsTitle: "Приём архива и распакованное состояние бандла",
    logsTitle: "Лог импорта / деплоя",
    summary: [
      { label: "Приём архива", value: "выбран", note: "Один tar.gz принимается как канонический переносимый артефакт.", tone: "lime" },
      { label: "Корень распаковки", value: "/opt/llm-tools-platform/offline_bundle", note: "Бандл распакован в целевой корень среды.", tone: "cyan" },
      { label: "Состояние деплоя", value: "частично", note: "Бандл проходит проверку, но среда на хосте ещё ждёт установку пакетов и загрузку образов.", tone: "orange" },
      { label: "Каноническая точка входа", value: "deploy.sh", note: "run_offline_bundle.sh остаётся верхнеуровневой обёрткой для удобного запуска.", tone: "cyan" },
    ],
    sources: [
      { path: "deploy/offline_bundle/scripts/install_host_apt_bundle.sh", role: "офлайн-установка и проверка системных пакетов", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/deploy.sh", role: "канонический сценарий деплоя", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/load_images.sh", role: "импорт Docker-образов", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/restore_state.sh", role: "восстановление состояния бандла", freshness: "present" },
      { path: "deploy/offline_bundle/scripts/run_offline_bundle.sh", role: "обёртка верхнего уровня для удобного запуска", freshness: "present" },
    ],
    stages: [
      { title: "Приём архива", status: "running", body: "Принять tar.gz артефакт и зарегистрировать метаданные импорта.", script: "UI archive selector" },
      { title: "Распаковка", status: "running", body: "Распаковать бандл в целевой корень офлайн-бандла.", script: "tar xzf ..." },
      { title: "Проверка бандла", status: "running", body: "Проверить env, models, манифест, образы и системные пакеты.", script: "validate_bundle.py --mode deploy" },
      { title: "Установка APT-бандла", status: "partial", body: "Установить или проверить офлайн-системные пакеты для обнаруженного дистрибутива.", script: "install_host_apt_bundle.sh" },
      { title: "Загрузка образов", status: "partial", body: "Загрузить Docker-образы из архивов бандла.", script: "load_images.sh" },
      { title: "Деплой и проверка", status: "partial", body: "Восстановить состояние, запустить deploy.sh и затем проверить среду выполнения.", script: "deploy.sh + verify_runtime.sh" },
    ],
    actions: [
      { title: "Выбрать архив", body: "Выбрать один tar.gz архив офлайн-бандла.", action: "Выбрать архив" },
      { title: "Распаковать бандл", body: "Распаковать архив и подготовить целевой корень бандла.", action: "Распаковать бандл" },
      { title: "Проверить системные пакеты", body: "Запустить проверку системных пакетов перед установкой.", action: "Проверить системные пакеты" },
      { title: "Установить APT-бандл", body: "Установить офлайн-системные пакеты точной версии и конфигурацию среды.", action: "Установить APT-бандл" },
      { title: "Деплоить среду", body: "Запустить канонический сценарий деплоя: загрузка образов, восстановление состояния, compose up и проверка.", action: "Деплоить среду" },
      { title: "Запустить офлайн-бандл", body: "Использовать верхнеуровневый launcher после прохождения обязательного деплоя.", action: "Запустить офлайн-бандл" },
    ],
    artifacts: [
      { title: "Приём архива", body: "llm-tools-platform-offline-bundle_v1.0.tar.gz получен с машины сборки", badge: "ready" },
      { title: "Распакованный бандл", body: "compose.offline.yaml, env.bundle, manifest.json, images/, models/ и state/ доступны", badge: "ready" },
      { title: "Установка системных пакетов", body: "versions.lock.json совпадает с целевым дистрибутивом, установка ещё ждёт настройки среды", badge: "partial" },
      { title: "Деплой среды", body: "Ожидается загрузка образов и выполнение deploy.sh на целевом хосте", badge: "partial" },
    ],
  },
};

let deployLogLines = [
  { mode: "build", stage: "build", text: "[10:04:03] build_bundle: starting export orchestration in deploy/offline_bundle" },
  { mode: "build", stage: "wheelhouse", text: "[10:04:18] build_wheelhouse: ok wheelhouse prepared for offline image builds" },
  { mode: "build", stage: "images", text: "[10:06:10] export_images: saved llm-tools-platform-backend-app-offline_v1.0.tar" },
  { mode: "build", stage: "host", text: "[10:07:42] build_host_apt_bundle: host-apt-bundle:ok distro=ubuntu-24.04" },
  { mode: "build", stage: "manifest", text: "[10:08:05] generate_manifest: manifest.json refreshed with checksums and build metadata" },
  { mode: "build", stage: "pack", text: "[10:08:34] archive: created llm-tools-platform-offline-bundle_v1.0.tar.gz from deploy/offline_bundle" },
  { mode: "import", stage: "archive", text: "[11:12:01] intake: selected llm-tools-platform-offline-bundle_v1.0.tar.gz for import" },
  { mode: "import", stage: "archive", text: "[11:12:08] unpack: extracted archive into /opt/llm-tools-platform/offline_bundle" },
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
let helpDrawerOpen = false;
let mobileNavOpen = false;
let dirtyFields = new Set();
let stagedFieldValues = new Map();
let revealedSecretFields = new Set();
let hiddenSecretFields = new Set();
let selectedConfigVariantByPath = {
  native: "runtime",
  container: "published_ports",
};
let pathBrowserState = {
  pathKey: null,
  fieldKey: null,
  kind: "file",
  cwd: "",
  entries: [],
  currentValue: "",
};
let actionCatalog = [];
let actionCatalogById = {};
let actionCatalogByTitle = {};
let jobPollTimers = new Map();
let jobLogOffsets = new Map();
let jobPollFailures = new Map();
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
let localRuntimeStateOverrides = new Map();
const helpContent = {
  overview: {
    title: { ru: "Обзор", en: "Overview" },
    body: {
      ru: "Короткая сводка системы: пути запуска, железо, сервисы и блокеры. Если что-то требует внимания, это должно читаться без скролла.",
      en: "A short system summary: runtime paths, hardware, services, and blockers. Attention states should be visible without scrolling.",
    },
    terms: [
      { ru: "Путь запуска: канонический сценарий старта среды.", en: "Runtime path: canonical scenario used to start the environment." },
      { ru: "Блокеры: то, что мешает запуску или стабильной работе.", en: "Blockers: conditions that prevent launch or stable runtime." },
    ],
    actions: [
      { ru: "Если видишь жёлтый или красный статус, открой «Запуск» или «Сервисы».", en: "If you see a warning or failure state, open Launch or Services." },
      { ru: "Подробные объяснения держи здесь, а не в карточках дашборда.", en: "Keep explanations here instead of inside dashboard cards." },
    ],
  },
  launch: {
    title: { ru: "Запуск", en: "Launch" },
    body: {
      ru: "Рабочая поверхность для выбора пути и контроля текущего старта. Кнопка и лог важнее описательных абзацев.",
      en: "Workspace for choosing a path and tracking the current start flow. The button and log matter more than explanatory copy.",
    },
    terms: [
      { ru: "Профиль запуска: конкретный подрежим внутри выбранного пути.", en: "Launch profile: specific sub-mode inside the selected runtime path." },
      { ru: "Проверки: prerequisite-сигналы перед стартом.", en: "Checks: prerequisite signals before start." },
    ],
    actions: [
      { ru: "Если старт идёт долго, смотри лог и затем вкладку «Сервисы».", en: "If launch takes too long, inspect the log and then Services." },
      { ru: "Если путь недоступен, открой «Показать проверки».", en: "If the path is unavailable, open Show Checks." },
    ],
  },
  config: {
    title: { ru: "Конфиг", en: "Config" },
    body: {
      ru: "Редактируй только видимые runtime-поля, затем явно применяй изменения. Источник и applied value важнее описания.",
      en: "Edit only the visible runtime fields, then apply changes explicitly. Source and applied value matter more than description.",
    },
    terms: [
      { ru: "Applied: фактическое значение после последнего применения.", en: "Applied: effective value after the last apply." },
      { ru: "Suggested: рекомендованное, но не автоматически применённое значение.", en: "Suggested: recommended but not auto-applied value." },
    ],
    actions: [
      { ru: "Если не уверен в поле, сначала проверь источник, потом правь значение.", en: "If you're unsure about a field, check its source before editing." },
    ],
  },
  services: {
    title: { ru: "Сервисы", en: "Services" },
    body: {
      ru: "Здесь оператор видит, что реально работает, что деградировало и какие логи подтверждают состояние.",
      en: "Here the operator sees what is really running, what degraded, and which logs confirm the state.",
    },
    terms: [
      { ru: "Готово: сервис отвечает и проходит проверки.", en: "Ready: the service responds and passes checks." },
      { ru: "Частично: часть сигналов есть, но запуск ещё не завершён.", en: "Partial: some signals are present, but startup is not finished." },
    ],
    actions: [
      { ru: "Пустую диагностику можно игнорировать; ориентируйся на список сервисов и лог.", en: "Empty diagnostics can be ignored; rely on the service list and log." },
    ],
  },
  deploy: {
    title: { ru: "Сборка / Деплой", en: "Build / Deploy" },
    body: {
      ru: "Пошаговый поток для офлайн-бандла: этап, лог, действия и артефакты. Источники нужны редко и не должны забивать экран.",
      en: "A step-by-step offline bundle flow: stage, log, actions, and artifacts. Sources are secondary and should not dominate the screen.",
    },
    terms: [
      { ru: "Стадия: текущий шаг канонического потока сборки и деплоя.", en: "Stage: current step in the canonical build/deploy flow." },
      { ru: "Артефакт: результат сборки или импорта, который можно проверить отдельно.", en: "Artifact: build or import output that can be verified separately." },
    ],
    actions: [
      { ru: "Если поток завис, сначала смотри stepper и лог, потом уже отдельные действия.", en: "If the flow stalls, inspect the stepper and log before individual actions." },
    ],
  },
  maintenance: {
    title: { ru: "Действия", en: "Actions" },
    body: {
      ru: "Safe maintenance-операции без toolbox-шума. Если блокеров нет, страница остаётся короткой и служебной.",
      en: "Safe maintenance operations without toolbox noise. When there are no blockers, the page should stay short and utilitarian.",
    },
    terms: [
      { ru: "Перечитать: заново загрузить данные из источников.", en: "Reload: read data again from source files or APIs." },
      { ru: "Пересчитать: заново вычислить производные значения.", en: "Recompute: derive computed values again." },
    ],
    actions: [
      { ru: "Используй эти действия как recovery flow, а не как постоянную навигацию.", en: "Use these actions as recovery flow, not as primary navigation." },
    ],
  },
};

function trimUiCopy(value, limit = 96) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

function summarizeJobForSidebar(label, detail) {
  const safeLabel = trimUiCopy(label, 48);
  const safeDetail = trimUiCopy(detail, 72);
  if (!safeLabel) {
    return safeDetail || (currentLanguage === "en" ? "No active job" : "Нет активной задачи");
  }
  if (!safeDetail) {
    return safeLabel;
  }
  return `${safeLabel}: ${safeDetail}`;
}

function setLocalRuntimeOverride(pathKey, override) {
  if (!pathKey) return;
  if (!override) {
    localRuntimeStateOverrides.delete(pathKey);
    return;
  }
  localRuntimeStateOverrides.set(pathKey, override);
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function mergeUniqueLogs(existing, incoming, keyForItem) {
  const merged = [];
  const seen = new Set();
  [...existing, ...incoming].forEach((item) => {
    const key = keyForItem(item);
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(item);
  });
  return merged;
}

function summarizeFailureDetail(detail) {
  const text = String(detail || "").replace(/\s+/g, " ").trim();
  if (!text) {
    return currentLanguage === "en" ? "Runtime action failed" : "Ошибка runtime-действия";
  }
  if (text.startsWith("unknown-action:")) {
    const actionId = text.split(":").slice(1).join(":");
    return currentLanguage === "en"
      ? `Action is not available in the backend catalog: ${actionId}`
      : `Действие отсутствует в backend catalog: ${actionId}`;
  }
  if (text.includes("missing-checksummed-path:")) {
    return currentLanguage === "en"
      ? "Manifest checksums are stale. Regenerate manifest.json before retrying deploy."
      : "Чексуммы manifest устарели. Пересобери manifest.json перед повторным deploy.";
  }
  return text;
}

function operatorShellHealth() {
  if (!controlPlaneOnline) {
    return {
      status: "pending",
      label: currentLanguage === "en" ? "Control plane pending" : "Контур не подтверждён",
      detail: currentLanguage === "en" ? "The operator backend has not answered yet." : "Operator backend ещё не подтвердил readiness.",
    };
  }
  return {
    status: "healthy",
    label: currentLanguage === "en" ? "System running" : "Система работает",
    detail: currentLanguage === "en"
      ? "Operator UI and control-plane routes respond."
      : "Operator UI и control-plane routes отвечают.",
  };
}

async function settleRuntimeState(pathKey, expectRunning, attempts = 8, delayMs = 1000) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await hydrateOperatorState();
    await hydrateRuntimeHealth();
    rerenderAll();
    const state = deriveLaunchRuntimeState(pathKey);
    const running = isPathRunning(pathKey);
    if (expectRunning ? running : !running) {
      return state;
    }
    await sleep(delayMs);
  }
  return deriveLaunchRuntimeState(pathKey);
}

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
  if (["partial", "degraded", "stale", "readable", "warmup", "not_started"].includes(status)) return "cyan";
  if (["blocked", "unavailable", "missing"].includes(status)) return "orange";
  return "neutral";
}

function cap(value) {
  const translated = currentLanguage === "en" ? {
    available: "Ready",
    running: "Running",
    not_started: "Not started",
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
    not_started: "Не запущено",
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
  if (path.key === "container") return currentLanguage === "en" ? "Offline Bundle / Containers" : "Офлайн-бандл / Контейнеры";
  return path.name;
}

function localizedField(item, key) {
  if (!item) return "";
  if (currentLanguage === "en" && item[`${key}En`]) return item[`${key}En`];
  return item[key];
}

function localizedHelpText(entry) {
  if (!entry) return "";
  return currentLanguage === "en" ? (entry.en || entry.ru || "") : (entry.ru || entry.en || "");
}

function currentHelpContent() {
  return helpContent[activeSection] || helpContent.overview;
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
    "Предлагаемый профиль среды": "Suggested Runtime Profile",
    "Suggested context budget": "Suggested Context Budget",
    "Предлагаемый бюджет контекста": "Suggested Context Budget",
    "Bundle archive target": "Bundle Archive Target",
    "Целевой архив": "Bundle Archive Target",
    "Runtime Preflight": "Runtime Preflight",
    "Launcher": "Launcher",
    "Bundle Manifest": "Bundle Manifest",
    "Manifest bundle": "Bundle Manifest",
    "Манифест офлайн-бандла": "Bundle Manifest",
    "Docker Socket": "Docker Socket",
    "Docker socket": "Docker Socket",
    "Бинарник Docker": "Docker CLI",
    "Local Safe Ports": "Local Safe Ports",
    "Target Default Ports": "Target Default Ports",
    "Adaptive recommended": "Adaptive Recommended",
    "Адаптивный набор": "Adaptive Recommended",
    "CPU fallback": "CPU Fallback",
    "CPU-режим": "CPU Fallback",
    "Реестр моделей и пути": "Model Registry & Paths",
    "GPU / Размещение": "GPU / Placement",
    "LLM / Контекст": "LLM / Context",
    "Параметры моделей": "Model Runtime",
    "Секреты и доступ": "Secrets / Access",
    "Профили Chainlit": "Chainlit Profiles",
    "Безопасные локальные порты": "Local Safe Ports",
    "Целевые стандартные порты": "Target Default Ports",
    "Настройки backend и runtime": "Runtime / Backend",
    "Артефакты и пути": "Artifact Mounts",
    "Эмбеддеры и модели": "Embedders / Models",
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
    "События деградации": "Fallback Events",
  } : {
    "Detected host OS": "Определённая ОС",
    "GPU visibility": "Видимость GPU",
    "GPU inventory": "Инвентарь GPU",
    "CPU / memory": "CPU / память",
    "Suggested runtime profile": "Предлагаемый профиль среды",
    "Suggested context budget": "Предлагаемый бюджет контекста",
    "Bundle archive target": "Целевой архив",
    "Runtime Preflight": "Предпроверка runtime",
    "Launcher": "Launcher",
    "Bundle Manifest": "Манифест офлайн-бандла",
    "Docker Socket": "Docker socket",
    "Docker CLI": "Бинарник Docker",
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
    "Model Registry & Paths": "Реестр моделей и пути",
    "GPU / Placement": "GPU / Размещение",
    "LLM / Context": "LLM / Контекст",
    "Model Runtime": "Параметры моделей",
    "Secrets / Access": "Секреты и доступ",
    "Ports & URLs": "Порты и URL",
    "Chainlit Profiles": "Профили Chainlit",
    "Parsing / Compare": "Парсинг и сравнение",
    "Local Safe Ports": "Безопасные локальные порты",
    "Target Default Ports": "Целевые стандартные порты",
    "Runtime / Backend": "Настройки backend и runtime",
    "Monitoring": "Мониторинг",
    "Artifact Mounts": "Артефакты и пути",
    "Embedders / Models": "Эмбеддеры и модели",
    "Agent API port": "Порт Agent API",
    "Prometheus port": "Порт Prometheus",
    "Grafana port": "Порт Grafana",
    "Grafana admin user": "Пользователь Grafana",
    "Grafana admin password": "Пароль Grafana",
    "Fallback-событий": "События деградации",
    "Offline strict JSON": "Offline strict JSON",
    "Preflight required": "Обязательный preflight",
    "Parity smoke required": "Обязательный parity smoke",
    "Adaptive recommended": "Адаптивный набор",
    "CPU fallback": "CPU-режим",
    "Single GPU": "Одна GPU",
    "Multi GPU": "Несколько GPU",
    "Parser safe mode": "Безопасный парсинг",
    "Parser strict mode": "Строгий парсинг",
    "Launch Profiles": "Профили запуска",
    "env Chain": "Цепочка env",
    "Runtime State": "Состояние среды",
    "Bundle Files": "Файлы бандла",
    "Docker Engine State": "Состояние Docker engine",
    "Build Bundle": "Сборка офлайн-бандла",
    "Import / Deploy": "Импорт / деплой",
    "Build Actions": "Действия сборки",
    "Import and Deploy Actions": "Действия импорта и деплоя",
    "Build Stages": "Стадии сборки",
    "Target Host Stages": "Стадии на целевом хосте",
    "Build and Export Log": "Лог сборки и экспорта",
    "Import and Deploy Log": "Лог импорта / деплоя",
    "Build": "Сборка",
    "Wheelhouse": "Wheelhouse",
    "Images": "Образы",
    "Host": "Хост",
    "Manifest": "Манифест",
    "Pack": "Упаковка",
    "Operator": "Operator",
    "Launcher": "Launcher",
  };
  return labels[label] || label;
}

function displayText(text) {
  const texts = currentLanguage === "en" ? {
    "канонический env runtime и сервисов": "canonical env for runtime and services",
    "переопределения для нативного пути запуска": "overrides for the native runtime path",
    "сгенерированный применённый план runtime": "generated applied runtime plan",
    "постоянные overrides размещения": "persistent placement overrides",
    "контракт env для бандла": "env contract for the bundle",
    "топология контейнеров": "container topology",
    "манифест артефактов": "artifact manifest",
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
    "Загрузка архивов образов": "Bundle image loading",
    "Запуск офлайн-бандла": "Offline bundle runtime",
    "Импорт и проверка согласованности": "Import and parity validation",
    "Минимальный API + orchestration flow для workflow и endpoint checks.": "Minimal API and orchestration flow for workflow and endpoint checks.",
    "Путь панели оператора с backend-оркестрацией и локальным состоянием сессии.": "Operator UI path with backend orchestration and local session state.",
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
    "Состояние бандла видно сразу, а запуск контейнеров зависит от Docker Engine и доступа к сокету.": "Bundle state is visible immediately, while container startup still depends on Docker engine and socket access.",
    "Проверки native и host probes берутся из репозитория.": "Native checks and host probes are derived from repository state.",
    "Нет предупреждений": "No warnings",
    "Backend не сообщил о дополнительных блокерах runtime сверх текущего состояния доступности.": "Backend has not reported additional runtime blockers beyond the current availability state.",
    "Пока нет активности оператора": "No operator activity yet",
    "Здесь появятся действия, применение конфига и завершённые jobs.": "Actions, config applies, and completed jobs will appear here.",
    "Факты о железе собираются на стороне backend.": "Host facts are gathered server-side.",
    "Используется для объяснения возможностей runtime и предлагаемых значений.": "Used to explain runtime capabilities and suggested defaults.",
    "Все обнаруженные NVIDIA GPU перечислены для multi-GPU planning.": "All detected NVIDIA GPUs are listed for multi-GPU aware planning.",
    "Нужно для оценки размеров среды и бандла.": "Used for runtime and bundle sizing context.",
    "Соответствует каноническому сценарию запуска для применённого плана.": "Matches the canonical launcher flow for the applied plan.",
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
    "Локальный runtime разработчика с запуском через launcher, управлением путями моделей и видимостью сервисов.": "Local developer runtime launched through the canonical launcher with model-path control and service visibility.",
    "Локальная среда запуска с оркестрацией через launcher, прямым контролем путей моделей и видимостью сервисов.": "Local developer runtime with launcher orchestration, direct model-path control, and visible service state.",
    "Путь запуска через бандл и compose для офлайн-развёртывания, проверки согласованности и запуска артефактов.": "Runtime path through bundle and compose for offline rollout, parity checks, and artifact-based deployment.",
    "Путь через офлайн-бандл с загрузкой образов, деплоем, проверками согласованности и запуском артефактов на сервере.": "Offline bundle path for image loading, deploy, parity checks, and artifact-based server startup.",
    "Нативный путь запуска доступен. Открой «Конфиг», чтобы посмотреть приоритет источников, или перейди в «Запуск» и выбери профиль.": "Native runtime is available. Open Config to inspect source precedence or go to Launch and choose a profile.",
    "Профиль для локального запуска рядом с текущей панелью оператора без конфликта опубликованных портов.": "Profile for local bundle execution next to the current operator UI without published-port conflicts.",
    "Подставляет локальные безопасные порты для запуска рядом с текущей панелью оператора.": "Stages safe local ports for running the bundle next to the current operator UI.",
    "Рекомендуется для локального теста офлайн-бандла на той же машине, где уже работает панель оператора.": "Recommended when testing the bundle on the same machine where the operator UI already runs.",
    "Контейнерный деплой делит порт с текущей панелью оператора": "Container deploy shares a port with the current operator UI",
    "Если запускать офлайн-бандл из этого же локального backend на `8000`, контейнерный `agent-api` попытается занять тот же порт. Для безопасного запуска нужен другой порт или отдельное окружение.": "If the bundle starts from this same local backend on `8000`, its container `agent-api` will try to take the same port. Use a different port profile or a separate environment.",
    "Профиль запуска, режим backend и общий режим устройства.": "Launch profile, backend mode, and overall device mode.",
    "Пути к моделям и конфигу реестра. Здесь изменения обычно вносятся вручную.": "Model paths and registry config. These fields are usually edited manually, with recommendations shown next to each field.",
    "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.": "Model placement and GPU limits. Some values come from presets, others remain manual.",
    "Порты локального нативного пути и URL сервисов.": "Ports for the local native path and service URLs.",
    "Профили Chainlit. Обычно редактируются вручную под конкретный сценарий.": "Chainlit profiles. These are usually edited manually for a specific workflow.",
    "Параметры strict JSON и сравнения. Здесь полезны безопасный и строгий пресеты.": "Strict JSON and compare settings. Safe and strict presets are useful here.",
    "Канонический портовый профиль для target-host и offline release contract.": "Canonical port profile for the target host and offline release contract.",
    "Настройки наблюдаемости для Prometheus и Grafana внутри offline bundle.": "Observability settings for Prometheus and Grafana inside the offline bundle.",
    "Пути к моделям, загрузкам и отчётам внутри layout offline bundle.": "Paths for models, uploads, and reports inside the offline bundle layout.",
    "Offline-контракт парсинга и сравнения внутри env.bundle.": "Offline parser and compare contract inside env.bundle.",
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
    "Host facts are gathered server-side for the operator UI.": "Факты о железе собираются на стороне сервера.",
    "Used to explain runtime capabilities and suggested defaults.": "Используется для объяснения возможностей среды выполнения и предлагаемых значений.",
    "All detected NVIDIA devices are listed for multi-GPU aware planning.": "Все обнаруженные NVIDIA GPU перечислены для планирования multi-GPU.",
    "Used for runtime and bundle sizing context.": "Нужно для оценки размеров runtime и bundle.",
    "Matches the canonical launcher flow for generated applied plans.": "Соответствует каноническому сценарию запуска для применённого плана.",
    "Mirrors the current applied or suggested operator value.": "Отражает текущее применённое значение оператора.",
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
    "artifact manifest": "манифест артефактов",
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
    "Bundle image loading": "Загрузка архивов образов",
    "Offline bundle runtime": "Запуск офлайн-бандла",
    "Import and parity validation": "Импорт и проверка согласованности",
    "Minimal API and orchestration flow for workflow and endpoint checks.": "Минимальный API + orchestration flow для workflow и endpoint checks.",
    "Operator UI path with backend orchestration and local session state.": "Путь панели оператора с backend-оркестрацией и локальным состоянием сессии.",
    "Full native launcher flow with a hardware-aware plan.": "Полный native launcher flow с hardware-aware планом.",
    "Can start, but warmup and service readiness may lag by one probe cycle.": "Запускается, но warmup и готовность сервисов могут отставать на один цикл проверки.",
    "Canonical entrypoint for native runtime preflight and planning.": "Канонический вход для preflight и planning нативного запуска.",
    "Canonical launcher for native and container runtime paths.": "Канонический launcher для нативного и контейнерного путей запуска.",
    "Artifact manifest for deploy/offline_bundle.": "Манифест артефактов для deploy/offline_bundle.",
    "filesystem probe": "проверка файловой системы",
    "Readable": "Читается",
    "Missing": "Отсутствует",
    "Bundle state is visible immediately, while container startup still depends on Docker engine and socket access.": "Состояние бандла видно сразу, а запуск контейнеров зависит от Docker Engine и доступа к сокету.",
    "Native checks and host probes are derived from repository state.": "Проверки native и host probes берутся из репозитория.",
    "No warnings": "Нет предупреждений",
    "Backend has not reported additional runtime blockers beyond the current availability state.": "Backend не сообщил о дополнительных блокерах runtime сверх текущего состояния доступности.",
    "No operator activity yet": "Пока нет активности оператора",
    "Actions, config applies, and completed jobs will appear here.": "Здесь появятся действия, применение конфига и завершённые задачи.",
    "Local developer runtime launched through the canonical launcher with model-path control and service visibility.": "Локальный runtime разработчика с запуском через launcher, управлением путями моделей и видимостью сервисов.",
    "Local developer runtime with launcher orchestration, direct model-path control, and visible service state.": "Локальный runtime разработчика с оркестрацией через launcher, прямым контролем путей моделей и видимостью сервисов.",
    "Runtime path through bundle and compose for offline rollout, parity checks, and artifact-based deployment.": "Путь запуска через бандл и compose для офлайн-развёртывания, проверки согласованности и запуска артефактов.",
    "Native runtime is available. Open Config to inspect source precedence or go to Launch and choose a profile.": "Нативный путь запуска доступен. Открой «Конфиг», чтобы посмотреть приоритет источников, или перейди в «Запуск» и выбери профиль.",
    "Profile for local bundle execution next to the current operator UI without published-port conflicts.": "Профиль для локального запуска рядом с текущей панелью оператора без конфликта опубликованных портов.",
    "Stages safe local ports for running the bundle next to the current operator UI.": "Подставляет локальные безопасные порты для запуска рядом с текущей панелью оператора.",
    "Recommended when testing the bundle on the same machine where the operator UI already runs.": "Рекомендуется для локального теста офлайн-бандла на той же машине, где уже работает панель оператора.",
    "Container deploy shares a port with the current operator UI": "Контейнерный деплой делит порт с текущей панелью оператора",
    "If the bundle starts from this same local backend on `8000`, its container `agent-api` will try to take the same port. Use a different port profile or a separate environment.": "Если запускать офлайн-бандл из этого же локального backend на `8000`, контейнерный `agent-api` попытается занять тот же порт. Для безопасного запуска нужен другой порт или отдельное окружение.",
    "Профиль запуска, режим backend и общий режим устройства.": "Профиль запуска, режим backend и общий режим устройства.",
    "Пути к моделям и registry-конфигу. Здесь обычно правки ручные, но рекомендации видны рядом.": "Пути к моделям и конфигу реестра. Здесь изменения обычно вносятся вручную.",
    "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.": "Размещение моделей и ограничения по GPU. Часть параметров меняется пресетами, часть вручную.",
    "Порты локального native path и service URLs.": "Порты локального нативного пути и URL сервисов.",
    "Профили Chainlit. Обычно редактируются вручную по конкретному workflow.": "Профили Chainlit. Обычно редактируются вручную под конкретный сценарий.",
    "Параметры strict JSON и compare path. Здесь полезны safe/strict пресеты.": "Параметры strict JSON и сравнения. Здесь полезны безопасный и строгий пресеты.",
    "Профиль для локального запуска рядом с текущим operator UI без конфликта published ports.": "Профиль для локального запуска рядом с текущим operator UI без конфликта опубликованных портов.",
    "Канонический портовый профиль для target-host и offline release contract.": "Канонический портовый профиль для целевого хоста и offline release-контракта.",
    "Настройки observability для Prometheus и Grafana внутри offline bundle.": "Настройки наблюдаемости для Prometheus и Grafana внутри offline bundle.",
    "Пути к моделям, uploads и reports внутри offline bundle layout.": "Пути к моделям, загрузкам и отчётам внутри layout offline bundle.",
    "Offline parser/compare contract внутри env.bundle.": "Offline-контракт парсинга и сравнения внутри env.bundle.",
    "Offline bundle path for image loading, deploy, parity checks, and artifact-based server startup.": "Путь через офлайн-бандл с загрузкой образов, деплоем, проверками согласованности и запуском артефактов на сервере.",
    "Launch profile, backend mode, and overall device mode.": "Профиль запуска, режим backend и общий режим устройства.",
    "Model paths and registry config. These fields are usually edited manually, with recommendations shown next to each field.": "Пути к моделям и конфигу реестра. Здесь изменения обычно вносятся вручную.",
    "Model placement and GPU limits. Some values come from presets, others remain manual.": "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.",
    "Ports for the local native path and service URLs.": "Порты локального нативного пути и URL сервисов.",
    "Chainlit profiles. These are usually edited manually for a specific workflow.": "Профили Chainlit. Обычно редактируются вручную под конкретный сценарий.",
    "Strict JSON and compare settings. Safe and strict presets are useful here.": "Параметры strict JSON и сравнения. Здесь полезны безопасный и строгий пресеты.",
    "Canonical port profile for the target host and offline release contract.": "Канонический портовый профиль для целевого хоста и offline release-контракта.",
    "Observability settings for Prometheus and Grafana inside the offline bundle.": "Настройки наблюдаемости для Prometheus и Grafana внутри offline bundle.",
    "Paths for models, uploads, and reports inside the offline bundle layout.": "Пути к моделям, загрузкам и отчётам внутри layout offline bundle.",
    "Usually points to the repository-local models.yaml.": "Обычно должен указывать на repo-local models.yaml.",
    "Set the main LLM artifact for this runtime path.": "Укажи основной LLM artifact для runtime path.",
    "Fill this only when the multimodal path is actually used.": "Заполняется только если multimodal path реально используется.",
    "Should point to the intent embedder used by the current runtime path.": "Должен указывать на intent embedder текущего runtime path.",
    "Should point to the retrieval embedder used by the current runtime path.": "Должен указывать на retrieval embedder текущего runtime path.",
    "This path must stay aligned with the bundle runtime layout.": "Путь должен совпадать с runtime layout bundle.",
    "The main user path goes through run_offline_bundle.sh and deploy.sh, not through the dev compose launcher for the checkout repository.": "Основной пользовательский путь идёт через run_offline_bundle.sh и deploy.sh, а не через dev compose launcher checkout-репозитория.",
    "Bundle image archives can be loaded into Docker only when the engine and socket are available.": "Архивы образов можно загружать в Docker только когда доступны engine и сокет.",
    "Manifest, env.bundle, and the deploy surface remain readable even before the runtime is up.": "Manifest, env.bundle и поверхность деплоя читаются даже когда сам runtime ещё не поднят.",
    "The container runtime path points to deploy/offline_bundle/scripts: first load bundle images, then deploy or run the offline bundle without a dev build from the checkout repository.": "Контейнерный путь запуска ведёт в deploy/offline_bundle/scripts: сначала можно загрузить образы bundle, затем выполнить deploy или run offline bundle без dev-сборки checkout-репозитория.",
    "This value is interpreted inside the offline bundle/container layout.": "Это значение интерпретируется внутри layout offline bundle / контейнера.",
    "Value is empty and must be set explicitly.": "Значение пустое и должно быть задано явно.",
    "Число запросов orchestration, видимых через `agent_api` metrics.": "Число запросов оркестрации, видимых через метрики `agent_api`.",
    "Счётчик degraded/fallback событий по runtime и workflow.": "Счётчик событий деградации и fallback по среде выполнения и сценариям.",
    "Факты о железе собираются на стороне backend.": "Факты о железе собираются на стороне сервера.",
    "Используется для объяснения возможностей runtime и предлагаемых значений.": "Используется для объяснения возможностей runtime и предлагаемых значений.",
    "Все обнаруженные NVIDIA GPU перечислены для multi-GPU planning.": "Все обнаруженные NVIDIA GPU перечислены для планирования multi-GPU.",
    "Проверки native и host probes берутся из репозитория.": "Проверки нативного пути и хоста берутся из состояния репозитория.",
    "Состояние бандла видно сразу, а запуск контейнеров зависит от Docker Engine и доступа к сокету.": "Состояние бандла видно сразу, а запуск контейнеров зависит от Docker Engine и доступа к сокету.",
    "Здесь появятся действия, применение конфига и завершённые jobs.": "Здесь появятся действия, применение конфига и завершённые задачи.",
    "Локальный developer-runtime с orchestration через launcher, прямым контролем путей моделей и видимостью сервисов.": "Локальная среда запуска с оркестрацией через launcher, прямым контролем путей моделей и видимостью сервисов.",
    "Путь через офлайн-бандл с загрузкой образов, деплоем, проверками согласованности и запуском артефактов на сервере.": "Путь через офлайн-бандл с загрузкой образов, деплоем, проверками согласованности и запуском артефактов на сервере.",
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
  if (message.startsWith("Resolved host path exists but is not a .gguf file:")) {
    return `Путь существует, но это не .gguf файл: ${message.slice("Resolved host path exists but is not a .gguf file:".length).trim()}`;
  }
  if (message.startsWith("Resolved host directory exists but common model markers were not found yet:")) {
    return `Папка существует, но типовые файлы модели пока не найдены: ${message.slice("Resolved host directory exists but common model markers were not found yet:".length).trim()}`;
  }
  if (message.startsWith("Bundle-relative model path resolves locally:")) {
    return `Bundle-путь локально разрешается: ${message.slice("Bundle-relative model path resolves locally:".length).trim()}`;
  }
  if (message.startsWith("Bundle-relative model path is not present in deploy/offline_bundle/models yet:")) {
    return `Bundle-путь пока не найден в deploy/offline_bundle/models: ${message.slice("Bundle-relative model path is not present in deploy/offline_bundle/models yet:".length).trim()}`;
  }
  if (message === "This container target is expected to be filled by an external host mount before startup.") {
    return "Этот путь внутри контейнера должен быть заполнен внешним host mount до запуска.";
  }
  if (message.startsWith("Path does not exist on the host yet:")) {
    return `Путь пока не существует на хосте: ${message.slice("Path does not exist on the host yet:".length).trim()}`;
  }
  if (message.startsWith("Expected a `.gguf` model file for")) {
    return `Ожидается .gguf-файл модели: ${message.split(":").slice(1).join(":").trim()}`;
  }
  if (message.startsWith("The selected `.gguf` file is empty:")) {
    return `Выбранный .gguf файл пуст: ${message.slice("The selected `.gguf` file is empty:".length).trim()}`;
  }
  if (message.startsWith("The file exists, but its name does not look like an mmproj artifact:")) {
    return `Файл существует, но его имя не похоже на mmproj-артефакт: ${message.slice("The file exists, but its name does not look like an mmproj artifact:".length).trim()}`;
  }
  if (message.startsWith("Host model file looks valid:")) {
    return `Файл модели выглядит корректно: ${message.slice("Host model file looks valid:".length).trim()}`;
  }
  if (message.startsWith("Model directory looks plausible:")) {
    return `Папка модели выглядит правдоподобно: ${message.slice("Model directory looks plausible:".length).trim()}`;
  }
  if (message.startsWith("Directory exists, but common model marker files were not found yet:")) {
    return `Папка существует, но типовые файлы модели пока не найдены: ${message.slice("Directory exists, but common model marker files were not found yet:".length).trim()}`;
  }
  if (message === "VLM and mmproj must be filled together.") {
    return "VLM и mmproj должны быть заполнены парой.";
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeAnsiText(text) {
  return String(text ?? "").replace(/␛(?=\[)/g, "\u001b");
}

function stripAnsi(text) {
  return normalizeAnsiText(text).replace(/\u001b\[[0-9;]*m/g, "");
}

function detectLogTone(text, stream = "") {
  const plain = stripAnsi(text).toLowerCase();
  const streamKey = String(stream || "").toLowerCase();
  if (streamKey.includes("stderr")) return "error";
  if (/(traceback|attributeerror|exception|ошибка|error|failed:|порт.*конфликт|port-conflict|missing)/.test(plain)) return "error";
  if (/(warning|warn|degraded|устар|fallback|требует внимания)/.test(plain)) return "warning";
  if (/(models:skip|\bskip\b|пропуск)/.test(plain)) return "skip";
  if (/(✓|готов|loaded|загружен|ok\b|models:ok|completed:)/.test(plain)) return "ok";
  return streamKey.includes("system") ? "info" : "neutral";
}

function ansiClassesFromState(state) {
  return [
    state.bold ? "ansi-bold" : "",
    state.dim ? "ansi-dim" : "",
    state.fg ? `ansi-${state.fg}` : "",
  ].filter(Boolean).join(" ");
}

function renderAnsiMarkup(text) {
  const input = normalizeAnsiText(text);
  const ansiRegex = /\u001b\[([0-9;]*)m/g;
  const colorMap = {
    30: "black",
    31: "red",
    32: "green",
    33: "yellow",
    34: "blue",
    35: "magenta",
    36: "cyan",
    37: "white",
    90: "muted",
    91: "red",
    92: "green",
    93: "yellow",
    94: "blue",
    95: "magenta",
    96: "cyan",
    97: "white",
  };
  const state = { fg: "", bold: false, dim: false };
  const parts = [];
  let lastIndex = 0;

  function pushChunk(chunk) {
    if (!chunk) return;
    const classes = ansiClassesFromState(state);
    const content = escapeHtml(chunk);
    parts.push(classes ? `<span class="${classes}">${content}</span>` : content);
  }

  for (const match of input.matchAll(ansiRegex)) {
    pushChunk(input.slice(lastIndex, match.index));
    const codes = String(match[1] || "0")
      .split(";")
      .map((code) => Number(code || 0));
    if (!codes.length) {
      codes.push(0);
    }
    codes.forEach((code) => {
      if (code === 0) {
        state.fg = "";
        state.bold = false;
        state.dim = false;
      } else if (code === 1) {
        state.bold = true;
      } else if (code === 2) {
        state.dim = true;
      } else if (code === 22) {
        state.bold = false;
        state.dim = false;
      } else if (code === 39) {
        state.fg = "";
      } else if (colorMap[code]) {
        state.fg = colorMap[code];
      }
    });
    lastIndex = match.index + match[0].length;
  }

  pushChunk(input.slice(lastIndex));
  return parts.join("");
}

function renderLogBody(text, tone) {
  const markup = renderAnsiMarkup(text);
  const toneClass = tone && tone !== "neutral" ? ` log-body-${tone}` : "";
  return `<span class="log-body${toneClass}">${markup}</span>`;
}

function renderLogLine(lineText) {
  const translated = translateSeedLogLine(lineText);
  const normalized = normalizeAnsiText(translated);
  const match = normalized.match(/^\[([^\]]+)\]\s+([^:]+):\s?(.*)$/);
  const timestamp = match?.[1] || "";
  const stream = match?.[2] || "";
  const body = match?.[3] ?? normalized;
  const tone = detectLogTone(body, stream);
  const streamClass = String(stream).toLowerCase().includes("stderr")
    ? "log-stream-error"
    : String(stream).toLowerCase().includes("system")
      ? "log-stream-system"
      : "log-stream-runtime";
  return `<span class="log-entry log-entry-${tone}">`
    + `${timestamp ? `<span class="log-timestamp">[${escapeHtml(timestamp)}]</span>` : ""}`
    + `${stream ? `<span class="log-stream ${streamClass}">${escapeHtml(stream)}</span>` : ""}`
    + `${renderLogBody(body, tone)}`
    + `</span>`;
}

function renderLogConsole(node, lines, emptyText) {
  if (!node) return;
  if (!lines.length) {
    node.textContent = emptyText;
    return;
  }
  node.innerHTML = lines.map((line) => renderLogLine(line)).join("");
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

function configSourceAction(item, pathKey) {
  if (item.freshness === "missing") {
    if (item.path.endsWith(".env.native")) {
      return currentLanguage === "en"
        ? (pathKey === "native" ? "Create from template or keep absent if the active path does not need native overrides." : "Not required for this runtime path.")
        : (pathKey === "native" ? "Создай из шаблона или оставь отсутствующим, если текущему пути не нужны native overrides." : "Для этого пути запуска не требуется.");
    }
    if (item.path.endsWith(".env.runtime")) {
      return currentLanguage === "en"
        ? "This file appears after Apply or the first runtime plan generation."
        : "Этот файл появится после Apply или первой генерации runtime-плана.";
    }
    if (item.path.endsWith(".env.hardware.override")) {
      return currentLanguage === "en"
        ? "Create the overrides file before pinning GPU placement or hardware policy."
        : "Создай файл overrides перед фиксацией GPU placement или hardware policy.";
    }
    return currentLanguage === "en" ? "Create the missing source before applying path-specific changes." : "Создай отсутствующий источник перед применением path-specific изменений.";
  }
  if (item.freshness === "generated") {
    return currentLanguage === "en" ? "Regenerate this file from Apply if the runtime plan changed." : "Перегенерируй этот файл через Apply, если runtime-план изменился.";
  }
  if (item.freshness === "stale") {
    return currentLanguage === "en" ? "Refresh this source before treating it as the active runtime truth." : "Обнови источник перед тем как считать его актуальным runtime-состоянием.";
  }
  return currentLanguage === "en" ? "No operator action required right now." : "Сейчас дополнительных действий не требуется.";
}

function summarizeConfigSources(pathKey, sourceFiles) {
  const missing = sourceFiles.filter((item) => item.freshness === "missing");
  const stale = sourceFiles.filter((item) => item.freshness === "stale");
  if (missing.length) {
    return {
      title: currentLanguage === "en" ? "What to do next" : "Что делать дальше",
      tone: "orange",
      summary: currentLanguage === "en"
        ? `${missing.length} source file(s) are missing for this runtime path.`
        : `${missing.length} файла источника отсутствуют для этого пути запуска.`,
      items: missing.map((item) => ({
        label: item.path,
        body: configSourceAction(item, pathKey),
      })),
    };
  }
  if (stale.length) {
    return {
      title: currentLanguage === "en" ? "Refresh before deploy" : "Что обновить перед deploy",
      tone: "cyan",
      summary: currentLanguage === "en"
        ? `${stale.length} source file(s) are readable but no longer current.`
        : `${stale.length} источника читаются, но уже не являются актуальными.`,
      items: stale.map((item) => ({
        label: item.path,
        body: configSourceAction(item, pathKey),
      })),
    };
  }
  return {
    title: currentLanguage === "en" ? "Config chain is ready" : "Цепочка конфига готова",
    tone: "lime",
    summary: currentLanguage === "en"
      ? "The visible config sources are present for this runtime path."
      : "Видимые источники конфига присутствуют для этого пути запуска.",
    items: [
      {
        label: currentLanguage === "en" ? "Next operator step" : "Следующий шаг",
        body: currentLanguage === "en"
          ? "Review variant presets, stage field changes, then apply explicitly."
          : "Проверь вариант, подготовь изменения в полях и применяй их явно.",
      },
    ],
  };
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
  document.title = currentLanguage === "en" ? "llm-tools-platform | Operator Console" : "llm-tools-platform | Операторская панель";

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
    ["#help-drawer-button-label", "Help"],
    ["#help-drawer-eyebrow", "Help"],
    ["#help-drawer-button-note", "Section descriptions, terms, and short operator scenarios."],
    ["#mobile-nav-toggle", "Menu"],
    ["#topbar-start-button-label", "Launch"],
    ["#sidebar-hardware-label", "Hardware Environment"],
    ["#sidebar-summary-label", "Status Summary"],
    ["#sidebar-available-paths-label", "Available Paths"],
    ["#sidebar-healthy-services-label", "Healthy Services"],
    ["#sidebar-dirty-config-label", "Unsaved Changes"],
    ["#section-overview .eyebrow", "Overview"],
    ["#section-overview h2", "System overview"],
    ["#section-overview .support-copy", "Start with runtime paths, then hardware, services, and blockers."],
    ["#overview-metrics-eyebrow", "Metrics"],
    ["#overview-metrics-title", "Key Prometheus signals for the operator summary"],
    ["#overview-metrics-pill", "Observability"],
    ["#overview-hardware-eyebrow", "Hardware"],
    ["#section-launch .eyebrow", "Launch"],
    ["#section-launch h2", "Choose and launch a path"],
    ["#section-launch .support-copy", "Choose a path, inspect its current state, and launch without unnecessary scrolling."],
    ["#launch-profiles-eyebrow", "Run Profiles"],
    ["#launch-profiles-title", "Subordinate presets for the selected runtime path"],
    ["#launch-log-eyebrow", "Launch Log"],
    ["#launch-log-title", "Current or latest execution log"],
    ["#launch-fail-eyebrow", "Launch Status"],
    ["#launch-fail-title", "Current state summary"],
    ["#launch-next-eyebrow", "Next Step"],
    ["#launch-next-title", "What to do after launch"],
    ["#section-config .eyebrow", "Config"],
    ["#section-config h2", "env editor with runtime-path awareness and explicit apply"],
    ["#section-config .support-copy", "Changes write to real env/config sources and are never auto-saved."],
    ["#section-services .eyebrow", "Services and Metrics"],
    ["#section-services h2", "State, readiness, logs, and parser diagnostics"],
    ["#section-services .support-copy", "Logs and metrics are part of the operator decision loop."],
    ["#section-deploy .eyebrow", "Build / Deploy"],
    ["#section-deploy h2", "Offline bundle control"],
    ["#section-deploy .support-copy", "Current stage, log, and actions matter more than descriptive cards."],
    ["#section-maintenance .eyebrow", "Actions"],
    ["#section-maintenance h2", "Only allowlisted maintenance scenarios"],
    ["#section-maintenance .support-copy", "Safe operator actions stay visible, typed, and bound to the selected runtime path."],
    ["#sidebar-system-label", "System"],
    ["#overview-hardware-title", "Hardware summary"],
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
    ["#path-browser-eyebrow", "Choose Path"],
    ["#path-browser-title", "Model Path"],
    ["#path-browser-close", "Close"],
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
    ["#help-drawer-button-label", "Справка"],
    ["#help-drawer-eyebrow", "Справка"],
    ["#help-drawer-button-note", "Описания разделов, термины и короткие сценарии."],
    ["#mobile-nav-toggle", "Меню"],
    ["#topbar-start-button-label", "Запуск"],
    ["#sidebar-hardware-label", "Аппаратная среда"],
    ["#sidebar-summary-label", "Сводка состояния"],
    ["#sidebar-available-paths-label", "Доступные пути"],
    ["#sidebar-healthy-services-label", "Здоровые сервисы"],
    ["#sidebar-dirty-config-label", "Незасейвленные правки"],
    ["#section-overview .eyebrow", "Обзор"],
    ["#section-overview h2", "Обзор системы"],
    ["#section-overview .support-copy", "Сначала статус путей, потом железо, сервисы и блокеры."],
    ["#overview-metrics-eyebrow", "Метрики"],
    ["#overview-metrics-title", "Ключевые сигналы Prometheus для сводки оператора"],
    ["#overview-metrics-pill", "Наблюдаемость"],
    ["#overview-hardware-eyebrow", "Железо"],
    ["#section-launch .eyebrow", "Запуск"],
    ["#section-launch h2", "Выбор и запуск пути"],
    ["#section-launch .support-copy", "Выбери путь, смотри текущее состояние и запускай без лишней прокрутки."],
    ["#launch-profiles-eyebrow", "Профили запуска"],
    ["#launch-profiles-title", "Подчинённые пресеты выбранного пути запуска"],
    ["#launch-log-eyebrow", "Лог запуска"],
    ["#launch-log-title", "Ход текущей или последней задачи"],
    ["#launch-fail-eyebrow", "Статус запуска"],
    ["#launch-fail-title", "Сводка текущего состояния"],
    ["#launch-next-eyebrow", "Следующий шаг"],
    ["#launch-next-title", "Что делать после запуска"],
    ["#section-config .eyebrow", "Конфиг"],
    ["#section-config h2", "Редактор env с учётом выбранного пути запуска и явным применением"],
    ["#section-config .support-copy", "Изменения пишутся в реальные env/config источники и не сохраняются автоматически."],
    ["#section-services .eyebrow", "Сервисы и метрики"],
    ["#section-services h2", "Состояние, готовность, логи и диагностика парсинга"],
    ["#section-services .support-copy", "Логи и метрики входят в операторский цикл принятия решения."],
    ["#section-deploy .eyebrow", "Сборка / Деплой"],
    ["#section-deploy h2", "Управление офлайн-бандлом"],
    ["#section-deploy .support-copy", "Текущий этап, лог и действия важнее описательных карточек."],
    ["#section-maintenance .eyebrow", "Действия"],
    ["#section-maintenance h2", "Только разрешённые maintenance-сценарии"],
    ["#section-maintenance .support-copy", "Безопасные действия оператора видны, типизированы и привязаны к выбранному пути запуска."],
    ["#overview-hardware-title", "Аппаратная сводка"],
    ["#overview-hardware-pill", "Только справка"],
    ["#overview-warnings-eyebrow", "Предупреждения"],
    ["#overview-warnings-title", "Блокеры парсинга и среды выполнения"],
    ["#overview-warnings-pill", "Требует внимания"],
    ["#overview-health-eyebrow", "Здоровье сервисов"],
    ["#overview-health-title", "Готовность активного пути"],
    ["#overview-activity-eyebrow", "Последние действия"],
    ["#overview-activity-title", "Недавние события запуска и конфига"],
    ["#section-config .panel:nth-of-type(1) .eyebrow", "Источники конфига"],
    ["#section-config .panel:nth-of-type(1) h3", "Происхождение выбранного пути запуска"],
    ["#section-config .panel:nth-of-type(2) .eyebrow", "Варианты"],
    ["#section-config .panel:nth-of-type(2) h3", "Варианты и готовые наборы значений"],
    ["#section-config .panel:nth-of-type(3) .eyebrow", "Редактируемые поля"],
    ["#section-config .panel:nth-of-type(3) h3", "Сгруппированные настройки запуска и парсинга"],
    ["#apply-config-button", "Применить изменения"],
    ["#services-support-copy", "Логи здесь помогают принимать решение, а не просто лежат внизу страницы."],
    ["#services-status-eyebrow", "Сервисы"],
    ["#services-status-title", "Статус выбранного пути"],
    ["#services-diagnostics-eyebrow", "Диагностика парсера"],
    ["#services-diagnostics-title", "Проверки для выбранного пути"],
    ["#services-observability-eyebrow", "Метрики и Grafana"],
    ["#services-observability-title", "Данные для оценки состояния, задержек и наблюдаемости"],
    ["#services-logs-eyebrow", "Логи"],
    ["#services-logs-title", "Сводный поток событий и логов"],
    ["#deploy-summary-eyebrow", "Сводка режима"],
    ["#deploy-sources-title", "Канонические сценарии и корни артефактов"],
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
    ["#maintenance-blockers-title", "Что мешает полной паритетности запуска"],
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
    ["#reason-modal-title", "Детали среды выполнения"],
    ["#reason-modal-close", "Закрыть"],
    ["#ui-settings-modal-eyebrow", "Настройки интерфейса"],
    ["#ui-settings-modal-title", "Настройки UI"],
    ["#ui-settings-modal-close", "Закрыть"],
    ["#path-browser-eyebrow", "Выбор пути"],
    ["#path-browser-title", "Путь модели"],
    ["#path-browser-close", "Закрыть"],
  ];

  textMap.forEach(([selector, text]) => {
    const node = document.querySelector(selector);
    if (node) {
      node.textContent = text;
    }
  });

  const launchButton = document.querySelector("#topbar-start-button");
  if (launchButton) {
    const label = currentLanguage === "en" ? "Launch or stop the selected runtime path" : "Запустить или остановить выбранный путь запуска";
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
  renderTopbarHint();
  renderOverviewHero();
  renderOverviewPathCards();
  renderOverviewMetrics();
  renderHardware();
  renderWarnings();
  renderServiceOverview();
  renderActivity();
  renderLaunchStrip();
  renderLaunchFailureSummary();
  renderProfiles();
  renderLaunchLogs();
  renderConfig();
  renderServicesStrip();
  renderServices();
  renderDeployStrip();
  renderDeploy();
  renderMaintenance();
  renderHelpDrawer();
  syncMobileNav();
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

function getMetricValue(labels, fallback) {
  const labelList = Array.isArray(labels) ? labels : [labels];
  for (const label of labelList) {
    const metric = hardwareMetrics.find((item) => item.label === label || item.labelEn === label);
    if (metric?.value !== undefined && metric?.value !== null && String(metric.value).trim()) {
      return metric.value;
    }
  }
  if (fallback !== undefined) {
    return fallback;
  }
  return currentLanguage === "en" ? "Unknown" : "Неизвестно";
}

function renderSidebarState() {
  const sidebar = document.querySelector("#operator-sidebar");
  if (sidebar) {
    const hostSummary = getMetricValue(["CPU / memory", "CPU / память"]);
    const gpuSummary = getMetricValue(["GPU visibility", "Инвентарь GPU"]);
    sidebar.title = currentLanguage === "en"
      ? `Host: ${hostSummary}; GPU: ${gpuSummary}; warnings: ${warnings.length}`
      : `Хост: ${hostSummary}; GPU: ${gpuSummary}; предупреждений: ${warnings.length}`;
  }
}

function renderTopbarHint() {
  const node = document.querySelector("#topbar-inline-hint");
  if (!node) return;
  const activeJob = [...runningJobs.values()].find((job) => isActiveJobStatus(job.status));
  if (activeJob) {
    node.textContent = currentLanguage === "en"
      ? `Active job: ${activeJob.label}. Inspect the current state before switching context.`
      : `Активная задача: ${activeJob.label}. Сначала проверь текущее состояние, потом переключай контекст.`;
    return;
  }
  const sectionHints = {
    overview: currentLanguage === "en"
      ? "This screen should answer whether the system is okay in a few seconds."
      : "Этот экран должен за несколько секунд отвечать, всё ли в порядке с системой.",
    launch: currentLanguage === "en"
      ? "Choose a path, inspect checks, then launch and follow the log."
      : "Выбери путь, проверь проверки, затем запускай и смотри лог.",
    config: currentLanguage === "en"
      ? "Edit only what you need and apply changes explicitly."
      : "Меняй только нужное и применяй изменения явно.",
    services: currentLanguage === "en"
      ? "The service list and the log are the primary evidence surfaces."
      : "Список сервисов и лог здесь являются главными evidence-поверхностями.",
    deploy: currentLanguage === "en"
      ? "Use the stepper and log first, then the actions and artifacts."
      : "Сначала смотри stepper и лог, затем действия и артефакты.",
    maintenance: currentLanguage === "en"
      ? "Maintenance actions are recovery tools, not a primary workflow."
      : "Maintenance-действия нужны для recovery, а не как основной сценарий.",
  };
  node.textContent = sectionHints[activeSection] || sectionHints.overview;
}

function isActiveJobStatus(status) {
  return ["queued", "running", "cancelling"].includes(status);
}

function getActiveJobForPath(pathKey) {
  return [...runningJobs.values()].find((job) => job.pathKey === pathKey && isActiveJobStatus(job.status));
}

function renderHelpDrawer() {
  const drawer = document.querySelector("#help-drawer");
  if (!drawer) return;
  const content = currentHelpContent();
  drawer.hidden = !helpDrawerOpen;
  document.querySelector("#help-drawer-eyebrow").textContent = currentLanguage === "en" ? "Help" : "Справка";
  document.querySelector("#help-drawer-section-title").textContent = localizedHelpText(content.title);
  document.querySelector("#help-drawer-section-body").textContent = localizedHelpText(content.body);
  document.querySelector("#help-drawer-title").textContent = currentLanguage === "en" ? "Current section context" : "Контекст текущего раздела";
  document.querySelector("#help-drawer-close").textContent = currentLanguage === "en" ? "Close" : "Закрыть";
  document.querySelector("#help-drawer-section-label").textContent = currentLanguage === "en" ? "Section" : "Раздел";
  document.querySelector("#help-drawer-terms-label").textContent = currentLanguage === "en" ? "Terms" : "Термины";
  document.querySelector("#help-drawer-actions-label").textContent = currentLanguage === "en" ? "What to do if..." : "Что делать если...";
  document.querySelector("#help-drawer-terms").innerHTML = (content.terms || []).map((item) => `
    <div class="metric-card">
      <p>${displayText(localizedHelpText(item))}</p>
    </div>
  `).join("");
  document.querySelector("#help-drawer-actions").innerHTML = (content.actions || []).map((item) => `
    <div class="metric-card">
      <p>${displayText(localizedHelpText(item))}</p>
    </div>
  `).join("");
}

function setHelpDrawerOpen(nextValue) {
  helpDrawerOpen = Boolean(nextValue);
  renderHelpDrawer();
}

function syncMobileNav() {
  const sidebar = document.querySelector("#operator-sidebar");
  const toggle = document.querySelector("#mobile-nav-toggle");
  if (!sidebar || !toggle) return;
  const isMobileViewport = window.matchMedia("(max-width: 1180px)").matches;
  const shouldShowMobileDrawer = isMobileViewport && mobileNavOpen;
  sidebar.classList.toggle("mobile-open", shouldShowMobileDrawer);
  toggle.setAttribute("aria-expanded", shouldShowMobileDrawer ? "true" : "false");
  toggle.setAttribute("aria-label", currentLanguage === "en" ? "Toggle section navigation" : "Открыть или закрыть навигацию по разделам");
  if (isMobileViewport) {
    sidebar.hidden = !shouldShowMobileDrawer;
    sidebar.setAttribute("aria-hidden", shouldShowMobileDrawer ? "false" : "true");
    sidebar.inert = !shouldShowMobileDrawer;
  } else {
    sidebar.hidden = false;
    sidebar.removeAttribute("aria-hidden");
    sidebar.inert = false;
  }
}

function runtimeHealthTone(status) {
  if (status === "stopping") return "neutral";
  if (["running", "healthy"].includes(status)) return "lime";
  if (["building", "deploying", "not_started"].includes(status)) return "cyan";
  if (status === "not_started") return "cyan";
  if (["degraded", "unknown"].includes(status)) return "orange";
  return "orange";
}

function runtimeHealthLabel(status) {
  const map = currentLanguage === "en"
    ? {
        healthy: "Runtime healthy",
        running: "Runtime healthy",
        stopping: "Stopping",
        building: "Build in progress",
        deploying: "Deploy in progress",
        failed: "Runtime failed",
        blocked: "Blocked",
        degraded: "Degraded",
        unknown: "Unknown",
        not_started: "Not started",
      }
    : {
        healthy: "Система работает",
        running: "Система работает",
        stopping: "Остановка",
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
  const localOverride = localRuntimeStateOverrides.get(pathKey);
  const runtimeHealth = runtimeHealthByPath[pathKey];
  const runtimeJob = getActiveJobForPath(pathKey);
  const pathLogs = logLines.filter((line) => line.path === pathKey);
  const latestLog = pathLogs[pathLogs.length - 1]?.text || "";

  if (runtimeJob?.actionKind === "stop" || runtimeJob?.status === "cancelling") {
    return {
      status: "stopping",
      detail: runtimeJob.detail,
      reason: runtimeJob.detail,
      nextAction: currentLanguage === "en" ? "Wait until the current stop operation finishes." : "Дождись завершения текущей операции остановки.",
    };
  }
  if (runtimeJob?.status === "queued" || runtimeJob?.status === "running") {
    return {
      status: pathKey === "container" ? "building" : "deploying",
      detail: runtimeJob.detail,
      reason: runtimeJob.detail,
      nextAction: currentLanguage === "en" ? "Wait for the current operator job to finish." : "Дождись завершения текущей operator-задачи.",
    };
  }
  if (lastJobSummary?.pathKey === pathKey && lastJobSummary.outcome === "failed") {
    return {
      status: "failed",
      detail: lastJobSummary.detail,
      reason: lastJobSummary.detail,
      failedStage: lastJobSummary.stage || "",
      nextAction: pathKey === "container"
        ? (currentLanguage === "en" ? "Inspect the launch log, then verify Docker, ports, and bundle diagnostics." : "Сначала смотри журнал запуска, затем проверь Docker, порты и диагностику офлайн-бандла.")
        : (currentLanguage === "en" ? "Inspect the launch log and service checks before retrying." : "Сначала смотри журнал запуска и проверки сервисов перед повтором."),
    };
  }
  if (localOverride) {
    return localOverride;
  }
  if (runtimeHealth?.status && runtimeHealth.status !== "unknown") {
    return {
      status: runtimeHealth.status,
      detail: displayText(localizedField(runtimeHealth, "summary") || ""),
      reason: displayText(localizedField(runtimeHealth, "reason") || ""),
      nextAction: displayText(localizedField(runtimeHealth, "nextAction") || ""),
      checkSummary: currentLanguage === "en"
        ? `${runtimeHealth.runningChecks || 0}/${runtimeHealth.checkCount || 0} checks ready`
        : `${runtimeHealth.runningChecks || 0}/${runtimeHealth.checkCount || 0} проверок готовы`,
    };
  }
  if (latestLog) {
    return {
      status: "not_started",
      detail: latestLog,
      reason: latestLog,
      nextAction: currentLanguage === "en" ? "Inspect the latest log line, then start the selected path." : "Сначала посмотри последнюю строку лога, затем запусти выбранный путь.",
    };
  }
  return {
    status: "unknown",
    detail: currentLanguage === "en" ? "No runtime signal yet" : "Пока нет сигнала от runtime",
    reason: currentLanguage === "en" ? "No published operator signal yet." : "Пока нет опубликованного operator-сигнала.",
    nextAction: currentLanguage === "en" ? "Refresh the operator state or start a runtime action." : "Перечитай состояние оператора или запусти runtime-действие.",
  };
}

function shouldShowLaunchFailSummary(state) {
  return ["building", "deploying", "stopping", "failed", "blocked", "degraded"].includes(state.status);
}

function renderSystemStatus() {
  const selectedRows = serviceRows.filter((row) => row.path === selectedServicesPath);
  const activeJobCount = [...runningJobs.values()].filter((job) => isActiveJobStatus(job.status)).length;
  const shellHealth = operatorShellHealth();

  let pillClass = shellHealth.status === "healthy" ? "lime" : shellHealth.status === "degraded" ? "orange" : "neutral";
  let pillText = shellHealth.label;
  let sideTitle = shellHealth.detail;
  let sideBody = "";

  if (activeJobCount > 0) {
    pillClass = "cyan";
    pillText = currentLanguage === "en" ? `Job running: ${activeJobCount}` : `Идёт задача: ${activeJobCount}`;
    sideTitle = runningJobs.values().next().value?.label || sideTitle;
    sideBody = runningJobs.values().next().value?.detail || sideBody;
  } else if (lastJobSummary) {
    sideTitle = lastJobSummary.label;
    sideBody = lastJobSummary.detail;
  }

  const activeJob = [...runningJobs.values()].find((job) => isActiveJobStatus(job.status));
  const failedJob = !activeJob && lastJobSummary?.outcome === "failed" ? lastJobSummary : null;
  const jobPillText = activeJob
    ? summarizeJobForSidebar(activeJob.label, activeJob.detail)
    : failedJob?.label
      ? summarizeJobForSidebar(failedJob.label, summarizeFailureDetail(failedJob.detail))
      : lastJobSummary?.label
        ? summarizeJobForSidebar(lastJobSummary.label, lastJobSummary.detail)
        : (currentLanguage === "en" ? "No active job" : "Нет активной задачи");

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
  const jobTone = activeJob ? "cyan" : (lastJobSummary?.outcome === "failed" ? "orange" : (lastJobSummary ? "lime" : "neutral"));
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
        <h4>${displayText(path.launchSource)}</h4>
        <p>${currentLanguage === "en" ? `${path.configSources.length} config sources visible` : `Видно ${path.configSources.length} источника конфига`}</p>
        <div class="service-meta-list">
          <span class="service-meta-pill">${cap(path.status)}</span>
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

function getStopActionIdForPath(pathKey) {
  if (pathKey === "native") return "runtime.native.stop";
  if (pathKey === "container") return "runtime.container.stop";
  return null;
}

function isPathRunning(pathKey) {
  const state = deriveLaunchRuntimeState(pathKey);
  return ["healthy", "running"].includes(state.status);
}

function launchPrimaryLabel(pathKey) {
  const activeJob = getActiveJobForPath(pathKey);
  if (activeJob?.actionKind === "stop" || activeJob?.status === "cancelling") {
    return currentLanguage === "en" ? "Stopping..." : "Остановка...";
  }
  if (activeJob?.actionKind === "start") {
    return currentLanguage === "en" ? "Stop" : "Остановить";
  }
  return isPathRunning(pathKey)
    ? (currentLanguage === "en" ? "Stop" : "Остановить")
    : (currentLanguage === "en" ? "Launch" : "Запуск");
}

function topbarRuntimeBadgeState(pathKey) {
  const activeJob = getActiveJobForPath(pathKey);
  if (activeJob?.actionKind === "stop" || activeJob?.status === "cancelling") {
    return { tone: "neutral", text: currentLanguage === "en" ? "Stopping..." : "Остановка..." };
  }
  if (activeJob?.actionKind === "start") {
    return { tone: "cyan", text: currentLanguage === "en" ? "Starting..." : "Запуск..." };
  }
  if (isPathRunning(pathKey)) {
    return { tone: "lime", text: currentLanguage === "en" ? "Running" : "Запущено" };
  }
  return { tone: runtimeHealthTone(deriveLaunchRuntimeState(pathKey).status), text: currentLanguage === "en" ? "Stopped" : "Остановлено" };
}

function launchInlineState(path, runtimeState, runtimeJob) {
  if (runtimeJob?.actionKind === "stop" || runtimeJob?.status === "cancelling") {
    return {
      tone: "neutral",
      title: currentLanguage === "en" ? "Stopping current path" : "Останавливаю текущий путь",
      body: currentLanguage === "en"
        ? "The stop operation is in progress. Wait until the runtime path is released."
        : "Идёт операция остановки. Дождись, пока путь запуска будет освобождён.",
      badge: currentLanguage === "en" ? "Stopping..." : "Остановка...",
    };
  }
  if (runtimeJob?.actionKind === "start") {
    return {
      tone: "cyan",
      title: path.key === "container"
        ? (currentLanguage === "en" ? "Offline bundle launch is active" : "Идёт запуск офлайн-бандла")
        : (currentLanguage === "en" ? "Native launch is active" : "Идёт нативный запуск"),
      body: path.key === "container"
        ? (currentLanguage === "en" ? "The offline bundle launch is still running. You can stop the process from this card." : "Запуск офлайн-бандла ещё выполняется. Процесс можно остановить прямо из этой карточки.")
        : (currentLanguage === "en" ? "The native launch is still running. You can stop the process from this card." : "Нативный запуск ещё выполняется. Процесс можно остановить прямо из этой карточки."),
      badge: currentLanguage === "en" ? "Starting..." : "Запуск...",
    };
  }
  if (isPathRunning(path.key)) {
    return {
      tone: "lime",
      title: path.key === "container"
        ? (currentLanguage === "en" ? "Offline bundle is running" : "Офлайн-бандл запущен")
        : (currentLanguage === "en" ? "Runtime is running" : "Среда запущена"),
      body: currentLanguage === "en"
        ? "The selected path is active. Use the same button to stop it."
        : "Выбранный путь активен. Для остановки используй ту же кнопку.",
      badge: currentLanguage === "en" ? "Running" : "Запущено",
    };
  }
  return {
    tone: runtimeHealthTone(runtimeState.status),
    title: currentLanguage === "en" ? "Launch state" : "Состояние запуска",
    body: runtimeState.nextAction || runtimeState.reason || (currentLanguage === "en" ? "Choose the path and start it when ready." : "Выбери путь и запусти его, когда будешь готов."),
    badge: runtimeHealthLabel(runtimeState.status),
  };
}

async function cancelOperatorJob(jobId, context) {
  const response = await fetch(`/operator/jobs/${jobId}/cancel`, {
    method: "POST",
    headers: {
      Accept: "application/json",
    },
  });
  if (!response.ok) {
    throw new Error(await responseDetail(response));
  }
  const job = await response.json();
  runningJobs.set(jobId, {
    jobId,
    label: context.label,
    pathKey: context.pathKey || selectedLaunchPath,
    status: job.status,
    actionKind: "stop",
    detail: currentLanguage === "en" ? "cancelling launch" : "отмена запуска",
  });
  appendImmediateJobLifecycleLog(
    context,
    currentLanguage === "en"
      ? `cancelling ${context.label}`
      : `отмена запуска: ${context.label}`,
  );
  showToast(
    currentLanguage === "en" ? "Launch is stopping" : "Запуск останавливается",
    pathTitle(runtimePaths[context.pathKey || selectedLaunchPath]),
    "info",
  );
  renderActivity();
  renderSystemStatus();
  rerenderAll();
}

async function triggerPrimaryActionForPath(pathKey) {
  const path = runtimePaths[pathKey];
  if (!path) return;
  selectedLaunchPath = pathKey;
  rerenderAll();
  const activeJob = getActiveJobForPath(pathKey);
  if (activeJob?.actionKind === "start" && activeJob.status !== "cancelling") {
    await cancelOperatorJob(activeJob.jobId, {
      surface: "runtime",
      pathKey,
      label: pathTitle(path),
      actionKind: "stop",
    });
    return;
  }
  if (activeJob) {
    return;
  }
  const shouldStop = isPathRunning(pathKey);
  if (!shouldStop && !isLaunchable(pathKey)) {
    switchSection("launch");
    openReasonModal(pathKey);
    return;
  }
  const actionId = shouldStop ? getStopActionIdForPath(pathKey) : getActionIdForPath(pathKey);
  if (!actionId) {
    activityFeed.unshift(`${currentLanguage === "en" ? "No mapped action for" : "Действие не сопоставлено для"} ${pathTitle(path)}`);
    renderActivity();
    return;
  }
  await runOperatorAction(actionId, {
    surface: "runtime",
    pathKey,
    label: pathTitle(path),
    actionKind: shouldStop ? "stop" : "start",
  });
}

function getActionIdForDeployLabel(label) {
  const overrides = {
    "Собрать bundle": "deploy.bundle.build",
    "Собрать офлайн-бандл": "deploy.bundle.build",
    "Собрать host APT bundle": "deploy.host_packages.build",
    "Собрать APT-бандл": "deploy.host_packages.build",
    "Экспортировать images": "deploy.images.export",
    "Экспортировать образы": "deploy.images.export",
    "Сгенерировать manifest": "deploy.bundle.manifest",
    "Сгенерировать манифест": "deploy.bundle.manifest",
    "Pack tar.gz": "deploy.bundle.pack",
    "Упаковать tar.gz": "deploy.bundle.pack",
    "Распаковать бандл": null,
    "Проверить host packages": "deploy.host.check",
    "Проверить системные пакеты": "deploy.host.check",
    "Установить host APT bundle": "deploy.host.install",
    "Установить APT-бандл": "deploy.host.install",
    "Деплоить runtime": "deploy.runtime.deploy",
    "Деплоить среду": "deploy.runtime.deploy",
    "Запустить offline bundle": "deploy.runtime.run",
    "Запустить офлайн-бандл": "deploy.runtime.run",
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
  const runtimeState = deriveLaunchRuntimeState(path.key);
  const runtimeJob = getActiveJobForPath(path.key);
  const inlineState = launchInlineState(path, runtimeState, runtimeJob);
  const isRunning = ["healthy", "running"].includes(runtimeState.status);
  const primaryLabel = runtimeJob?.actionKind === "stop" || runtimeJob?.status === "cancelling"
    ? (currentLanguage === "en" ? "Stopping..." : "Остановка...")
    : runtimeJob?.actionKind === "start"
      ? (currentLanguage === "en" ? "Stop" : "Остановить")
      : isRunning
        ? (currentLanguage === "en" ? "Stop" : "Остановить")
        : path.key === "container"
          ? (currentLanguage === "en" ? "Run Bundle" : "Запустить офлайн-бандл")
          : (currentLanguage === "en" ? "Launch" : "Запустить");
  const blockedLabel = path.key === "container"
    ? (currentLanguage === "en" ? "Bundle Run Blocked" : "Запуск офлайн-бандла заблокирован")
    : (currentLanguage === "en" ? "Launch Blocked" : "Запуск заблокирован");
  const statusPillClass = isRunning ? "lime" : runtimeHealthTone(runtimeState.status);
  const statusPillText = isRunning
    ? (currentLanguage === "en" ? "Running" : "Запущено")
    : runtimeHealthLabel(runtimeState.status);
  const card = document.createElement("article");
  card.className = `runtime-card ${path.status === "unavailable" ? "unavailable" : ""}`;
  card.innerHTML = `
    <div class="path-card-header">
      <div>
        <div class="eyebrow">${currentLanguage === "en" ? "Launch Path" : "Путь запуска"}</div>
        <h3>${pathTitle(path)}</h3>
        <p>${displayText(localizedField(path, "description"))}</p>
      </div>
      <span class="status-pill ${statusPillClass}">${statusPillText}</span>
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
    <div class="launch-runtime-inline" data-llm-tools-platform-hook="operator-launch-runtime-state">
      <div class="launch-runtime-inline-head">
        <strong class="launch-runtime-inline-title">${inlineState.title}</strong>
        <span class="status-pill ${inlineState.tone}">${inlineState.badge}</span>
      </div>
      <p class="launch-runtime-inline-copy">${inlineState.body}</p>
    </div>
    <div class="button-row">
      <button class="primary-button launch-button" data-path="${path.key}" ${(runtimeJob?.actionKind === "stop" || runtimeJob?.status === "cancelling") || ((!isLaunchable(path.key) && !isRunning) && !runtimeJob) ? "disabled" : ""}>
        ${(!isLaunchable(path.key) && !runtimeJob && !isRunning) ? blockedLabel : primaryLabel}
      </button>
      ${showSafePortsButton ? `
        <button class="ghost-button safe-ports-button" data-path="${path.key}">
          ${currentLanguage === "en" ? "Stage safe local ports" : "Подставить локальные безопасные порты"}
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
  const os = getMetricValue(["Определённая ОС", "Detected OS", "Detected host OS", "Detected Host OS"]);
  const cpu = getMetricValue(["CPU / память", "CPU / memory"]);
  const gpuVisibility = getMetricValue(["Видимость GPU", "GPU visibility", "GPU Visibility"]);
  const gpuInventory = getMetricValue(["Инвентарь GPU", "GPU inventory", "GPU Inventory"], "");
  const cpuParts = String(cpu || "").split(" / ").map((item) => item.trim()).filter(Boolean);
  const architecture = cpuParts[0] || (currentLanguage === "en" ? "Unknown" : "Неизвестно");
  const memory = cpuParts.slice(1).join(" / ") || (currentLanguage === "en" ? "Unknown" : "Неизвестно");
  const hasDetailedInventory = String(gpuInventory || "").trim() && !["Неизвестно", "Unknown", "GPU NVIDIA не обнаружены", "No NVIDIA GPU detected"].includes(String(gpuInventory).trim());
  const hardwareFacts = [
    {
      label: currentLanguage === "en" ? "Kernel / OS" : "Ядро / ОС",
      value: os,
    },
    {
      label: currentLanguage === "en" ? "Architecture" : "Архитектура",
      value: architecture,
    },
    {
      label: currentLanguage === "en" ? "Memory" : "Память",
      value: memory,
    },
    {
      label: currentLanguage === "en" ? "GPU Status" : "GPU статус",
      value: gpuVisibility,
    },
  ].filter((item) => {
    const normalized = String(item.value || "").trim();
    return normalized && !["Неизвестно", "Unknown"].includes(normalized);
  });
  const inventoryRows = hasDetailedInventory ? String(gpuInventory).split("; ").map((item) => item.trim()).filter(Boolean) : [];
  container.innerHTML = `
    <div class="metric-card hardware-card">
      <div class="metric-label">${currentLanguage === "en" ? "Hardware Summary" : "Аппаратная сводка"}</div>
      <div class="hardware-facts">
        ${hardwareFacts.map((item) => `
          <div class="hardware-fact-row">
            <div class="source-label">${displayText(item.label)}</div>
            <div class="source-value hardware-fact-value">${displayText(item.value)}</div>
          </div>
        `).join("")}
      </div>
      ${inventoryRows.length ? `
        <div class="hardware-inventory">
          <div class="source-label">${currentLanguage === "en" ? "GPU Inventory" : "Инвентарь GPU"}</div>
          <div class="hardware-inventory-list">
            ${inventoryRows.map((item) => `<div class="source-value hardware-inventory-item">${displayText(item)}</div>`).join("")}
          </div>
        </div>
      ` : ""}
      <p>${currentLanguage === "en" ? "The card separates host, architecture, memory, and GPU details into a quick operator checklist." : "Карточка разделяет хост, архитектуру, память и детали GPU в быстрый операторский список."}</p>
    </div>
  `;
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
      <strong>${currentLanguage === "en" ? `${running} of ${rows.length || 0} ready` : `${running} из ${rows.length || 0} готовы`}</strong>
      <p>${blocked ? (currentLanguage === "en" ? `Blocked: ${blocked}.` : `Заблокировано: ${blocked}.`) : displayText("Явных блокировок в опубликованных проверках нет.")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Diagnostics" : "Диагностика"}</div>
      <strong>${currentLanguage === "en" ? `${pathDiagnostics.length || 0} signals` : `${pathDiagnostics.length || 0} сигналов`}</strong>
      <p>${pathDiagnostics.length ? (currentLanguage === "en" ? "Check diagnostics first, then metrics and logs below." : "Сначала смотри диагностику, затем метрики и журнал ниже.") : (currentLanguage === "en" ? "Backend has not sent additional parser/runtime signals yet." : "Backend пока не прислал дополнительных сигналов парсинга или среды выполнения.")}</p>
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
  const partialStages = mode.stages.filter((item) => item.status === "partial").length;

  container.innerHTML = `
    <div class="workspace-card deploy-strip-card">
      <div class="source-label">${currentLanguage === "en" ? "Active Mode" : "Активный режим"}</div>
      <strong>${displayLabel(mode.label)}</strong>
      <span class="workspace-caption">${currentLanguage === "en" ? "Current operator surface" : "Текущий операторский режим"}</span>
    </div>
    <div class="workspace-card deploy-strip-card">
      <div class="source-label">${currentLanguage === "en" ? "Flow Stages" : "Этапы потока"}</div>
      <strong>${currentLanguage === "en" ? `${runningStages} running` : `${runningStages} в работе`}</strong>
      <span class="workspace-caption">${currentLanguage === "en" ? `${partialStages} partial, ${blockedStages} blocked` : `${partialStages} частично, ${blockedStages} заблокированы`}</span>
    </div>
    <div class="workspace-card deploy-strip-card">
      <div class="source-label">${currentLanguage === "en" ? "Actions" : "Действия"}</div>
      <strong>${currentLanguage === "en" ? `${mode.actions.length} available` : `${mode.actions.length} доступны`}</strong>
      <span class="workspace-caption">${currentLanguage === "en" ? "Operator commands only" : "Только operator-команды"}</span>
    </div>
    <div class="workspace-card deploy-strip-card">
      <div class="source-label">${currentLanguage === "en" ? "Observability" : "Наблюдаемость"}</div>
      <strong>${currentLanguage === "en" ? `${links.length} links` : `${links.length} перехода`}</strong>
      <span class="workspace-caption">${currentLanguage === "en" ? "Grafana and Prometheus" : "Grafana и Prometheus"}</span>
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
        <p>${displayText("Backend не сообщил о дополнительных блокерах среды выполнения сверх текущего состояния доступности.")}</p>
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
  container.className = "service-overview-list service-compact-list";
  container.innerHTML = Object.values(runtimePaths).map((path) => {
    const rows = serviceRows.filter((row) => row.path === path.key);
    const running = rows.filter((row) => row.status === "running").length;
    const degraded = rows.filter((row) => row.status === "degraded").length;
    const blocked = rows.filter((row) => row.status === "blocked").length;
    return `
      <div class="service-compact-row">
        <div class="service-compact-copy">
          <div class="source-label">${pathTitle(path)}</div>
          <strong>${currentLanguage === "en" ? `${running}/${rows.length} ready, ${blocked} blocked` : `${running}/${rows.length} готовы, ${blocked} заблокированы`}</strong>
          <p>${currentLanguage === "en" ? `${degraded} degraded services` : `${degraded} сервисов в деградации`}</p>
        </div>
        <span class="status-pill ${chipClass(path.status)}">${cap(path.status)}</span>
      </div>
    `;
  }).join("");
}

function renderActivity() {
  const container = document.querySelector("#recent-activity");
  const maintenanceContainer = document.querySelector("#maintenance-activity-feed");
  const recentItems = activityFeed.slice(0, 5);
  const emptyMarkup = `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "No Recent Activity" : "Пока нет недавней активности"}</div>
      <p>${currentLanguage === "en" ? "Completed jobs and maintenance actions appear here." : "Здесь появятся завершённые jobs и maintenance-действия."}</p>
    </div>
  `;
  const overviewItems = recentItems.slice(0, 3);
  container.innerHTML = overviewItems.length ? overviewItems.map((item) => `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Latest Event" : "Последнее событие"}</div>
      <strong>${displayText(translateSeedLogLine(item))}</strong>
    </div>
  `).join("") : emptyMarkup;
  if (maintenanceContainer) {
    maintenanceContainer.innerHTML = recentItems.length ? recentItems.map((item) => `
      <div class="metric-card">
        <div class="source-label">${currentLanguage === "en" ? "Latest Event" : "Последнее событие"}</div>
        <strong>${displayText(translateSeedLogLine(item))}</strong>
      </div>
    `).join("") : emptyMarkup;
  }
}

function renderProfiles() {
  const container = document.querySelector("#profile-grid");
  const profiles = runtimePaths[selectedLaunchPath].profiles;
  container.className = "profile-grid profile-list";
  container.innerHTML = profiles.map((profile) => `
    <div class="profile-row">
      <div class="profile-row-copy">
        <div class="source-label">${pathTitle(runtimePaths[selectedLaunchPath])}</div>
        <strong>${displayLabel(localizedField(profile, "title"))}</strong>
        <p>${cap(profile.status)}</p>
      </div>
      <span class="profile-radio-dot ${chipClass(profile.status)}" aria-hidden="true"></span>
    </div>
  `).join("");
}

function renderLaunchStrip() {
  const container = document.querySelector("#launch-strip");
  if (!container) return;
  const path = runtimePaths[selectedLaunchPath];
  const state = deriveLaunchRuntimeState(selectedLaunchPath);
  const runtimeJob = getActiveJobForPath(selectedLaunchPath);
  const runtimeStateDetail = state.status === "failed" ? summarizeFailureDetail(state.detail) : state.detail;
  const activeJobDetail = runtimeJob
    ? runtimeJob.detail
    : (lastJobSummary?.pathKey === selectedLaunchPath
      ? (lastJobSummary.outcome === "failed" ? summarizeFailureDetail(lastJobSummary.detail) : lastJobSummary.detail)
      : (currentLanguage === "en" ? "Launch actions will appear here." : "Здесь появится ход текущего запуска."));
  const nextAction = selectedLaunchPath === "container"
    ? (hasBundlePortConflict()
      ? (currentLanguage === "en" ? "Stage safe local ports before deploy" : "Сначала подставь локальные безопасные порты")
      : (currentLanguage === "en" ? "Open build/deploy after launch" : "После запуска перейди в Сборка / Деплой"))
    : (currentLanguage === "en" ? "Verify services and logs after start" : "После запуска проверь сервисы и логи");

  container.innerHTML = `
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Selected path" : "Выбранный путь"}</div>
      <strong>${pathTitle(path)}</strong>
      <p>${displayText(localizedField(path, "description"))}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Runtime state" : "Состояние среды"}</div>
      <strong>${runtimeHealthLabel(state.status)}</strong>
      <p>${runtimeStateDetail || (state.checkSummary || "")}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Active job" : "Активная задача"}</div>
      <strong>${runtimeJob ? runtimeJob.label : (currentLanguage === "en" ? "No active job" : "Нет активной задачи")}</strong>
      <p>${activeJobDetail}</p>
    </div>
    <div class="workspace-card">
      <div class="source-label">${currentLanguage === "en" ? "Next step" : "Следующий шаг"}</div>
      <strong>${currentLanguage === "en" ? "Operator action" : "Действие оператора"}</strong>
      <p>${state.nextAction || nextAction}</p>
    </div>
  `;
}

function renderLaunchFailureSummary() {
  const panel = document.querySelector("#launch-fail-summary");
  const pill = document.querySelector("#launch-fail-pill");
  const list = document.querySelector("#launch-fail-list");
  const state = deriveLaunchRuntimeState(selectedLaunchPath);

  panel.hidden = !shouldShowLaunchFailSummary(state);
  if (panel.hidden) {
    list.innerHTML = "";
    return;
  }

  pill.className = `status-pill ${runtimeHealthTone(state.status)}`;
  pill.textContent = runtimeHealthLabel(state.status);
  list.innerHTML = `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Summary" : "Сводка"}</div>
      <strong>${state.detail || (currentLanguage === "en" ? "Runtime status requires attention." : "Состояние среды требует внимания.")}</strong>
      <p>${state.reason || (currentLanguage === "en" ? "Inspect the operator log and diagnostics." : "Проверь журнал оператора и диагностику.")}</p>
    </div>
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Next operator step" : "Следующий шаг оператора"}</div>
      <strong>${state.nextAction || (currentLanguage === "en" ? "Inspect the launch log first." : "Сначала посмотри журнал запуска.")}</strong>
      <p>${state.failedStage
        ? (currentLanguage === "en" ? `Last failing stage: ${state.failedStage}` : `Последний проблемный этап: ${state.failedStage}`)
        : (state.checkSummary || (currentLanguage === "en" ? "Use Services and Deploy to isolate the issue." : "Используй Сервисы и Сборка / Деплой для локализации проблемы."))}</p>
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
  renderLogConsole(
    logConsole,
    lines,
    currentLanguage === "en"
      ? "No launch log for this runtime path yet."
      : "Для этого пути запуска пока нет launch-лога.",
  );

  const nextSteps = document.querySelector("#launch-next-steps");
  const items = selectedLaunchPath === "container"
    ? [
        runtimeState.nextAction || (currentLanguage === "en" ? "Check Build / Deploy and the container health cards." : "Проверь Сборка / Деплой и карточки состояния контейнерного пути."),
        hasBundlePortConflict()
          ? (currentLanguage === "en" ? "Apply safe local ports before retrying deploy." : "Перед повторным деплоем подставь локальные безопасные порты.")
          : (currentLanguage === "en" ? "If build or deploy stalls, inspect Docker, ports, and diagnostics." : "Если сборка или деплой застопорились, проверь Docker, порты и диагностику."),
      ]
    : [
        currentLanguage === "en" ? "Open Services to verify endpoints and logs." : "Открой Сервисы и проверь конечные точки и логи.",
        currentLanguage === "en" ? "Open Config if runtime profile or device mode needs adjustment." : "Открой Конфиг, если нужно поправить профиль запуска или режим устройства.",
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

function shouldShowField(pathKey, field) {
  if (!field?.visibleWhen) return true;
  return Object.entries(field.visibleWhen).every(([depKey, expected]) => {
    const dependency = findField(pathKey, depKey);
    if (!dependency) return false;
    return String(getFieldValue(pathKey, dependency)) === String(expected);
  });
}

function configFieldTone(field) {
  if (field.pathPolicy === "host_path_flexible") return "host-source";
  if (field.pathPolicy === "bundle_internal_path") return "container-target";
  return "default";
}

function validationStatusTone(validation) {
  const status = String(validation?.status || "").toLowerCase();
  if (["ok", "warning", "error", "info"].includes(status)) {
    return status;
  }
  return "";
}

function fieldValidationStatus(pathKey, field) {
  return validationStatusTone(getFieldValidation(pathKey, field));
}

function configFieldClasses(pathKey, field) {
  const classes = [`config-field-${configFieldTone(field)}`];
  const validationStatus = fieldValidationStatus(pathKey, field);
  if (validationStatus) {
    classes.push(`config-field-validation-${validationStatus}`);
  }
  return classes.join(" ");
}

function configFieldRoleLabel(field) {
  if (field.pathPolicy === "host_path_flexible") {
    return currentLanguage === "en" ? "Host source" : "Источник хоста";
  }
  if (field.pathPolicy === "bundle_internal_path") {
    return currentLanguage === "en" ? "Container target" : "Цель в контейнере";
  }
  return "";
}

function pathValidationMapKey(pathKey, fieldKey) {
  return `${pathKey}:${fieldKey}`;
}

function isBundleInternalModelField(field) {
  return Boolean(field?.pathPolicy === "bundle_internal_path" && [
    "MODEL_PATH_LLM",
    "MODEL_PATH_VLM",
    "MMPROJ_PATH",
    "MODEL_PATH_EMBEDDING_INTENT",
    "MODEL_PATH_EMBEDDING_RETRIEVAL",
  ].includes(field.key));
}

function bundleSelectionToContainerPath(selectedPath) {
  const normalized = String(selectedPath || "").replaceAll("\\", "/");
  const marker = "/deploy/offline_bundle/models/";
  const markerIndex = normalized.indexOf(marker);
  if (markerIndex === -1) return null;
  const relativePath = normalized.slice(markerIndex + marker.length).replace(/^\/+/, "");
  if (!relativePath) return null;
  return `/app/backend/models/${relativePath}`;
}

function bundleContainerPathToBrowserPath(containerPath) {
  const rawValue = String(containerPath || "").trim().replaceAll("\\", "/");
  if (!rawValue.startsWith("/app/backend/models/")) return "./deploy/offline_bundle/models";
  const relativePath = rawValue.slice("/app/backend/models/".length).replace(/^\/+/, "");
  return relativePath
    ? `./deploy/offline_bundle/models/${relativePath}`
    : "./deploy/offline_bundle/models";
}

function isResolvableHostBrowserPath(pathValue) {
  const rawValue = String(pathValue || "").trim();
  if (!rawValue) return false;
  if (rawValue.startsWith("/models/")) return false;
  if (rawValue.startsWith("/app/")) return false;
  if (rawValue.startsWith("/opt/llm-tools-platform/")) return false;
  return true;
}

function getPathBrowserStartPath(field, currentValue) {
  if (field?.pathPolicy === "bundle_internal_path") {
    return bundleContainerPathToBrowserPath(currentValue);
  }
  if (field?.pathPolicy === "host_path_flexible") {
    return isResolvableHostBrowserPath(currentValue) ? currentValue : "";
  }
  return currentValue;
}

function localBundlePathValidation(field, value) {
  const rawValue = String(value || "").trim();
  if (!rawValue) {
    return {
      status: "info",
      message: currentLanguage === "en"
        ? "Value is empty and must be set explicitly."
        : "Значение пустое и должно быть задано явно.",
    };
  }
  if (rawValue.startsWith("/app/backend/models/")) {
    return {
      status: "info",
      message: currentLanguage === "en"
        ? "Container-side model path will be validated against the offline bundle layout after apply."
        : "Container-side путь модели будет проверен относительно layout офлайн-бандла после применения.",
    };
  }
  if (rawValue.startsWith("/opt/llm-tools-platform/external/")) {
    return {
      status: "info",
      message: currentLanguage === "en"
        ? "This container target is expected to be provided by an external host mount before startup."
        : "Этот container path должен быть заполнен внешним host mount перед запуском.",
    };
  }
  return {
    status: "warning",
    message: currentLanguage === "en"
      ? "Expected a container-side model path under /app/backend/models/ or an external mount target under /opt/llm-tools-platform/external/."
      : "Ожидается container-side путь под /app/backend/models/ или target внешнего mount под /opt/llm-tools-platform/external/.",
  };
}

function getModelPairValidation(pathKey, field) {
  if (pathKey !== "container") return null;
  const modelSourceMode = String(getFieldValue(pathKey, findField(pathKey, "MODEL_SOURCE_MODE")) || "");
  if (!modelSourceMode) return null;
  const pairKeys = [
    ["MODEL_PATH_VLM", "MMPROJ_PATH"],
    ["HOST_MODEL_PATH_VLM", "HOST_MMPROJ_PATH"],
  ];
  for (const [leftKey, rightKey] of pairKeys) {
    if (field.key !== leftKey && field.key !== rightKey) continue;
    if (leftKey.startsWith("HOST_") && modelSourceMode !== "external_host_mounts") return null;
    const leftField = findField(pathKey, leftKey);
    const rightField = findField(pathKey, rightKey);
    const leftValue = String(getFieldValue(pathKey, leftField) || "").trim();
    const rightValue = String(getFieldValue(pathKey, rightField) || "").trim();
    if ((leftValue && !rightValue) || (!leftValue && rightValue)) {
      return {
        status: "warning",
        message: currentLanguage === "en"
          ? "VLM and mmproj must be filled together."
          : "VLM и mmproj должны быть заполнены парой.",
      };
    }
  }
  return null;
}

function getFieldValidation(pathKey, field) {
  const stagedValidation = stagedPathValidations.get(pathValidationMapKey(pathKey, field.key));
  const pairValidation = getModelPairValidation(pathKey, field);
  if (stagedValidation && stagedValidation.value === String(getFieldValue(pathKey, field) || "").trim()) {
    return stagedValidation.validation || pairValidation || field.validation || null;
  }
  return pairValidation || field.validation || null;
}

async function validatePathField(pathKey, field, value) {
  const rawValue = String(value || "").trim();
  const mapKey = pathValidationMapKey(pathKey, field.key);
  if (!rawValue) {
    stagedPathValidations.delete(mapKey);
    renderConfig();
    return;
  }
  if (isBundleInternalModelField(field)) {
    stagedPathValidations.set(mapKey, {
      value: rawValue,
      validation: localBundlePathValidation(field, rawValue),
    });
    renderConfig();
    return;
  }
  if (field.pathPolicy !== "host_path_flexible") {
    stagedPathValidations.delete(mapKey);
    renderConfig();
    return;
  }
  try {
    const params = new URLSearchParams({
      path: rawValue,
      kind: field.pickerKind || "file",
      field_key: field.key,
    });
    const response = await fetch(`/operator/path-browser/validate?${params.toString()}`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = await response.json();
    stagedPathValidations.set(mapKey, {
      value: rawValue,
      validation: {
        status: payload.status || (payload.valid ? "ok" : "error"),
        message: payload.message || "",
        checks: payload.checks || [],
      },
    });
  } catch (error) {
    stagedPathValidations.set(mapKey, {
      value: rawValue,
      validation: {
        status: "error",
        message: String(error?.message || error || ""),
      },
    });
  }
  renderConfig();
}

function queuePathValidation(pathKey, field, value) {
  const mapKey = pathValidationMapKey(pathKey, field.key);
  const existing = pathValidationTimers.get(mapKey);
  if (existing) {
    window.clearTimeout(existing);
  }
  const timer = window.setTimeout(() => {
    pathValidationTimers.delete(mapKey);
    validatePathField(pathKey, field, value);
  }, 220);
  pathValidationTimers.set(mapKey, timer);
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
  const text = String(value ?? "");
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (_error) {
      // Fall through to legacy copy path when the browser rejects clipboard access.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.top = "-1000px";
  textarea.style.left = "-1000px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  try {
    const copied = document.execCommand("copy");
    if (!copied) {
      throw new Error("clipboard-copy-command-failed");
    }
  } finally {
    textarea.remove();
  }
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
  if (field.control === "toggle") {
    const checked = String(value).toLowerCase() === "true";
    return `
      <label class="config-toggle">
        <input type="checkbox" ${checked ? "checked" : ""} ${field.editable === false ? "disabled" : ""} />
        <span class="config-toggle-track"><span class="config-toggle-thumb"></span></span>
        <span class="config-toggle-label">${checked ? "true" : "false"}</span>
      </label>
    `;
  }
  if (field.control === "select" && Array.isArray(field.options) && field.options.length) {
    return `
      <select ${field.editable === false ? "disabled" : ""}>
        ${field.options.map((option) => `
          <option value="${option.value}" ${String(option.value) === String(value) ? "selected" : ""}>${displayLabel(localizedField(option, "label"))}</option>
        `).join("")}
      </select>
    `;
  }
  const inputType = field.secret && !isVisible
    ? "password"
    : (field.control === "number" ? "number" : field.control === "url" ? "url" : "text");
  const inputStep = field.control === "number" ? " step=\"any\"" : "";
  const input = `<input type="${inputType}" value="${value}"${inputStep} ${field.editable === false ? "disabled" : ""} />`;
  if (field.pickerKind && field.editable !== false && !field.secret) {
    return `
      <div class="config-input-row config-input-row-picker">
        ${input}
        <button
          type="button"
          class="config-picker-button"
          data-open-picker="${field.key}"
          title="${displayLabel(localizedField(field, "pickerLabel"))}"
          aria-label="${displayLabel(localizedField(field, "pickerLabel"))}"
        >
          ${displayLabel(localizedField(field, "pickerLabel"))}
        </button>
      </div>
    `;
  }
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
    <div class="metric-card config-source-card config-source-card-${chipClass(item.freshness)}">
      <div class="config-source-card-head">
        <div class="source-label">${currentLanguage === "en" ? "Source File" : "Файл-источник"}</div>
        <span class="status-pill ${chipClass(item.freshness)}">${cap(item.freshness)}</span>
      </div>
      <strong class="mono-line">${item.path}</strong>
      <p>${displayText(localizedField(item, "role"))}</p>
      <div class="config-source-action">
        <span class="source-label">${currentLanguage === "en" ? "Operator Action" : "Действие оператора"}</span>
        <p>${configSourceAction(item, selectedConfigPath)}</p>
      </div>
    </div>
  `).join("");

  const sourceGuidance = summarizeConfigSources(selectedConfigPath, pathState.sourceFiles);
  document.querySelector("#config-source-guidance").innerHTML = `
    <div class="config-source-guidance-head">
      <p class="eyebrow">${sourceGuidance.title}</p>
      <span class="status-pill ${sourceGuidance.tone}">${pathTitle(path)}</span>
    </div>
    <strong>${sourceGuidance.summary}</strong>
    <div class="config-guidance-list">
      ${sourceGuidance.items.map((item) => `
        <div class="config-guidance-item">
          <div class="source-label">${escapeHtml(item.label)}</div>
          <p>${escapeHtml(item.body)}</p>
        </div>
      `).join("")}
    </div>
  `;

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
  document.querySelector("#config-variant-helper").innerHTML = renderConfigVariantHelper(selectedConfigPath, selectedVariant);

  const presetList = document.querySelector("#config-preset-list");
  const presets = selectedVariant?.presets || [];
  presetList.innerHTML = presets.length ? presets.map((preset) => `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Preset" : "Пресет"}</div>
      <strong>${displayLabel(localizedField(preset, "title"))}</strong>
      <p>${displayText(localizedField(preset, "description"))}</p>
      <div class="button-row" style="margin-top: 12px;">
        <button class="ghost-button config-preset-button" data-preset-id="${preset.presetId}">${currentLanguage === "en" ? "Stage values" : "Подставить значения"}</button>
      </div>
    </div>
  `).join("") : `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "Manual editing" : "Ручное редактирование"}</div>
      <p>${currentLanguage === "en" ? "This variant has no meaningful preset. Edit the fields below directly." : "У этого варианта нет осмысленного пресета. Изменяй поля ниже вручную."}</p>
    </div>
  `;

  const groups = document.querySelector("#config-groups");
  const visibleGroups = (selectedVariant?.groups || [])
    .map((group) => ({ ...group, fields: (group.fields || []).filter((field) => shouldShowField(selectedConfigPath, field)) }))
    .filter((group) => group.fields.length);
  groups.innerHTML = visibleGroups.map((group) => `
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
          <label class="config-field ${configFieldClasses(selectedConfigPath, field)}" data-key="${field.key}">
            <div class="config-label-row">
              <div class="config-label-stack">
                <span class="config-key">${displayLabel(localizedField(field, "label"))}</span>
                ${configFieldRoleLabel(field) ? `<span class="config-role-badge">${configFieldRoleLabel(field)}</span>` : ""}
              </div>
              ${uiSettings.showLiteralEnvKeys ? `<span class="config-key-hint">${field.key}</span>` : ""}
            </div>
            <p class="config-help">${displayText(localizedField(field, "description") || "")}</p>
            ${renderFieldControl(selectedConfigPath, field)}
            <div class="config-meta">
              <span>${currentLanguage === "en" ? "Applied" : "Применено"}: <span class="mono-line">${field.secret && !isSecretFieldVisible(selectedConfigPath, field) ? maskSecretValue(field.applied) : field.applied}</span></span>
              ${uiSettings.showSourceFiles ? `<span>${currentLanguage === "en" ? "Source" : "Источник"}: <span class="mono-line">${field.source}</span></span>` : ""}
              ${field.pathPolicy ? `<span>${currentLanguage === "en" ? "Path policy" : "Политика пути"}: ${translatePathPolicy(field.pathPolicy)}</span>` : ""}
              ${field.pathExample ? `<span>${currentLanguage === "en" ? "Example" : "Пример"}: <span class="mono-line">${field.pathExample}</span></span>` : ""}
              ${getFieldValidation(selectedConfigPath, field) ? `
                <span class="config-validation-note config-validation-note-${fieldValidationStatus(selectedConfigPath, field)}">
                  <span class="config-validation-badge">${currentLanguage === "en" ? (getFieldValidation(selectedConfigPath, field).status || "info") : (
                    getFieldValidation(selectedConfigPath, field).status === "ok" ? "ok" :
                    getFieldValidation(selectedConfigPath, field).status === "warning" ? "предупреждение" :
                    getFieldValidation(selectedConfigPath, field).status === "error" ? "ошибка" :
                    "к сведению"
                  )}</span>
                  <span>${displayValidationMessage(getFieldValidation(selectedConfigPath, field).message || "")}</span>
                </span>
              ` : ""}
            </div>
          </label>
        `).join("")}
      </div>
    </section>
  `).join("");

  groups.querySelectorAll("input, select").forEach((control) => {
    const eventName = control.tagName === "SELECT" || control.type === "checkbox"
      ? "change"
      : "input";
    control.addEventListener(eventName, () => {
      const fieldNode = control.closest(".config-field");
      const field = findField(selectedConfigPath, fieldNode.dataset.key);
      const nextValue = control.type === "checkbox" ? (control.checked ? "true" : "false") : control.value;
      if (control.type === "checkbox") {
        const toggleLabel = fieldNode.querySelector(".config-toggle-label");
        if (toggleLabel) toggleLabel.textContent = nextValue;
      }
      setStagedFieldValue(selectedConfigPath, fieldNode.dataset.key, nextValue);
      if (field?.pathPolicy || field?.pickerKind) {
        queuePathValidation(selectedConfigPath, field, nextValue);
      }
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

  groups.querySelectorAll("[data-open-picker]").forEach((button) => {
    button.addEventListener("click", async () => {
      const field = findField(selectedConfigPath, button.dataset.openPicker);
      if (!field) return;
      await openPathBrowser(selectedConfigPath, field);
    });
  });

  groups.querySelectorAll("[data-copy-secret]").forEach((button) => {
    button.addEventListener("click", async () => {
      const fieldNode = button.closest(".config-field");
      const field = findField(selectedConfigPath, button.dataset.copySecret);
      if (!fieldNode || !field) return;
      try {
        await copyToClipboard(getFieldValue(selectedConfigPath, field));
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

async function fetchPathBrowser(path, kind) {
  const params = new URLSearchParams({ kind });
  if (path) params.set("path", path);
  const response = await fetch(`/operator/path-browser?${params.toString()}`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function renderPathBrowserModal() {
  const modal = document.querySelector("#path-browser-modal");
  const title = document.querySelector("#path-browser-title");
  const cwd = document.querySelector("#path-browser-current-path");
  const list = document.querySelector("#path-browser-entry-list");
  const selectCurrent = document.querySelector("#path-browser-select-current");
  const field = pathBrowserState.fieldKey ? findField(pathBrowserState.pathKey, pathBrowserState.fieldKey) : null;

  title.textContent = field
    ? displayLabel(localizedField(field, "label"))
    : (currentLanguage === "en" ? "Choose path" : "Выбор пути");
  cwd.textContent = pathBrowserState.cwd || (currentLanguage === "en" ? "Allowed roots" : "Разрешённые корни");
  selectCurrent.textContent = currentLanguage === "en" ? "Use this folder" : "Выбрать эту папку";
  selectCurrent.hidden = pathBrowserState.kind !== "directory" || !pathBrowserState.cwd;

  list.innerHTML = `
    ${pathBrowserState.parentPath ? `
      <button type="button" class="path-browser-entry path-browser-entry-parent" data-path-browser-parent="${escapeHtml(pathBrowserState.parentPath)}">
        <span class="path-browser-entry-glyph path-browser-entry-glyph-parent">..</span>
        <span>${currentLanguage === "en" ? "Parent folder" : "Родительская папка"}</span>
      </button>
    ` : ""}
    ${pathBrowserState.entries.map((entry) => `
      <div class="path-browser-entry ${entry.type === "directory" ? "path-browser-entry-dir" : ""}">
        <button type="button" class="path-browser-open-button" data-path-browser-open="${escapeHtml(entry.path)}">
          <span class="path-browser-entry-glyph ${entry.type === "directory" ? "path-browser-entry-glyph-dir" : "path-browser-entry-glyph-file"}">${entry.type === "directory" ? "D" : "F"}</span>
          <span>${escapeHtml(entry.name)}</span>
        </button>
        ${entry.selectable ? `
          <button
            type="button"
            class="ghost-button path-browser-select-button"
            data-path-browser-select="${escapeHtml(entry.path)}"
          >
            ${currentLanguage === "en" ? "Use" : "Выбрать"}
          </button>
        ` : ""}
      </div>
    `).join("")}
  `;

  list.querySelectorAll("[data-path-browser-open]").forEach((button) => {
    button.addEventListener("click", async () => {
      pathBrowserState = {
        ...pathBrowserState,
        ...(await fetchPathBrowser(button.dataset.pathBrowserOpen, pathBrowserState.kind)),
      };
      renderPathBrowserModal();
    });
  });

  list.querySelectorAll("[data-path-browser-parent]").forEach((button) => {
    button.addEventListener("click", async () => {
      pathBrowserState = {
        ...pathBrowserState,
        ...(await fetchPathBrowser(button.dataset.pathBrowserParent, pathBrowserState.kind)),
      };
      renderPathBrowserModal();
    });
  });

  list.querySelectorAll("[data-path-browser-select]").forEach((button) => {
    button.addEventListener("click", () => {
      applyPathBrowserSelection(button.dataset.pathBrowserSelect);
    });
  });

  selectCurrent.onclick = () => applyPathBrowserSelection(pathBrowserState.cwd);
  if (!modal.open) modal.showModal();
}

function applyPathBrowserSelection(pathValue) {
  const field = findField(pathBrowserState.pathKey, pathBrowserState.fieldKey);
  if (!field) return;
  const resolvedPathValue = isBundleInternalModelField(field)
    ? bundleSelectionToContainerPath(pathValue)
    : pathValue;
  if (isBundleInternalModelField(field) && !resolvedPathValue) {
    showToast(
      currentLanguage === "en" ? "Could not use this path" : "Не удалось использовать этот путь",
      currentLanguage === "en"
        ? "For container-side model paths choose a file or folder inside deploy/offline_bundle/models."
        : "Для container-side model paths выбери файл или папку внутри deploy/offline_bundle/models.",
      "error",
    );
    return;
  }
  setStagedFieldValue(pathBrowserState.pathKey, field.key, resolvedPathValue);
  queuePathValidation(pathBrowserState.pathKey, field, resolvedPathValue);
  showToast(
    currentLanguage === "en" ? "Path selected" : "Путь выбран",
    displayLabel(localizedField(field, "label")),
  );
  document.querySelector("#path-browser-modal").close();
  renderConfig();
}

function renderConfigVariantHelper(pathKey, variant) {
  if (!variant || pathKey !== "container") return "";
  const modelSourceMode = String(getFieldValue(pathKey, findField(pathKey, "MODEL_SOURCE_MODE")) || "bundle_layout");
  if (variant.variantId === "model_source_mode") {
    return `
      <article class="config-variant-helper-card">
        <div class="config-helper-head">
          <p class="eyebrow">${currentLanguage === "en" ? "How It Works" : "Как это работает"}</p>
          <strong>${currentLanguage === "en" ? "Choose where models come from before startup" : "Сначала выбери, откуда контейнеры возьмут модели"}</strong>
        </div>
        <div class="config-flow-diagram">
          <div class="config-flow-node ${modelSourceMode === "bundle_layout" ? "active" : ""}">
            <span class="config-flow-kicker">${currentLanguage === "en" ? "Bundle mode" : "Bundle-режим"}</span>
            <strong>deploy/offline_bundle/models</strong>
          </div>
          <span class="config-flow-arrow">→</span>
          <div class="config-flow-node">
            <span class="config-flow-kicker">${currentLanguage === "en" ? "Mounted to" : "Монтируется в"}</span>
            <strong>/app/backend/models</strong>
          </div>
          <span class="config-flow-arrow">→</span>
          <div class="config-flow-node ${modelSourceMode === "external_host_mounts" ? "active" : ""}">
            <span class="config-flow-kicker">${currentLanguage === "en" ? "External mode" : "Внешний режим"}</span>
            <strong>/opt/llm-tools-platform/external/...</strong>
          </div>
        </div>
        <p class="config-helper-note">${currentLanguage === "en"
          ? "Bundle layout keeps everything self-contained. External host mounts let you reuse models from another disk without copying them into the bundle."
          : "Bundle layout хранит всё внутри офлайн-бандла. External host mounts позволяют использовать модели с другого диска без копирования их в bundle."}</p>
      </article>
    `;
  }
  if (variant.variantId === "artifact_mounts") {
    const isExternal = modelSourceMode === "external_host_mounts";
    return `
      <article class="config-variant-helper-card">
        <div class="config-helper-head">
          <p class="eyebrow">${currentLanguage === "en" ? "Path Contract" : "Схема путей"}</p>
          <strong>${currentLanguage === "en" ? "Host source and container target are different things" : "Источник на хосте и путь в контейнере — это разные вещи"}</strong>
        </div>
        <div class="config-flow-diagram">
          <div class="config-flow-node ${isExternal ? "active" : ""}">
            <span class="config-flow-kicker">${currentLanguage === "en" ? "Host source" : "Источник хоста"}</span>
            <strong>${isExternal ? "/mnt/models/..." : "deploy/offline_bundle/models/..."}</strong>
          </div>
          <span class="config-flow-arrow">→</span>
          <div class="config-flow-node">
            <span class="config-flow-kicker">${currentLanguage === "en" ? "Mount step" : "Этап монтирования"}</span>
            <strong>${isExternal ? "read-only bind mount" : "./models → /app/backend/models"}</strong>
          </div>
          <span class="config-flow-arrow">→</span>
          <div class="config-flow-node">
            <span class="config-flow-kicker">${currentLanguage === "en" ? "Runtime path" : "Путь для runtime"}</span>
            <strong>${isExternal ? "/opt/llm-tools-platform/external/..." : "/app/backend/models/..."}</strong>
          </div>
        </div>
        <p class="config-helper-note">${currentLanguage === "en"
          ? "For VLM always provide both the model file and the mmproj file. For embedders point to the whole model directory, not a single file inside it."
          : "Для VLM всегда задавай и файл модели, и файл mmproj. Для embedder-путей указывай целую папку модели, а не отдельный файл внутри неё."}</p>
      </article>
    `;
  }
  return "";
}

async function openPathBrowser(pathKey, field) {
  const currentValue = String(getFieldValue(pathKey, field) || "").trim();
  const startPath = getPathBrowserStartPath(field, currentValue);
  pathBrowserState = {
    pathKey,
    fieldKey: field.key,
    kind: field.pickerKind || "file",
    cwd: "",
    entries: [],
    currentValue,
    parentPath: null,
  };
  try {
    pathBrowserState = {
      ...pathBrowserState,
      ...(await fetchPathBrowser(startPath, pathBrowserState.kind)),
    };
    renderPathBrowserModal();
  } catch (error) {
    showToast(
      currentLanguage === "en" ? "Could not open path browser" : "Не удалось открыть выбор пути",
      String(error?.message || error || ""),
      "error",
    );
  }
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
    (payload.updatedKeys || []).forEach((fieldKey) => {
      stagedPathValidations.delete(pathValidationMapKey(selectedConfigPath, fieldKey));
    });
    [...stagedFieldValues.keys()]
      .filter((key) => key.startsWith(`${selectedConfigPath}:`))
      .forEach((key) => stagedFieldValues.delete(key));
    dirtyFields = new Set([...dirtyFields].filter((key) => !key.startsWith(`${selectedConfigPath}:`)));
    const appliedPathValidations = (payload.updatedKeys || [])
      .map((fieldKey) => findField(selectedConfigPath, fieldKey))
      .filter((field) => field?.pathPolicy)
      .map((field) => ({
        field,
        validation: getModelPairValidation(selectedConfigPath, field) || field.validation || null,
      }))
      .filter((entry) => entry.validation);
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Applied ${payload.updatedKeys.length} config changes for ${pathTitle(runtimePaths[selectedConfigPath])}`
        : `Применено ${payload.updatedKeys.length} изменений конфига для ${pathTitle(runtimePaths[selectedConfigPath])}`,
    );
    appliedPathValidations.forEach(({ field, validation }) => {
      activityFeed.unshift(
        currentLanguage === "en"
          ? `Validation for ${displayLabel(localizedField(field, "label"))}: ${validation.message || validation.status || "info"}`
          : `Валидация для ${displayLabel(localizedField(field, "label"))}: ${validation.message || validation.status || "к сведению"}`,
      );
    });
    if (appliedPathValidations.length) {
      const hasError = appliedPathValidations.some(({ validation }) => validation.status === "error");
      const hasWarning = appliedPathValidations.some(({ validation }) => validation.status === "warning");
      showToast(
        currentLanguage === "en" ? "Path validation refreshed" : "Валидация путей обновлена",
        hasError
          ? (currentLanguage === "en" ? "One or more applied paths are invalid." : "Один или несколько применённых путей некорректны.")
          : hasWarning
            ? (currentLanguage === "en" ? "Applied paths need review." : "Применённые пути требуют проверки.")
            : (currentLanguage === "en" ? "Applied paths look consistent." : "Применённые пути выглядят согласованно."),
        hasError ? "error" : hasWarning ? "warning" : "success",
      );
    }
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
  grid.className = "service-grid service-compact-list";
  const rows = serviceRows.filter((row) => row.path === selectedServicesPath);
  grid.innerHTML = rows.length ? rows.map((row) => `
    <div class="service-compact-row">
      <div class="service-compact-copy">
        <div class="source-label">${displayLabel(row.name)}</div>
        <strong>${row.endpoint}</strong>
        <p>${displayText(localizedField(row, "stage"))} · ${displayText(localizedField(row, "freshness"))}</p>
      </div>
      <span class="status-pill ${chipClass(row.status)}">${cap(row.status)}</span>
    </div>
  `).join("") : `
    <div class="metric-card">
      <div class="source-label">${currentLanguage === "en" ? "No Service Checks Yet" : "Пока нет проверок сервисов"}</div>
      <p>${displayText("Backend ещё не опубликовал проверки для этого пути запуска.")}</p>
    </div>
  `;

  const pathDiagnostics = diagnostics.filter((item) => item.path === selectedServicesPath);
  const diagnosticsPill = document.querySelector("#diagnostics-status-pill");
  const diagnosticsPanel = document.querySelector("#diagnostics-list").closest(".panel");
  diagnosticsPill.className = `status-pill ${pathDiagnostics.some((item) => item.tone === "orange") ? "orange" : (pathDiagnostics.length ? "cyan" : "neutral")}`;
  diagnosticsPill.textContent = pathDiagnostics.length
    ? (currentLanguage === "en" ? `Diagnostics: ${pathDiagnostics.length}` : `Диагностика: ${pathDiagnostics.length}`)
    : (currentLanguage === "en" ? "No runtime diagnostics" : "Нет диагностики среды");
  diagnosticsPanel.hidden = !pathDiagnostics.length;
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
    `).join("") : "";

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
    ${services.map((service) => `<option value="${service}">${displayText(cap(service))}</option>`).join("")}
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
  renderLogConsole(
    logConsole,
    visibleLines,
    displayText("Для этого пути запуска ещё нет загруженных логов. Сначала проверь диагностику и состояние сервисов выше."),
  );
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

  document.querySelector("#deploy-stage-grid").innerHTML = mode.stages.map((item, index) => `
    <div class="deploy-step-card">
      <span class="deploy-step-index">${index + 1}</span>
      <div class="deploy-step-copy">
        <div class="source-label">${displayLabel(localizedField(item, "title"))}</div>
        <strong>${displayText(localizedField(item, "body"))}</strong>
        <span class="deploy-step-script">${item.script}</span>
      </div>
      <span class="status-pill ${chipClass(item.status)}">${cap(item.status)}</span>
    </div>
  `).join("");

  document.querySelector("#deploy-action-grid").innerHTML = mode.actions.map((item) => `
    <div class="action-card">
      <h4>${displayLabel(localizedField(item, "title"))}</h4>
      <p>${displayText(localizedField(item, "body"))}</p>
      <div class="button-row" style="margin-top: 14px;">
        <button class="primary-button deploy-action-button" data-action="${item.action}" ${!getActionIdForDeployLabel(item.action) ? "disabled" : ""}>${displayLabel(localizedField(item, "title"))}</button>
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
    ${stages.map((stage) => `<option value="${stage}">${displayText(cap(stage))}</option>`).join("")}
  `;
  if (stages.includes(previous)) {
    select.value = previous;
  } else {
    select.value = "all";
  }
}

function renderDeployLogs(filter) {
  const logConsole = document.querySelector("#deploy-log-console");
  renderLogConsole(
    logConsole,
    deployLogLines
      .filter((line) => line.mode === selectedDeployMode)
      .filter((line) => filter === "all" || line.stage === filter)
      .map((line) => line.text),
    currentLanguage === "en" ? "No deploy log loaded yet." : "Лог деплоя пока не загружен.",
  );
}

function renderMaintenance() {
  document.querySelector("#action-grid").innerHTML = maintenanceActions.map((item) => `
    <div class="action-card">
      <h4>${displayLabel(localizedField(item, "title"))}</h4>
      <div class="button-row" style="margin-top: 14px;">
        <button class="primary-button action-button" data-action="${item.action}">${displayLabel(localizedField(item, "action"))}</button>
      </div>
    </div>
  `).join("");

  const blockersPanel = document.querySelector("#maintenance-blockers-panel");
  blockersPanel.hidden = !blockers.length;
  document.querySelector("#blocker-list").innerHTML = blockers.length ? blockers.map((item) => `
    <div class="blocker-card">
      <h4>${displayLabel(localizedField(item, "title"))}</h4>
      <p>${displayText(localizedField(item, "body"))}</p>
    </div>
  `).join("") : "";

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
      await triggerPrimaryActionForPath(button.dataset.path);
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
  mobileNavOpen = false;
  renderTopbarHint();
  renderHelpDrawer();
  syncMobileNav();
}

function initNavigation() {
  sectionButtons.forEach((button) => {
    button.addEventListener("click", () => switchSection(button.dataset.target));
  });
  const mobileToggle = document.querySelector("#mobile-nav-toggle");
  if (mobileToggle) {
    mobileToggle.addEventListener("click", () => {
      mobileNavOpen = !mobileNavOpen;
      syncMobileNav();
    });
  }
  window.matchMedia("(max-width: 1180px)").addEventListener("change", () => {
    mobileNavOpen = false;
    syncMobileNav();
  });
  const helpOpen = document.querySelector("#help-drawer-open");
  const helpClose = document.querySelector("#help-drawer-close");
  helpOpen?.addEventListener("click", () => setHelpDrawerOpen(true));
  helpClose?.addEventListener("click", () => setHelpDrawerOpen(false));
}

function initLogFilter() {
  const select = document.querySelector("#log-filter");
  select.addEventListener("change", () => renderLogs(select.value));
  const deploySelect = document.querySelector("#deploy-log-filter");
  deploySelect.addEventListener("change", () => renderDeployLogs(deploySelect.value));
}

function updateTopbarAction() {
  const button = document.querySelector("#topbar-start-button");
  const label = document.querySelector("#topbar-start-button-label");
  const note = document.querySelector("#topbar-start-button-note");
  const badge = document.querySelector("#topbar-runtime-badge");
  const path = runtimePaths[selectedLaunchPath];
  if (!button) return;
  button.setAttribute("aria-expanded", launchMenuOpen ? "true" : "false");
  if (label) {
    label.textContent = launchPrimaryLabel(selectedLaunchPath);
  }
  if (note) {
    note.textContent = pathTitle(path);
  }
  if (badge) {
    const badgeState = topbarRuntimeBadgeState(selectedLaunchPath);
    badge.className = `status-pill topbar-runtime-badge ${badgeState.tone}`;
    badge.textContent = badgeState.text;
  }
  const isRunning = isPathRunning(selectedLaunchPath);
  const activeJob = getActiveJobForPath(selectedLaunchPath);
  button.dataset.mode = activeJob?.actionKind === "start" || isRunning ? "stop" : "launch";
  button.disabled = activeJob?.actionKind === "stop" || activeJob?.status === "cancelling";
  const actionLabel = activeJob?.actionKind === "stop" || activeJob?.status === "cancelling"
    ? (currentLanguage === "en" ? "Stop runtime" : "Остановить среду")
    : activeJob?.actionKind === "start"
      ? (currentLanguage === "en" ? "Cancel launch" : "Остановить запуск")
      : isRunning
        ? (currentLanguage === "en" ? "Stop runtime" : "Остановить среду")
        : (currentLanguage === "en" ? "Launch runtime" : "Запустить среду");
  button.setAttribute(
    "title",
    `${actionLabel}: ${pathTitle(path)}`,
  );
  button.setAttribute("aria-label", `${actionLabel}: ${pathTitle(path)}`);
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
    >
      <span class="launch-menu-copy">
        <strong>${pathTitle(path)}</strong>
        <span>${displayText(localizedField(path, "description"))}</span>
      </span>
      <span class="status-pill ${isPathRunning(path.key) ? "lime" : chipClass(path.status)}">${isPathRunning(path.key) ? (currentLanguage === "en" ? "Running" : "Запущено") : cap(path.status)}</span>
    </button>
  `).join("");
  [...menu.querySelectorAll(".launch-menu-item")].forEach((button) => {
    button.addEventListener("click", async () => {
      const { path } = button.dataset;
      launchMenuOpen = false;
      selectedLaunchPath = path;
      rerenderAll();
    });
  });
}

function initTopbarAction() {
  const primaryButton = document.querySelector("#topbar-start-button");
  const menu = document.querySelector("#topbar-launch-menu");
  primaryButton.addEventListener("click", (event) => {
    event.stopPropagation();
    if (event.target.closest(".launch-button-icon")) {
      launchMenuOpen = !launchMenuOpen;
      updateTopbarAction();
      return;
    }
    launchMenuOpen = false;
    updateTopbarAction();
    triggerPrimaryActionForPath(selectedLaunchPath);
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
    build: { key: "build", name: currentLanguage === "en" ? "Build Bundle" : "Сборка офлайн-бандла" },
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
    if (String(detail).startsWith("unknown-action:")) {
      return String(detail);
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
    logLines = mergeUniqueLogs(
      payload.logLines || [],
      logLines,
      (line) => `${line.path || ""}|${line.service || ""}|${line.text || ""}`,
    );
    maintenanceActions = payload.maintenanceActions || maintenanceActions;
    blockers = payload.blockers || blockers;
    activityFeed = payload.activityFeed || activityFeed;
    deploySurface = payload.deploySurface || deploySurface;
    deployLogLines = mergeUniqueLogs(
      payload.deployLogLines || [],
      deployLogLines,
      (line) => `${line.mode || ""}|${line.stage || ""}|${line.text || ""}`,
    );
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
  Object.entries(runtimeHealthByPath).forEach(([pathKey, payload]) => {
    if (payload?.status && payload.status !== "unknown") {
      localRuntimeStateOverrides.delete(pathKey);
    }
  });
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
    const deployFilter = document.querySelector("#deploy-log-filter");
    if (deployFilter && deployFilter.value !== "all") {
      deployFilter.value = "all";
    }
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
  const serviceFilter = document.querySelector("#log-filter");
  if (serviceFilter && serviceFilter.value !== "all") {
    serviceFilter.value = "all";
  }
  renderLogs(document.querySelector("#log-filter").value || "all");
  renderLaunchLogs();
}

function appendImmediateJobLifecycleLog(context, message) {
  const timestamp = new Date().toISOString();
  if (context.surface === "deploy") {
    deployLogLines.push({
      mode: context.mode || selectedDeployMode,
      stage: "queue",
      text: `[${timestamp}] system: ${message}`,
    });
    const deployFilter = document.querySelector("#deploy-log-filter");
    if (deployFilter && deployFilter.value !== "all") {
      deployFilter.value = "all";
    }
    renderDeployLogs(document.querySelector("#deploy-log-filter").value || "all");
    return;
  }
  logLines.push({
    path: context.pathKey || selectedLaunchPath,
    service: "operator",
    text: `[${timestamp}] queue/system: ${message}`,
  });
  const serviceFilter = document.querySelector("#log-filter");
  if (serviceFilter && serviceFilter.value !== "all") {
    serviceFilter.value = "all";
  }
  renderLogs(document.querySelector("#log-filter").value || "all");
  renderLaunchLogs();
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
    const existingJob = runningJobs.get(jobId);
    jobPollFailures.delete(jobId);
    runningJobs.set(jobId, {
      jobId,
      label: context.label,
      pathKey: context.pathKey || selectedLaunchPath,
      status: job.status,
      actionKind: existingJob?.actionKind || context.actionKind || "start",
      detail: job.current_stage || (currentLanguage === "en" ? "processing" : "выполнение"),
    });
    appendJobLogs(job, context);
    renderSystemStatus();

    if (["completed", "failed", "cancelled"].includes(job.status)) {
      clearInterval(jobPollTimers.get(jobId));
      jobPollTimers.delete(jobId);
      jobPollFailures.delete(jobId);
      runningJobs.delete(jobId);
      const failureDetail = summarizeFailureDetail(job.logs?.at(-1)?.message || "");
      lastJobSummary = {
        label: context.label,
        pathKey: context.pathKey || selectedLaunchPath,
        outcome: job.status,
        detail: job.status === "completed"
          ? (currentLanguage === "en" ? `completed (${job.current_stage || "done"})` : `завершено (${job.current_stage || "готово"})`)
          : job.status === "cancelled"
            ? (currentLanguage === "en" ? "cancelled by operator" : "остановлено оператором")
          : `${currentLanguage === "en" ? `failed (${job.current_stage || "error"})` : `ошибка (${job.current_stage || "ошибка"})`}: ${failureDetail}`,
      };
      if (job.status === "failed") {
        showToast(
          currentLanguage === "en" ? "Runtime action failed" : "Ошибка runtime-действия",
          summarizeFailureDetail(job.logs?.at(-1)?.message || lastJobSummary.detail),
          "error",
        );
      } else if (job.status === "cancelled") {
        showToast(
          currentLanguage === "en" ? "Launch cancelled" : "Запуск остановлен",
          pathTitle(runtimePaths[context.pathKey || selectedLaunchPath]),
          "success",
        );
      }
      activityFeed.unshift(
        currentLanguage === "en"
          ? `${context.label} ${job.status === "completed" ? "completed" : job.status === "cancelled" ? "cancelled" : "failed"} (${job.current_stage || "done"})`
          : `${context.label} ${job.status === "completed" ? "завершён" : job.status === "cancelled" ? "остановлен" : "завершился с ошибкой"} (${job.current_stage || "done"})`,
      );
      if (job.status === "completed" && context.actionKind === "stop") {
        setLocalRuntimeOverride(context.pathKey || selectedLaunchPath, {
          status: "not_started",
          detail: currentLanguage === "en" ? "Runtime was stopped." : "Среда остановлена.",
          reason: currentLanguage === "en" ? "The selected runtime path is no longer running." : "Выбранный путь запуска больше не работает.",
          nextAction: currentLanguage === "en" ? "Start the selected path when needed." : "При необходимости снова запусти выбранный путь.",
        });
      }
      if (job.status === "cancelled") {
        setLocalRuntimeOverride(context.pathKey || selectedLaunchPath, {
          status: "not_started",
          detail: currentLanguage === "en" ? "Launch was cancelled." : "Запуск остановлен.",
          reason: currentLanguage === "en" ? "The current operator launch job was interrupted by the operator." : "Текущая задача запуска была прервана оператором.",
          nextAction: currentLanguage === "en" ? "Start the selected path again when you are ready." : "При необходимости снова запусти выбранный путь.",
        });
      }
      await hydrateOperatorState();
      await hydrateRuntimeHealth();
      rerenderAll();
      if (job.status === "completed") {
        const targetPath = context.pathKey || selectedLaunchPath;
        if (context.actionKind === "start") {
          await settleRuntimeState(targetPath, true);
        } else if (context.actionKind === "stop") {
          await settleRuntimeState(targetPath, false, 4, 500);
        }
      }
    }
  } catch (error) {
    const failures = (jobPollFailures.get(jobId) || 0) + 1;
    jobPollFailures.set(jobId, failures);
    if (failures < 3) {
      runningJobs.set(jobId, {
        jobId,
        label: context.label,
        pathKey: context.pathKey || selectedLaunchPath,
        status: "running",
        actionKind: context.actionKind || "start",
        detail: currentLanguage === "en" ? "refreshing state" : "обновление состояния",
      });
      renderSystemStatus();
      return;
    }
    clearInterval(jobPollTimers.get(jobId));
    jobPollTimers.delete(jobId);
    jobPollFailures.delete(jobId);
    runningJobs.delete(jobId);
    if (context.actionKind === "stop") {
      setLocalRuntimeOverride(context.pathKey || selectedLaunchPath, {
        status: "not_started",
        detail: currentLanguage === "en" ? "Runtime stop completed; control plane became unavailable." : "Остановка завершена; control plane стал недоступен.",
        reason: currentLanguage === "en" ? "The stop action likely brought down the selected runtime path together with its local control plane." : "Операция остановки, вероятно, завершила выбранный путь запуска вместе с локальным control plane.",
        nextAction: currentLanguage === "en" ? "Use Launch to start the path again when needed." : "Используй «Запуск», чтобы при необходимости поднять путь снова.",
      });
      lastJobSummary = {
        label: context.label,
        pathKey: context.pathKey || selectedLaunchPath,
        outcome: "completed",
        detail: currentLanguage === "en" ? "stopped (control plane unavailable after stop)" : "остановлено (control plane недоступен после stop)",
      };
      appendImmediateJobLifecycleLog(
        context,
        currentLanguage === "en"
          ? `stop completed for ${context.label}; control plane became unavailable`
          : `остановка завершена для ${context.label}; control plane стал недоступен`,
      );
      activityFeed.unshift(
        currentLanguage === "en"
          ? `${context.label} stopped; control plane became unavailable`
          : `${context.label} остановлен; control plane стал недоступен`,
      );
      showToast(
        currentLanguage === "en" ? "Runtime stopped" : "Среда остановлена",
        pathTitle(runtimePaths[context.pathKey || selectedLaunchPath]),
        "success",
      );
      renderActivity();
      renderSystemStatus();
      rerenderAll();
      return;
    }
    const failureDetail = summarizeFailureDetail(
      error?.message || (currentLanguage === "en" ? "operation refresh failed" : "ошибка обновления состояния"),
    );
    lastJobSummary = {
      label: context.label,
      pathKey: context.pathKey || selectedLaunchPath,
      outcome: "failed",
      detail: currentLanguage === "en"
        ? `could not refresh operation state: ${failureDetail}`
        : `не удалось обновить состояние операции: ${failureDetail}`,
    };
    appendImmediateJobLifecycleLog(
      context,
      currentLanguage === "en"
        ? `could not refresh operation state for ${context.label}: ${failureDetail}`
        : `не удалось обновить состояние операции для ${context.label}: ${failureDetail}`,
    );
    showToast(
      currentLanguage === "en" ? "Operation refresh failed" : "Ошибка обновления операции",
      currentLanguage === "en"
        ? `Could not refresh state for ${context.label}`
        : `Не удалось обновить состояние для ${context.label}`,
      "error",
    );
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Could not refresh operation for ${context.label}`
        : `Не удалось обновить состояние операции для ${context.label}`,
    );
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
      jobId: job.job_id,
      label: context.label,
      pathKey: context.pathKey || selectedLaunchPath,
      status: "queued",
      actionKind: context.actionKind || "start",
      detail: currentLanguage === "en" ? "queued" : "в очереди",
    });
    appendImmediateJobLifecycleLog(
      context,
      context.actionKind === "stop"
        ? (currentLanguage === "en"
          ? `queued stop for ${context.label} via ${actionId}`
          : `поставлена в очередь остановка: ${context.label} через ${actionId}`)
        : (currentLanguage === "en"
          ? `queued ${context.label} via ${actionId}`
          : `поставлено в очередь: ${context.label} через ${actionId}`),
    );
    activityFeed.unshift(
      context.actionKind === "stop"
        ? (currentLanguage === "en" ? `${context.label} stop queued via ${actionId}` : `${context.label} поставлен на остановку через ${actionId}`)
        : (currentLanguage === "en" ? `${context.label} queued via ${actionId}` : `${context.label} поставлен в очередь через ${actionId}`),
    );
    showToast(
      context.actionKind === "stop"
        ? (currentLanguage === "en" ? "Runtime is stopping" : "Среда останавливается")
        : (currentLanguage === "en" ? "Runtime is starting" : "Среда запускается"),
      pathTitle(runtimePaths[context.pathKey || selectedLaunchPath]),
      "info",
    );
    renderActivity();
    renderSystemStatus();
    jobLogOffsets.set(job.job_id, 0);
    jobPollFailures.set(job.job_id, 0);
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
      detail: summarizeFailureDetail(error.message || (currentLanguage === "en" ? "internal error" : "внутренняя ошибка")),
    };
    appendImmediateJobLifecycleLog(
      context,
      context.actionKind === "stop"
        ? (currentLanguage === "en"
          ? `failed to stop ${context.label}: ${summarizeFailureDetail(error.message || "internal error")}`
          : `не удалось остановить ${context.label}: ${summarizeFailureDetail(error.message || "внутренняя ошибка")}`)
        : (currentLanguage === "en"
          ? `failed to start ${context.label}: ${summarizeFailureDetail(error.message || "internal error")}`
          : `не удалось запустить ${context.label}: ${summarizeFailureDetail(error.message || "внутренняя ошибка")}`),
    );
    showToast(
      context.actionKind === "stop"
        ? (currentLanguage === "en" ? "Could not stop runtime" : "Не удалось остановить среду")
        : (currentLanguage === "en" ? "Could not start runtime" : "Не удалось запустить среду"),
      summarizeFailureDetail(error.message || (currentLanguage === "en" ? "internal error" : "внутренняя ошибка")),
      "error",
    );
    activityFeed.unshift(
      context.actionKind === "stop"
        ? (currentLanguage === "en" ? `Could not stop runtime: ${context.label} (${error.message || "internal error"})` : `Не удалось остановить среду: ${context.label} (${error.message || "внутренняя ошибка"})`)
        : (currentLanguage === "en" ? `Could not start runtime: ${context.label} (${error.message || "internal error"})` : `Не удалось запустить среду: ${context.label} (${error.message || "внутренняя ошибка"})`),
    );
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
      showToast(
        currentLanguage === "en" ? "Config reloaded" : "Конфиг перезагружен",
        currentLanguage === "en" ? "Operator state was refreshed from backend sources." : "Состояние оператора перечитано из backend-источников.",
      );
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
      showToast(
        currentLanguage === "en" ? "Runtime paths updated" : "Пути запуска обновлены",
        currentLanguage === "en" ? "Availability was recomputed from the operator API." : "Доступность пересчитана из operator API.",
      );
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
      showToast(
        currentLanguage === "en" ? "Deploy surface reloaded" : "Deploy surface обновлён",
        displayLabel(deploySurface[selectedDeployMode].label),
      );
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
    showToast(
      currentLanguage === "en" ? "Action not wired" : "Действие не подключено",
      displayLabel(actionLabel),
      "error",
    );
    renderActivity();
  } catch (error) {
    activityFeed.unshift(
      currentLanguage === "en"
        ? `Could not execute maintenance action: ${displayLabel(actionLabel)}`
        : `Не удалось выполнить действие обслуживания: ${displayLabel(actionLabel)}`,
    );
    showToast(
      currentLanguage === "en" ? "Maintenance action failed" : "Ошибка maintenance-действия",
      summarizeFailureDetail(error?.message || displayLabel(actionLabel)),
      "error",
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
  document.querySelector("#path-browser-close").addEventListener("click", () => {
    document.querySelector("#path-browser-modal").close();
  });
}

init();
