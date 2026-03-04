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

    @pytest.mark.asyncio
    async def test_summarize_empty_text(self, base_state):
        base_state["full_text"] = ""
        base_state["doc_type"] = "tz"

        result = await summarize_node(base_state)
        assert "пуст" in result["summary"].lower()


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

        from orchestrator.chainlit_app import _detect_intent
        self._detect_intent = _detect_intent
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
