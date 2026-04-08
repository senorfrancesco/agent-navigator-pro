# Operator UI Env Secrets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать operator UI и native launcher устойчивыми к секретам со спецсимволами за счёт безопасного чтения и записи `.env` / `env.bundle`.

**Architecture:** Python control plane становится source of truth для env parsing и env writing: `.env` читается через `python-dotenv`, а operator apply path пишет секреты в quoted-формате вместо сырого `KEY=value`. Shell compatibility scripts больше не `source`-ят `.env` как shell-код, а загружают значения через безопасный Python-based loader и экспортируют уже готовые строки в окружение процесса.

**Tech Stack:** Python, `python-dotenv`, Bash, pytest

---

### Task 1: Зафиксировать регрессию на operator env round-trip

**Files:**
- Modify: `backend/tests/test_operator_config_service.py`
- Modify: `backend/tests/test_runtime_launcher.py`

- [ ] **Step 1: Написать падающий тест на quoted secret round-trip в operator config**

```python
def test_config_service_apply_config_quotes_secret_values_and_preserves_round_trip(...):
    dangerous_value = "pa$$w'rd $(echo hacked) #bang"
    result = config_service.apply_config("native", {"CHAINLIT_ADMIN_PASSWORD": dangerous_value})
    env_text = (tmp_path / "backend" / ".env").read_text(encoding="utf-8")
    assert "CHAINLIT_ADMIN_PASSWORD='" in env_text
    assert parse_env_file(tmp_path / "backend" / ".env")["CHAINLIT_ADMIN_PASSWORD"] == dangerous_value
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd backend && pytest tests/test_operator_config_service.py -q -k quoted_secret`
Expected: FAIL, потому что текущий writer пишет сырой `KEY=value` без кавычек.

- [ ] **Step 3: Написать падающий launcher regression test на секрет со спецсимволами**

```python
def test_launcher_loads_special_character_secrets_without_shell_evaluation(tmp_path):
    set_key(backend_env, "CHAINLIT_ADMIN_PASSWORD", "pa$$w'rd $(echo hacked) #bang", quote_mode="always")
    result = _run_script("launcher.sh", "--target", "native", "--profile", "adaptive", env=env)
    assert result.returncode == 0
```

- [ ] **Step 4: Запустить launcher test и убедиться, что он падает**

Run: `cd backend && pytest tests/test_runtime_launcher.py -q -k special_character_secrets`
Expected: FAIL, потому что `launcher.sh` делает `source "$ENV_FILE"` и интерпретирует строку как shell input.

### Task 2: Перевести Python env contract на безопасный parser/writer

**Files:**
- Modify: `backend/orchestrator/operator_runtime_service.py`
- Modify: `backend/orchestrator/operator_config_service.py`
- Test: `backend/tests/test_operator_config_service.py`

- [ ] **Step 1: Заменить ad-hoc `.env` parser на `python-dotenv`**

```python
from dotenv import dotenv_values

def parse_env_file(path: Path) -> Dict[str, str]:
    values = dotenv_values(path)
    return {
        str(key): "" if value is None else str(value)
        for key, value in values.items()
        if key
    }
```

- [ ] **Step 2: Перевести apply path на quoted env writes**

```python
from dotenv import set_key

if value == "":
    replacement = f"{key}="
else:
    set_key(path, key, value, quote_mode="always", encoding="utf-8")
```

- [ ] **Step 3: Прогнать таргетный pytest**

Run: `cd backend && pytest tests/test_operator_config_service.py -q`
Expected: PASS

### Task 3: Убрать сырой `source` из operator/native shell path

**Files:**
- Modify: `scripts/launcher.sh`
- Modify: `scripts/run_native.sh`
- Modify: `scripts/run_all.sh`
- Test: `backend/tests/test_runtime_launcher.py`

- [ ] **Step 1: Добавить safe env loader для bash-скриптов**

```bash
mapfile -d '' -t env_entries < <("$ENV_LOADER_PYTHON" - "$file" <<'PY'
from dotenv import dotenv_values
...
PY
)
for entry in "${env_entries[@]}"; do
  export "${entry}"
done
```

- [ ] **Step 2: Переключить launcher/native/run_all на новый loader**

```bash
load_env_file "$ENV_FILE" "backend env"
load_env_file "$RUNTIME_ENV_FILE" "runtime overrides"
```

- [ ] **Step 3: Прогнать shell/Python verification**

Run: `bash -n scripts/launcher.sh scripts/run_native.sh scripts/run_all.sh`
Expected: PASS

Run: `cd backend && pytest tests/test_runtime_launcher.py -q -k "special_character_secrets or uses_backend_env_for_runtime_preflight"`
Expected: PASS

### Task 4: Зафиксировать решение и verification

**Files:**
- Modify: `TASKS.md`

- [ ] **Step 1: Добавить progress note в operator UI backlog**

```md
Progress: operator env apply path теперь пишет quoted secrets, Python control plane читает `.env` через `python-dotenv`, а launcher/native path больше не `source`-ят `.env` как shell-код.
Follow-up: stop scripts всё ещё используют legacy env sourcing и требуют отдельного hardening slice.
```

- [ ] **Step 2: Прогнать финальную project-aware verification**

Run: `cd backend && pytest tests/test_operator_config_service.py tests/test_runtime_launcher.py -q`
Expected: PASS

Run: `bash -n scripts/launcher.sh scripts/run_native.sh scripts/run_all.sh`
Expected: PASS

Run: `git diff --check`
Expected: PASS
