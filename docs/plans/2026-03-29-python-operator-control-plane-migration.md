# Python Operator Control Plane Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Полностью перевести operator UI и deploy/runtime orchestration на каноническую Python-логику, оставив shell-скрипты только как compatibility/adaptor layer.

**Architecture:** Web-first operator UI разговаривает только с Python control plane в `backend/orchestrator/`, который владеет state-моделью, allowlisted actions, job lifecycle, log streaming, config apply и deploy/runtime orchestration. Shell-скрипты не исчезают, но перестают быть продуктовым API: они либо вызываются Python-слоем как низкоуровневые host-команды, либо становятся thin wrappers вокруг Python entrypoints.

**Tech Stack:** FastAPI, Python orchestration services, async subprocess/job runner, existing `deploy/offline_bundle/scripts`, existing `scripts/launcher.sh`, static/operator web UI, pytest.

---

### Task 1: Зафиксировать Python control plane как канонический backend для UI

**Files:**
- Modify: `TASKS.md`
- Modify: `backend/orchestrator/agent_api.py`
- Modify: `backend/orchestrator/operator_ui_api.py`
- Create: `backend/tests/test_operator_ui_api.py`

**Step 1: Write the failing test**

```python
def test_operator_ui_state_exposes_python_control_plane_contract():
    state = build_operator_state()
    assert state["delivery"]["control_plane"] == "python"
    assert state["delivery"]["shell_role"] == "compatibility"
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_ui_api.py::test_operator_ui_state_exposes_python_control_plane_contract -v`
Expected: FAIL because `delivery` contract is missing or incomplete.

**Step 3: Write minimal implementation**

- Добавить в operator state явный delivery contract:
  - `control_plane = python`
  - `ui_mode = web-first`
  - `shell_role = compatibility`
  - `actions_backend = python_operator_runner`
- Зафиксировать то же решение в `TASKS.md` как canonical direction.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_ui_api.py::test_operator_ui_state_exposes_python_control_plane_contract -v`
Expected: PASS

**Step 5: Commit**

```bash
git add TASKS.md backend/orchestrator/agent_api.py backend/orchestrator/operator_ui_api.py backend/tests/test_operator_ui_api.py
git commit -m "docs(operator): define python control plane contract"
```

### Task 2: Вынести runtime path discovery и config source resolution в Python service

**Files:**
- Create: `backend/orchestrator/operator_runtime_service.py`
- Modify: `backend/orchestrator/operator_ui_api.py`
- Modify: `backend/orchestrator/operator_ui_actions.py`
- Test: `backend/tests/test_operator_runtime_service.py`

**Step 1: Write the failing test**

```python
def test_runtime_service_discovers_native_and_bundle_paths():
    service = OperatorRuntimeService(repo_root=tmp_path)
    summary = service.get_runtime_paths()
    assert "native" in summary
    assert "bundle" in summary
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_runtime_service.py::test_runtime_service_discovers_native_and_bundle_paths -v`
Expected: FAIL because `OperatorRuntimeService` does not exist.

**Step 3: Write minimal implementation**

- Создать Python service, который владеет:
  - availability checks для `native` и `offline_bundle/container`
  - resolution launch/config sources
  - effective runtime summary
  - hardware-aware suggested defaults
- Перевести `operator_ui_api.py` на использование service вместо ad-hoc helper logic.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_runtime_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/orchestrator/operator_runtime_service.py backend/orchestrator/operator_ui_api.py backend/orchestrator/operator_ui_actions.py backend/tests/test_operator_runtime_service.py
git commit -m "feat(operator): move runtime discovery into python service"
```

### Task 3: Вынести config apply и env merge в Python service

**Files:**
- Create: `backend/orchestrator/operator_config_service.py`
- Modify: `backend/orchestrator/operator_ui_api.py`
- Modify: `backend/orchestrator/operator_ui_actions.py`
- Test: `backend/tests/test_operator_config_service.py`

**Step 1: Write the failing test**

```python
def test_apply_config_updates_selected_env_source(tmp_path):
    service = OperatorConfigService(repo_root=tmp_path)
    result = service.apply_config(
        path_key="native",
        updates={"APP_PORT": "9010"},
    )
    assert result.applied["APP_PORT"] == "9010"
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_config_service.py::test_apply_config_updates_selected_env_source -v`
Expected: FAIL because apply logic still lives outside a canonical Python config service.

**Step 3: Write minimal implementation**

- Создать Python config service, который умеет:
  - читать editable env/config sources по path
  - собирать effective values
  - валидировать updates
  - применять изменения в правильный source file
  - возвращать structured diff `before/after/applied`
- Перевести UI API на explicit `Apply` через этот service.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_config_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/orchestrator/operator_config_service.py backend/orchestrator/operator_ui_api.py backend/orchestrator/operator_ui_actions.py backend/tests/test_operator_config_service.py
git commit -m "feat(operator): add python config apply service"
```

### Task 4: Вынести offline bundle build/import/deploy lifecycle в Python deploy service

**Files:**
- Create: `backend/orchestrator/operator_deploy_service.py`
- Modify: `backend/orchestrator/operator_ui_actions.py`
- Modify: `backend/orchestrator/operator_ui_api.py`
- Test: `backend/tests/test_operator_deploy_service.py`

**Step 1: Write the failing test**

```python
def test_deploy_service_exposes_build_and_import_stage_catalog():
    service = OperatorDeployService(repo_root=tmp_path)
    assert "build_bundle" in service.list_build_stages()
    assert "import_bundle" in service.list_import_stages()
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_deploy_service.py::test_deploy_service_exposes_build_and_import_stage_catalog -v`
Expected: FAIL because deploy lifecycle is not modeled by a dedicated Python service.

**Step 3: Write minimal implementation**

- Создать Python deploy service, который владеет:
  - build bundle stage model
  - archive pack/unpack orchestration
  - validate/import/deploy/verify orchestration
  - mapping на allowlisted low-level commands/scripts
  - artifact metadata и stage status
- Оставить shell-скрипты только как adapters для конкретных host-операций.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_deploy_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/orchestrator/operator_deploy_service.py backend/orchestrator/operator_ui_actions.py backend/orchestrator/operator_ui_api.py backend/tests/test_operator_deploy_service.py
git commit -m "feat(operator): add python deploy lifecycle service"
```

### Task 5: Перевести job lifecycle, stage model и log streaming на Python-first contract

**Files:**
- Create: `backend/orchestrator/operator_jobs.py`
- Modify: `backend/orchestrator/operator_ui_actions.py`
- Modify: `backend/orchestrator/operator_ui_api.py`
- Test: `backend/tests/test_operator_jobs.py`

**Step 1: Write the failing test**

```python
def test_job_store_tracks_stage_transitions_and_logs():
    job_store = OperatorJobStore()
    job_id = job_store.create_job(action_id="deploy.bundle.build")
    job_store.append_log(job_id, "build", "started")
    job_store.set_stage(job_id, "build", "running")
    snapshot = job_store.get(job_id)
    assert snapshot["current_stage"] == "build"
    assert snapshot["logs"][-1]["message"] == "started"
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_jobs.py::test_job_store_tracks_stage_transitions_and_logs -v`
Expected: FAIL because job management still lives as thin in-memory helpers inside action registry.

**Step 3: Write minimal implementation**

- Вынести job store в отдельный Python module
- Зафиксировать structured job schema:
  - `job_id`
  - `action_id`
  - `status`
  - `current_stage`
  - `stages[]`
  - `logs[]`
  - `started_at`
  - `finished_at`
  - `exit_code`
- Перевести action runner и API endpoints на эту модель.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_jobs.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/orchestrator/operator_jobs.py backend/orchestrator/operator_ui_actions.py backend/orchestrator/operator_ui_api.py backend/tests/test_operator_jobs.py
git commit -m "feat(operator): extract python job lifecycle model"
```

### Task 6: Свести shell-скрипты к compatibility wrappers и documented fallback path

**Files:**
- Modify: `scripts/launcher.sh`
- Modify: `deploy/offline_bundle/scripts/run_offline_bundle.sh`
- Modify: `deploy/offline_bundle/scripts/deploy.sh`
- Modify: `docs/plans/2026-03-29-operator-ui-web-runtime-and-deploy-build-plan.md`
- Modify: `TASKS.md`

**Step 1: Write the failing test**

```python
def test_launcher_shell_is_not_marked_as_primary_ui_api():
    text = Path("TASKS.md").read_text()
    assert "scripts/launcher.sh" in text
    assert "compatibility wrapper" in text
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_docs_contract.py::test_launcher_shell_is_not_marked_as_primary_ui_api -v`
Expected: FAIL because documentation/backlog do not yet fully demote shell to compatibility role.

**Step 3: Write minimal implementation**

- Зафиксировать в docs и backlog:
  - shell не является каноническим UI API
  - shell остаётся ручным CLI/recovery path
  - по возможности shell вызывает Python entrypoints, а не держит свою state machine
- Не делать destructive rewrite shell в этом task; только зафиксировать роль и минимальную thin-wrapper direction.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_docs_contract.py::test_launcher_shell_is_not_marked_as_primary_ui_api -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/launcher.sh deploy/offline_bundle/scripts/run_offline_bundle.sh deploy/offline_bundle/scripts/deploy.sh docs/plans/2026-03-29-operator-ui-web-runtime-and-deploy-build-plan.md TASKS.md
git commit -m "docs(operator): demote shell scripts to compatibility layer"
```

### Task 7: Перевести operator frontend на Python-only API adapters

**Files:**
- Modify: `prototype/operator-ui/app.js`
- Modify: `prototype/operator-ui/README.md`
- Modify: `backend/orchestrator/operator_ui_api.py`
- Test: `backend/tests/test_operator_ui_frontend_contract.py`

**Step 1: Write the failing test**

```python
def test_frontend_contract_exposes_actions_jobs_and_config_apply():
    app_contract = get_operator_frontend_contract()
    assert "/operator/actions/run" in app_contract["required_endpoints"]
    assert "/operator/jobs/{job_id}" in app_contract["required_endpoints"]
    assert "/operator/config/{path_key}/apply" in app_contract["required_endpoints"]
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_ui_frontend_contract.py::test_frontend_contract_exposes_actions_jobs_and_config_apply -v`
Expected: FAIL because frontend contract is not yet explicit and fully Python-backed.

**Step 3: Write minimal implementation**

- Сделать frontend adapters зависимыми только от Python API endpoints
- Убрать remaining mock-only assumptions из action handling
- Зафиксировать API contract в README и tests

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_ui_frontend_contract.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add prototype/operator-ui/app.js prototype/operator-ui/README.md backend/orchestrator/operator_ui_api.py backend/tests/test_operator_ui_frontend_contract.py
git commit -m "feat(operator-ui): switch frontend contract to python api"
```

### Task 8: Финальная verification wave и cutover criteria

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-29-python-operator-control-plane-migration.md`
- Test: `backend/tests/test_operator_runtime_service.py`
- Test: `backend/tests/test_operator_config_service.py`
- Test: `backend/tests/test_operator_deploy_service.py`
- Test: `backend/tests/test_operator_jobs.py`
- Test: `backend/tests/test_operator_ui_api.py`

**Step 1: Write the failing test**

```python
def test_python_control_plane_cutover_checklist_is_complete():
    checklist = load_cutover_checklist()
    assert checklist["ui_backend"] == "python"
    assert checklist["runtime_service"] is True
    assert checklist["deploy_service"] is True
    assert checklist["shell_primary_api"] is False
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_operator_cutover.py::test_python_control_plane_cutover_checklist_is_complete -v`
Expected: FAIL until all migration criteria are documented and wired.

**Step 3: Write minimal implementation**

- Зафиксировать cutover criteria:
  - UI uses Python endpoints only
  - runtime/config/deploy/jobs services exist
  - shell scripts are compatibility layer
  - privileged actions are server-side only
  - operator logs/jobs are structured
- Обновить backlog statuses и close conditions.

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_operator_cutover.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add TASKS.md docs/plans/2026-03-29-python-operator-control-plane-migration.md backend/tests/test_operator_cutover.py
git commit -m "docs(operator): add python control plane cutover criteria"
```
