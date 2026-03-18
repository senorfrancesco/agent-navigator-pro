#!/bin/bash
# ============================================================
# Полный скрипт установки Agent Navigator Pro для Ubuntu
# Устанавливает: системные зависимости, tmux, Anaconda,
# Python окружение и все библиотеки
# ============================================================

set -e  # Выход при ошибке

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color
SKIP_CREATE=false
DOCKER_RELOGIN_REQUIRED=false
CUDA_TOOLKIT_TARGET="12.8"
CUDA_TOOLKIT_APT_PACKAGE="cuda-toolkit-12-8"
CUDA_DRIVER_MIN_VERSION_LINUX="570.124.06"
PYTORCH_VERSION="2.10.0"
TORCHVISION_VERSION="0.25.0"
TORCHAUDIO_VERSION="2.10.0"
PYTORCH_CUDA_INDEX_URL="https://download.pytorch.org/whl/cu128"
PYTORCH_CPU_INDEX_URL="https://download.pytorch.org/whl/cpu"
GPU_RUNTIME_READY=false
CUDA_TOOLKIT_READY=false
CUDA_TOOLKIT_STATUS="missing"
NVIDIA_DRIVER_STATUS="missing"
NVIDIA_DRIVER_VERSION=""
PYTORCH_INSTALL_TARGET="cpu"
LLAMA_CPP_CUDA_REBUILD_STATUS="skipped"

is_wsl() {
    [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null
}

prompt_yes_no() {
    local prompt="$1"
    local default="${2:-n}"

    if [[ "${AGENT_NAVIGATOR_ASSUME_YES:-0}" == "1" ]]; then
        return 0
    fi

    local suffix="[y/N]"
    if [[ "$default" == "y" ]]; then
        suffix="[Y/n]"
    fi

    read -r -p "$prompt $suffix: " reply
    if [[ -z "$reply" ]]; then
        reply="$default"
    fi
    [[ "$reply" =~ ^[Yy]$ ]]
}

version_ge() {
    local current="$1"
    local required="$2"
    [ "$current" = "$required" ] && return 0
    [ "$(printf '%s\n%s\n' "$required" "$current" | sort -V | head -n 1)" = "$required" ]
}

resolve_cuda_repo_slug() {
    case "${VERSION_ID:-}" in
        "22.04")
            echo "ubuntu2204"
            ;;
        "24.04")
            echo "ubuntu2404"
            ;;
        *)
            return 1
            ;;
    esac
}

install_cuda_toolkit_12_8() {
    local repo_slug="$1"
    local keyring_file="cuda-keyring_1.1-1_all.deb"
    local keyring_url="https://developer.download.nvidia.com/compute/cuda/repos/${repo_slug}/x86_64/${keyring_file}"

    echo "Установка CUDA Toolkit ${CUDA_TOOLKIT_TARGET} из репозитория NVIDIA (${repo_slug})..."
    wget "$keyring_url"
    sudo dpkg -i "$keyring_file"
    sudo apt-get update
    sudo apt-get -y install "$CUDA_TOOLKIT_APT_PACKAGE"
    rm -f "$keyring_file"
}

install_managed_config() {
    local source_path="$1"
    local target_path="$2"
    local label="$3"

    if [ ! -f "$source_path" ]; then
        echo -e "${YELLOW}[!]${NC} Исходный файл $label не найден: $source_path"
        return 0
    fi

    mkdir -p "$(dirname "$target_path")"

    if [ -f "$target_path" ]; then
        if cmp -s "$source_path" "$target_path"; then
            echo -e "${GREEN}[OK]${NC} $label уже установлен и совпадает с репозиторным шаблоном"
            return 0
        fi

        echo -e "${YELLOW}[WARNING]${NC} $label уже существует и отличается от репозиторного шаблона:"
        echo "  target: $target_path"
        if prompt_yes_no "Перезаписать $label репозиторной версией?" "n"; then
            cp "$source_path" "$target_path"
            echo -e "${GREEN}[OK]${NC} $label обновлён"
        else
            echo -e "${YELLOW}[SKIP]${NC} $label оставлен без изменений"
        fi
        return 0
    fi

    cp "$source_path" "$target_path"
    echo -e "${GREEN}[OK]${NC} $label установлен: $target_path"
}

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Agent Navigator Pro - Полная установка для Ubuntu${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "${YELLOW}После установки canonical runtime entrypoint: ./scripts/launcher.sh${NC}"
echo ""

if ! prompt_yes_no "Начать guided установку зависимостей для Agent Navigator?" "y"; then
    echo -e "${YELLOW}[INFO]${NC} Установка отменена пользователем."
    exit 0
fi

# Проверка что скрипт запущен на Ubuntu/Debian
if [ ! -f /etc/os-release ]; then
    echo -e "${RED}[ERROR]${NC} Не удалось определить операционную систему"
    exit 1
fi

source /etc/os-release
if [[ "$ID" != "ubuntu" ]] && [[ "$ID" != "debian" ]]; then
    echo -e "${YELLOW}[WARNING]${NC} Этот скрипт оптимизирован для Ubuntu/Debian"
    echo -e "${YELLOW}[WARNING]${NC} Текущая ОС: $NAME"
    read -p "Продолжить установку? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

echo -e "${GREEN}Операционная система: $NAME $VERSION${NC}"
echo ""

# ============================================================
# Шаг 1: Обновление системы и установка системных зависимостей
# ============================================================

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 1: Установка системных зависимостей${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

echo "Обновление списка пакетов..."
sudo apt-get update

echo ""
echo "Установка базовых инструментов..."
sudo apt-get install -y \
    wget \
    curl \
    git \
    build-essential \
    software-properties-common \
    ca-certificates \
    gnupg \
    lsb-release

echo -e "${GREEN}[OK]${NC} Базовые инструменты установлены"

# ============================================================
# Шаг 2: Установка tmux
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 2: Установка tmux${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

if command -v tmux &> /dev/null; then
    TMUX_VERSION=$(tmux -V)
    echo -e "${GREEN}[OK]${NC} tmux уже установлен: $TMUX_VERSION"
else
    echo "Установка tmux..."
    sudo apt-get install -y tmux
    echo -e "${GREEN}[OK]${NC} tmux установлен"
fi

# Установка конфигурации tmux из репозитория
echo ""
echo "Установка конфигурации tmux..."
mkdir -p "$HOME/.config/tmux"

if [ -f "$PROJECT_ROOT/config/tmux/tmux.conf" ]; then
    install_managed_config "$PROJECT_ROOT/config/tmux/tmux.conf" "$HOME/.config/tmux/tmux.conf" "tmux.conf"
    install_managed_config "$PROJECT_ROOT/config/tmux/tmux.conf.local" "$HOME/.config/tmux/tmux.conf.local" "tmux.conf.local"
else
    echo -e "${YELLOW}[!]${NC} config/tmux/ не найден, пропускаю"
fi

# ============================================================
# Шаг 3: Установка библиотек для Python и обработки документов
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 3: Установка библиотек для Python${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

echo "Установка зависимостей для Python и обработки документов..."
sudo apt-get install -y \
    python3-dev \
    python3-pip \
    python3-venv \
    libssl-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libopenblas-dev \
    liblapack-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    gfortran

echo -e "${GREEN}[OK]${NC} Библиотеки для Python установлены"

# ============================================================
# Шаг 4: Установка Docker
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 4: Установка Docker${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

if is_wsl; then
    echo -e "${BLUE}[WSL]${NC} Для WSL ожидается Docker Desktop на Windows host с включённой WSL integration."
    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version 2>/dev/null || true)
        if docker info >/dev/null 2>&1; then
            echo -e "${GREEN}[OK]${NC} Docker доступен внутри WSL: ${DOCKER_VERSION:-docker}"
        else
            echo -e "${RED}[ERROR]${NC} Команда docker видна, но daemon недоступен из WSL."
            echo "Что проверить:"
            echo "  1. Docker Desktop запущен на Windows"
            echo "  2. Settings -> Resources -> WSL Integration -> текущий distro включён"
            echo "  3. Внутри WSL команда 'docker info' должна работать"
            if prompt_yes_no "Пропустить шаг Docker и продолжить остальные шаги установки?" "n"; then
                echo -e "${YELLOW}[SKIP]${NC} Шаг Docker пропущен для WSL."
            else
                echo -e "${YELLOW}[INFO]${NC} Остановка установки до исправления Docker Desktop / WSL integration."
                exit 1
            fi
        fi
    else
        echo -e "${RED}[ERROR]${NC} docker не найден внутри WSL."
        echo "Для WSL этот проект не устанавливает Docker Engine внутрь дистрибутива автоматически."
        echo "Ожидаемый путь:"
        echo "  1. Запустить Docker Desktop на Windows"
        echo "  2. Включить WSL integration для текущего distro"
        echo "  3. Убедиться, что в WSL работает 'docker info'"
        if prompt_yes_no "Пропустить шаг Docker и продолжить остальные шаги установки?" "n"; then
            echo -e "${YELLOW}[SKIP]${NC} Шаг Docker пропущен для WSL."
        else
            echo -e "${YELLOW}[INFO]${NC} Остановка установки до появления Docker в WSL."
            exit 1
        fi
    fi
elif command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version)
    echo -e "${GREEN}[OK]${NC} Docker уже установлен: $DOCKER_VERSION"
else
    echo "Установка Docker..."

    # Удаление старых версий
    sudo apt-get remove -y docker docker-engine docker.io containerd runc || true

    # Установка Docker из официального репозитория
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Добавление пользователя в группу docker
    sudo usermod -aG docker $USER
    DOCKER_RELOGIN_REQUIRED=true

    echo -e "${GREEN}[OK]${NC} Docker установлен"
    echo -e "${YELLOW}[!]${NC} ВАЖНО: Выйдите и войдите заново для применения прав docker"
fi

# ============================================================
# Шаг 5: Проверка NVIDIA GPU и CUDA (опционально)
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 5: Проверка NVIDIA GPU и CUDA${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

if command -v nvidia-smi &> /dev/null; then
    echo -e "${GREEN}[OK]${NC} NVIDIA драйверы установлены"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
    echo ""
    NVIDIA_DRIVER_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1 | tr -d '[:space:]')"
    if [ -n "$NVIDIA_DRIVER_VERSION" ] && version_ge "$NVIDIA_DRIVER_VERSION" "$CUDA_DRIVER_MIN_VERSION_LINUX"; then
        NVIDIA_DRIVER_STATUS="ok"
        GPU_RUNTIME_READY=true
        echo -e "${GREEN}[OK]${NC} Версия драйвера подходит для baseline CUDA ${CUDA_TOOLKIT_TARGET}: ${NVIDIA_DRIVER_VERSION}"
    else
        NVIDIA_DRIVER_STATUS="too-old"
        echo -e "${YELLOW}[WARNING]${NC} версия драйвера NVIDIA слишком старая для baseline CUDA ${CUDA_TOOLKIT_TARGET}"
        echo "Обновите драйвер NVIDIA до версии не ниже 570.124.06 (ветка R570+)."
        echo "Текущая версия драйвера: ${NVIDIA_DRIVER_VERSION:-unknown}"
        echo "Инструкция: https://ubuntu.com/server/docs/nvidia-drivers-installation"
        if ! prompt_yes_no "Продолжить без GPU baseline CUDA ${CUDA_TOOLKIT_TARGET}?" "y"; then
            exit 0
        fi
    fi

    # Проверка CUDA Toolkit
    if command -v nvcc &> /dev/null; then
        CUDA_VERSION=$(nvcc --version | grep "release" | awk '{print $5}' | cut -d',' -f1)
        if [[ "$CUDA_VERSION" == ${CUDA_TOOLKIT_TARGET}* ]]; then
            CUDA_TOOLKIT_READY=true
            CUDA_TOOLKIT_STATUS="ok"
            echo -e "${GREEN}[OK]${NC} CUDA toolkit ${CUDA_VERSION} установлен"
        else
            CUDA_TOOLKIT_STATUS="mismatch"
            echo -e "${YELLOW}[WARNING]${NC} Обнаружен CUDA toolkit ${CUDA_VERSION}, но baseline проекта ожидает ${CUDA_TOOLKIT_TARGET}.x"
        fi
    else
        CUDA_TOOLKIT_STATUS="missing"
        echo -e "${YELLOW}[WARNING]${NC} CUDA toolkit не обнаружен"
    fi

    if [ "$GPU_RUNTIME_READY" = true ] && [ "$CUDA_TOOLKIT_READY" = false ]; then
        if prompt_yes_no "Установить CUDA toolkit ${CUDA_TOOLKIT_TARGET} для native CUDA build path?" "y"; then
            CUDA_REPO_SLUG="$(resolve_cuda_repo_slug || true)"
            if [ -z "$CUDA_REPO_SLUG" ]; then
                echo -e "${YELLOW}[WARNING]${NC} Автоматическая установка CUDA toolkit поддержана только для Ubuntu 22.04 и 24.04"
                echo "Установите CUDA toolkit ${CUDA_TOOLKIT_TARGET}.x вручную по NVIDIA guide:"
                echo "https://docs.nvidia.com/cuda/cuda-installation-guide-linux/"
                CUDA_TOOLKIT_STATUS="manual-required"
            else
                install_cuda_toolkit_12_8 "$CUDA_REPO_SLUG"
                if command -v nvcc &> /dev/null; then
                    CUDA_VERSION=$(nvcc --version | grep "release" | awk '{print $5}' | cut -d',' -f1)
                    if [[ "$CUDA_VERSION" == ${CUDA_TOOLKIT_TARGET}* ]]; then
                        CUDA_TOOLKIT_READY=true
                        CUDA_TOOLKIT_STATUS="ok"
                        echo -e "${GREEN}[OK]${NC} CUDA toolkit ${CUDA_VERSION} установлен"
                        echo -e "${YELLOW}[!]${NC} Добавьте в ~/.bashrc:"
                        echo 'export PATH=/usr/local/cuda/bin:$PATH'
                        echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH'
                    else
                        CUDA_TOOLKIT_STATUS="mismatch"
                        echo -e "${YELLOW}[WARNING]${NC} После установки nvcc сообщает ${CUDA_VERSION}; expected ${CUDA_TOOLKIT_TARGET}.x"
                    fi
                else
                    CUDA_TOOLKIT_STATUS="missing"
                    echo -e "${YELLOW}[WARNING]${NC} CUDA toolkit installation завершилась без доступного nvcc"
                fi
            fi
        fi
    fi
else
    NVIDIA_DRIVER_STATUS="missing"
    echo -e "${YELLOW}[WARNING]${NC} NVIDIA драйверы не обнаружены"
    echo "Для работы с GPU необходимо установить драйверы NVIDIA"
    echo "Для baseline CUDA ${CUDA_TOOLKIT_TARGET} рекомендуется драйвер ветки R570+ и не ниже ${CUDA_DRIVER_MIN_VERSION_LINUX}"
    echo "Инструкция: https://ubuntu.com/server/docs/nvidia-drivers-installation"
    if ! prompt_yes_no "Продолжить без GPU поддержки?" "y"; then
        exit 0
    fi
fi

# ============================================================
# Шаг 6: Установка Anaconda/Miniconda
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 6: Установка Anaconda/Miniconda${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

if command -v conda &> /dev/null; then
    CONDA_VERSION=$(conda --version)
    echo -e "${GREEN}[OK]${NC} Conda уже установлена: $CONDA_VERSION"
else
    echo "Установка Miniconda..."

    # Определение архитектуры
    ARCH=$(uname -m)
    if [ "$ARCH" == "x86_64" ]; then
        MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
    elif [ "$ARCH" == "aarch64" ]; then
        MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh"
    else
        echo -e "${RED}[ERROR]${NC} Неподдерживаемая архитектура: $ARCH"
        exit 1
    fi

    echo "Скачивание Miniconda для $ARCH..."
    wget -O miniconda_installer.sh "$MINICONDA_URL"

    echo "Установка Miniconda..."
    bash miniconda_installer.sh -b -p "$HOME/miniconda3"

    # Инициализация conda
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
    conda init bash

    # Удаление установщика
    rm miniconda_installer.sh

    echo -e "${GREEN}[OK]${NC} Miniconda установлена"
fi

# Инициализация conda в текущей сессии
if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
fi

if ! command -v conda >/dev/null 2>&1; then
    echo -e "${RED}[ERROR]${NC} Conda не найдена после шага установки"
    exit 1
fi

CONDA_BASE_PREFIX="$(conda info --base 2>/dev/null || true)"
if [ -z "$CONDA_BASE_PREFIX" ]; then
    echo -e "${RED}[ERROR]${NC} Не удалось определить conda base prefix"
    exit 1
fi

# ============================================================
# Шаг 7: Проверка conda base и Python env
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 7: Проверка conda base и Python env${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

echo "Conda base: $CONDA_BASE_PREFIX"
echo "Активация conda base..."
if ! conda activate base; then
    echo -e "${RED}[ERROR]${NC} Не удалось активировать conda base"
    exit 1
fi
echo -e "${GREEN}[OK]${NC} Conda base активирована"

# ============================================================
# Шаг 8: Установка Python зависимостей
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 8: Установка Python зависимостей${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# Обновление pip
echo "Обновление pip..."
python -m pip install --upgrade pip setuptools wheel

# Установка зависимостей
echo ""
echo "Установка зависимостей из requirements.txt..."
cd backend

# Установка с детальным выводом
if ! pip install -r requirements.txt --no-cache-dir; then
    echo ""
    echo -e "${RED}[ERROR]${NC} Не удалось установить Python зависимости"
    echo "Если ошибка связана с Conda Terms of Service, выполните официальные команды:"
    echo "  conda tos accept"
    echo "или точечно по каналам:"
    echo "  conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main"
    echo "  conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r"
    echo "После этого повторите установку."
    exit 1
fi

echo ""
if [ "$GPU_RUNTIME_READY" = true ]; then
    echo "Canonical PyTorch GPU baseline: torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 via cu128"
    echo "Установка PyTorch ${PYTORCH_VERSION} (CUDA 12.8 / cu128)..."
    pip install --no-cache-dir \
        --index-url "$PYTORCH_CUDA_INDEX_URL" \
        "torch==${PYTORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}"
    PYTORCH_INSTALL_TARGET="cu128"
else
    echo "Canonical PyTorch CPU baseline: torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 via cpu"
    echo "Установка PyTorch ${PYTORCH_VERSION} (CPU only)..."
    pip install --no-cache-dir \
        --index-url "$PYTORCH_CPU_INDEX_URL" \
        "torch==${PYTORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}"
    PYTORCH_INSTALL_TARGET="cpu"
fi

# Специальная установка llama-cpp-python с поддержкой CUDA (если доступна)
if command -v nvcc &> /dev/null; then
    echo ""
    echo "Переустановка llama-cpp-python с поддержкой CUDA (native build path, требуется CUDA toolkit)..."
    CMAKE_ARGS="-DLLAMA_CUBLAS=on" pip install llama-cpp-python[server] --force-reinstall --no-cache-dir
    LLAMA_CPP_CUDA_REBUILD_STATUS="done"
fi

cd ..
echo -e "${GREEN}[OK]${NC} Python зависимости установлены"

# ============================================================
# Шаг 9: Создание структуры директорий
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 9: Создание структуры директорий${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

echo "Создание необходимых директорий..."
mkdir -p backend/models/gguf
mkdir -p backend/models/st
mkdir -p backend/open_webui_uploads
mkdir -p backend/logs
mkdir -p for_cli

# Установка прав доступа
chmod 777 backend/open_webui_uploads
chmod +x scripts/run_all.sh 2>/dev/null || true
chmod +x scripts/start_system_test.sh 2>/dev/null || true

echo -e "${GREEN}[OK]${NC} Структура директорий создана"

# ============================================================
# Шаг 10: Настройка конфигурации
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 10: Настройка конфигурации${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

if [ ! -f "backend/.env" ]; then
    echo "Создание файла .env из шаблона..."
    cp backend/.env.example backend/.env
    echo -e "${GREEN}[OK]${NC} Файл .env создан"
    echo -e "${YELLOW}[!]${NC} CHAINLIT_AUTH_SECRET будет сгенерирован автоматически через bootstrap; вручную нужно проверить пути к моделям и пароли"
else
    echo -e "${YELLOW}[!]${NC} Файл backend/.env уже существует, пропускаю"
fi

# ============================================================
# Шаг 11: Создание скрипта активации окружения
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 11: Создание вспомогательных скриптов${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# Создание скрипта быстрой активации
cat > activate_env.sh << 'EOF'
#!/bin/bash
# Быстрая активация conda base

if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
else
    echo "ERROR: Conda не найдена"
    exit 1
fi

conda activate base

echo "Conda base активирована"
echo "Python: $(python --version)"
echo "Путь: $(which python)"
EOF

chmod +x activate_env.sh
echo -e "${GREEN}[OK]${NC} Создан скрипт activate_env.sh"

# ============================================================
# Проверка установки
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Проверка установки${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

conda activate base

echo "Python версия: $(python --version)"
echo "Pip версия: $(pip --version)"
echo "Conda окружение: $(conda info --envs | grep '*')"

# Проверка ключевых пакетов
echo ""
echo "Проверка установленных пакетов..."
python -c "import fastapi; print('FastAPI:', fastapi.__version__)" || echo -e "${RED}[ERROR]${NC} FastAPI не установлен"
python -c "import langchain; print('LangChain:', langchain.__version__)" || echo -e "${RED}[ERROR]${NC} LangChain не установлен"
python -c "import langgraph; print('LangGraph:', langgraph.__version__)" || echo -e "${RED}[ERROR]${NC} LangGraph не установлен"
python -c "import torch; print('PyTorch:', torch.__version__)" || echo -e "${RED}[ERROR]${NC} PyTorch не установлен"

echo ""
echo -e "${BLUE}GPU / CUDA readiness summary${NC}"
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi: ok"
else
    echo "nvidia-smi: missing"
fi
if [ "$NVIDIA_DRIVER_STATUS" = "ok" ]; then
    echo "NVIDIA driver: ${NVIDIA_DRIVER_VERSION} (ok)"
elif [ "$NVIDIA_DRIVER_STATUS" = "too-old" ]; then
    echo "NVIDIA driver: ${NVIDIA_DRIVER_VERSION:-unknown} (too old, need >= ${CUDA_DRIVER_MIN_VERSION_LINUX})"
else
    echo "NVIDIA driver: missing"
fi
if [ "$CUDA_TOOLKIT_STATUS" = "ok" ]; then
    echo "CUDA toolkit: ${CUDA_TOOLKIT_TARGET}.x (ok)"
elif [ "$CUDA_TOOLKIT_STATUS" = "mismatch" ]; then
    echo "CUDA toolkit: wrong version (expected ${CUDA_TOOLKIT_TARGET}.x)"
elif [ "$CUDA_TOOLKIT_STATUS" = "manual-required" ]; then
    echo "CUDA toolkit: manual install required"
else
    echo "CUDA toolkit: missing"
fi
echo "PyTorch build target: ${PYTORCH_INSTALL_TARGET}"
echo "llama-cpp-python CUDA rebuild: ${LLAMA_CPP_CUDA_REBUILD_STATUS}"

# ============================================================
# Завершение
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}Установка завершена успешно! 🎉${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "${YELLOW}Следующие шаги:${NC}"
echo ""
echo "1. ${BLUE}Скачайте модели или проверьте их наличие:${NC}"
echo "   ./scripts/models/install_models.sh --dry-run"
echo "   ./scripts/models/install_models.sh --ensure-present"
echo ""
echo "2. ${BLUE}Настройте конфигурацию:${NC}"
echo "   nano backend/.env"
echo "   # Укажите пути к моделям и при необходимости admin/grafana пароли"
echo "   ./scripts/bootstrap_env.sh --check --target=native"
echo "   # Если CHAINLIT_AUTH_SECRET пустой или дефолтный, bootstrap сам его сгенерирует/обновит"
echo ""
echo "3. ${BLUE}Активируйте окружение:${NC}"
echo "   source activate_env.sh"
echo "   # или"
echo "   conda activate base"
echo ""
echo "4. ${BLUE}Запустите систему:${NC}"
echo "   ./scripts/launcher.sh --target native"
echo ""
echo "5. ${BLUE}При необходимости используйте compatibility / legacy scripts:${NC}"
echo "   ./scripts/run_native.sh"
echo "   ./scripts/run_all.sh"
echo "   ./scripts/run_openwebui.sh   # legacy path"
echo ""
echo "6. ${BLUE}Откройте браузер:${NC}"
echo "   http://localhost:3000"
echo ""
echo -e "${YELLOW}Документация:${NC}"
echo "   - README.md - Основная документация"
echo "   - INSTALLATION.md - Руководство по установке"
echo ""
echo -e "${YELLOW}Полезные команды:${NC}"
echo "   - tmux attach -t agent-navigator-native  # Подключиться к native сессии"
echo "   - ./scripts/stop_native.sh               # Остановить native runtime"
echo "   - ./scripts/stop_all.sh                  # Остановить compose/container path"
echo ""
if [ "$DOCKER_RELOGIN_REQUIRED" = true ]; then
    echo -e "${YELLOW}[!] Не забудьте выйти и войти заново для применения прав docker!${NC}"
fi
echo ""
