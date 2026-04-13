@echo off
REM ============================================================
REM Полный скрипт установки llm-tools-platform для Windows
REM Устанавливает: Anaconda, Python окружение, все библиотеки
REM ============================================================

echo ============================================================
echo llm-tools-platform - Полная установка для Windows
echo ============================================================
echo.

REM Проверка прав администратора
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] ВНИМАНИЕ: Для установки некоторых компонентов
    echo     рекомендуется запустить скрипт от имени администратора
    echo.
    pause
)

REM ============================================================
REM Шаг 1: Проверка и установка необходимых инструментов
REM ============================================================

echo ============================================================
echo Шаг 1: Проверка системных требований
echo ============================================================
echo.

REM Проверка PowerShell
where powershell >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PowerShell не найден. Установите PowerShell.
    pause
    exit /b 1
)
echo [OK] PowerShell найден

REM Проверка curl
where curl >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] curl не найден. Используется PowerShell для загрузки
    set USE_POWERSHELL=1
) else (
    echo [OK] curl найден
    set USE_POWERSHELL=0
)

REM Проверка Git
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Git не найден. Рекомендуется установить Git
    echo           Скачайте с https://git-scm.com/download/win
    pause
) else (
    echo [OK] Git найден
)

echo.

REM ============================================================
REM Шаг 2: Проверка Docker Desktop
REM ============================================================

echo ============================================================
echo Шаг 2: Проверка Docker Desktop
echo ============================================================
echo.

where docker >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Docker не найден
    echo [!] Для работы Open WebUI требуется Docker Desktop
    echo.
    echo Установите Docker Desktop:
    echo 1. Скачайте с https://www.docker.com/products/docker-desktop
    echo 2. Установите и запустите Docker Desktop
    echo 3. Перезапустите этот скрипт
    echo.
    choice /C YN /M "Продолжить без Docker?"
    if errorlevel 2 exit /b 0
) else (
    echo [OK] Docker найден
    docker --version
)

echo.

REM ============================================================
REM Шаг 3: Проверка NVIDIA GPU и CUDA
REM ============================================================

echo ============================================================
echo Шаг 3: Проверка NVIDIA GPU и CUDA
echo ============================================================
echo.

where nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] NVIDIA драйверы не обнаружены
    echo [!] Для работы с GPU требуются драйверы NVIDIA
    echo.
    echo Установите драйверы NVIDIA:
    echo https://www.nvidia.com/Download/index.aspx
    echo.
    choice /C YN /M "Продолжить без GPU поддержки?"
    if errorlevel 2 exit /b 0
) else (
    echo [OK] NVIDIA драйверы установлены
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
)

echo.

REM ============================================================
REM Шаг 4: Установка Miniconda
REM ============================================================

echo ============================================================
echo Шаг 4: Установка Miniconda
echo ============================================================
echo.

where conda >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Conda уже установлена
    conda --version
    goto :setup_environment
)

echo [!] Conda не найдена. Начинаю установку Miniconda...
echo.

REM Определение архитектуры
if "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    set MINICONDA_URL=https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe
    echo Архитектура: x86_64
) else (
    set MINICONDA_URL=https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86.exe
    echo Архитектура: x86
)

echo.
echo Скачивание Miniconda...

if %USE_POWERSHELL%==1 (
    powershell -Command "& {Invoke-WebRequest -Uri '%MINICONDA_URL%' -OutFile 'miniconda_installer.exe'}"
) else (
    curl -o miniconda_installer.exe %MINICONDA_URL%
)

if %errorlevel% neq 0 (
    echo [ERROR] Не удалось скачать Miniconda
    pause
    exit /b 1
)

echo.
echo Запуск установщика Miniconda...
echo.
echo ВАЖНЫЕ НАСТРОЙКИ ПРИ УСТАНОВКЕ:
echo 1. Выберите "Just Me" (рекомендуется)
echo 2. ОБЯЗАТЕЛЬНО отметьте "Add Miniconda to PATH"
echo 3. Оставьте остальные настройки по умолчанию
echo.
pause

start /wait miniconda_installer.exe /InstallationType=JustMe /RegisterPython=1 /AddToPath=1

REM Удаление установщика
del miniconda_installer.exe

echo.
echo [OK] Miniconda установлена
echo.
echo ВАЖНО: Перезапустите этот скрипт в новом окне командной строки
echo        для продолжения настройки окружения
echo.
pause
exit /b 0

:setup_environment

REM ============================================================
REM Шаг 5: Создание conda окружения
REM ============================================================

echo.
echo ============================================================
echo Шаг 5: Создание conda окружения diploma_llm
echo ============================================================
echo.

REM Проверка существования окружения
call conda env list | findstr /C:"diploma_llm" >nul 2>&1
if %errorlevel% equ 0 (
    echo [!] Окружение diploma_llm уже существует
    choice /C YN /M "Удалить и пересоздать окружение?"
    if errorlevel 2 goto :install_packages
    if errorlevel 1 (
        echo Удаление существующего окружения...
        call conda env remove -n diploma_llm -y
    )
)

echo Создание нового окружения с Python 3.11...
call conda create -n diploma_llm python=3.11 -y
if %errorlevel% neq 0 (
    echo [ERROR] Не удалось создать conda окружение
    pause
    exit /b 1
)

echo [OK] Окружение создано успешно
echo.

:install_packages

REM ============================================================
REM Шаг 6: Установка Python зависимостей
REM ============================================================

echo ============================================================
echo Шаг 6: Установка Python зависимостей
echo ============================================================
echo.

REM Активация окружения
call conda activate diploma_llm
if %errorlevel% neq 0 (
    echo [ERROR] Не удалось активировать окружение
    pause
    exit /b 1
)

echo Обновление pip...
python -m pip install --upgrade pip setuptools wheel

echo.
echo Установка зависимостей из requirements.txt...
echo Это может занять несколько минут...
echo.

cd backend
pip install -r requirements.txt --no-cache-dir
if %errorlevel% neq 0 (
    echo [ERROR] Ошибка при установке зависимостей
    cd ..
    pause
    exit /b 1
)

REM Специальная установка llama-cpp-python с CUDA (если доступна)
where nvcc >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo Переустановка llama-cpp-python с поддержкой CUDA...
    set CMAKE_ARGS=-DLLAMA_CUBLAS=on
    pip install llama-cpp-python[server] --force-reinstall --no-cache-dir
)

cd ..
echo.
echo [OK] Все зависимости установлены

REM ============================================================
REM Шаг 7: Создание структуры директорий
REM ============================================================

echo.
echo ============================================================
echo Шаг 7: Создание структуры директорий
echo ============================================================
echo.

echo Создание необходимых директорий...
if not exist "backend\models\gguf" mkdir backend\models\gguf
if not exist "backend\models\st" mkdir backend\models\st
if not exist "backend\open_webui_uploads" mkdir backend\open_webui_uploads
if not exist "backend\logs" mkdir backend\logs
if not exist "for_cli" mkdir for_cli

echo [OK] Директории созданы

REM ============================================================
REM Шаг 8: Настройка конфигурации
REM ============================================================

echo.
echo ============================================================
echo Шаг 8: Настройка конфигурации
echo ============================================================
echo.

if not exist "backend\.env" (
    echo Создание файла конфигурации .env...
    copy backend\.env.example backend\.env
    echo [OK] Файл .env создан
    echo [!] ВАЖНО: Отредактируйте backend\.env и укажите пути к моделям
) else (
    echo [!] Файл backend\.env уже существует, пропускаю
)

REM ============================================================
REM Шаг 9: Создание вспомогательных скриптов
REM ============================================================

echo.
echo ============================================================
echo Шаг 9: Создание вспомогательных скриптов
echo ============================================================
echo.

REM Создание скрипта активации окружения
echo @echo off > activate_env.bat
echo call conda activate diploma_llm >> activate_env.bat
echo echo Окружение diploma_llm активировано >> activate_env.bat
echo python --version >> activate_env.bat

REM Создание скрипта запуска всех сервисов для Windows
echo @echo off > start_all_services.bat
echo call conda activate diploma_llm >> start_all_services.bat
echo echo Запуск всех сервисов llm-tools-platform... >> start_all_services.bat
echo echo. >> start_all_services.bat
echo start "Agent API" cmd /k "cd backend\orchestrator && uvicorn agent_api:app --host 0.0.0.0 --port 8000" >> start_all_services.bat
echo timeout /t 2 /nobreak ^>nul >> start_all_services.bat
echo start "Document Server" cmd /k "cd backend\services\document_server && uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001" >> start_all_services.bat
echo timeout /t 2 /nobreak ^>nul >> start_all_services.bat
echo start "Legal Server" cmd /k "cd backend\services\legal_server && uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002" >> start_all_services.bat
echo timeout /t 2 /nobreak ^>nul >> start_all_services.bat
echo start "Model Server" cmd /k "cd backend\services\model_manager && python unified_model_server.py" >> start_all_services.bat
echo echo. >> start_all_services.bat
echo echo Все сервисы запущены! >> start_all_services.bat
echo echo Откройте http://localhost:8000/health для проверки >> start_all_services.bat

echo [OK] Скрипты созданы:
echo      - activate_env.bat (активация окружения)
echo      - start_all_services.bat (запуск всех сервисов)

REM ============================================================
REM Шаг 10: Проверка установки
REM ============================================================

echo.
echo ============================================================
echo Шаг 10: Проверка установки
echo ============================================================
echo.

call conda activate diploma_llm

echo Python версия:
python --version

echo.
echo Pip версия:
pip --version

echo.
echo Conda окружение:
conda info --envs | findstr "*"

echo.
echo Проверка установленных пакетов...
python -c "import fastapi; print('FastAPI:', fastapi.__version__)" 2>nul || echo [ERROR] FastAPI не установлен
python -c "import langchain; print('LangChain:', langchain.__version__)" 2>nul || echo [ERROR] LangChain не установлен
python -c "import langgraph; print('LangGraph:', langgraph.__version__)" 2>nul || echo [ERROR] LangGraph не установлен

REM ============================================================
REM Завершение
REM ============================================================

echo.
echo ============================================================
echo Установка завершена успешно! 🎉
echo ============================================================
echo.
echo Следующие шаги:
echo.
echo 1. Скачайте необходимые модели:
echo    - GGUF модели (Qwen) -^> backend\models\gguf\
echo    - SentenceTransformers -^> backend\models\st\
echo.
echo 2. Настройте конфигурацию:
echo    notepad backend\.env
echo    # Укажите пути к моделям
echo.
echo 3. Активируйте окружение:
echo    activate_env.bat
echo.
echo 4. Запустите все сервисы:
echo    start_all_services.bat
echo.
echo 5. Запустите Open WebUI в Docker:
echo    docker run -d -p 3000:8080 ^
echo      --add-host=host.docker.internal:host-gateway ^
echo      -v open-webui:/app/backend/data ^
echo      -v %cd%\backend\open_webui_uploads:/app/backend/data/uploads ^
echo      --name open-webui --restart always ^
echo      ghcr.io/open-webui/open-webui:main
echo.
echo 6. Откройте браузер:
echo    http://localhost:3000
echo.
echo Документация:
echo    - README.md - Основная документация
echo    - INSTALLATION.md - Руководство по установке
echo    - CLAUDE_MEMORY.md - Справочник для Claude AI
echo.
echo Полезные команды:
echo    - docker logs open-webui  # Логи Open WebUI
echo    - conda deactivate  # Деактивировать окружение
echo.
pause
