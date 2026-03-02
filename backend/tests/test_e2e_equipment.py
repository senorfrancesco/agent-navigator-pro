"""
E2E тесты — реальный PDF ТЗ + синтетическая смета.

Требования:
- Реальный PDF: /home/seral/HDD/proj/dev_1_conda/documents/f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf
- Запущенные микросервисы: Document Server (8001), UMS (8090), Legal Server (8002)
- Marker: @pytest.mark.integration

Покрытие:
- Извлечение таблиц из реального PDF
- LLM-экстракция с map-reduce чанкингом
- Полный workflow сравнения ТЗ vs синтетическая смета
- Полный workflow анализа одного документа
"""

import os
import sys
import pytest
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Реальный PDF
PDF_PATH = "/home/seral/HDD/proj/dev_1_conda/documents/f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf"
PDF_EXISTS = os.path.exists(PDF_PATH)

# Синтетическая смета — 8 позиций с частичным совпадением ТЗ
SYNTHETIC_SMETA_ITEMS = [
    {"№": 1, "Наименование": "Сервер HP ProLiant DL380 Gen10",
     "Характеристики": "2x Intel Xeon Silver 4214R, 64GB DDR4, 2x SSD 960GB SATA",
     "Количество": 2, "Ед.изм.": "шт", "Цена": 450000, "Сумма": 900000},
    {"№": 2, "Наименование": "Ноутбук MSI Sword 17 HX B14V",
     "Характеристики": "Intel Core i7-14700HX, 16GB DDR5, 512GB SSD, 17.3\" FHD",
     "Количество": 5, "Ед.изм.": "шт", "Цена": 95000, "Сумма": 475000},
    {"№": 3, "Наименование": "Монитор Dell U2723QE",
     "Характеристики": "27\", 4K UHD, IPS, USB-C, 90W PD",
     "Количество": 10, "Ед.изм.": "шт", "Цена": 45000, "Сумма": 450000},
    {"№": 4, "Наименование": "Док-станция ATEN UH3236",
     "Характеристики": "USB-C, HDMI 2.0, DP 1.4, GbE, USB 3.2",
     "Количество": 10, "Ед.изм.": "шт", "Цена": 12000, "Сумма": 120000},
    {"№": 5, "Наименование": "ИБП APC Smart-UPS 3000VA",
     "Характеристики": "SMT3000RMI2U, 2700W, LCD, Rack 2U",
     "Количество": 2, "Ед.изм.": "шт", "Цена": 85000, "Сумма": 170000},
    {"№": 6, "Наименование": "Коммутатор Cisco Catalyst 9200L-24P",
     "Характеристики": "24 порта GbE PoE+, 4x 10G SFP+, Network Essentials",
     "Количество": 1, "Ед.изм.": "шт", "Цена": 180000, "Сумма": 180000},
    {"№": 7, "Наименование": "Принтер HP LaserJet Pro M404dn",
     "Характеристики": "A4, дуплекс, 38 стр/мин, Ethernet",
     "Количество": 3, "Ед.изм.": "шт", "Цена": 25000, "Сумма": 75000},
    {"№": 8, "Наименование": "Кабель патч-корд Cat6 UTP 3м",
     "Характеристики": "RJ45, LSZH, 568B",
     "Количество": 50, "Ед.изм.": "шт", "Цена": 200, "Сумма": 10000},
]


def _create_synthetic_smeta_xlsx(tmpdir: str) -> str:
    """Создаёт временный XLSX файл с синтетической сметой."""
    try:
        import openpyxl
    except ImportError:
        pytest.skip("openpyxl not installed")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Смета"

    # Заголовок
    ws.append(["Коммерческое предложение на поставку оборудования"])
    ws.append([])

    # Шапка таблицы
    headers = ["№", "Наименование", "Характеристики", "Количество", "Ед.изм.", "Цена", "Сумма"]
    ws.append(headers)

    # Данные
    for item in SYNTHETIC_SMETA_ITEMS:
        ws.append([item[h] for h in headers])

    # Итого
    total = sum(item["Сумма"] for item in SYNTHETIC_SMETA_ITEMS)
    ws.append([])
    ws.append(["", "ИТОГО", "", "", "", "", total])

    filepath = os.path.join(tmpdir, "smeta_synthetic.xlsx")
    wb.save(filepath)
    return filepath


# ============================================================================
# E2E: Equipment Workflow
# ============================================================================

@pytest.mark.integration
@pytest.mark.skipif(not PDF_EXISTS, reason=f"Real PDF not found: {PDF_PATH}")
class TestE2EEquipment:

    @pytest.mark.asyncio
    async def test_extract_tables_from_real_pdf(self):
        """Извлечение таблиц из реального PDF ТЗ через Document Server."""
        import httpx
        from orchestrator.workflows.equipment import _extract_tables_from_doc

        async with httpx.AsyncClient(timeout=60.0) as client:
            items = await _extract_tables_from_doc(client, PDF_PATH)

        print(f"[E2E] Extracted {len(items)} items from tables")
        for it in items[:5]:
            print(f"  - {it['name'][:60]} | {it.get('specs', '')[:40]}")

        assert len(items) >= 5, f"Expected >= 5 items from tables, got {len(items)}"

    @pytest.mark.asyncio
    async def test_extract_llm_chunked_real_pdf(self):
        """LLM-экстракция с map-reduce чанкингом из реального PDF."""
        import httpx
        from orchestrator.workflows.equipment import _extract_items_llm, _chunk_text

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Сначала загружаем текст чтобы проверить чанкинг
            doc_url = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
            resp = await client.post(f"{doc_url}/load_document", json={"path": PDF_PATH})
            resp.raise_for_status()
            text = resp.json().get("text", "")
            assert len(text) > 1000, f"PDF text too short: {len(text)} chars"

            chunks = await _chunk_text(client, text)
            print(f"[E2E] Text: {len(text)} chars → {len(chunks)} chunks")
            assert len(chunks) >= 3, f"Expected >= 3 chunks for {len(text)} chars, got {len(chunks)}"

            # LLM-экстракция
            items = await _extract_items_llm(client, PDF_PATH, [])

        print(f"[E2E] LLM extracted {len(items)} items")
        for it in items[:5]:
            print(f"  - {it['name'][:60]}")

        assert 5 <= len(items) <= 30, f"Expected 5-30 items, got {len(items)}"

    @pytest.mark.asyncio
    async def test_full_comparison_workflow(self):
        """Полный workflow: реальный PDF ТЗ vs синтетическая смета XLSX."""
        from orchestrator.workflows.equipment import create_equipment_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            smeta_path = _create_synthetic_smeta_xlsx(tmpdir)

            workflow = create_equipment_graph()
            initial_state = {
                "input_1": PDF_PATH,
                "input_2": smeta_path,
                "name_1": "ТЗ_реальный.pdf",
                "name_2": "smeta_synthetic.xlsx",
                "mode": "tz_vs_smeta",
                "items_1": [],
                "items_2": [],
                "matches": [],
                "analysis_results": [],
                "final_report": "",
                "errors": [],
                "session_id": "",
            }

            t_start = time.perf_counter()
            final_state = {}
            async for event in workflow.astream(initial_state):
                for node_name, output in event.items():
                    final_state.update(output)
                    print(f"[E2E] Node '{node_name}' completed")

            elapsed = time.perf_counter() - t_start
            print(f"[E2E] Full workflow completed in {elapsed:.1f}s")

            # Проверки
            report = final_state.get("final_report", "")
            assert report, "Report should not be empty"
            assert "Анализ соответствия" in report or "Сравнение" in report

            items_1 = final_state.get("items_1", [])
            items_2 = final_state.get("items_2", [])
            print(f"[E2E] Items: TZ={len(items_1)}, Smeta={len(items_2)}")

            assert len(items_1) >= 3, f"TZ should have >= 3 items, got {len(items_1)}"
            assert len(items_2) >= 5, f"Smeta should have >= 5 items, got {len(items_2)}"

            results = final_state.get("analysis_results", [])
            print(f"[E2E] Analysis results: {len(results)}")
            assert len(results) > 0, "Should have analysis results"

            # Проверяем что есть разные вердикты (PASS, FAIL, GAP, PARTIAL)
            verdicts = {r.get("result") for r in results}
            print(f"[E2E] Verdicts: {verdicts}")
            assert len(verdicts) >= 2, f"Expected diverse verdicts, got {verdicts}"


# ============================================================================
# E2E: Document Analysis Workflow
# ============================================================================

@pytest.mark.integration
@pytest.mark.skipif(not PDF_EXISTS, reason=f"Real PDF not found: {PDF_PATH}")
class TestE2EDocumentAnalysis:

    @pytest.mark.asyncio
    async def test_single_doc_analysis_real_pdf(self):
        """Полный workflow анализа одного документа на реальном PDF ТЗ."""
        from orchestrator.workflows.document_analysis import create_analysis_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("UPLOADS_DIR", tmpdir)

                workflow = create_analysis_graph()
                initial_state = {
                    "input_path": PDF_PATH,
                    "doc_name": "ТЗ_реальный.pdf",
                    "doc_type": "",
                    "doc_metadata": {},
                    "items": [],
                    "full_text": "",
                    "summary": "",
                    "final_report": "",
                    "errors": [],
                }

                t_start = time.perf_counter()
                final_state = {}
                async for event in workflow.astream(initial_state):
                    for node_name, output in event.items():
                        final_state.update(output)
                        print(f"[E2E] Node '{node_name}' completed")

                elapsed = time.perf_counter() - t_start
                print(f"[E2E] Document analysis completed in {elapsed:.1f}s")

                # Тип документа
                doc_type = final_state.get("doc_type", "")
                print(f"[E2E] Doc type: {doc_type}")
                assert doc_type == "tz", f"Expected 'tz', got '{doc_type}'"

                # Позиции
                items = final_state.get("items", [])
                print(f"[E2E] Items: {len(items)}")
                for it in items[:5]:
                    print(f"  - {it['name'][:60]}")
                assert len(items) >= 5, f"Expected >= 5 items, got {len(items)}"

                # Сводка
                summary = final_state.get("summary", "")
                print(f"[E2E] Summary length: {len(summary)} chars")
                assert len(summary) > 50, f"Summary too short: {len(summary)} chars"

                # Отчёт
                report = final_state.get("final_report", "")
                assert report, "Report should not be empty"
                assert "Анализ документа" in report
                assert "Техническое задание" in report

                # Файл сохранён
                saved_files = [f for f in os.listdir(tmpdir) if f.startswith("Report_Analysis_")]
                assert len(saved_files) == 1, f"Expected 1 saved report, got {len(saved_files)}"
                print(f"[E2E] Report saved: {saved_files[0]}")

                # Метаданные
                meta = final_state.get("doc_metadata", {})
                assert meta.get("pages", 0) > 0, "Should have page count"
                assert meta.get("chars", 0) > 1000, "Should have substantial char count"
                print(f"[E2E] Meta: pages={meta.get('pages')}, chars={meta.get('chars')}, tables={meta.get('tables_count')}")
