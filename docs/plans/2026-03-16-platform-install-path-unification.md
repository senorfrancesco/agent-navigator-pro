# План: Platform Install Path Unification

## Goal

Свести install path к одному каноническому координатору и одновременно дать явные platform-specific wrappers для:

- Windows
- WSL
- Ubuntu
- Ubuntu Server

## Scope

### In scope

- `launcher.sh --install` как единый user-facing install entrypoint;
- `bootstrap_env.sh --install` как делегат в installer coordinator;
- `scripts/install/install.sh` как dispatcher по платформам;
- platform-specific wrappers:
  - `install_windows.ps1`
  - `install_wsl.sh`
  - `install_ubuntu.sh`
  - `install_ubuntu_server.sh`
- реальная `detect_os.sh` логика для safe platform classification;
- обновление `README.md` и `docs/scripts/*`.

### Out of scope

- полный rewrite `setup_ubuntu.sh`;
- автоматическая установка всего на Windows host без WSL;
- production container rollout;
- runtime/orchestration refactor.

## Design

- Канонический install entrypoint остаётся один:

```bash
./scripts/launcher.sh --install
```

- На bash-capable системах он идёт через:

```text
launcher.sh -> bootstrap_env.sh --install -> scripts/install/install.sh
```

- `install.sh`:
  - принимает `--platform auto|windows|wsl|ubuntu|ubuntu-server`;
  - по `auto` использует `detect_os.sh`;
  - dispatch'ит в platform-specific wrapper;
  - поддерживает `--dry-run`.

- `setup_ubuntu.sh` остаётся heavy installer backend для Ubuntu-like flows:
  - `ubuntu`
  - `ubuntu-server`
  - `wsl`

- Windows path оформляется как отдельный PowerShell bootstrap script, который:
  - не ломает проект;
  - валидирует prerequisites;
  - направляет пользователя в WSL/Docker Desktop path.

## Verification

- `bash -n` для всех новых/изменённых shell scripts;
- targeted pytest на installer dispatcher contract;
- smoke:
  - `./scripts/install/detect_os.sh --print-platform`
  - `AGENT_NAVIGATOR_TEST_MODE=1 ./scripts/install/install.sh --platform auto`
  - `AGENT_NAVIGATOR_TEST_MODE=1 ./scripts/launcher.sh --install --platform ubuntu`
- doc sync review между `README.md`, `docs/scripts/README.md`, `docs/scripts/installers.md`.

## Exit Criteria

- `launcher.sh --install` больше не ведёт напрямую в `setup_ubuntu.sh`;
- platform wrappers существуют для Windows, WSL, Ubuntu, Ubuntu Server;
- docs явно описывают supported install paths;
- installer dispatcher протестирован без реального destructive install.
