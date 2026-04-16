from orchestrator import tool_job_store as tool_job_store_module


def test_tool_job_store_persists_records_across_singleton_reset():
    store = tool_job_store_module.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    store.mark_running(job.job_id)
    store.finish_completed(
        job.job_id,
        {
            "assistant_message": "completed",
            "run_id": "run-1",
            "state_ref": "run:1",
        },
    )

    tool_job_store_module._STORE_SINGLETON = None
    reloaded_store = tool_job_store_module.get_tool_job_store()
    reloaded_job = reloaded_store.get(job.job_id)

    assert reloaded_job is not None
    assert reloaded_job.status == "completed"
    assert reloaded_job.route_prefix == "/tool-server"
    assert reloaded_job.status_url == f"/tool-server/tool-jobs/{job.job_id}"
    assert reloaded_job.response["assistant_message"] == "completed"


def test_tool_job_store_reconcile_marks_incomplete_jobs_failed():
    store = tool_job_store_module.get_tool_job_store()
    queued = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix=None,
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    running = store.create_job(
        tool_name="analyze_equipment_deep",
        route_prefix=None,
        request_payload={"requested_tool": "analyze_equipment_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    cancelling = store.create_job(
        tool_name="compare_documents_deep",
        route_prefix=None,
        request_payload={"requested_tool": "compare_documents_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    store.mark_running(running.job_id)
    store.mark_cancelling(cancelling.job_id)

    updated_count = store.reconcile_incomplete_jobs()

    assert updated_count == 3
    assert store.get(queued.job_id).status == "failed"
    assert store.get(running.job_id).error_summary == "interrupted:process-restart"
    assert store.get(cancelling.job_id).current_stage == "interrupted"


def test_tool_job_store_persists_extended_status_payload_and_terminal_result():
    store = tool_job_store_module.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )

    store.update_job_status(
        job.job_id,
        current_stage="indexing",
        status_text="Индексируем документ.",
        progress={"phase": "indexing", "fraction": 0.4},
        status_history=[
            {
                "key": "stage:indexing",
                "title": "Индексация",
                "content": "Индексируем документ.",
            }
        ],
        embeds=[{"kind": "status", "title": "Индексация"}],
        sources=[{"source_id": "src-1", "title": "contract.pdf"}],
        artifacts=[{"artifact_id": "artifact-1", "kind": "report"}],
        result_preview="Черновик промежуточного результата",
    )
    store.finish_failed(job.job_id, "Модель занята")

    reloaded_job = store.get(job.job_id)

    assert reloaded_job is not None
    assert reloaded_job.current_stage == "failed"
    assert reloaded_job.status_payload is not None
    assert reloaded_job.status_payload["status_text"] == "deep-job завершён со статусом failed."
    assert reloaded_job.status_payload["progress"]["phase"] == "indexing"
    assert reloaded_job.status_payload["status_history"][0]["key"] == "stage:indexing"
    assert reloaded_job.status_payload["embeds"][0]["kind"] == "status"
    assert reloaded_job.status_payload["sources"][0]["source_id"] == "src-1"
    assert reloaded_job.status_payload["artifacts"][0]["artifact_id"] == "artifact-1"
    assert reloaded_job.result_payload is not None
    assert "Модель занята" in reloaded_job.result_payload["assistant_message"]


def test_tool_job_store_records_terminal_delivery_idempotently():
    store = tool_job_store_module.get_tool_job_store()
    job = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_document_deep"},
        execution_metadata={"execution_mode": "async"},
    )
    store.finish_completed(job.job_id, {"assistant_message": "done"})

    recorded = store.record_terminal_delivery(job.job_id, result_message_id="assistant-result-1")
    second = store.record_terminal_delivery(job.job_id, result_message_id="assistant-result-2")
    reloaded_job = store.get(job.job_id)

    assert recorded.result_message_id == "assistant-result-1"
    assert recorded.terminal_emitted_at is not None
    assert second.result_message_id == "assistant-result-1"
    assert second.terminal_emitted_at == recorded.terminal_emitted_at
    assert reloaded_job is not None
    assert reloaded_job.result_message_id == "assistant-result-1"
    assert reloaded_job.terminal_emitted_at == recorded.terminal_emitted_at


def test_tool_job_store_find_latest_active_by_chat_id_prefers_newest_active_job():
    store = tool_job_store_module.get_tool_job_store()
    older = store.create_job(
        tool_name="analyze_document_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_document_deep", "chat_id": "chat-1"},
        execution_metadata={"execution_mode": "async"},
    )
    newer = store.create_job(
        tool_name="analyze_equipment_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "analyze_equipment_deep", "chat_id": "chat-1"},
        execution_metadata={"execution_mode": "async"},
    )
    unrelated = store.create_job(
        tool_name="compare_documents_deep",
        route_prefix="/tool-server",
        request_payload={"requested_tool": "compare_documents_deep", "chat_id": "chat-2"},
        execution_metadata={"execution_mode": "async"},
    )
    store.finish_completed(older.job_id, {"assistant_message": "done"})

    found = store.find_latest_active_by_chat_id("chat-1")

    assert found is not None
    assert found.job_id == newer.job_id
    assert found.request_payload["chat_id"] == "chat-1"
    assert unrelated.job_id != found.job_id
