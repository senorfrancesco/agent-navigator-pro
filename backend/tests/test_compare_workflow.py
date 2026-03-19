import os
import sys
from unittest.mock import AsyncMock, Mock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.workflows.compare import (
    _infer_compare_llm,
    analyze_differences_node,
    generate_report_node,
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


@pytest.mark.asyncio
async def test_compare_load_documents_detects_policy_vs_contract_mode():
    state = {
        "input_1": "/tmp/policy.pdf",
        "input_2": "/tmp/contract.pdf",
        "name_1": "Положение о дистанционной работе.pdf",
        "name_2": "Трудовой договор о дистанционной работе.pdf",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    policy_response = Mock()
    policy_response.raise_for_status.return_value = None
    policy_response.json.return_value = {
        "text": "Положение о дистанционной работе. Локальный нормативный акт определяет порядок взаимодействия и отчетности."
    }
    contract_response = Mock()
    contract_response.raise_for_status.return_value = None
    contract_response.json.return_value = {
        "text": "Трудовой договор о дистанционной работе. Работодатель и работник согласовали условия дистанционной работы."
    }

    with patch("orchestrator.workflows.compare.get_shared_client", new_callable=AsyncMock) as mock_client_factory:
        fake_client = AsyncMock()
        fake_client.post.side_effect = [policy_response, contract_response]
        mock_client_factory.return_value = fake_client

        result = await load_documents_node(state)

    assert result["document_role_1"] == "policy"
    assert result["document_role_2"] == "contract"
    assert result["pair_relation_type"] == "policy_vs_contract"
    assert result["compare_mode_selected"] == "heterogeneous_alignment"
    assert result["chunks_old"]
    assert result["chunks_new"]


@pytest.mark.asyncio
async def test_compare_analyze_uses_semantic_fallback_for_policy_vs_contract_structural_only():
    state = {
        "input_1": "/tmp/policy.pdf",
        "input_2": "/tmp/contract.pdf",
        "name_1": "Положение о дистанционной работе.pdf",
        "name_2": "Трудовой договор о дистанционной работе.pdf",
        "text_1": "Положение о дистанционной работе. Работодатель определяет порядок взаимодействия, отчетности и обеспечения оборудованием.",
        "text_2": "Трудовой договор о дистанционной работе. Работник выполняет обязанности дистанционно, соблюдает локальные акты и отчитывается работодателю.",
        "document_role_1": "policy",
        "document_role_2": "contract",
        "pair_relation_type": "policy_vs_contract",
        "compare_mode_selected": "heterogeneous_alignment",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [
            {"type": "ADDED", "new_text": "Работодатель утверждает график взаимодействия.", "old_text": ""},
            {"type": "DELETED", "old_text": "Работник обязан лично передать оборудование.", "new_text": ""},
        ],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.ums_client.async_infer", new_callable=AsyncMock) as mock_infer:
        mock_infer.return_value = {
            "content": """
            {
              "relation_summary": "Документы связаны по теме дистанционной работы, но относятся к разным юридическим ролям: локальное положение и индивидуальный трудовой договор.",
              "key_findings": [
                "Положение задает общие правила взаимодействия и отчетности.",
                "Договор должен отражать часть этих правил в индивидуальных условиях."
              ],
              "coverage_gaps": [
                "В договоре нужно явно закрепить порядок взаимодействия и отчетности.",
                "Следует уточнить обеспечение оборудованием и правила возврата имущества."
              ],
              "conflicts": [],
              "recommended_actions": [
                "Проверить, что договор не противоречит локальному положению."
              ]
            }
            """
        }

        result = await analyze_differences_node(state)

    assert result["analysis_results"]
    assert any(item.get("type") == "LEGAL_SUMMARY" for item in result["analysis_results"])
    assert any(item.get("type") == "COVERAGE_GAP" for item in result["analysis_results"])
    assert mock_infer.await_count == 1


@pytest.mark.asyncio
async def test_compare_report_renders_legal_summary_and_appendix_for_heterogeneous_pair():
    state = {
        "input_1": "/tmp/policy.pdf",
        "input_2": "/tmp/contract.pdf",
        "name_1": "Положение.pdf",
        "name_2": "Договор.pdf",
        "document_role_1": "policy",
        "document_role_2": "contract",
        "pair_relation_type": "policy_vs_contract",
        "compare_mode_selected": "heterogeneous_alignment",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [],
        "analysis_results": [
            {
                "type": "LEGAL_SUMMARY",
                "relation_summary": "Это локальное положение и шаблон договора.",
                "key_findings": ["Документы связаны по теме дистанционной работы."],
                "coverage_gaps": ["В договоре не хватает порядка отчетности."],
                "recommended_actions": ["Добавить ссылку на локальное положение."],
            },
            {"type": "ADDED", "content": "Новый пункт о графике взаимодействия."},
        ],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    result = await generate_report_node(state)

    report = result["final_report"]
    assert "## Юридический вывод" in report
    assert "## Что отсутствует / требует отражения" in report
    assert "## Приложение: различия по пунктам" in report
    assert "локальное положение" in report.lower()
