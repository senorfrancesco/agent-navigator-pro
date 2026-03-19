"""Shared pytest bootstrap for backend test imports.

Keeps single-file and full-suite runs consistent by ensuring the backend
package root is on ``sys.path`` regardless of invocation order.
"""

from __future__ import annotations

import os
import sys

import pytest


BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)


@pytest.fixture(autouse=True)
def isolate_orchestration_state_store(monkeypatch, tmp_path):
    from orchestrator import state_store as state_store_module

    db_url = f"sqlite:///{tmp_path / 'orchestrator_state.db'}"
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB_URL", db_url)
    monkeypatch.setattr(state_store_module, "DEFAULT_STATE_DB_URL", db_url, raising=False)
    monkeypatch.setattr(state_store_module, "_STORE_SINGLETON", None, raising=False)
    yield
    monkeypatch.setattr(state_store_module, "_STORE_SINGLETON", None, raising=False)


@pytest.fixture(autouse=True)
def isolate_knowledge_base_store(monkeypatch, tmp_path):
    from orchestrator import knowledge_base_store as kb_store_module

    db_url = f"sqlite:///{tmp_path / 'orchestrator_kb.db'}"
    monkeypatch.setenv("ORCHESTRATOR_KB_DB_URL", db_url)
    monkeypatch.setattr(kb_store_module, "DEFAULT_KB_DB_URL", db_url, raising=False)
    monkeypatch.setattr(kb_store_module, "_STORE_SINGLETON", None, raising=False)
    yield
    monkeypatch.setattr(kb_store_module, "_STORE_SINGLETON", None, raising=False)


@pytest.fixture(autouse=True)
def isolate_ums_runtime_state(monkeypatch):
    from services.model_manager import unified_model_server as ums_server

    original_state = {
        "active_model": ums_server.state.get("active_model"),
        "processes": dict(ums_server.state.get("processes") or {}),
        "placements": dict(ums_server.state.get("placements") or {}),
        "admission": dict(ums_server.state.get("admission") or {}),
        "device_mode": ums_server.state.get("device_mode"),
        "runtime_budget": dict(ums_server.state.get("runtime_budget") or {}),
        "dynamic_ports": ums_server.state.get("dynamic_ports"),
        "system_profile": ums_server.state.get("system_profile"),
        "tier_config": ums_server.state.get("tier_config"),
    }
    original_locks = dict(getattr(ums_server, "_model_start_locks", {}))
    ums_server.state["active_model"] = None
    ums_server.state["processes"] = {}
    ums_server.state["placements"] = {}
    ums_server.state["admission"] = {}
    ums_server.state["runtime_budget"] = {}
    ums_server.state["system_profile"] = None
    ums_server.state["tier_config"] = None
    monkeypatch.setattr(ums_server, "_model_start_locks", {}, raising=False)
    yield
    ums_server.state.update(original_state)
    monkeypatch.setattr(ums_server, "_model_start_locks", original_locks, raising=False)
