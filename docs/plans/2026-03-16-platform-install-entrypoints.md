# Platform Install Entrypoints Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Свести install-path к одному каноническому входу через `launcher.sh` и добавить platform-specific install scripts для Windows, WSL, Ubuntu и Ubuntu Server без переписывания текущего runtime contour.

**Architecture:** `launcher.sh` остаётся единственным user-facing entrypoint и принимает `--install --platform <...>`. `bootstrap_env.sh` становится dispatcher'ом install-mode и делегирует в platform-specific wrappers. Для Linux-платформ wrappers переиспользуют текущий `setup_ubuntu.sh` или его scaffold-contract, а Windows path остаётся safe guidance/check wrapper без попытки запускать unsupported native runtime.

**Tech Stack:** Bash, PowerShell, existing launcher/bootstrap shell scripts, repo docs under `README.md` and `docs/scripts/`.

---

### Task 1: Зафиксировать platform matrix и канонический install path

**Files:**
- Modify: `README.md`
- Modify: `docs/scripts/README.md`
- Modify: `docs/scripts/installers.md`
- Create: `docs/plans/2026-03-16-platform-install-entrypoints.md`

**Step 1: Зафиксировать канонический user-facing контракт**

Новый контракт:

- `./scripts/launcher.sh --install --platform ubuntu`
- `./scripts/launcher.sh --install --platform ubuntu-server`
- `./scripts/launcher.sh --install --platform wsl`
- `powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 -Mode Guide`

**Step 2: Зафиксировать поддерживаемые semantics**

- `launcher.sh` — единственный canonical entrypoint.
- `bootstrap_env.sh --install` больше не жёстко привязан только к `setup_ubuntu.sh`.
- `windows` path — guide/check wrapper, не обещает full native runtime.
- `wsl` path — wrapper вокруг Linux install path с WSL-specific checks.
- `ubuntu` и `ubuntu-server` используют существующий Ubuntu bootstrap path, но через разные wrappers и docs.

**Step 3: Проверка**

Run: `git diff --check`
Expected: no whitespace issues after docs/code updates.

### Task 2: Реализовать единый install dispatcher в launcher/bootstrap

**Files:**
- Modify: `scripts/launcher.sh`
- Modify: `scripts/bootstrap_env.sh`
- Test: `backend/tests/test_install_entrypoints.py`

**Step 1: Расширить launcher аргументом `--platform`**

Добавить разбор:

- `--platform windows|wsl|ubuntu|ubuntu-server|auto`

Для обычного runtime path этот аргумент не обязателен; используется только в `--install`.

**Step 2: Прокинуть platform в bootstrap install mode**

`launcher.sh --install --platform ...` должен звать:

```bash
bash "$SCRIPT_DIR/bootstrap_env.sh" --install "--target=$TARGET" "--platform=$PLATFORM"
```

**Step 3: Реализовать dispatcher в bootstrap_env.sh**

В `install` режиме:

- `platform=auto`
  - Linux + WSL => `wsl`
  - Linux + Ubuntu Server => `ubuntu-server`
  - Linux + Ubuntu Desktop/unknown Ubuntu => `ubuntu`
  - Windows shell напрямую не поддерживается этим bash path
- `platform=ubuntu` => `scripts/install/install_ubuntu.sh`
- `platform=ubuntu-server` => `scripts/install/install_ubuntu_server.sh`
- `platform=wsl` => `scripts/install/install_wsl.sh`
- unsupported platform => error с ясной подсказкой

**Step 4: Написать targeted tests**

В `backend/tests/test_install_entrypoints.py` покрыть:

- `launcher.sh` принимает `--install --platform=ubuntu`
- `bootstrap_env.sh --install --platform=ubuntu` диспатчит в ожидаемый wrapper
- `platform=auto` на Linux/WSL выбирает корректный wrapper
- unsupported platform даёт понятную ошибку

**Step 5: Проверка**

Run:
- `pytest backend/tests/test_install_entrypoints.py -q`
- `bash -n scripts/launcher.sh scripts/bootstrap_env.sh`

Expected: PASS.

### Task 3: Добавить platform-specific install wrappers

**Files:**
- Create: `scripts/install/install_ubuntu.sh`
- Create: `scripts/install/install_ubuntu_server.sh`
- Create: `scripts/install/install_wsl.sh`
- Create: `scripts/install/install_windows.ps1`
- Possibly modify: `scripts/setup_ubuntu.sh`
- Test: `backend/tests/test_install_entrypoints.py`

**Step 1: Ubuntu wrapper**

`install_ubuntu.sh`:

- поддерживает `--help`, `--dry-run`
- при реальном запуске делегирует в `scripts/setup_ubuntu.sh`
- печатает, что canonical runtime path после установки: `./scripts/launcher.sh --target native`

**Step 2: Ubuntu Server wrapper**

`install_ubuntu_server.sh`:

- поддерживает `--help`, `--dry-run`
- валидирует, что среда Linux/Ubuntu-like
- делегирует в `setup_ubuntu.sh`
- печатает server-oriented note:
  - после установки использовать `launcher.sh`
  - `run_openwebui.sh` не рекомендован

**Step 3: WSL wrapper**

`install_wsl.sh`:

- поддерживает `--help`, `--dry-run`
- проверяет наличие `WSL_DISTRO_NAME` или `/proc/version` with `microsoft`
- печатает WSL-specific prerequisites:
  - запуск внутри Ubuntu WSL distro
  - Docker Desktop/WSL integration optional for container path
- при реальном запуске делегирует в `install_ubuntu.sh`

**Step 4: Windows PowerShell wrapper**

`install_windows.ps1`:

- поддерживает режимы `Guide`, `Check`, `Help`
- проверяет наличие:
  - `wsl.exe`
  - `docker`
  - `git`
- не пытается запускать Linux runtime напрямую
- печатает канонический flow:
  1. установить WSL/Ubuntu
  2. перейти в WSL
  3. запустить `./scripts/launcher.sh --install --platform wsl`

**Step 5: Проверка**

Run:
- `bash -n scripts/install/install_ubuntu.sh scripts/install/install_ubuntu_server.sh scripts/install/install_wsl.sh`
- `pwsh -NoProfile -File scripts/install/install_windows.ps1 -Mode Help` if `pwsh` is available, otherwise syntax review only

Expected: wrappers are syntactically valid and non-destructive by default.

### Task 4: Обновить script docs и README под platform matrix

**Files:**
- Modify: `README.md`
- Modify: `docs/scripts/README.md`
- Modify: `docs/scripts/installers.md`

**Step 1: README**

Добавить явную install matrix:

- Windows
- WSL
- Ubuntu Desktop
- Ubuntu Server

И подчеркнуть:

- canonical entrypoint: `launcher.sh`
- Windows native runtime не является supported primary path
- Windows script — guidance/check wrapper toward WSL

**Step 2: Script manual**

В `docs/scripts/README.md` добавить:

- platform install quick-start table
- примеры команд для каждого platform path
- различие `installer wrappers` vs `runtime launchers`

**Step 3: Installers doc**

Обновить `docs/scripts/installers.md`:

- зафиксировать уже созданные platform wrappers
- привязать их к official docs
- явно описать, какой path safe, какой guide-only

**Step 4: Проверка**

Run:
- manual read-through
- `git diff --check`

Expected: docs consistent with scripts.

### Task 5: Прогнать живой smoke главного скрипта

**Files:**
- Verify: `scripts/launcher.sh`
- Verify: `scripts/bootstrap_env.sh`
- Verify: `scripts/install/install_ubuntu.sh`
- Verify: `scripts/install/install_ubuntu_server.sh`
- Verify: `scripts/install/install_wsl.sh`

**Step 1: Non-destructive smoke**

Run:
- `./scripts/launcher.sh --install --platform ubuntu --report-only` is invalid by design; install path should ignore report-only and go to bootstrap install dispatcher
- `AGENT_NAVIGATOR_TEST_MODE=1 ./scripts/launcher.sh --install --platform ubuntu`
- `AGENT_NAVIGATOR_TEST_MODE=1 ./scripts/launcher.sh --install --platform ubuntu-server`
- `AGENT_NAVIGATOR_TEST_MODE=1 ./scripts/launcher.sh --install --platform wsl`

Expected: dispatcher path resolves without runtime launch.

**Step 2: Wrapper smoke**

Run:
- `bash scripts/install/install_ubuntu.sh --help`
- `bash scripts/install/install_ubuntu_server.sh --help`
- `bash scripts/install/install_wsl.sh --help`

Expected: help text is clear and points back to canonical `launcher.sh`.

**Step 3: Final verification**

Run:
- `pytest backend/tests/test_install_entrypoints.py -q`
- `bash -n scripts/launcher.sh scripts/bootstrap_env.sh scripts/install/install_ubuntu.sh scripts/install/install_ubuntu_server.sh scripts/install/install_wsl.sh`
- `git diff --check`

## Notes

- Не затрагивать незавершённый `T3.17` container backend compose slice.
- Не уводить проект в Windows-native runtime support; Windows остаётся entry guidance to WSL.
- `setup_ubuntu.sh` не переписывать полностью; только обернуть и переиспользовать.
