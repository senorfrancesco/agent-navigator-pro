from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from utils.full_stack import (
    ComposeSmokeHarness,
    ProbeOutcome,
    ProbeSpec,
    docker_available,
    probe_url,
    wait_for_readiness,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_wait_for_readiness_succeeds_after_retries():
    responses = [
        SimpleNamespace(status_code=503),
        SimpleNamespace(status_code=503),
        SimpleNamespace(status_code=200),
    ]

    def fake_get(url, timeout):
        assert timeout == 1.0
        return responses.pop(0)

    clock = _Clock()

    def fake_sleep(seconds):
        clock.advance(seconds)

    result = wait_for_readiness(
        [ProbeSpec(name="ums", url="http://localhost:8090/health", timeout_s=1.0)],
        timeout_s=10.0,
        poll_interval_s=0.5,
        request_get=fake_get,
        sleep=fake_sleep,
        clock=clock,
    )

    assert result.ready is True
    assert result.attempts == 3
    assert all(outcome.ok for outcome in result.probe_outcomes)


def test_probe_url_returns_failure_detail():
    def fake_get(url, timeout):
        raise TimeoutError("probe timeout")

    outcome = probe_url(ProbeSpec(name="agent", url="http://localhost:8000/status"), request_get=fake_get)

    assert outcome.ok is False
    assert "probe timeout" in outcome.detail
    assert outcome.status_code is None


def test_compose_harness_collects_diagnostics_and_tears_down_on_failure():
    calls = []

    def fake_runner(args, cwd=None, capture_output=None, text=None, check=None):
        calls.append(tuple(args))
        if args[:3] == ["docker", "compose", "up"]:
            return SimpleNamespace(returncode=0, stdout="up ok", stderr="")
        if args[:3] == ["docker", "compose", "ps"]:
            return SimpleNamespace(returncode=0, stdout="stack ps", stderr="")
        if args[:3] == ["docker", "compose", "logs"]:
            return SimpleNamespace(returncode=0, stdout="stack logs", stderr="")
        if args[:3] == ["docker", "compose", "down"]:
            return SimpleNamespace(returncode=0, stdout="down ok", stderr="")
        if args[:3] == ["docker", "compose", "version"]:
            return SimpleNamespace(returncode=0, stdout="Docker Compose version v2", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def always_unhealthy(url, timeout):
        return SimpleNamespace(status_code=503)

    clock = _Clock()

    def fake_sleep(seconds):
        clock.advance(seconds)

    harness = ComposeSmokeHarness(
        project_root=os.getcwd(),
        probe_specs=[ProbeSpec(name="ums", url="http://localhost:8090/health", timeout_s=1.0)],
        runner=fake_runner,
        request_get=always_unhealthy,
        sleep=fake_sleep,
        clock=clock,
    )

    result = harness.run(timeout_s=0.0, poll_interval_s=0.0)

    assert result.ok is False
    assert result.started is True
    assert result.diagnostics is not None
    assert result.diagnostics.ps.stdout == "stack ps"
    assert result.diagnostics.logs.stdout == "stack logs"
    assert any(call[:3] == ("docker", "compose", "up") for call in calls)
    assert any(call[:3] == ("docker", "compose", "down") for call in calls)


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_FULL_STACK_SMOKE") != "1", reason="full-stack smoke is opt-in")
@pytest.mark.skipif(not docker_available(), reason="docker compose is not available")
def test_full_stack_compose_smoke():
    harness = ComposeSmokeHarness()
    result = harness.run(timeout_s=float(os.getenv("FULL_STACK_SMOKE_TIMEOUT_S", "300")), poll_interval_s=float(os.getenv("FULL_STACK_SMOKE_POLL_INTERVAL_S", "5")))
    if not result.ok:
        diagnostics = result.diagnostics
        message = [result.summary()]
        if diagnostics is not None:
            message.append("=== docker compose ps ===")
            message.append(diagnostics.ps.stdout or diagnostics.ps.stderr)
            message.append("=== docker compose logs ===")
            message.append(diagnostics.logs.stdout or diagnostics.logs.stderr)
            message.append("=== probes ===")
            for outcome in diagnostics.probe_outcomes:
                message.append(f"{outcome.name}: ok={outcome.ok} detail={outcome.detail}")
        pytest.fail("\n".join(message))
