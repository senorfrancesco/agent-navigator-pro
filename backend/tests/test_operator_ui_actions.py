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
