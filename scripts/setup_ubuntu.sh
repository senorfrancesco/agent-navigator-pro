#!/bin/bash
# ============================================================
# Полный скрипт установки Agent Navigator Pro для Ubuntu
# Устанавливает: системные зависимости, tmux, Anaconda,
# Python окружение и все библиотеки
# ============================================================

set -e  # Выход при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Agent Navigator Pro - Полная установка для Ubuntu${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "${YELLOW}После установки canonical runtime entrypoint: ./scripts/launcher.sh${NC}"
echo ""

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

# Установка конфигурации tmux (Oh My Tmux) из репозитория
echo ""
echo "Установка конфигурации tmux..."
mkdir -p "$HOME/.config/tmux"

if [ -f "$PROJECT_ROOT/config/tmux/tmux.conf" ]; then
    cp "$PROJECT_ROOT/config/tmux/tmux.conf" "$HOME/.config/tmux/tmux.conf"
    cp "$PROJECT_ROOT/config/tmux/tmux.conf.local" "$HOME/.config/tmux/tmux.conf.local"
    echo -e "${GREEN}[OK]${NC} Конфигурация tmux установлена (~/.config/tmux/)"
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
    gfortran

echo -e "${GREEN}[OK]${NC} Библиотеки для Python установлены"

# ============================================================
# Шаг 4: Установка Docker (для Open WebUI)
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 4: Установка Docker${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

if command -v docker &> /dev/null; then
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

    # Проверка CUDA
    if command -v nvcc &> /dev/null; then
        CUDA_VERSION=$(nvcc --version | grep "release" | awk '{print $5}' | cut -d',' -f1)
        echo -e "${GREEN}[OK]${NC} CUDA установлена: $CUDA_VERSION"
    else
        echo -e "${YELLOW}[WARNING]${NC} CUDA toolkit не обнаружен"
        read -p "Установить CUDA toolkit? (y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "Установка CUDA toolkit..."
            wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
            sudo dpkg -i cuda-keyring_1.1-1_all.deb
            sudo apt-get update
            sudo apt-get -y install cuda-toolkit-12-4
            rm cuda-keyring_1.1-1_all.deb
            echo -e "${GREEN}[OK]${NC} CUDA toolkit установлен"
            echo -e "${YELLOW}[!]${NC} Добавьте в ~/.bashrc:"
            echo 'export PATH=/usr/local/cuda/bin:$PATH'
            echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH'
        fi
    fi
else
    echo -e "${YELLOW}[WARNING]${NC} NVIDIA драйверы не обнаружены"
    echo "Для работы с GPU необходимо установить драйверы NVIDIA"
    echo "Инструкция: https://ubuntu.com/server/docs/nvidia-drivers-installation"
    read -p "Продолжить без GPU поддержки? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
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

# ============================================================
# Шаг 7: Создание conda окружения
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 7: Создание conda окружения diploma_llm${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# Проверка существования окружения
if conda env list | grep -q "diploma_llm"; then
    echo -e "${YELLOW}[!]${NC} Окружение diploma_llm уже существует"
    read -p "Удалить и пересоздать? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Удаление существующего окружения..."
        conda env remove -n diploma_llm -y
    else
        SKIP_CREATE=true
    fi
fi

if [ "$SKIP_CREATE" != true ]; then
    echo "Создание окружения с Python 3.11..."
    conda create -n diploma_llm python=3.11 -y
    echo -e "${GREEN}[OK]${NC} Окружение создано"
fi

# ============================================================
# Шаг 8: Установка Python зависимостей
# ============================================================

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}Шаг 8: Установка Python зависимостей${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# Активация окружения
conda activate diploma_llm

# Обновление pip
echo "Обновление pip..."
python -m pip install --upgrade pip setuptools wheel

# Установка зависимостей
echo ""
echo "Установка зависимостей из requirements.txt..."
cd backend

# Установка с детальным выводом
pip install -r requirements.txt --no-cache-dir

# Специальная установка llama-cpp-python с поддержкой CUDA (если доступна)
if command -v nvcc &> /dev/null; then
    echo ""
    echo "Переустановка llama-cpp-python с поддержкой CUDA..."
    CMAKE_ARGS="-DLLAMA_CUBLAS=on" pip install llama-cpp-python[server] --force-reinstall --no-cache-dir
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
chmod +x backend/run_all.sh
chmod +x start_system_test.sh 2>/dev/null || true

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
    echo -e "${YELLOW}[!]${NC} Отредактируйте backend/.env и укажите пути к моделям"
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
# Быстрая активация окружения diploma_llm

if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
else
    echo "ERROR: Conda не найдена"
    exit 1
fi

conda activate diploma_llm

echo "Окружение diploma_llm активировано"
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

conda activate diploma_llm

echo "Python версия: $(python --version)"
echo "Pip версия: $(pip --version)"
echo "Conda окружение: $(conda info --envs | grep '*')"

# Проверка ключевых пакетов
echo ""
echo "Проверка установленных пакетов..."
python -c "import fastapi; print('FastAPI:', fastapi.__version__)" || echo -e "${RED}[ERROR]${NC} FastAPI не установлен"
python -c "import langchain; print('LangChain:', langchain.__version__)" || echo -e "${RED}[ERROR]${NC} LangChain не установлен"
python -c "import langgraph; print('LangGraph:', langgraph.__version__)" || echo -e "${RED}[ERROR]${NC} LangGraph не установлен"

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
echo "1. ${BLUE}Скачайте модели:${NC}"
echo "   - GGUF модели (Qwen) → backend/models/gguf/"
echo "   - SentenceTransformers → backend/models/st/"
echo ""
echo "2. ${BLUE}Настройте конфигурацию:${NC}"
echo "   nano backend/.env"
echo "   # Укажите пути к моделям"
echo ""
echo "3. ${BLUE}Активируйте окружение:${NC}"
echo "   source activate_env.sh"
echo "   # или"
echo "   conda activate diploma_llm"
echo ""
echo "4. ${BLUE}Запустите систему:${NC}"
echo "   cd backend"
echo "   ./run_all.sh"
echo ""
echo "5. ${BLUE}Запустите Open WebUI:${NC}"
echo "   docker run -d -p 3000:8080 \\"
echo "     --add-host=host.docker.internal:host-gateway \\"
echo "     -v open-webui:/app/backend/data \\"
echo "     -v \$(pwd)/backend/open_webui_uploads:/app/backend/data/uploads \\"
echo "     --name open-webui --restart always \\"
echo "     ghcr.io/open-webui/open-webui:main"
echo ""
echo "6. ${BLUE}Откройте браузер:${NC}"
echo "   http://localhost:3000"
echo ""
echo -e "${YELLOW}Документация:${NC}"
echo "   - README.md - Основная документация"
echo "   - INSTALLATION.md - Руководство по установке"
echo "   - CLAUDE_MEMORY.md - Справочник для Claude AI"
echo ""
echo -e "${YELLOW}Полезные команды:${NC}"
echo "   - tmux attach -t agent-navigator  # Подключиться к сессии"
echo "   - tmux kill-session -t agent-navigator  # Остановить все сервисы"
echo "   - docker logs open-webui  # Логи Open WebUI"
echo ""
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}[!] Не забудьте выйти и войти заново для применения прав docker!${NC}"
fi
echo ""
