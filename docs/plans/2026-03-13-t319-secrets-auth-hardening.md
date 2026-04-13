# T3.19 Production Secrets & Auth Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Добавить minimal production-safe secret hardening для launcher/bootstrap/runtime docs, чтобы система не поощряла запуск с дефолтными credentials и явно валидировала критичные auth secrets.

**Architecture:** Hardening остаётся ops-layer feature: bootstrap/launcher проверяют критичные env values, docs описывают required overrides, runtime output больше не публикует insecure defaults как canonical login hint. Chainlit/UMS/Grafana contract не переписывается.

**Tech Stack:** Bash launcher/bootstrap scripts, env validation, pytest script tests, docs/TASKS sync.

---

## Implemented

- `scripts/bootstrap_env.sh` теперь читает `backend/.env` и валидирует critical secrets.
- known insecure defaults блокируются fail-fast, если не задан `LLM_TOOLS_PLATFORM_ALLOW_INSECURE_DEFAULTS=1`.
- `scripts/run_all.sh` больше не показывает пароль в stdout.
- `backend/tests/test_runtime_launcher.py` покрывает bootstrap security guard и отсутствие password hint в launcher output.
- `backend/.env.example`, `README.md`, `docs/deploy-guide.md`, `TASKS.md` синхронизированы под required secret rotation.

## Verification

- `pytest backend/tests/test_runtime_launcher.py -q -k 'bootstrap or insecure or launcher'`
- `bash -n scripts/bootstrap_env.sh scripts/launcher.sh scripts/run_all.sh`
- `git diff --check`
