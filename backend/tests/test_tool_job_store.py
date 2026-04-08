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
