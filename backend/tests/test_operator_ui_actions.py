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
