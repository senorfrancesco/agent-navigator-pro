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
    _determine_compare_mode,
    _resolve_analysis_batch_size,
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
async def test_compare_load_documents_detects_same_base_law_amendment_pair():
    state = {
        "input_1": "/tmp/law_2021.pdf",
        "input_2": "/tmp/law_2023.pdf",
        "name_1": "H12100110_1621890000.pdf",
        "name_2": "H12300274_1688590800.pdf",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    law_2021 = Mock()
    law_2021.raise_for_status.return_value = None
    law_2021.json.return_value = {
        "text": (
            "ЗАКОН РЕСПУБЛИКИ БЕЛАРУСЬ 24 мая 2021 г. № 110-З "
            "Об изменении законов по вопросам средств массовой информации "
            "Статья 1. Внести в Закон Республики Беларусь от 17 июля 2008 г. № 427-З "
            "«О средствах массовой информации» следующие изменения:"
        )
    }
    law_2023 = Mock()
    law_2023.raise_for_status.return_value = None
    law_2023.json.return_value = {
        "text": (
            "ЗАКОН РЕСПУБЛИКИ БЕЛАРУСЬ 30 июня 2023 г. № 274-З "
            "Об изменении Закона Республики Беларусь «О средствах массовой информации» "
            "Статья 1. Внести в Закон Республики Беларусь от 17 июля 2008 г. № 427-З "
            "«О средствах массовой информации» следующие изменения:"
        )
    }

    with patch("orchestrator.workflows.compare.get_shared_client", new_callable=AsyncMock) as mock_client_factory:
        fake_client = AsyncMock()
        fake_client.post.side_effect = [law_2021, law_2023]
        mock_client_factory.return_value = fake_client

        result = await load_documents_node(state)

    assert result["pair_relation_type"] == "same_base_law_amendments"
    assert result["compare_mode_selected"] == "semantic_compare"
    assert result["base_document_title_1"] == "О средствах массовой информации"
    assert result["base_document_title_2"] == "О средствах массовой информации"


def test_compare_mode_does_not_treat_shared_reference_as_same_base_amendment():
    relation_type, compare_mode = _determine_compare_mode(
        "other",
        "other",
        "Доклад.pdf",
        "Методичка.pdf",
        text_1=(
            "Аналитический доклад. В тексте упоминается Закон Республики Беларусь "
            "«О средствах массовой информации», но документ не вносит в него изменения."
        ),
        text_2=(
            "Методические рекомендации по применению норм. Документ ссылается на Закон "
            "Республики Беларусь «О средствах массовой информации», но не является законом о внесении изменений."
        ),
    )

    assert relation_type == "unknown"
    assert compare_mode == "semantic_compare"


@pytest.mark.asyncio
async def test_compare_match_batches_lowers_threshold_for_same_base_law_amendments():
    state = {
        "input_1": "/tmp/law_2021.pdf",
        "input_2": "/tmp/law_2023.pdf",
        "name_1": "law_2021.pdf",
        "name_2": "law_2023.pdf",
        "chunks_old": ["old chunk"],
        "chunks_new": ["new chunk"],
        "pair_relation_type": "same_base_law_amendments",
        "compare_mode_selected": "semantic_compare",
        "matches": [],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "status": "success",
        "matches": [{"type": "MODIFIED", "old_text": "old chunk", "new_text": "new chunk", "similarity_score": 0.7}],
    }

    with patch("orchestrator.workflows.compare.get_shared_client", new_callable=AsyncMock) as mock_client_factory:
        fake_client = AsyncMock()
        fake_client.post.return_value = response
        mock_client_factory.return_value = fake_client

        await match_chunks_node(state)

    _, kwargs = fake_client.post.call_args
    assert kwargs["json"]["threshold"] == 0.64


def test_compare_analysis_batch_size_uses_single_item_for_same_base_law_amendments():
    state = {
        "pair_relation_type": "same_base_law_amendments",
        "compare_mode_selected": "semantic_compare",
    }

    assert _resolve_analysis_batch_size(state) == 1


def test_compare_analysis_batch_size_uses_smaller_batch_for_generic_semantic_compare():
    state = {
        "pair_relation_type": "unknown",
        "compare_mode_selected": "semantic_compare",
    }

    assert _resolve_analysis_batch_size(state) == 2


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
        mock_infer.side_effect = [
            {
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
            },
            {
                "content": """
                [
                  {
                    "is_critical": true,
                    "diff": "Добавлен отдельный фрагмент о графике взаимодействия.",
                    "impact": "Положение подробнее регулирует порядок коммуникации."
                  },
                  {
                    "is_critical": true,
                    "diff": "Удалён фрагмент о личной передаче оборудования.",
                    "impact": "Нужно проверить, как обязанность закреплена в договоре."
                  }
                ]
                """
            },
        ]

        result = await analyze_differences_node(state)

    assert result["analysis_results"]
    assert any(item.get("type") == "LEGAL_SUMMARY" for item in result["analysis_results"])
    assert any(item.get("type") == "COVERAGE_GAP" for item in result["analysis_results"])
    assert any(item.get("type") == "ADDED" and item.get("diff") for item in result["analysis_results"])
    assert any(item.get("type") == "DELETED" and item.get("diff") for item in result["analysis_results"])
    assert mock_infer.await_count == 2


@pytest.mark.asyncio
async def test_compare_analyze_retries_partial_batch_item_by_item_for_semantic_compare():
    state = {
        "input_1": "/tmp/law_2021.pdf",
        "input_2": "/tmp/law_2023.pdf",
        "name_1": "law_2021.pdf",
        "name_2": "law_2023.pdf",
        "text_1": "Закон 2021 года вносит изменения в закон о СМИ.",
        "text_2": "Закон 2023 года вносит изменения в закон о СМИ.",
        "document_role_1": "other",
        "document_role_2": "other",
        "pair_relation_type": "unknown",
        "compare_mode_selected": "semantic_compare",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [
            {"type": "MODIFIED", "old_text": "Старая норма 1.", "new_text": "Новая норма 1.", "similarity_score": 0.1},
            {"type": "MODIFIED", "old_text": "Старая норма 2.", "new_text": "Новая норма 2.", "similarity_score": 0.1},
        ],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.ums_client.async_infer", new_callable=AsyncMock) as mock_infer:
        mock_infer.side_effect = [
            {"content": '[{"is_critical": true, "diff": "Только один результат", "impact": "Недостаточно"}]'},
            {"content": '[{"is_critical": true, "diff": "Изменена первая норма.", "impact": "Меняется регулирование первой темы."}]'},
            {"content": '[{"is_critical": true, "diff": "Изменена вторая норма.", "impact": "Меняется регулирование второй темы."}]'},
        ]

        result = await analyze_differences_node(state)

    modified = [item for item in result["analysis_results"] if item.get("type") == "MODIFIED"]
    assert len(modified) == 2
    assert "первая норма" in modified[0]["diff"].lower()
    assert "вторая норма" in modified[1]["diff"].lower()
    assert mock_infer.await_count == 3


@pytest.mark.asyncio
async def test_compare_analyze_runs_semantic_summary_for_same_base_law_structural_only():
    state = {
        "input_1": "/tmp/law_2021.pdf",
        "input_2": "/tmp/law_2023.pdf",
        "name_1": "law_2021.pdf",
        "name_2": "law_2023.pdf",
        "text_1": "Закон 2021 года вносит изменения в закон о СМИ и ограничении доступа.",
        "text_2": "Закон 2023 года меняет закон о СМИ и вводит регулирование новостных агрегаторов.",
        "document_role_1": "other",
        "document_role_2": "other",
        "pair_relation_type": "same_base_law_amendments",
        "compare_mode_selected": "semantic_compare",
        "base_document_title_1": "О средствах массовой информации",
        "base_document_title_2": "О средствах массовой информации",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [
            {"type": "DELETED", "old_text": "Старый пункт об ограничении доступа.", "new_text": ""},
            {"type": "ADDED", "old_text": "", "new_text": "Новый пункт о новостных агрегаторах."},
        ],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.ums_client.async_infer", new_callable=AsyncMock) as mock_infer:
        mock_infer.side_effect = [
            {
                "content": """
                {
                  "relation_summary": "Оба документа вносят поправки в один и тот же базовый закон о СМИ, но охватывают разные тематические блоки изменений.",
                  "key_findings": [
                    "Поправки 2021 года усиливают контур ограничений и обязанностей.",
                    "Поправки 2023 года добавляют регулирование новостных агрегаторов."
                  ],
                  "coverage_gaps": [
                    "Нужно сопоставить, заменяют ли новые правила прежние механизмы ограничения доступа."
                  ],
                  "conflicts": [],
                  "recommended_actions": [
                    "Сверить, какие темы являются развитием прежнего регулирования, а какие совершенно новы."
                  ]
                }
                """
            },
            {
                "content": """
                [
                  {
                    "is_critical": true,
                    "diff": "Из прежней редакции исключён фрагмент об ограничении доступа.",
                    "impact": "Меняется структура оснований для ограничения распространения информации."
                  }
                ]
                """
            },
            {
                "content": """
                [
                  {
                    "is_critical": true,
                    "diff": "Добавлено регулирование новостных агрегаторов как нового объекта надзора.",
                    "impact": "Появляется новый круг обязанных субъектов."
                  }
                ]
                """
            },
        ]

        result = await analyze_differences_node(state)

    assert any(item.get("type") == "LEGAL_SUMMARY" for item in result["analysis_results"])
    assert any(item.get("type") == "COVERAGE_GAP" for item in result["analysis_results"])
    assert any(item.get("type") == "DELETED" and item.get("diff") for item in result["analysis_results"])
    assert any(item.get("type") == "ADDED" and item.get("diff") for item in result["analysis_results"])
    assert mock_infer.await_count == 3


@pytest.mark.asyncio
async def test_compare_analyze_adds_semantic_meaning_for_structural_chunks():
    state = {
        "input_1": "/tmp/law_v1.pdf",
        "input_2": "/tmp/law_v2.pdf",
        "name_1": "Закон_v1.pdf",
        "name_2": "Закон_v2.pdf",
        "text_1": "Редакция 1 закона о СМИ.",
        "text_2": "Редакция 2 закона о СМИ.",
        "document_role_1": "other",
        "document_role_2": "other",
        "pair_relation_type": "unknown",
        "compare_mode_selected": "unknown",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [
            {"type": "DELETED", "old_text": "Старый пункт об ограничении доступа.", "new_text": ""},
            {"type": "ADDED", "old_text": "", "new_text": "Новый пункт о новостных агрегаторах."},
        ],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.ums_client.async_infer", new_callable=AsyncMock) as mock_infer:
        mock_infer.return_value = {
            "content": """
            [
              {
                "is_critical": true,
                "diff": "Удалена норма об ограничении доступа, меняется основание правоприменения.",
                "impact": "Снижается прямое регулирование соответствующего случая."
              },
              {
                "is_critical": true,
                "diff": "Добавлено регулирование новостных агрегаторов как нового объекта закона.",
                "impact": "Появляются новые обязанности и контур надзора."
              }
            ]
            """
        }

        result = await analyze_differences_node(state)

    assert mock_infer.await_count == 1
    assert len(result["analysis_results"]) == 2
    assert result["analysis_results"][0]["type"] == "DELETED"
    assert "основание правоприменения" in result["analysis_results"][0]["diff"]
    assert result["analysis_results"][1]["type"] == "ADDED"
    assert "новостных агрегаторов" in result["analysis_results"][1]["diff"]


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


@pytest.mark.asyncio
async def test_compare_report_renders_semantic_meaning_for_structural_entries():
    state = {
        "input_1": "/tmp/old.pdf",
        "input_2": "/tmp/new.pdf",
        "name_1": "old.pdf",
        "name_2": "new.pdf",
        "document_role_1": "other",
        "document_role_2": "other",
        "pair_relation_type": "unknown",
        "compare_mode_selected": "unknown",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [],
        "analysis_results": [
            {
                "type": "DELETED",
                "content": "Старый пункт об ограничении доступа.",
                "diff": "Удалена норма о прямом ограничении доступа.",
                "impact": "Меняется регуляторная рамка.",
            },
            {
                "type": "ADDED",
                "content": "Новый пункт о новостных агрегаторах.",
                "diff": "Добавлен новый объект правового регулирования.",
                "impact": "Возникают новые обязанности для владельцев агрегаторов.",
            },
        ],
        "final_report": "",
        "errors": [],
        "session_id": "session-1",
    }

    result = await generate_report_node(state)

    report = result["final_report"]
    assert "### ❌ УДАЛЕНО" in report
    assert "**Суть:** Удалена норма о прямом ограничении доступа." in report
    assert "**Влияние:** Меняется регуляторная рамка." in report
    assert "### ✅ ДОБАВЛЕНО" in report
    assert "**Суть:** Добавлен новый объект правового регулирования." in report


@pytest.mark.asyncio
async def test_compare_report_renders_elapsed_time_from_runtime_context():
    state = {
        "input_1": "/tmp/old.pdf",
        "input_2": "/tmp/new.pdf",
        "name_1": "old.pdf",
        "name_2": "new.pdf",
        "document_role_1": "other",
        "document_role_2": "other",
        "pair_relation_type": "unknown",
        "compare_mode_selected": "unknown",
        "chunks_old": [],
        "chunks_new": [],
        "matches": [],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "runtime_context": {"started_at_monotonic": 100.0},
        "session_id": "session-1",
    }

    with patch("orchestrator.workflows.compare.time.monotonic", return_value=225.0):
        result = await generate_report_node(state)

    report = result["final_report"]
    assert "**Время выполнения:** 2 мин. 5 сек." in report
