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
