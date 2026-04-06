from __future__ import annotations

import asyncio

import pytest

from orchestrator import operator_ui_actions


@pytest.mark.asyncio
async def test_start_action_job_queues_under_running_event_loop(monkeypatch):
    async def fake_run_job(job, spec):
        operator_ui_actions.JOB_STORE.mark_running(job.job_id)
        operator_ui_actions.JOB_STORE.set_stage(job.job_id, spec.group, "running")
        operator_ui_actions.JOB_STORE.finish_job(job.job_id, exit_code=0)

    monkeypatch.setattr(operator_ui_actions, "_run_job", fake_run_job)

    job = await operator_ui_actions.start_action_job("runtime.native.launch")
    await asyncio.sleep(0)

    stored = operator_ui_actions.get_job(job.job_id)
    assert stored is not None
    assert stored.job_id == job.job_id
    assert stored.status in {"queued", "running", "completed"}


def test_action_catalog_exposes_runtime_stop_actions():
    actions = operator_ui_actions.build_action_catalog()

    assert actions["runtime.native.stop"].command[-1].endswith("scripts/stop_native.sh")
    assert actions["runtime.container.stop"].command[-1].endswith("deploy/offline_bundle/scripts/stop_offline_bundle.sh")


@pytest.mark.asyncio
async def test_cancel_action_job_marks_running_job_as_cancelling(monkeypatch):
    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 4321

    job = operator_ui_actions.JOB_STORE.create_job(
        action_id="deploy.runtime.run",
        title="Run Offline Bundle",
        command=["bash", "run_offline_bundle.sh"],
        cwd="/tmp",
        privileged=False,
    )
    operator_ui_actions.JOB_STORE.mark_running(job.job_id)
    monkeypatch.setattr(operator_ui_actions.os, "killpg", lambda pid, sig: None)
    operator_ui_actions.JOB_PROCESSES[job.job_id] = FakeProcess()

    cancelled = await operator_ui_actions.cancel_action_job(job.job_id)

    assert cancelled.status == "cancelling"
    assert cancelled.current_stage == "cancel"
    assert cancelled.logs[-1].message == f"cancelling:{job.action_id}"
