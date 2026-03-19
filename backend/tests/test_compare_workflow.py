import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.workflows.compare import (
    _infer_compare_llm,
    analyze_differences_node,
    load_documents_node,
    match_chunks_node,
)
from services.observability import render_metrics_text, reset_observability_metrics


@pytest.fixture(autouse=True)
def _reset_compare_metrics():
    reset_observability_metrics()
    yield
    reset_observability_metrics()


@pytest.mark.asyncio
async def test_compare_load_documents_failure_records_metric():
    state = {
        "input_1": "/tmp/old.docx",
        "input_2": "/tmp/new.docx",
        "name_1": "old.docx",
        "name_2": "new.docx",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.get_shared_client", new_callable=AsyncMock) as mock_client_factory:
        fake_client = AsyncMock()
        fake_client.post.side_effect = RuntimeError("doc server unavailable")
        mock_client_factory.return_value = fake_client

        result = await load_documents_node(state)

    assert any("Error loading docs" in error for error in result["errors"])
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'component="compare_workflow"' in metrics
    assert 'fallback="load_documents_failed"' in metrics


@pytest.mark.asyncio
async def test_compare_match_batches_failure_records_metric():
    state = {
        "input_1": "/tmp/old.docx",
        "input_2": "/tmp/new.docx",
        "name_1": "old.docx",
        "name_2": "new.docx",
        "chunks_old": ["old chunk"],
        "chunks_new": ["new chunk"],
        "matches": [],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.get_shared_client", new_callable=AsyncMock) as mock_client_factory:
        fake_client = AsyncMock()
        fake_client.post.side_effect = RuntimeError("legal server unavailable")
        mock_client_factory.return_value = fake_client

        result = await match_chunks_node(state)

    assert any("Error in batch matching" in error for error in result["errors"])
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'component="compare_workflow"' in metrics
    assert 'fallback="match_batches_failed"' in metrics


@pytest.mark.asyncio
async def test_compare_analyze_partial_parse_records_metric():
    state = {
        "input_1": "/tmp/old.docx",
        "input_2": "/tmp/new.docx",
        "name_1": "old.docx",
        "name_2": "new.docx",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [
            {"type": "MODIFIED", "old_text": "a", "new_text": "b", "score": 0.2},
            {"type": "MODIFIED", "old_text": "c", "new_text": "d", "score": 0.2},
        ],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.ums_client.async_infer", new_callable=AsyncMock) as mock_infer:
        mock_infer.return_value = {
            "content": '[{"is_critical": true, "diff": "changed", "impact": "impact"}]'
        }

        result = await analyze_differences_node(state)

    assert result["analysis_results"]
    metrics = render_metrics_text()
    assert "agent_nav_fallback_events_total" in metrics
    assert 'component="compare_workflow"' in metrics
    assert 'fallback="analyze_parse_partial"' in metrics


@pytest.mark.asyncio
async def test_compare_infer_fallback_metadata_uses_resolved_model_id():
    with patch("orchestrator.workflows.compare.ums_client.async_infer", new_callable=AsyncMock) as mock_infer:
        mock_infer.return_value = {"content": "ok"}

        _, model_execution = await _infer_compare_llm("prompt", {"temperature": 0.1})

    assert model_execution is not None
    assert model_execution["role_key"] == "llm.legal_compare"
    assert model_execution["used_model_id"] == "qwen-14b-llm"
