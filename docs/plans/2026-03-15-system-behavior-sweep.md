# System Behavior Sweep

**Goal:** собрать repeatable harness, который прогоняет поведение системы через `/execute_orchestration` и `Chainlit` UI при смене control-plane параметров и generation overrides.

**Architecture:** общий `scorecard/report` contract живёт в `backend/evals/system_behavior_sweep.py`. API и UI используют один и тот же dataset сценариев и возвращают единый JSON-отчёт, чтобы сравнивать route, groundedness, citations и pending-action semantics без ad hoc ручных заметок.

**Current implementation slice:**
- canonical scenario dataset: `backend/evals/data/system_behavior_scenarios.yaml`
- shared scorecard evaluator and report builder
- full control-plane matrix builder
- API runner for `/execute_orchestration`
- thin `playwright-cli` UI adapter with fail-fast dependency check

**Verification:**
- `pytest backend/tests/test_system_behavior_sweep.py -q`
- live API sweep on native stack:
  - `python backend/evals/system_behavior_sweep.py --surface api --json-output /tmp/system-behavior-api.json`
  - current result: `5 passed / 0 failed / 0 error`
- focused regression after fixing live findings:
  - `pytest backend/tests/test_system_behavior_sweep.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q`
  - repeated live API sweep confirms:
    - `general_chat-basic` returns a clean Russian short answer
    - `rag-qa-grounded` returns `5 [1]` for `murka_note.txt`
- live local run requires native stack and local browser automation dependency:
  - `python backend/evals/system_behavior_sweep.py --surface api`
  - `python backend/evals/system_behavior_sweep.py --surface both`

**Follow-up:**
- расширить dataset на harder prompts и explicit generation sweeps per scenario;
- укрепить `PlaywrightCliRunner` под реальный `Chainlit` DOM after repeated live runs;
- добавить artifact export для screenshots / raw page text when UI scorecard fails.
- modal drift between starter flow and settings-only `assistant_mode` change fixed in `chainlit_app.py` via UI-local delta-aware preset application.
