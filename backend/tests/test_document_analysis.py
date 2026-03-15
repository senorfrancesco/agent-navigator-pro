"""
Тесты для Document Analysis Workflow — классификация, экстракция, суммаризация, отчёт, граф, intent.

Покрытие:
- classify_doc_type: keyword scoring для типов документов
- classify_and_load_node: загрузка + метаданные
- extract_positions_node: двухпроходная экстракция
- summarize_node: map-reduce суммаризация
- generate_analysis_report_node: генерация Markdown отчёта
- create_analysis_graph: полный граф (mocked)
- _detect_intent: document_analysis intent detection
"""

import os
import sys
import pytest
import tempfile
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

# Пути для импорта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.workflows.document_analysis import (
    classify_doc_type,
    classify_and_load_node,
    extract_positions_node,
    summarize_node,
    generate_analysis_report_node,
    create_analysis_graph,
    DocumentAnalysisState,
    _DOC_TYPE_KEYWORDS,
    _SUMMARY_PROMPTS,
    _DOC_TYPE_LABELS,
)
from services.observability import render_metrics_text, reset_observability_metrics


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def base_state() -> DocumentAnalysisState:
    return {
        "input_path": "/tmp/test.pdf",
        "doc_name": "test.pdf",
        "doc_type": "",
        "doc_metadata": {},
        "items": [],
        "full_text": "",
        "summary": "",
        "final_report": "",
        "errors": [],
    }


@pytest.fixture(autouse=True)
def _reset_document_analysis_metrics():
    reset_observability_metrics()
    yield
    reset_observability_metrics()


@pytest.fixture
def tz_text():
    return (
        "ТЕХНИЧЕСКОЕ ЗАДАНИЕ\n"
        "1. Предмет закупки: поставка серверного оборудования\n"
        "2. Требования к поставляемому оборудованию:\n"
        "- Сервер: 2x Intel Xeon, 64GB RAM, 2x SSD 960GB\n"
        "- Ноутбук: Intel Core i7, 32GB RAM, 1TB SSD\n"
        "3. Срок поставки: 10 рабочих дней\n"
        "4. Гарантия: не менее 12 месяцев\n"
    )


@pytest.fixture
def smeta_text():
    return (
        "СМЕТНЫЙ РАСЧЕТ\n"
        "Стоимость работ по монтажу оборудования\n"
        "1. Монтаж серверной стойки — 50000 руб.\n"
        "2. Прокладка кабельных трасс — 30000 руб.\n"
        "Итого: 80000 руб.\n"
    )


@pytest.fixture
def kp_text():
    return (
        "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ\n"
        "Условия оплаты: 100% предоплата\n"
        "Срок действия предложения: 30 дней\n"
        "Ценовое предложение на поставку оборудования\n"
    )


@pytest.fixture
def legal_text():
    return (
        "ДОГОВОР ПОСТАВКИ №123\n"
        "Стороны договора:\n"
        "Заказчик — ООО «Рога и Копыта»\n"
        "Поставщик — ООО «Серверы.рф»\n"
        "Предмет договора: поставка оборудования\n"
        "Контракт вступает в силу...\n"
    )


# ============================================================================
# Tests: classify_doc_type
# ============================================================================

class TestClassifyDocType:
    def test_classify_tz_document(self, tz_text):
        assert classify_doc_type(tz_text) == "tz"

    def test_classify_smeta_document(self, smeta_text):
        assert classify_doc_type(smeta_text) == "smeta"

    def test_classify_kp_document(self, kp_text):
        assert classify_doc_type(kp_text) == "kp"

    def test_classify_legal_document(self, legal_text):
        assert classify_doc_type(legal_text) == "legal"

    def test_classify_unknown_fallback(self):
        assert classify_doc_type("Привет мир, это просто текст без ключевых слов.") == "other"

    def test_classify_empty_text(self):
        assert classify_doc_type("") == "other"

    def test_classify_highest_score_wins(self):
        """Если есть ключевые слова нескольких типов — побеждает тот, у кого больше совпадений."""
        text = "техническое задание, предмет закупки, требования к поставляемому, тз на, договор"
        # tz: 4 совпадения, legal: 1 ("договор")
        assert classify_doc_type(text) == "tz"

    def test_legal_label_is_not_contract(self):
        assert _DOC_TYPE_LABELS["legal"] == "Юридический / нормативный документ"


# ============================================================================
# Tests: classify_and_load_node
# ============================================================================

class TestClassifyAndLoadNode:
    @pytest.mark.asyncio
    async def test_classify_tz_document(self, base_state, tz_text):
        mock_response_load = MagicMock()
        mock_response_load.status_code = 200
        mock_response_load.raise_for_status = MagicMock()
        mock_response_load.json.return_value = {"text": tz_text}

        mock_response_pages = MagicMock()
        mock_response_pages.status_code = 200
        mock_response_pages.raise_for_status = MagicMock()
        mock_response_pages.json.return_value = {"total_pages": 5}

        mock_response_tables = MagicMock()
        mock_response_tables.status_code = 200
        mock_response_tables.raise_for_status = MagicMock()
        mock_response_tables.json.return_value = {"tables": [{"data": []}, {"data": []}]}

        async def mock_post(url, **kwargs):
            if "/load_document" in url:
                return mock_response_load
            elif "/load_pages" in url:
                return mock_response_pages
            elif "/extract_tables" in url:
                return mock_response_tables
            return mock_response_load

        with patch("orchestrator.workflows.document_analysis.get_shared_client") as mock_get_client:
            client_instance = AsyncMock()
            client_instance.post = AsyncMock(side_effect=mock_post)
            mock_get_client.return_value = client_instance

            result = await classify_and_load_node(base_state)
        assert result["doc_type"] == "tz"
        assert result["full_text"] == tz_text
        assert result["doc_metadata"]["pages"] == 5
        assert result["doc_metadata"]["tables_count"] == 2
        assert result["doc_metadata"]["chars"] == len(tz_text)

    @pytest.mark.asyncio
    async def test_metadata_extraction(self, base_state, smeta_text):
        mock_response_load = MagicMock()
        mock_response_load.status_code = 200
        mock_response_load.raise_for_status = MagicMock()
        mock_response_load.json.return_value = {"text": smeta_text}

        mock_response_pages = MagicMock()
        mock_response_pages.status_code = 200
        mock_response_pages.raise_for_status = MagicMock()
        mock_response_pages.json.return_value = {"total_pages": 3}

        mock_response_tables = MagicMock()
        mock_response_tables.status_code = 200
        mock_response_tables.raise_for_status = MagicMock()
        mock_response_tables.json.return_value = {"tables": []}

        async def mock_post(url, **kwargs):
            if "/load_document" in url:
                return mock_response_load
            elif "/load_pages" in url:
                return mock_response_pages
            elif "/extract_tables" in url:
                return mock_response_tables
            return mock_response_load

        with patch("orchestrator.workflows.document_analysis.get_shared_client") as mock_get_client:
            client_instance = AsyncMock()
            client_instance.post = AsyncMock(side_effect=mock_post)
            mock_get_client.return_value = client_instance

            result = await classify_and_load_node(base_state)
        assert result["doc_type"] == "smeta"
        meta = result["doc_metadata"]
        assert "pages" in meta
        assert "chars" in meta
        assert "tables_count" in meta
        assert "format" in meta
        assert meta["format"] == "PDF"


# ============================================================================
# Tests: extract_positions_node
# ============================================================================

class TestExtractPositionsNode:
    @pytest.mark.asyncio
    async def test_extract_with_tables(self, base_state):
        base_state["input_path"] = "/tmp/test.xlsx"

        table_items = [
            {"name": "Сервер HP", "specs": "2x Xeon", "quantity": "2", "price": "100000", "unit": "шт", "source": "table", "page": 1},
        ]

        with patch("orchestrator.workflows.document_analysis._extract_tables_from_doc", new_callable=AsyncMock) as mock_tables, \
             patch("orchestrator.workflows.document_analysis._extract_items_llm", new_callable=AsyncMock) as mock_llm:
            mock_tables.return_value = table_items
            mock_llm.return_value = []

            result = await extract_positions_node(base_state)

        assert len(result["items"]) == 1
        assert result["items"][0]["name"] == "Сервер HP"
        assert result["items"][0]["source"] == "table"

    @pytest.mark.asyncio
    async def test_extract_llm_fallback(self, base_state):
        llm_items = [
            {"name": "Ноутбук MSI", "specs": "i7, 32GB", "quantity": "5", "price": "", "unit": "", "source": "text", "page": None},
        ]

        with patch("orchestrator.workflows.document_analysis._extract_tables_from_doc", new_callable=AsyncMock) as mock_tables, \
             patch("orchestrator.workflows.document_analysis._extract_items_llm", new_callable=AsyncMock) as mock_llm:
            mock_tables.return_value = []
            mock_llm.return_value = llm_items

            result = await extract_positions_node(base_state)

        assert len(result["items"]) == 1
        assert result["items"][0]["source"] == "text"

    @pytest.mark.asyncio
    async def test_extract_llm_failure_records_metric(self, base_state):
        with patch("orchestrator.workflows.document_analysis._extract_tables_from_doc", new_callable=AsyncMock) as mock_tables, \
             patch("orchestrator.workflows.document_analysis._extract_items_llm", new_callable=AsyncMock) as mock_llm:
            mock_tables.return_value = []
            mock_llm.side_effect = RuntimeError("llm exploded")

            result = await extract_positions_node(base_state)

        assert any("LLM extraction failed" in error for error in result["errors"])
        metrics = render_metrics_text()
        assert "agent_nav_fallback_events_total" in metrics
        assert 'component="document_analysis"' in metrics
        assert 'fallback="llm_extract_failed"' in metrics

    @pytest.mark.asyncio
    async def test_extract_dedup(self, base_state):
        table_items = [
            {"name": "Сервер HP ProLiant", "specs": "2x Xeon", "quantity": "2", "price": "100000", "unit": "шт", "source": "table", "page": 1},
        ]
        text_items = [
            {"name": "Сервер HP ProLiant", "specs": "2x Xeon Gold", "quantity": "2", "price": "", "unit": "", "source": "text", "page": None},
        ]

        with patch("orchestrator.workflows.document_analysis._extract_tables_from_doc", new_callable=AsyncMock) as mock_tables, \
             patch("orchestrator.workflows.document_analysis._extract_items_llm", new_callable=AsyncMock) as mock_llm:
            mock_tables.return_value = table_items
            mock_llm.return_value = text_items

            result = await extract_positions_node(base_state)

        # table preferred — dedup оставляет table source
        assert len(result["items"]) == 1
        assert result["items"][0]["source"] == "table"

    @pytest.mark.asyncio
    async def test_extract_empty_doc(self, base_state):
        with patch("orchestrator.workflows.document_analysis._extract_tables_from_doc", new_callable=AsyncMock) as mock_tables, \
             patch("orchestrator.workflows.document_analysis._extract_items_llm", new_callable=AsyncMock) as mock_llm:
            mock_tables.return_value = []
            mock_llm.return_value = []

            result = await extract_positions_node(base_state)

        assert result["items"] == []


# ============================================================================
# Tests: summarize_node
# ============================================================================

class TestSummarizeNode:
    @pytest.mark.asyncio
    async def test_summarize_tz_prompt(self, base_state, tz_text):
        base_state["full_text"] = tz_text
        base_state["doc_type"] = "tz"

        with patch("orchestrator.workflows.document_analysis._chunk_text", new_callable=AsyncMock) as mock_chunk, \
             patch("orchestrator.workflows.document_analysis.ums_client") as mock_ums:
            mock_chunk.return_value = [tz_text]
            mock_ums.async_infer = AsyncMock(return_value={
                "content": "- Срок поставки: 10 дней\n- Гарантия: 12 месяцев"
            })

            result = await summarize_node(base_state)

        assert "поставки" in result["summary"].lower() or "гарантия" in result["summary"].lower()
        # Проверяем что промпт содержит tz-специфичные ключевые слова
        call_args = mock_ums.async_infer.call_args
        prompt = call_args[1]["prompt"] if "prompt" in call_args[1] else call_args[0][1]["prompt"]
        assert "сроки поставки" in prompt.lower() or "гарантия" in prompt.lower()

    @pytest.mark.asyncio
    async def test_summarize_smeta_prompt(self, base_state, smeta_text):
        base_state["full_text"] = smeta_text
        base_state["doc_type"] = "smeta"

        with patch("orchestrator.workflows.document_analysis._chunk_text", new_callable=AsyncMock) as mock_chunk, \
             patch("orchestrator.workflows.document_analysis.ums_client") as mock_ums:
            mock_chunk.return_value = [smeta_text]
            mock_ums.async_infer = AsyncMock(return_value={
                "content": "- Итоговая стоимость: 80000 руб."
            })

            result = await summarize_node(base_state)

        call_args = mock_ums.async_infer.call_args
        prompt = call_args[1]["prompt"] if "prompt" in call_args[1] else call_args[0][1]["prompt"]
        assert "стоимость" in prompt.lower()

    @pytest.mark.asyncio
    async def test_summarize_llm_error(self, base_state, tz_text):
        base_state["full_text"] = tz_text
        base_state["doc_type"] = "tz"

        with patch("orchestrator.workflows.document_analysis._chunk_text", new_callable=AsyncMock) as mock_chunk, \
             patch("orchestrator.workflows.document_analysis.ums_client") as mock_ums:
            mock_chunk.return_value = [tz_text]
            mock_ums.async_infer = AsyncMock(side_effect=Exception("LLM timeout"))

            result = await summarize_node(base_state)

        # Не должен упасть — вернёт ошибку в summary или errors
        assert "errors" in result
        assert any("failed" in e.lower() or "timeout" in e.lower() for e in result["errors"])
        metrics = render_metrics_text()
        assert "agent_nav_fallback_events_total" in metrics
        assert 'component="document_analysis"' in metrics
        assert 'fallback="summarize_chunk_failed"' in metrics

    @pytest.mark.asyncio
    async def test_summarize_accepts_choices_response_shape(self, base_state, tz_text):
        base_state["full_text"] = tz_text
        base_state["doc_type"] = "tz"

        with patch("orchestrator.workflows.document_analysis._chunk_text", new_callable=AsyncMock) as mock_chunk, \
             patch("orchestrator.workflows.document_analysis.ums_client") as mock_ums:
            mock_chunk.return_value = [tz_text]
            mock_ums.async_infer = AsyncMock(return_value={
                "choices": [{"text": "- Срок поставки: 20 рабочих дней\n- Гарантия: 36 месяцев"}]
            })

            result = await summarize_node(base_state)

        assert "20 рабочих дней" in result["summary"]
        assert "36 месяцев" in result["summary"]

    @pytest.mark.asyncio
    async def test_summarize_empty_text(self, base_state):
        base_state["full_text"] = ""
        base_state["doc_type"] = "tz"

        result = await summarize_node(base_state)
        assert "пуст" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_summarize_reduce_failure_records_metric(self, base_state, tz_text):
        base_state["full_text"] = tz_text
        base_state["doc_type"] = "tz"

        with patch("orchestrator.workflows.document_analysis._chunk_text", new_callable=AsyncMock) as mock_chunk, \
             patch("orchestrator.workflows.document_analysis.ums_client") as mock_ums:
            mock_chunk.return_value = [tz_text[:80], tz_text[80:]]
            mock_ums.async_infer = AsyncMock(side_effect=[
                {"content": "- Срок поставки: 10 дней"},
                {"content": "- Гарантия: 12 месяцев"},
                RuntimeError("reduce exploded"),
            ])

            result = await summarize_node(base_state)

        assert "10 дней" in result["summary"]
        assert any("Reduce summarization failed" in error for error in result["errors"])
        metrics = render_metrics_text()
        assert "agent_nav_fallback_events_total" in metrics
        assert 'component="document_analysis"' in metrics
        assert 'fallback="reduce_summarization_failed"' in metrics


# ============================================================================
# Tests: generate_analysis_report_node
# ============================================================================

class TestGenerateAnalysisReportNode:
    @pytest.mark.asyncio
    async def test_report_contains_metadata(self, base_state):
        base_state["doc_type"] = "tz"
        base_state["doc_metadata"] = {"pages": 12, "chars": 18694, "tables_count": 3, "format": "PDF"}
        base_state["items"] = []
        base_state["summary"] = "Тестовая сводка"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                result = await generate_analysis_report_node(base_state)

        report = result["final_report"]
        assert "Техническое задание" in report
        assert "12" in report  # pages
        assert "18694" in report  # chars

    @pytest.mark.asyncio
    async def test_report_contains_items(self, base_state):
        base_state["doc_type"] = "tz"
        base_state["doc_metadata"] = {"pages": 5, "chars": 5000, "tables_count": 1, "format": "PDF"}
        base_state["items"] = [
            {"name": "Сервер HP", "specs": "2x Xeon", "quantity": "2", "price": "100000", "source": "table"},
            {"name": "Ноутбук MSI", "specs": "i7", "quantity": "5", "price": "50000", "source": "text"},
        ]
        base_state["summary"] = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                result = await generate_analysis_report_node(base_state)

        report = result["final_report"]
        assert "Сервер HP" in report
        assert "Ноутбук MSI" in report
        assert "2 шт." in report or "| 2 |" in report

    @pytest.mark.asyncio
    async def test_report_does_not_cut_specs_to_80_chars(self, base_state):
        base_state["doc_type"] = "tz"
        base_state["doc_metadata"] = {"pages": 5, "chars": 5000, "tables_count": 1, "format": "PDF"}
        long_specs = (
            "Тип устройства: Сервер, Тип корпуса: Rack 19, TPM 2.0, Количество отсеков: 24, "
            "Количество блоков питания: 2, Мощность блока питания: 1400 Вт"
        )
        base_state["items"] = [
            {"name": "Сервер HP", "specs": long_specs, "quantity": "2", "price": "100000", "source": "table"},
        ]
        base_state["summary"] = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                result = await generate_analysis_report_node(base_state)

        report = result["final_report"]
        assert "Мощность блока питания: 1400 Вт" in report

    @pytest.mark.asyncio
    async def test_report_contains_summary(self, base_state):
        base_state["doc_type"] = "tz"
        base_state["doc_metadata"] = {"pages": 5, "chars": 5000, "tables_count": 0, "format": "PDF"}
        base_state["items"] = []
        base_state["summary"] = "- Срок поставки: 10 дней\n- Гарантия: 12 месяцев"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                result = await generate_analysis_report_node(base_state)

        report = result["final_report"]
        assert "Ключевые требования" in report
        assert "10 дней" in report

    @pytest.mark.asyncio
    async def test_report_empty_items(self, base_state):
        base_state["doc_type"] = "other"
        base_state["doc_metadata"] = {"pages": 1, "chars": 100, "tables_count": 0, "format": "TXT"}
        base_state["items"] = []
        base_state["summary"] = "Текст"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                result = await generate_analysis_report_node(base_state)

        report = result["final_report"]
        assert "не обнаружены" in report.lower() or "Позиции оборудования" in report

    @pytest.mark.asyncio
    async def test_report_file_saved(self, base_state):
        base_state["doc_type"] = "tz"
        base_state["doc_metadata"] = {"pages": 5, "chars": 5000, "tables_count": 0, "format": "PDF"}
        base_state["items"] = []
        base_state["summary"] = "Сводка"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                result = await generate_analysis_report_node(base_state)

            report = result["final_report"]
            assert "Отчет сохранен" in report
            # Проверяем что файл реально создан
            files = os.listdir(tmpdir)
            assert any(f.startswith("Report_Analysis_") for f in files)

    @pytest.mark.asyncio
    async def test_report_uses_legal_label(self, base_state):
        base_state["doc_type"] = "legal"
        base_state["doc_metadata"] = {"pages": 10, "chars": 10000, "tables_count": 0, "format": "PDF"}
        base_state["items"] = []
        base_state["summary"] = "Нормативный акт"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                result = await generate_analysis_report_node(base_state)

        report = result["final_report"]
        assert "Тип:** Юридический / нормативный документ" in report
        assert "Тип:** Договор/Контракт" not in report

    @pytest.mark.asyncio
    async def test_report_dedup(self, base_state):
        base_state["doc_type"] = "tz"
        base_state["doc_name"] = "test.pdf"
        base_state["doc_metadata"] = {"pages": 5, "chars": 5000, "tables_count": 0, "format": "PDF"}
        base_state["items"] = []
        base_state["summary"] = "Сводка"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
                # Первый вызов — сохраняет
                result1 = await generate_analysis_report_node(base_state)
                assert "Отчет сохранен" in result1["final_report"]

                # Второй вызов (в течение 60с) — дедуплицируется
                result2 = await generate_analysis_report_node(base_state)
                assert "уже сохранен" in result2["final_report"]


# ============================================================================
# Tests: create_analysis_graph
# ============================================================================

class TestCreateAnalysisGraph:
    def test_graph_compiles(self):
        graph = create_analysis_graph()
        assert graph is not None

    @pytest.mark.asyncio
    async def test_full_run_mocked(self, base_state, tz_text):
        with patch("orchestrator.workflows.document_analysis.classify_and_load_node", new_callable=AsyncMock) as mock_classify, \
             patch("orchestrator.workflows.document_analysis.extract_positions_node", new_callable=AsyncMock) as mock_extract, \
             patch("orchestrator.workflows.document_analysis.summarize_node", new_callable=AsyncMock) as mock_summarize, \
             patch("orchestrator.workflows.document_analysis.generate_analysis_report_node", new_callable=AsyncMock) as mock_report:

            mock_classify.return_value = {
                "doc_name": "test.pdf",
                "doc_type": "tz",
                "doc_metadata": {"pages": 5, "chars": 5000, "tables_count": 1, "format": "PDF"},
                "full_text": tz_text,
                "errors": [],
            }
            mock_extract.return_value = {
                "items": [{"name": "Сервер", "specs": "test", "quantity": "1", "price": "", "source": "table"}],
                "errors": [],
            }
            mock_summarize.return_value = {
                "summary": "Тестовая сводка",
                "errors": [],
            }
            mock_report.return_value = {
                "final_report": "# Тестовый отчёт",
            }

            graph = create_analysis_graph()
            final_state = {}
            async for event in graph.astream(base_state):
                for node_name, output in event.items():
                    final_state.update(output)

            assert final_state.get("final_report") == "# Тестовый отчёт"
            assert mock_classify.called
            assert mock_extract.called
            assert mock_summarize.called
            assert mock_report.called


# ============================================================================
# Tests: Intent Detection (document_analysis)
# ============================================================================

class TestDocumentAnalysisIntent:
    """Тесты intent detection для document_analysis в chainlit_app.

    chainlit_app.py импортирует chainlit (недоступен в тесте),
    поэтому мокаем модуль chainlit перед импортом.
    """

    @pytest.fixture(autouse=True)
    def _setup_chainlit_mock(self):
        """Мокаем chainlit модуль и импортируем _detect_intent."""
        import importlib

        # Мок chainlit
        mock_cl = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = None  # Нет rag_pipeline
        mock_cl.user_session = mock_session
        mock_cl.Message = MagicMock()
        mock_cl.AskActionMessage = MagicMock()
        mock_cl.Action = MagicMock()
        mock_cl.Step = MagicMock()
        mock_cl.User = MagicMock()
        mock_cl.on_chat_start = lambda f: f
        mock_cl.on_chat_resume = lambda f: f
        mock_cl.on_message = lambda f: f
        mock_cl.password_auth_callback = lambda f: f
        mock_cl.data_layer = lambda f: f

        # Мок chainlit.data.sql_alchemy
        mock_sql_alchemy = MagicMock()
        sys.modules["chainlit"] = mock_cl
        sys.modules["chainlit.data"] = MagicMock()
        sys.modules["chainlit.data.sql_alchemy"] = mock_sql_alchemy

        # Перезагружаем chainlit_app с мокнутым chainlit
        if "orchestrator.chainlit_app" in sys.modules:
            importlib.reload(sys.modules["orchestrator.chainlit_app"])
        else:
            import orchestrator.chainlit_app
        chainlit_app = sys.modules["orchestrator.chainlit_app"]

        from orchestrator.chainlit_app import (
            _get_intent_decision,
            _needs_doc_question_regen,
            _resolve_target_doc_name,
            _extract_citation_ids,
            _citations_are_valid,
            _has_sufficient_evidence,
            _build_doc_question_deterministic_fallback,
            _compute_confidence_v1,
            _build_sources_from_rag_result,
            _strip_model_source_sections,
            _register_loaded_document,
            _get_active_docs,
            _get_active_doc_ids,
            _set_active_doc_ids,
            _get_session_docs,
        )
        from orchestrator.orchestration_runtime import (
            build_route_choice_state,
            detect_intent,
            is_docs_summary_query,
            is_social_query,
            resolve_pending_action_selection,
        )

        def _detect_intent_wrapper(
            query: str,
            *,
            file_count: int = 0,
            has_session_docs: bool = False,
            classifier_result: Optional[dict] = None,
        ) -> str:
            session_docs = chainlit_app._get_session_docs() if has_session_docs else {}
            return detect_intent(
                query,
                file_count=file_count,
                has_session_docs=has_session_docs,
                active_docs_count=len(session_docs),
                classifier_result=classifier_result,
            )

        def _build_route_choice_state_wrapper(
            *,
            query: str,
            recommended_route: str,
            new_files: list,
            mode: str,
        ) -> dict:
            return build_route_choice_state(
                query=query,
                recommended_route=recommended_route,
                new_files=new_files,
                mode=mode,
                trace_id=mock_session.get("request_trace_id"),
                active_doc_ids=chainlit_app._get_active_doc_ids(),
            )

        self._detect_intent = _detect_intent_wrapper
        self._get_intent_decision = _get_intent_decision
        self._resolve_pending_route_choice = resolve_pending_action_selection
        self._is_social_query = is_social_query
        self._is_docs_summary_query = is_docs_summary_query
        self._needs_doc_question_regen = _needs_doc_question_regen
        self._resolve_target_doc_name = _resolve_target_doc_name
        self._extract_citation_ids = _extract_citation_ids
        self._citations_are_valid = _citations_are_valid
        self._has_sufficient_evidence = _has_sufficient_evidence
        self._build_doc_question_deterministic_fallback = _build_doc_question_deterministic_fallback
        self._compute_confidence_v1 = _compute_confidence_v1
        self._build_sources_from_rag_result = _build_sources_from_rag_result
        self._strip_model_source_sections = _strip_model_source_sections
        self._register_loaded_document = _register_loaded_document
        self._get_active_docs = _get_active_docs
        self._set_active_doc_ids = _set_active_doc_ids
        self._get_session_docs = _get_session_docs
        self._build_route_choice_state = _build_route_choice_state_wrapper
        self._mock_session = mock_session
        self._mock_cl = mock_cl

        yield

        # Cleanup
        for mod_name in ["chainlit", "chainlit.data", "chainlit.data.sql_alchemy"]:
            sys.modules.pop(mod_name, None)
        sys.modules.pop("orchestrator.chainlit_app", None)

    def test_single_file_analysis_keyword(self):
        result = self._detect_intent("Проанализируй этот документ", file_count=1)
        assert result == "document_analysis"

    def test_single_file_analysis_keyword_содержание(self):
        result = self._detect_intent("Покажи документ", file_count=1)
        assert result == "document_analysis"

    def test_single_file_no_keyword(self):
        """1 файл без analysis keyword → НЕ document_analysis."""
        result = self._detect_intent("что написано в файле", file_count=1)
        assert result != "document_analysis"

    def test_two_files_analysis_keyword(self):
        """2 файла + analysis keyword → НЕ document_analysis."""
        result = self._detect_intent("Проанализируй документы", file_count=2)
        assert result != "document_analysis"

    def test_session_doc_analysis(self):
        """1 doc в сессии + analysis keyword → document_analysis."""
        with patch("orchestrator.chainlit_app._get_session_docs",
                    return_value={"doc1.pdf": {"text": "test"}}):
            result = self._detect_intent("Проанализируй", file_count=0, has_session_docs=True)

        assert result == "document_analysis"

    def test_two_docs_tz_kp_query_prefers_equipment_analysis(self):
        session_docs = {
            "Quotation_12.pdf": {"text": "Коммерческое предложение на поставку оборудования"},
            "f5.pdf": {"text": "Техническое задание на поставку серверного оборудования"},
        }
        classifier_result = {
            "intent": "document_question",
            "confidence": 0.39,
            "margin": 0.115,
            "needs_rag": True,
        }

        with patch("orchestrator.chainlit_app._detect_equipment_mode", return_value="tz_vs_smeta"):
            result = self._get_intent_decision(
                "Есть тз и коммерческое предложение. Что нам подходит из коммерческого предложения?",
                file_count=2,
                has_session_docs=True,
                session_docs=session_docs,
                classifier_result=classifier_result,
            )

        assert result["intent"] == "equipment_analysis"
        assert result["requires_choice"] is True
        assert result["recommended_route"] == "equipment_analysis"

    def test_two_docs_low_margin_compare_becomes_choice(self):
        session_docs = {
            "old.pdf": {"text": "Старая редакция договора"},
            "new.pdf": {"text": "Новая редакция договора"},
        }
        classifier_result = {
            "intent": "general_chat",
            "confidence": 0.44,
            "margin": 0.005,
            "needs_rag": False,
        }

        result = self._get_intent_decision(
            "Сравни эти два документа",
            file_count=2,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )

        assert result["intent"] == "compare_documents"
        assert result["requires_choice"] is True
        assert result["recommended_route"] == "compare_documents"

    def test_two_docs_low_margin_without_compare_keyword_still_choice(self):
        session_docs = {
            "old.pdf": {"text": "Старая редакция договора"},
            "new.pdf": {"text": "Новая редакция договора"},
        }
        classifier_result = {
            "intent": "document_question",
            "confidence": 0.53,
            "margin": 0.01,
            "needs_rag": True,
        }

        result = self._get_intent_decision(
            "По этим двум документам дай общий вывод",
            file_count=2,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )

        assert result["requires_choice"] is True
        assert result["recommended_route"] in {"compare_documents", "document_question"}

    def test_document_question_stays_document_question_without_equipment_signal(self):
        session_docs = {
            "doc1.pdf": {"text": "Гарантийные обязательства"},
            "doc2.pdf": {"text": "Сроки поставки"},
        }
        classifier_result = {
            "intent": "document_question",
            "confidence": 0.71,
            "margin": 0.20,
            "needs_rag": True,
        }

        result = self._get_intent_decision(
            "Что написано в документах про гарантию?",
            file_count=2,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )

        assert result["intent"] == "document_question"
        assert result["requires_choice"] is False

    def test_docs_summary_query_routes_to_documents_summary(self):
        session_docs = {
            "doc1.pdf": {"text": "Текст 1"},
            "doc2.pdf": {"text": "Текст 2"},
        }
        classifier_result = {
            "intent": "document_question",
            "confidence": 0.66,
            "margin": 0.09,
            "needs_rag": True,
        }
        result = self._get_intent_decision(
            "О чём эти документы?",
            file_count=2,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )
        assert result["intent"] == "documents_summary"
        assert result["requires_choice"] is False

    def test_analyze_two_docs_query_routes_to_documents_summary(self):
        session_docs = {
            "contract_v1.pdf": {"text": "Договор версия 1"},
            "contract_v2.pdf": {"text": "Договор версия 2"},
        }
        classifier_result = {
            "intent": "document_analysis",
            "confidence": 0.64,
            "margin": 0.05,
            "needs_rag": True,
        }
        result = self._get_intent_decision(
            "Проанализируй эти документы",
            file_count=2,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )
        assert result["intent"] == "documents_summary"
        assert result["requires_choice"] is False

    def test_classifier_doc_question_without_docs_requests_upload(self):
        classifier_result = {
            "intent": "document_question",
            "confidence": 0.70,
            "margin": 0.22,
            "needs_rag": True,
        }
        result = self._get_intent_decision(
            "А об обычных облаках на небе?",
            file_count=0,
            has_session_docs=False,
            session_docs={},
            classifier_result=classifier_result,
        )
        assert result["intent"] == "document_question"
        assert result["requires_choice"] is False
        assert result["reason"] == "missing_documents"
        assert result["action_required"]["type"] == "upload_required"

    def test_document_analysis_with_two_docs_requires_choice(self):
        session_docs = {
            "doc1.pdf": {"text": "Первый документ"},
            "doc2.pdf": {"text": "Второй документ"},
        }
        classifier_result = {
            "intent": "document_analysis",
            "confidence": 0.81,
            "margin": 0.30,
            "needs_rag": True,
        }
        result = self._get_intent_decision(
            "Сделай анализ документа",
            file_count=2,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )
        assert result["requires_choice"] is True
        assert result["reason"] == "document_analysis_multi_doc"

    def test_resolve_pending_route_choice(self):
        pending = {
            "expires_at": time.time() + 60,
            "choices": {"1": "equipment_analysis", "2": "compare_documents", "3": "document_question"},
        }
        assert self._resolve_pending_route_choice("1", pending) == "equipment_analysis"
        assert self._resolve_pending_route_choice("отмена", pending) == "cancel"
        assert self._resolve_pending_route_choice("9", pending) is None

    def test_resolve_pending_route_choice_expired(self):
        pending = {
            "expires_at": time.time() - 1,
            "choices": {"1": "equipment_analysis"},
        }
        assert self._resolve_pending_route_choice("1", pending) is None

    def test_is_social_query_positive_and_negative(self):
        assert self._is_social_query("Спасибо!") is True
        assert self._is_social_query("Окей") is True
        assert self._is_social_query("Спасибо, сравни документы") is False

    def test_social_guard_with_active_docs(self):
        session_docs = {
            "Quotation_12.pdf": {"text": "Коммерческое предложение"},
            "f5.pdf": {"text": "Техническое задание"},
        }
        classifier_result = {
            "intent": "greeting",
            "confidence": 0.72,
            "margin": 0.28,
            "needs_rag": False,
        }
        result = self._get_intent_decision(
            "Спасибо",
            file_count=0,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )

        assert result["intent"] == "greeting"
        assert result["requires_choice"] is False
        assert result["reason"] == "social_guard"

    def test_social_guard_without_classifier(self):
        session_docs = {
            "Quotation_12.pdf": {"text": "Коммерческое предложение"},
        }
        result = self._get_intent_decision(
            "Привет",
            file_count=0,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=None,
        )
        assert result["intent"] == "greeting"
        assert result["reason"] == "social_guard"

    def test_doc_question_regen_detection_when_docs_loaded(self):
        text = "Пожалуйста, предоставьте тексты ТЗ и коммерческого предложения для анализа."
        assert self._needs_doc_question_regen(text, has_session_docs=True) is True

    def test_doc_question_regen_not_required_without_docs(self):
        text = "Пожалуйста, предоставьте тексты ТЗ и коммерческого предложения для анализа."
        assert self._needs_doc_question_regen(text, has_session_docs=False) is False

    def test_doc_question_regen_detects_clarification_pattern(self):
        text = (
            "Не могу предоставить точный ответ. "
            "Пожалуйста, уточните требования ТЗ и содержание коммерческого предложения."
        )
        assert self._needs_doc_question_regen(text, has_session_docs=True) is True

    def test_resolve_target_doc_name_by_filename_stem(self):
        active_docs = [
            {"display_name": "Quotation_12.pdf", "version": 1},
            {"display_name": "H12300274_1688590800.pdf", "version": 1},
        ]
        target = self._resolve_target_doc_name(
            "В документе quotation_12 какие есть позиции?",
            active_docs,
        )
        assert target == "Quotation_12.pdf"

    def test_register_same_filename_creates_new_version(self):
        store = {}
        self._mock_session.get.side_effect = lambda key: store.get(key)
        self._mock_session.set.side_effect = lambda key, value: store.__setitem__(key, value)

        first = self._register_loaded_document(
            display_name="Quotation_12.pdf",
            path="/tmp/q1.pdf",
            text="v1",
            source_message_id="m1",
        )
        second = self._register_loaded_document(
            display_name="Quotation_12.pdf",
            path="/tmp/q2.pdf",
            text="v2",
            source_message_id="m2",
        )

        assert first["version"] == 1
        assert second["version"] == 2
        assert first["document_id"] != second["document_id"]
        legacy_docs = self._get_session_docs()
        assert legacy_docs["Quotation_12.pdf"]["version"] == 2

    def test_route_choice_state_keeps_active_order_and_id(self):
        store = {}
        self._mock_session.get.side_effect = lambda key: store.get(key)
        self._mock_session.set.side_effect = lambda key, value: store.__setitem__(key, value)

        first = self._register_loaded_document(
            display_name="doc_a.pdf",
            path="/tmp/a.pdf",
            text="A",
            source_message_id="m1",
        )
        second = self._register_loaded_document(
            display_name="doc_b.pdf",
            path="/tmp/b.pdf",
            text="B",
            source_message_id="m2",
        )

        ordered = [second["document_id"], first["document_id"]]
        self._set_active_doc_ids(ordered)
        state = self._build_route_choice_state(
            query="Сравни документы",
            recommended_route="compare_documents",
            new_files=[],
            mode="unknown",
        )

        assert state["active_doc_ids"] == ordered
        assert isinstance(state["route_choice_id"], str)
        assert len(state["route_choice_id"]) >= 6
        assert "origin_trace_id" in state
        assert state["choices"]["4"] == "documents_summary"

    def test_route_choice_state_keeps_active_docs_order_and_id(self):
        store = {}
        self._mock_session.get.side_effect = lambda key: store.get(key)
        self._mock_session.set.side_effect = lambda key, value: store.__setitem__(key, value)

        a = self._register_loaded_document(
            display_name="A.pdf",
            path="/tmp/a.pdf",
            text="a",
            source_message_id="m1",
        )
        b = self._register_loaded_document(
            display_name="B.pdf",
            path="/tmp/b.pdf",
            text="b",
            source_message_id="m2",
        )
        self._set_active_doc_ids([b["document_id"], a["document_id"]])

        state = self._build_route_choice_state(
            query="Сравни документы",
            recommended_route="compare_documents",
            new_files=[],
            mode="unknown",
        )
        assert state["active_doc_ids"] == [b["document_id"], a["document_id"]]
        assert state["route_choice_id"]
        assert state["choices"]["4"] == "documents_summary"

    def test_compare_low_confidence_without_equipment_signal_uses_unknown_mode(self):
        session_docs = {
            "C222.pdf": {"text": "Постановление, юридический текст"},
            "C221.pdf": {"text": "Постановление, юридический текст"},
        }
        classifier_result = {
            "intent": "compare_documents",
            "confidence": 0.62,
            "margin": 0.01,
            "needs_rag": True,
        }
        result = self._get_intent_decision(
            "Сравни эти документы юридические",
            file_count=2,
            has_session_docs=True,
            session_docs=session_docs,
            classifier_result=classifier_result,
        )
        assert result["requires_choice"] is True
        assert result["mode"] == "unknown"

    def test_extract_citation_ids(self):
        cited = self._extract_citation_ids("Ответ [1] и [3], но не [x]")
        assert cited == [1, 3]

    def test_strip_model_source_sections(self):
        text = (
            "Это основной ответ [1].\n\n"
            "### Источники\n"
            "- [1] foo\n\n"
            "### Надёжность\n"
            "- confidence: 0.8"
        )
        cleaned = self._strip_model_source_sections(text)
        assert cleaned.strip() == "Это основной ответ [1]."

    def test_citations_are_valid(self):
        assert self._citations_are_valid("Ответ [1][2]", source_count=2) is True
        assert self._citations_are_valid("Ответ [3]", source_count=2) is False
        assert self._citations_are_valid("Ответ без ссылок", source_count=2) is False

    def test_has_sufficient_evidence_simple_mode(self):
        sources = [
            {
                "source_id": 1,
                "document_id": "a.pdf",
                "chunk_id": 0,
                "char_span": {"start_char": 0, "end_char": 100},
                "page": None,
                "quote": "test",
                "raw_score": 0.2,
                "normalized_score": 1.0,
                "grade": None,
                "z_score": None,
            }
        ]
        assert self._has_sufficient_evidence(sources, mode="simple", query="Что написано?", citations_valid=True) is True

    def test_has_sufficient_evidence_corrective_uses_zscore(self):
        sources = [
            {
                "source_id": 1,
                "document_id": "a.pdf",
                "chunk_id": 0,
                "char_span": {"start_char": 0, "end_char": 100},
                "page": None,
                "quote": "test",
                "raw_score": 0.01,
                "normalized_score": 1.0,
                "grade": "poor",
                "z_score": -1.2,
            }
        ]
        assert self._has_sufficient_evidence(sources, mode="corrective", query="Что написано?", citations_valid=True) is False

    def test_has_sufficient_evidence_multihop_requires_multiple_citations(self):
        sources = [
            {
                "source_id": 1,
                "document_id": "a.pdf",
                "display_name": "a.pdf",
                "chunk_id": 0,
                "char_span": {"start_char": 0, "end_char": 100},
                "page": None,
                "quote": "Уведомление за 10 дней",
                "raw_score": 0.6,
                "normalized_score": 0.9,
                "grade": "good",
                "z_score": 0.5,
            },
            {
                "source_id": 2,
                "document_id": "b.pdf",
                "display_name": "b.pdf",
                "chunk_id": 1,
                "char_span": {"start_char": 101, "end_char": 200},
                "page": None,
                "quote": "Штраф 10 процентов",
                "raw_score": 0.55,
                "normalized_score": 0.88,
                "grade": "good",
                "z_score": 0.4,
            },
        ]
        assert (
            self._has_sufficient_evidence(
                sources,
                mode="simple",
                query="Сравни условия уведомления и штрафа между документами",
                citations_valid=True,
                cited_ids=[1],
            )
            is False
        )
        assert (
            self._has_sufficient_evidence(
                sources,
                mode="simple",
                query="Сравни условия уведомления и штрафа между документами",
                citations_valid=True,
                cited_ids=[1, 2],
            )
            is True
        )

    def test_deterministic_fallback_payload(self):
        sources = [
            {
                "source_id": 1,
                "document_id": "a.pdf",
                "chunk_id": 0,
                "char_span": {"start_char": 0, "end_char": 100},
                "page": None,
                "quote": "Короткая цитата",
                "raw_score": 0.5,
                "normalized_score": 1.0,
                "grade": "good",
                "z_score": 0.3,
            }
        ]
        payload = self._build_doc_question_deterministic_fallback("запрос", sources, "insufficient_evidence")
        assert payload["answer_mode"] == "insufficient_evidence"
        assert payload["fallback_type"] == "insufficient_evidence"
        assert payload["confidence_method"] == "heuristic_v1"
        assert payload["confidence_version"] == "1"

    def test_confidence_v1_low_for_insufficient(self):
        sources = [
            {
                "source_id": 1,
                "document_id": "a.pdf",
                "chunk_id": 0,
                "char_span": {"start_char": 0, "end_char": 100},
                "page": None,
                "quote": "Короткая цитата",
                "raw_score": 0.7,
                "normalized_score": 1.0,
                "grade": "excellent",
                "z_score": 1.2,
            }
        ]
        confidence, label = self._compute_confidence_v1(sources, [1], "insufficient_evidence")
        assert confidence <= 0.35
        assert label == "low"

    def test_confidence_v1_penalizes_multihop_single_citation(self):
        sources = [
            {
                "source_id": 1,
                "document_id": "a.pdf",
                "display_name": "a.pdf",
                "chunk_id": 0,
                "char_span": {"start_char": 0, "end_char": 100},
                "page": None,
                "quote": "Уведомление за 10 дней",
                "raw_score": 0.72,
                "normalized_score": 0.95,
                "grade": "excellent",
                "z_score": 1.0,
            },
            {
                "source_id": 2,
                "document_id": "b.pdf",
                "display_name": "b.pdf",
                "chunk_id": 1,
                "char_span": {"start_char": 101, "end_char": 200},
                "page": None,
                "quote": "Штраф 10 процентов",
                "raw_score": 0.69,
                "normalized_score": 0.92,
                "grade": "excellent",
                "z_score": 0.9,
            },
        ]
        low_confidence, low_label = self._compute_confidence_v1(
            sources,
            [1],
            "grounded_answer",
            query="Сравни условия уведомления и штрафа между документами",
        )
        high_confidence, high_label = self._compute_confidence_v1(
            sources,
            [1, 2],
            "grounded_answer",
            query="Сравни условия уведомления и штрафа между документами",
        )
        assert high_confidence > low_confidence
        assert low_label in {"low", "medium"}
        assert high_label in {"medium", "high"}

    def test_build_sources_from_rag_result(self):
        class DummyRetrieval:
            def __init__(self, text, score, index, metadata=None):
                self.text = text
                self.score = score
                self.index = index
                self.metadata = metadata or {}

        class DummyChunk:
            def __init__(self, start_char, end_char, metadata):
                self.start_char = start_char
                self.end_char = end_char
                self.metadata = metadata

        class DummyResult:
            def __init__(self, chunks):
                self.chunks = chunks

        class DummyRag:
            def __init__(self, chunks):
                self._chunks = chunks

        rag_result = DummyResult(
            [DummyRetrieval("Фрагмент А", 0.1, 0, {"grade": "good", "z_score": 0.2})]
        )
        rag = DummyRag([DummyChunk(10, 50, {"doc_name": "docA.pdf"})])
        sources = self._build_sources_from_rag_result(rag_result, rag)
        assert len(sources) == 1
        assert sources[0]["document_id"] == "docA.pdf"
        assert sources[0]["char_span"]["start_char"] == 10
