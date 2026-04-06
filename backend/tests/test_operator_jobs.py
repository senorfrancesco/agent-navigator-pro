from orchestrator.operator_jobs import OperatorJobStore


def test_job_store_tracks_stage_transitions_and_logs():
    job_store = OperatorJobStore()

    job = job_store.create_job(
        action_id="deploy.bundle.build",
        title="Build Bundle",
        command=["bash", "build_bundle.sh"],
        cwd="/tmp",
        privileged=False,
    )
    job_store.set_stage(job.job_id, "build", "running")
    job_store.append_log(job.job_id, "build", "started", stream="stdout")
    snapshot = job_store.get(job.job_id)

    assert snapshot is not None
    assert snapshot.current_stage == "build"
    assert snapshot.stages[-1].stage == "build"
    assert snapshot.stages[-1].status == "running"
    assert snapshot.logs[-1].message == "started"
    assert snapshot.logs[-1].stream == "stdout"


def test_job_store_marks_completion_with_exit_code():
    job_store = OperatorJobStore()

    job = job_store.create_job(
        action_id="runtime.native.launch",
        title="Launch Native Runtime",
        command=["bash", "launcher.sh"],
        cwd="/tmp",
        privileged=False,
    )
    job_store.mark_running(job.job_id)
    job_store.finish_job(job.job_id, exit_code=0)
    snapshot = job_store.get(job.job_id)

    assert snapshot is not None
    assert snapshot.status == "completed"
    assert snapshot.exit_code == 0
    assert snapshot.started_at is not None
    assert snapshot.finished_at is not None


def test_job_store_tracks_cancelling_and_cancelled_states():
    job_store = OperatorJobStore()

    job = job_store.create_job(
        action_id="deploy.runtime.run",
        title="Run Offline Bundle",
        command=["bash", "run_offline_bundle.sh"],
        cwd="/tmp",
        privileged=False,
    )
    job_store.mark_running(job.job_id)
    job_store.mark_cancelling(job.job_id)
    job_store.finish_cancelled(job.job_id)
    snapshot = job_store.get(job.job_id)

    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert snapshot.exit_code is None
    assert snapshot.finished_at is not None
