import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator import agent_api
from orchestrator.agent_api import OrchestrationRequest
from services.observability import reset_observability_metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_observability_metrics()
    yield
    reset_observability_metrics()


def test_health_endpoint_stays_available():
    assert agent_api.health() == {"status": "ok"}


def test_metrics_endpoint_exposes_orchestration_counter():
    asyncio.run(
        agent_api.orchestrate(
            OrchestrationRequest(
                message="Привет",
                runtime_mode="chat_only",
                assistant_mode="general_chat",
            )
        )
    )

    response = agent_api.metrics()
    payload = response.body.decode("utf-8")

    assert response.media_type == "text/plain; version=0.0.4; charset=utf-8"
    assert "llm_tools_platform_agent_api_orchestration_requests_total" in payload
