import os

import pytest

from orchestrator.state_store import SQLiteOrchestrationStateStore


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
