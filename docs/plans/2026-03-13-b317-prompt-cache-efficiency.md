## B3.17 — Проверить prompt-cache эффективность

**Status:** Implemented on 2026-03-13

### Goal

Сделать prompt-cache в `llama-server` явной backend policy, а не неявным runtime-допущением:

1. backend должен явно прокидывать `cache_prompt` в heavy text inference path;
2. оператор должен видеть effective prompt-cache policy в `/status`;
3. тестовая поверхность должна подтверждать, что policy реально уходит в upstream payload и не затрагивает `vllm`/embedding path.

### Context

Сейчас `UMS` уже управляет:

- backend mode (`llama-server` / `vllm`);
- runtime budgets;
- concurrency policy;
- placement metadata.

Но prompt-cache semantics для `llama-server` нигде не зафиксированы:

- нет env-driven policy;
- нет `/status` visibility;
- нет тестов, что `cache_prompt` реально включён или выключен.

Итог: даже если upstream runtime умеет reuse prompt-prefix, текущий backend contract не позволяет ни управлять этим, ни проверять это честно.

### Scope

В этой фазе:

1. Добавить backend helper `resolve_prompt_cache_policy()` в `UMS`.
2. Для local `llama-server` heavy text inference path явно добавлять `cache_prompt` в payload.
3. Для `vllm` path не добавлять `cache_prompt`.
4. Публиковать prompt-cache policy в `/status`.
5. Добавить targeted tests.

### Non-goals

- реальный performance benchmark prompt-cache hit-rate;
- slot pinning / `id_slot` orchestration;
- cache telemetry from upstream server internals;
- cache policy для embeddings;
- пер-request adaptive switching prompt-cache based on prompt shape.

### Implementation steps

#### Step 1. Policy resolution

В `backend/services/model_manager/unified_model_server.py`:

- добавить env-driven helper:
  - `UMS_LLAMA_CACHE_PROMPT`
- default:
  - `true` для `llama-server`
  - `false` / ignored для `vllm`

Нужно вернуть snapshot вида:

```json
{
  "enabled": true,
  "backend_mode": "llama-server"
}
```

#### Step 2. Payload wiring

В `UMS` infer path:

- если backend path локальный `llama-server` и тип модели `gguf` / `gguf-vl`,
  добавлять `cache_prompt=true|false` в payload;
- если backend `vllm`, не добавлять этот флаг.

#### Step 3. Status visibility

`GET /status` должен публиковать:

- `prompt_cache_policy.enabled`
- `prompt_cache_policy.backend_mode`

Чтобы launcher/operator/UI могли видеть effective state без чтения env напрямую.

#### Step 4. Tests

Добавить в `backend/tests/test_unified_model_server_startup.py`:

1. non-stream local `llama-server` infer includes `cache_prompt=true` by default
2. env override disables `cache_prompt`
3. `vllm` path does not include `cache_prompt`
4. `/status` exposes prompt-cache policy

### Verification

Targeted:

```bash
pytest backend/tests/test_unified_model_server_startup.py -q -k "cache_prompt or prompt_cache_policy"
```

Broader UMS slice:

```bash
pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q
```

Static:

```bash
python -m py_compile backend/services/model_manager/unified_model_server.py \
  backend/tests/test_unified_model_server_startup.py
git diff --check
```

### Verification status

Passed:

- `pytest backend/tests/test_benchmark_runner.py -q` -> `4 passed`
- `pytest backend/tests/test_unified_model_server_startup.py::test_status_exposes_prompt_cache_policy_for_local_llama -q` -> `1 passed`
- `timeout 20s pytest backend/tests/test_unified_model_server_startup.py::test_non_stream_local_llama_infer_enables_cache_prompt_by_default -vv` -> test passes before reproducing the known old pytest tail-hang on process shutdown
- `timeout 20s pytest backend/tests/test_unified_model_server_startup.py::test_non_stream_infer_proxies_to_vllm_with_auth_headers -vv` -> test passes before reproducing the same known old tail-hang
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py scripts/benchmark.py backend/tests/test_benchmark_runner.py`
- `git diff --check`

### Done when

- local `llama-server` prompt-cache policy явно управляется backend;
- `/status` публикует effective prompt-cache policy;
- `vllm` path не получает `cache_prompt`;
- targeted tests зелёные;
- статус зафиксирован в `TASKS.md`.
