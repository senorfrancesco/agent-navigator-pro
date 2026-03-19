# B3.37 — System Behavior Sweep

**Goal:** добавить repeatable system-level harness для проверки поведения модели и control-plane через API и Chainlit UI.

**Summary**

- Новый канонический harness: `backend/evals/system_behavior_sweep.py`
- Две поверхности:
  - `api` через `/execute_orchestration`
  - `ui` через thin Playwright CLI runner
- Одна общая scorecard-модель:
  - route allowed/forbidden
  - citations required/min count
  - missing-context request
  - refusal without evidence
  - no external-access claims
  - no fake repo-facts
  - pending action expectation

**Dataset**

- Канонический dataset:
  - `backend/evals/data/system_behavior_scenarios.yaml`
- Stable fixture:
  - `backend/evals/data/fixtures/murka_note.txt`

**Scope**

- full control-plane sweep helper:
  - `assistant_mode`
  - `runtime_mode`
  - `rag_scope`
  - `model_profile`
  - `prompt_profile`
- semantic scenario sweep:
  - `general_chat`
  - `coding`
  - `agentic`
  - `specific_tasks`
  - `rag_qa`

**Verification**

- `pytest backend/tests/test_system_behavior_sweep.py -q`
- live API run:
  - `python backend/evals/system_behavior_sweep.py --surface api --json-output /tmp/system_behavior_api_report.json`
- live UI run:
  - `python backend/evals/system_behavior_sweep.py --surface ui --chainlit-user ... --chainlit-password ...`

**Pragmatic note**

- UI path пока intentionally thin: внешний Playwright session/CLI остаётся transport layer.
- Если `playwright-cli` недоступен в окружении, UI runner не считается закрытым инфраструктурно и должен быть заменён на repo-owned wrapper отдельным follow-up.
