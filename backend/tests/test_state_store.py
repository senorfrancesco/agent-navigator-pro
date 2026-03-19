import os
import sys
from types import SimpleNamespace

import pytest

from orchestrator import state_store as state_store_module
from orchestrator.state_store import (
    SQLiteOrchestrationStateStore,
    StateStoreConfigurationError,
)


@pytest.fixture
def state_store(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'orchestrator_state.db'}"
    return SQLiteOrchestrationStateStore(db_url)


@pytest.mark.asyncio
async def test_get_or_create_run_reuses_same_thread(state_store):
    first = await state_store.get_or_create_run(
        thread_id="thread-1",
        session_id="session-a",
        workflow_type="chainlit",
    )
    second = await state_store.get_or_create_run(
        thread_id="thread-1",
        session_id="session-b",
        workflow_type="chainlit",
    )

    assert first.run_id == second.run_id
    assert first.state_ref == second.state_ref
    assert first.version == 1


@pytest.mark.asyncio
async def test_get_or_create_run_reuses_same_idempotency_key(state_store):
    first = await state_store.get_or_create_run(
        thread_id="thread-idem-a",
        session_id="session-idem-a",
        workflow_type="chainlit",
        idempotency_key="same-request",
    )
    second = await state_store.get_or_create_run(
        thread_id="thread-idem-b",
        session_id="session-idem-b",
        workflow_type="chainlit",
        idempotency_key="same-request",
    )

    assert first.run_id == second.run_id
    assert second.idempotency_key == "same-request"


@pytest.mark.asyncio
async def test_get_or_create_run_different_idempotency_key_creates_new_run(state_store):
    first = await state_store.get_or_create_run(
        thread_id="thread-x",
        session_id="session-x",
        workflow_type="chainlit",
        idempotency_key="request-a",
    )
    second = await state_store.get_or_create_run(
        thread_id="thread-y",
        session_id="session-y",
        workflow_type="chainlit",
        idempotency_key="request-b",
    )

    assert first.run_id != second.run_id


@pytest.mark.asyncio
async def test_load_run_by_session_fallback(state_store):
    created = await state_store.get_or_create_run(
        thread_id=None,
        session_id="session-only",
        workflow_type="api",
    )

    loaded = await state_store.load_run(session_id="session-only")

    assert loaded is not None
    assert loaded.run_id == created.run_id
    assert loaded.state_ref == f"run:{created.run_id}"


@pytest.mark.asyncio
async def test_load_run_by_state_ref(state_store):
    created = await state_store.get_or_create_run(
        thread_id="thread-state-ref",
        session_id="session-state-ref",
        workflow_type="chainlit",
    )

    loaded = await state_store.load_run(state_ref=created.state_ref)

    assert loaded is not None
    assert loaded.run_id == created.run_id
    assert loaded.state_ref == created.state_ref


@pytest.mark.asyncio
async def test_save_run_persists_snapshot_and_increments_version(state_store):
    created = await state_store.get_or_create_run(
        thread_id="thread-2",
        session_id="session-2",
        workflow_type="chainlit",
    )

    updated = await state_store.save_run(
        run_id=created.run_id,
        status="waiting_action",
        pending_action_id="choice-1",
        resume_state_blob={"active_doc_ids": ["doc-1"], "active_mode": "doc_qa"},
        checkpoint_blob={"route": "document_question", "trace_id": "t-1"},
        expected_version=created.version,
    )

    assert updated.version == created.version + 1
    assert updated.status == "waiting_action"
    assert updated.pending_action_id == "choice-1"
    assert updated.resume_state_blob == {"active_doc_ids": ["doc-1"], "active_mode": "doc_qa"}
    assert updated.checkpoint_blob == {"route": "document_question", "trace_id": "t-1"}


@pytest.mark.asyncio
async def test_save_run_can_clear_pending_action_without_dropping_checkpoint(state_store):
    created = await state_store.get_or_create_run(
        thread_id="thread-clear",
        session_id="session-clear",
        workflow_type="chainlit",
    )
    waiting = await state_store.save_run(
        run_id=created.run_id,
        status="waiting_action",
        pending_action_id="choice-2",
        resume_state_blob={"pending_action": {"type": "choose_route"}},
        checkpoint_blob={"route": "compare_documents"},
        last_error="temporary error",
        expected_version=created.version,
    )

    cleared = await state_store.save_run(
        run_id=created.run_id,
        status="completed",
        pending_action_id=None,
        resume_state_blob={"pending_action": None},
        checkpoint_blob=waiting.checkpoint_blob,
        last_error=None,
        expected_version=waiting.version,
    )

    assert cleared.pending_action_id is None
    assert cleared.checkpoint_blob == {"route": "compare_documents"}
    assert cleared.last_error is None
    assert cleared.version == waiting.version + 1


@pytest.mark.asyncio
async def test_save_run_rejects_version_mismatch(state_store):
    created = await state_store.get_or_create_run(
        thread_id="thread-3",
        session_id="session-3",
        workflow_type="chainlit",
    )
    await state_store.save_run(
        run_id=created.run_id,
        status="completed",
        pending_action_id=None,
        resume_state_blob={"ok": True},
        checkpoint_blob={"route": "general_chat"},
        expected_version=created.version,
    )

    with pytest.raises(RuntimeError, match="Version mismatch"):
        await state_store.save_run(
            run_id=created.run_id,
            status="completed",
            pending_action_id=None,
            resume_state_blob={"ok": True},
            checkpoint_blob={"route": "general_chat"},
            expected_version=created.version,
        )


@pytest.mark.asyncio
async def test_save_run_without_expected_version_is_monotonic(state_store):
    created = await state_store.get_or_create_run(
        thread_id="thread-no-expected",
        session_id="session-no-expected",
        workflow_type="chainlit",
    )

    first = await state_store.save_run(
        run_id=created.run_id,
        status="completed",
        pending_action_id=None,
        resume_state_blob={"step": 1},
        checkpoint_blob={"route": "general_chat"},
    )
    second = await state_store.save_run(
        run_id=created.run_id,
        status="completed",
        pending_action_id=None,
        resume_state_blob={"step": 2},
        checkpoint_blob={"route": "general_chat"},
    )

    assert first.version == created.version + 1
    assert second.version == first.version + 1
    assert second.updated_at >= first.updated_at


def test_get_orchestration_state_store_rejects_unsupported_url(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB_URL", "memory://state")
    monkeypatch.setattr(state_store_module, "_STORE_SINGLETON", None, raising=False)

    with pytest.raises(StateStoreConfigurationError, match="Unsupported"):
        state_store_module.get_orchestration_state_store()


def test_get_orchestration_state_store_selects_postgres_store(monkeypatch):
    class _FakeJson:
        @staticmethod
        def Jsonb(value):
            return value

    class _FakePsycopg:
        types = SimpleNamespace(json=_FakeJson)

        @staticmethod
        def connect(db_url):
            class _FakeCursor:
                description = [("run_id",), ("thread_id",), ("session_id",), ("workflow_type",), ("status",), ("state_ref",), ("pending_action_id",), ("resume_state_blob",), ("checkpoint_blob",), ("version",), ("created_at",), ("updated_at",), ("last_error",), ("idempotency_key",)]

                def execute(self, query, params=None):
                    self._row = None

                def fetchone(self):
                    return self._row

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            class _FakeConnection:
                autocommit = False

                def cursor(self):
                    return _FakeCursor()

                def commit(self):
                    return None

                def rollback(self):
                    return None

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            return _FakeConnection()

    monkeypatch.setenv("ORCHESTRATOR_STATE_DB_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setattr(state_store_module, "_STORE_SINGLETON", None, raising=False)
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg)

    store = state_store_module.get_orchestration_state_store()

    assert store.__class__.__name__ == "PostgresOrchestrationStateStore"
