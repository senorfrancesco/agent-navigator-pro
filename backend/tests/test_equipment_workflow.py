"""
Тесты для Equipment Workflow — хелперы, ноды, граф.

Покрытие:
- _detect_header_columns: детекция заголовков таблиц
- _parse_table_rows: парсинг строк таблиц в items
- _dedup_items: дедупликация позиций
- _item_to_text: конвертация item → текст для матчинга
- _route_after_extract: conditional edge routing
- load_and_extract_node: двухпроходная экстракция (mocked)
- match_items_node: семантический матчинг (mocked)
- evaluate_compliance_node: batch LLM evaluation (mocked)
- generate_equipment_report_node: генерация отчёта
- create_equipment_graph: полный граф (mocked e2e)
- Chainlit: _detect_equipment_mode
"""

import os
import sys
import pytest
import tempfile
import time
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

# Пути для импорта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.workflows.equipment import (
    _detect_header_columns,
    _parse_table_rows,
    _dedup_items,
    _normalize_spec_for_polish,
    _build_polish_source_specs,
    _build_polish_batches,
    _parse_polish_results_json,
    _polish_items_specs_llm,
    _item_to_text,
    _route_after_extract,
    truncate_text,
    detect_equipment_mode,
    load_and_extract_node,
    match_items_node,
    evaluate_compliance_node,
    generate_equipment_report_node,
    create_equipment_graph,
    EquipmentState,
    BATCH_SIZE,
    MAX_TEXT_FOR_LLM,
    _chunk_text,
    _split_by_lines,
    _extract_from_single_chunk,
    _extract_items_llm,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_table_data():
    """Типичная таблица оборудования из сметы."""
    return [
        ["№", "Наименование оборудования", "Характеристики", "Кол-во", "Ед.изм.", "Цена, руб."],
        ["1", "Коммутатор Cisco 2960", "48 портов, PoE+, 1Gbps", "10", "шт.", "45000"],
        ["2", "Сервер Dell PowerEdge R740", "2xXeon Gold, 128GB RAM", "3", "шт.", "350000"],
        ["3", "ИБП APC Smart-UPS 3000", "3000VA, rack-mount", "5", "шт.", "28000"],
        ["", "Итого", "", "", "", "1925000"],
    ]


@pytest.fixture
def sample_table_no_header():
    """Таблица без распознаваемого заголовка."""
    return [
        ["Abc", "Def", "Ghi"],
        ["1", "2", "3"],
    ]


@pytest.fixture
def sample_items_with_dupes():
    """Список items с дубликатами."""
    return [
        {"name": "Коммутатор Cisco 2960", "specs": "48 портов", "source": "table", "quantity": "10", "price": "45000", "unit": "шт.", "page": 1},
        {"name": "коммутатор cisco 2960", "specs": "48 портов, PoE", "source": "text", "quantity": "10", "price": "", "unit": "", "page": None},
        {"name": "Сервер Dell PowerEdge", "specs": "2xXeon", "source": "table", "quantity": "3", "price": "350000", "unit": "шт.", "page": 1},
        {"name": "ИБП APC Smart-UPS", "specs": "3000VA", "source": "text", "quantity": "5", "price": "28000", "unit": "", "page": None},
    ]


@pytest.fixture
def base_state():
    """Минимальный EquipmentState для тестов."""
    return {
        "input_1": "/tmp/test_tz.pdf",
        "input_2": "/tmp/test_smeta.pdf",
        "name_1": "ТЗ на оборудование.pdf",
        "name_2": "Смета КП.pdf",
        "mode": "tz_vs_smeta",
        "items_1": [],
        "items_2": [],
        "matches": [],
        "analysis_results": [],
        "final_report": "",
        "errors": [],
        "session_id": "test-session",
    }


@pytest.fixture
def state_with_items(base_state):
    """State с заполненными items."""
    base_state["items_1"] = [
        {"name": "Коммутатор Cisco 2960", "specs": "48 портов, PoE+", "quantity": "10", "price": "", "unit": "шт.", "source": "table", "page": 1},
        {"name": "Сервер Dell R740", "specs": "2xXeon Gold, 128GB", "quantity": "3", "price": "", "unit": "шт.", "source": "table", "page": 1},
    ]
    base_state["items_2"] = [
        {"name": "Cisco Catalyst 2960-X", "specs": "48 портов, PoE+, 1Gbps", "quantity": "10", "price": "45000", "unit": "шт.", "source": "table", "page": 1},
        {"name": "Dell PowerEdge R740xd", "specs": "2xXeon Gold 6230, 128GB DDR4", "quantity": "3", "price": "350000", "unit": "шт.", "source": "table", "page": 2},
        {"name": "ИБП APC Smart-UPS 3000", "specs": "3000VA", "quantity": "5", "price": "28000", "unit": "шт.", "source": "table", "page": 2},
    ]
    return base_state


# ============================================================================
# Tests: _detect_header_columns
# ============================================================================

class TestDetectHeaderColumns:

    def test_standard_header(self):
        row = ["№", "Наименование", "Характеристики", "Кол-во", "Ед.изм.", "Цена"]
        result = _detect_header_columns(row)
        assert "name" in result
        assert "specs" in result
        assert "quantity" in result
        assert "unit" in result
        assert "price" in result

    def test_minimal_header(self):
        row = ["Название товара", "Стоимость"]
        result = _detect_header_columns(row)
        assert "name" in result
        assert "price" in result
        assert len(result) >= 2

    def test_no_keywords(self):
        row = ["A", "B", "C"]
        result = _detect_header_columns(row)
        assert len(result) == 0

    def test_empty_cells(self):
        row = ["", "Наименование", "", "Цена"]
        result = _detect_header_columns(row)
        assert "name" in result
        assert "price" in result

    def test_case_insensitive(self):
        row = ["НАИМЕНОВАНИЕ ОБОРУДОВАНИЯ", "КОЛИЧЕСТВО", "ЦЕНА"]
        result = _detect_header_columns(row)
        assert "name" in result
        assert "quantity" in result
        assert "price" in result

    def test_partial_keywords(self):
        """Проверяем что 'характеристик' матчит 'Технические характеристики'."""
        row = ["Позиция", "Технические характеристики", "Объем"]
        result = _detect_header_columns(row)
        assert "name" in result  # "позиция" → name
        assert "specs" in result  # "характеристик" in "технические характеристики"

    def test_equipment_keyword(self):
        row = ["Оборудование", "Описание", "Кол.", "Сумма"]
        result = _detect_header_columns(row)
        assert "name" in result
        assert "specs" in result
        assert "quantity" in result
        assert "price" in result


# ============================================================================
# Tests: _parse_table_rows
# ============================================================================

class TestParseTableRows:

    def test_standard_table(self, sample_table_data):
        items, col_map = _parse_table_rows(sample_table_data)
        assert len(items) == 3  # 3 товара, "Итого" отфильтровано
        assert items[0]["name"] == "Коммутатор Cisco 2960"
        assert items[0]["quantity"] == "10"
        assert items[0]["price"] == "45000"
        assert items[0]["source"] == "table"
        assert "name" in col_map  # col_map should be returned

    def test_empty_table(self):
        items, col_map = _parse_table_rows([])
        assert items == []

    def test_single_row(self):
        items, col_map = _parse_table_rows([["Наименование", "Цена"]])
        assert items == []  # Только заголовок, нет данных

    def test_no_header(self, sample_table_no_header):
        items, col_map = _parse_table_rows(sample_table_no_header)
        assert items == []  # Заголовок не распознан

    def test_filters_totals(self, sample_table_data):
        """Строки 'Итого' фильтруются."""
        items, _ = _parse_table_rows(sample_table_data)
        names = [it["name"].lower() for it in items]
        assert not any("итого" in n for n in names)

    def test_filters_short_names(self):
        data = [
            ["Наименование", "Цена"],
            ["AB", "100"],  # Слишком короткое имя
            ["Коммутатор", "45000"],
        ]
        items, _ = _parse_table_rows(data)
        assert len(items) == 1
        assert items[0]["name"] == "Коммутатор"

    def test_page_info_passed(self, sample_table_data):
        items, _ = _parse_table_rows(sample_table_data, page_info=3)
        for item in items:
            assert item["page"] == 3

    def test_header_in_later_row(self):
        """Заголовок может быть не в первой строке (до 5 строк проверяем)."""
        data = [
            ["Приложение 1", "", "", ""],
            ["Спецификация оборудования", "", "", ""],
            ["Наименование", "Характеристики", "Кол-во", "Цена"],
            ["Принтер HP LaserJet", "A4, дуплекс", "5", "15000"],
        ]
        items, _ = _parse_table_rows(data)
        assert len(items) == 1
        assert items[0]["name"] == "Принтер HP LaserJet"

    def test_inherited_col_map(self):
        """Продолжение таблицы без заголовка с inherited_col_map."""
        # Первый фрагмент с заголовком
        header_table = [
            ["Наименование", "Характеристики", "Кол-во", "Цена"],
            ["Сервер HP", "2x Xeon", "2", "100000"],
        ]
        items1, col_map = _parse_table_rows(header_table)
        assert len(items1) == 1
        assert "name" in col_map

        # Второй фрагмент без заголовка — продолжение
        continuation = [
            ["Ноутбук Dell", "i7, 16GB", "5", "80000"],
            ["Монитор LG", "27, 4K", "10", "30000"],
        ]
        items2, _ = _parse_table_rows(continuation, inherited_col_map=col_map)
        assert len(items2) == 2
        assert items2[0]["name"] == "Ноутбук Dell"
        assert items2[1]["name"] == "Монитор LG"

    def test_tz_multiline_header_and_page_break_item(self):
        """ТЗ-таблица с двухстрочным заголовком и разрывом позиции между страницами."""
        data = [
            ["№\nп/п", "Наименование товара", "Количество", "Технические характеристики", None, "Ед. изм."],
            [None, None, None, "Требуемый параметр", "Требуемое значение", ""],
            ["2", "Система хранения данных", "1", "Гарантия", "не менее 12", "Мес"],
            ["3", "", "1", "Тип устройства - Сервер", "соответствие", ""],
            ["", "Сервер DELL PowerEdge R760\nили эквивалент", "", "Тип корпуса – Rack 19”", "соответствие", ""],
            [None, None, None, "Монтажная высота", "Не более 2", "Юнит"],
        ]

        items, _ = _parse_table_rows(data, page_info=6)

        assert len(items) == 2
        r760 = items[1]
        assert r760["name"] == "Сервер DELL PowerEdge R760 или эквивалент"
        assert r760["quantity"] == "1"
        assert r760["page"] == 6
        assert r760["raw_specs"][:3] == [
            {"p": "Тип устройства - Сервер", "v": "соответствие", "u": ""},
            {"p": "Тип корпуса – Rack 19”", "v": "соответствие", "u": ""},
            {"p": "Монтажная высота", "v": "Не более 2", "u": "Юнит"},
        ]


# ============================================================================
# Tests: _polish_items_specs_llm
# ============================================================================

class TestPolishItemsSpecsLlm:

    def test_normalize_spec_for_polish_removes_only_explicit_garbage_values(self):
        spec = {"p": "Наличие модуля TPM 2.0", "v": "соответствие", "u": ""}
        assert _normalize_spec_for_polish(spec) == "Наличие модуля TPM 2.0"

    def test_normalize_spec_for_polish_keeps_numeric_value_and_unit(self):
        spec = {"p": "Мощность блока питания", "v": "Не менее 1400", "u": "Вт"}
        assert _normalize_spec_for_polish(spec) == "Мощность блока питания: Не менее 1400 Вт"

    def test_normalize_spec_for_polish_converts_inline_param_value(self):
        spec = {"p": "Тип устройства - Сервер", "v": "соответствие", "u": ""}
        assert _normalize_spec_for_polish(spec) == "Тип устройства: Сервер"

    def test_build_polish_source_specs_deduplicates_and_skips_empty(self):
        raw_specs = [
            {"p": "Тип устройства", "v": "Сервер", "u": ""},
            {"p": "Тип устройства", "v": "Сервер", "u": ""},
            {"p": "", "v": "что-то", "u": ""},
            {"p": "Наличие TPM", "v": "соответствие", "u": ""},
        ]
        assert _build_polish_source_specs(raw_specs) == [
            "Тип устройства: Сервер",
            "Наличие TPM",
        ]

    def test_build_polish_batches_splits_by_payload_budget(self):
        items = [
            {
                "name": "Тяжёлая позиция",
                "raw_specs": [{"p": f"Параметр {i}", "v": "X" * 120, "u": ""} for i in range(20)],
            },
            {
                "name": "Короткая 1",
                "raw_specs": [{"p": "Тип устройства", "v": "Ноутбук", "u": ""}],
            },
            {
                "name": "Короткая 2",
                "raw_specs": [{"p": "Тип устройства", "v": "Монитор", "u": ""}],
            },
        ]

        batches = _build_polish_batches(items)

        assert len(batches) == 2
        assert [entry["item"]["name"] for entry in batches[0]] == ["Тяжёлая позиция"]
        assert [entry["item"]["name"] for entry in batches[1]] == ["Короткая 1", "Короткая 2"]

    def test_build_polish_batches_preserves_original_order(self):
        items = [
            {"name": "A", "raw_specs": [{"p": "P1", "v": "V1", "u": ""}]},
            {"name": "B", "raw_specs": [{"p": "P2", "v": "V2", "u": ""}]},
            {"name": "C", "raw_specs": [{"p": "P3", "v": "V3", "u": ""}]},
            {"name": "D", "raw_specs": [{"p": "P4", "v": "V4", "u": ""}]},
        ]
        batches = _build_polish_batches(items)
        ordered = [entry["item"]["name"] for batch in batches for entry in batch]
        assert ordered == ["A", "B", "C", "D"]

    def test_parse_polish_results_json_maps_by_id(self):
        content = '{"schema_version":"b3.11.v1","ok":true,"data":{"results":[{"id":1,"text":"Второй"},{"id":0,"text":"Первый"}]},"error":null}'
        assert _parse_polish_results_json(content, expected_ids=[0, 1]) == {0: "Первый", 1: "Второй"}

    def test_parse_polish_results_json_rejects_missing_expected_id(self):
        content = '{"schema_version":"b3.11.v1","ok":true,"data":{"results":[{"id":0,"text":"Первый"}]},"error":null}'
        assert _parse_polish_results_json(content, expected_ids=[0, 1]) is None

    def test_parse_polish_results_json_rejects_code_fence_wrapped_payload(self):
        content = '```json\n{"schema_version":"b3.11.v1","ok":true,"data":{"results":[{"id":0,"text":"Первый"}]},"error":null}\n```'
        assert _parse_polish_results_json(content, expected_ids=[0]) is None

    @pytest.mark.asyncio
    async def test_polish_items_specs_llm_uses_id_mapping_not_position_only(self):
        items = [
            {
                "name": "Сервер 1",
                "specs": "",
                "raw_specs": [{"p": "Тип устройства", "v": "Сервер", "u": ""}],
            },
            {
                "name": "Сервер 2",
                "specs": "",
                "raw_specs": [{"p": "Тип корпуса", "v": "Rack 19", "u": ""}],
            },
        ]

        response = {
            "content": (
                '{"schema_version":"b3.11.v1","ok":true,"data":{"results":['
                '{"id":1,"text":"Тип корпуса - Rack 19\\""},'
                '{"id":0,"text":"Тип устройства - Сервер"}'
                ']},"error":null}'
            )
        }

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=response)

            await _polish_items_specs_llm(items)

        assert items[0]["specs"] == "Тип устройства - Сервер"
        assert items[1]["specs"] == "Тип корпуса - Rack 19"

    @pytest.mark.asyncio
    async def test_polish_items_specs_llm_falls_back_when_batch_xml_invalid(self):
        items = [
            {
                "name": "Сервер 1",
                "specs": "",
                "raw_specs": [{"p": "Тип устройства", "v": "Сервер", "u": ""}],
            },
            {
                "name": "Сервер 2",
                "specs": "",
                "raw_specs": [{"p": "Тип корпуса", "v": "Rack 19", "u": ""}],
            },
        ]

        malformed_response = {
            "content": '{"schema_version":"b3.11.v1","ok":true,"data":{"results":[{"id":0,"text":"Тип устройства - Сервер"},{"text":"Тип корпуса - Rack 19"}]},"error":null}'
        }

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=malformed_response)

            await _polish_items_specs_llm(items)

        assert items[0]["specs"] == "Тип устройства: Сервер"
        assert items[1]["specs"] == "Тип корпуса: Rack 19"

    @pytest.mark.asyncio
    async def test_polish_items_specs_llm_sends_large_item_in_single_batch(self):
        large_item = {
            "name": "Тяжёлая позиция",
            "specs": "",
            "raw_specs": [{"p": f"Параметр {i}", "v": "X" * 120, "u": ""} for i in range(20)],
        }
        small_item = {
            "name": "Короткая позиция",
            "specs": "",
            "raw_specs": [{"p": "Тип устройства", "v": "Монитор", "u": ""}],
        }
        items = [large_item, small_item]

        responses = [
            {"content": '{"schema_version":"b3.11.v1","ok":true,"data":{"results":[{"id":0,"text":"Тяжелая спецификация"}]},"error":null}'},
            {"content": '{"schema_version":"b3.11.v1","ok":true,"data":{"results":[{"id":0,"text":"Короткая спецификация"}]},"error":null}'},
        ]

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(side_effect=responses)

            await _polish_items_specs_llm(items)

        assert mock_ums.async_infer.await_count == 2
        first_prompt = mock_ums.async_infer.await_args_list[0].args[1]["prompt"]
        second_prompt = mock_ums.async_infer.await_args_list[1].args[1]["prompt"]
        assert "Тяжёлая позиция" in first_prompt
        assert "Короткая позиция" not in first_prompt
        assert "Короткая позиция" in second_prompt

    @pytest.mark.asyncio
    async def test_polish_items_specs_llm_keeps_small_items_batched(self):
        items = [
            {"name": "A", "specs": "", "raw_specs": [{"p": "Тип", "v": "A", "u": ""}]},
            {"name": "B", "specs": "", "raw_specs": [{"p": "Тип", "v": "B", "u": ""}]},
            {"name": "C", "specs": "", "raw_specs": [{"p": "Тип", "v": "C", "u": ""}]},
        ]

        response = {
            "content": '{"schema_version":"b3.11.v1","ok":true,"data":{"results":[{"id":0,"text":"Spec A"},{"id":1,"text":"Spec B"},{"id":2,"text":"Spec C"}]},"error":null}'
        }

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=response)

            await _polish_items_specs_llm(items)

        assert mock_ums.async_infer.await_count == 1
        assert [item["specs"] for item in items] == ["Spec A", "Spec B", "Spec C"]


# ============================================================================
# Tests: _dedup_items
# ============================================================================

class TestDedupItems:

    def test_removes_duplicates(self, sample_items_with_dupes):
        result = _dedup_items(sample_items_with_dupes)
        names_lower = [it["name"].lower()[:50] for it in result]
        assert len(names_lower) == len(set(names_lower))

    def test_prefers_table_source(self, sample_items_with_dupes):
        """При дублях предпочитает source='table'."""
        result = _dedup_items(sample_items_with_dupes)
        cisco = [it for it in result if "cisco" in it["name"].lower()]
        assert len(cisco) == 1
        assert cisco[0]["source"] == "table"

    def test_empty_input(self):
        assert _dedup_items([]) == []

    def test_no_duplicates(self):
        items = [
            {"name": "Товар A", "source": "table"},
            {"name": "Товар B", "source": "text"},
        ]
        result = _dedup_items(items)
        assert len(result) == 2

    def test_empty_names_skipped(self):
        items = [
            {"name": "", "source": "table"},
            {"name": "   ", "source": "text"},
            {"name": "Реальный товар", "source": "table"},
        ]
        result = _dedup_items(items)
        assert len(result) == 1
        assert result[0]["name"] == "Реальный товар"

    def test_case_insensitive_dedup(self):
        items = [
            {"name": "Cisco Router 2960", "source": "text"},
            {"name": "cisco router 2960", "source": "table"},
        ]
        result = _dedup_items(items)
        assert len(result) == 1
        assert result[0]["source"] == "table"  # table preferred


# ============================================================================
# Tests: _item_to_text
# ============================================================================

class TestItemToText:

    def test_full_item(self):
        item = {"name": "Коммутатор", "specs": "48 портов", "quantity": "10"}
        text = _item_to_text(item)
        assert "Коммутатор" in text
        assert "48 портов" in text
        assert "кол-во: 10" in text

    def test_minimal_item(self):
        item = {"name": "Сервер"}
        text = _item_to_text(item)
        assert text == "Сервер"

    def test_empty_specs(self):
        item = {"name": "ИБП", "specs": "", "quantity": "5"}
        text = _item_to_text(item)
        assert "ИБП" in text
        assert "кол-во: 5" in text


# ============================================================================
# Tests: truncate_text
# ============================================================================

class TestTruncateText:

    def test_short_text_unchanged(self):
        assert truncate_text("hello", 100) == "hello"

    def test_long_text_truncated(self):
        text = "word " * 100
        result = truncate_text(text, 50)
        assert len(result) <= 55  # Учитываем "..."
        assert result.endswith("...")

    def test_exact_limit(self):
        text = "x" * 100
        assert truncate_text(text, 100) == text


# ============================================================================
# Tests: _route_after_extract
# ============================================================================

class TestRouteAfterExtract:

    def test_both_empty_goes_to_report(self, base_state):
        base_state["items_1"] = []
        base_state["items_2"] = []
        assert _route_after_extract(base_state) == "report"

    def test_items_1_present_goes_to_match(self, base_state):
        base_state["items_1"] = [{"name": "Test"}]
        base_state["items_2"] = []
        assert _route_after_extract(base_state) == "match"

    def test_items_2_present_goes_to_match(self, base_state):
        base_state["items_1"] = []
        base_state["items_2"] = [{"name": "Test"}]
        assert _route_after_extract(base_state) == "match"

    def test_both_present_goes_to_match(self, state_with_items):
        assert _route_after_extract(state_with_items) == "match"


# ============================================================================
# Tests: load_and_extract_node (mocked)
# ============================================================================

class TestLoadAndExtractNode:

    @pytest.mark.asyncio
    async def test_extracts_from_tables(self, base_state):
        """Двухпроходная экстракция: таблицы + LLM."""
        table_response = {
            "status": "success",
            "tables": [{
                "page": 1,
                "data": [
                    ["Наименование", "Характеристики", "Кол-во", "Цена"],
                    ["Коммутатор Cisco", "48 портов", "10", "45000"],
                ]
            }],
            "table_count": 1,
        }
        text_response = {
            "status": "success",
            "text": "Дополнительно требуется кабель UTP cat6 100м.",
        }
        llm_response = {
            "content": '[{"name": "Кабель UTP cat6", "specs": "100м", "quantity": "1", "price": ""}]',
        }

        mock_resp_table = MagicMock()
        mock_resp_table.json.return_value = table_response
        mock_resp_table.raise_for_status = MagicMock()

        mock_resp_text = MagicMock()
        mock_resp_text.json.return_value = text_response
        mock_resp_text.raise_for_status = MagicMock()

        async def mock_post(url, **kwargs):
            if "extract_tables" in url:
                return mock_resp_table
            if "load_document" in url:
                return mock_resp_text
            return mock_resp_table

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=llm_response)

            with patch("httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=mock_post)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client

                result = await load_and_extract_node(base_state)

        assert "items_1" in result
        assert "items_2" in result
        # Должны быть позиции из таблиц (минимум)
        assert isinstance(result["items_1"], list)

    @pytest.mark.asyncio
    async def test_handles_doc_server_error(self, base_state):
        """Document Server down → errors, items пусты."""
        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get_client.return_value = mock_client

            with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
                mock_ums.async_infer = AsyncMock(side_effect=Exception("UMS down"))

                result = await load_and_extract_node(base_state)

        assert len(result.get("errors", [])) > 0
        assert result["items_1"] == []
        assert result["items_2"] == []


# ============================================================================
# Tests: match_items_node (mocked)
# ============================================================================

class TestMatchItemsNode:

    @pytest.mark.asyncio
    async def test_empty_items_produces_gaps(self, base_state):
        """Одна сторона пуста — все позиции другой = GAP."""
        base_state["items_1"] = [
            {"name": "Коммутатор", "specs": "48 портов", "quantity": "10"},
        ]
        base_state["items_2"] = []

        result = await match_items_node(base_state)
        matches = result["matches"]
        assert len(matches) == 1
        assert matches[0]["type"] == "DELETED"

    @pytest.mark.asyncio
    async def test_both_empty(self, base_state):
        result = await match_items_node(base_state)
        assert result["matches"] == []

    @pytest.mark.asyncio
    async def test_calls_legal_server(self, state_with_items):
        """Legal Server вызывается с правильными параметрами."""
        legal_response = {
            "status": "success",
            "matches": [
                {
                    "type": "MODIFIED",
                    "old_text": "Коммутатор Cisco 2960. 48 портов, PoE+. кол-во: 10",
                    "new_text": "Cisco Catalyst 2960-X. 48 портов, PoE+, 1Gbps. кол-во: 10",
                    "similarity_score": 0.78,
                },
                {
                    "type": "ADDED",
                    "old_text": "",
                    "new_text": "ИБП APC Smart-UPS 3000. 3000VA. кол-во: 5",
                },
            ],
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = legal_response
        mock_resp.raise_for_status = MagicMock()

        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get_client.return_value = mock_client

            result = await match_items_node(state_with_items)
        matches = result["matches"]
        assert len(matches) == 2

        call_args = mock_client.post.call_args
        assert call_args[0][0].endswith("/match_batches")
        # Проверяем что post вызван с threshold=0.45 (снижен для recall аналогов)
        assert call_args[1]["json"]["threshold"] == 0.45

    @pytest.mark.asyncio
    async def test_legal_server_down(self, state_with_items):
        """Legal Server down → errors."""
        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_get_client.return_value = mock_client

            result = await match_items_node(state_with_items)

        assert result["matches"] == []
        assert len(result.get("errors", [])) > 0

    @pytest.mark.asyncio
    async def test_http_404_returns_errors(self, state_with_items):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("POST", "http://test/match_batches"),
            response=httpx.Response(404),
        )

        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get_client.return_value = mock_client

            result = await match_items_node(state_with_items)

        assert result["matches"] == []
        assert any("404" in err for err in result.get("errors", []))

# ============================================================================
# Tests: evaluate_compliance_node (mocked)
# ============================================================================

class TestEvaluateComplianceNode:

    @pytest.mark.asyncio
    async def test_structural_without_llm(self, base_state):
        """ADDED/DELETED обрабатываются без LLM."""
        base_state["matches"] = [
            {"type": "ADDED", "item_1": None, "item_2": {"name": "Новый товар"}},
            {"type": "DELETED", "item_1": {"name": "Удалённый товар"}, "item_2": None},
        ]

        result = await evaluate_compliance_node(base_state)
        results = result["analysis_results"]
        assert len(results) == 2
        assert all(r["result"] == "GAP" for r in results)

    @pytest.mark.asyncio
    async def test_batch_llm_evaluation(self, base_state):
        """MODIFIED обрабатываются через LLM."""
        base_state["matches"] = [
            {
                "type": "MODIFIED",
                "item_1": {"name": "Коммутатор Cisco 2960", "specs": "48 портов", "quantity": "10", "price": ""},
                "item_2": {"name": "Cisco 2960-X", "specs": "48 портов, PoE+", "quantity": "10", "price": "45000"},
            }
        ]

        llm_response = {
            "content": '[{"result": "PASS", "reason": "Характеристики соответствуют ТЗ"}]',
        }

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=llm_response)
            result = await evaluate_compliance_node(base_state)

        results = result["analysis_results"]
        assert len(results) == 1
        assert results[0]["result"] == "PASS"

    @pytest.mark.asyncio
    async def test_skips_on_matching_error(self, base_state):
        """Если Matching failed в errors — пропускаем evaluate."""
        base_state["errors"] = ["Matching failed: Connection refused"]
        base_state["matches"] = [{"type": "MODIFIED", "item_1": {}, "item_2": {}}]

        result = await evaluate_compliance_node(base_state)
        assert result["analysis_results"] == []

    @pytest.mark.asyncio
    async def test_empty_matches(self, base_state):
        result = await evaluate_compliance_node(base_state)
        assert result["analysis_results"] == []

    @pytest.mark.asyncio
    async def test_llm_error_produces_error_result(self, base_state):
        """LLM ошибка → result='ERROR' для batch."""
        base_state["matches"] = [
            {
                "type": "MODIFIED",
                "item_1": {"name": "Item1", "specs": "", "quantity": "1", "price": ""},
                "item_2": {"name": "Item2", "specs": "", "quantity": "1", "price": ""},
            }
        ]

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(side_effect=Exception("LLM timeout"))
            result = await evaluate_compliance_node(base_state)

        results = result["analysis_results"]
        assert len(results) == 1
        assert results[0]["result"] == "ERROR"

    @pytest.mark.asyncio
    async def test_smeta_vs_smeta_mode(self, base_state):
        """Режим smeta_vs_smeta использует другие статусы."""
        base_state["mode"] = "smeta_vs_smeta"
        base_state["matches"] = [
            {
                "type": "MODIFIED",
                "item_1": {"name": "Cisco 2960", "specs": "48 портов", "quantity": "10", "price": "40000"},
                "item_2": {"name": "Cisco 2960", "specs": "48 портов", "quantity": "10", "price": "45000"},
            }
        ]

        llm_response = {
            "content": '[{"result": "PRICE_CHANGE", "reason": "Цена увеличена на 5000 руб."}]',
        }

        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=llm_response)
            result = await evaluate_compliance_node(base_state)

        assert result["analysis_results"][0]["result"] == "PRICE_CHANGE"


# ============================================================================
# Tests: generate_equipment_report_node
# ============================================================================

class TestGenerateEquipmentReportNode:

    @pytest.mark.asyncio
    async def test_report_with_results(self, base_state):
        base_state["items_1"] = [{"name": "Коммутатор"}]
        base_state["items_2"] = [{"name": "Коммутатор Cisco"}]
        base_state["analysis_results"] = [
            {
                "item_1": {"name": "Коммутатор"},
                "item_2": {"name": "Коммутатор Cisco"},
                "result": "PASS",
                "reason": "Соответствует",
                "type": "MODIFIED",
            }
        ]

        with patch.dict(os.environ, {"UPLOADS_DIR": tempfile.mkdtemp()}):
            result = await generate_equipment_report_node(base_state)

        report = result["final_report"]
        assert "Анализ соответствия" in report
        assert "ТЗ на оборудование.pdf" in report
        assert "Смета КП.pdf" in report
        assert "PASS" in report
        assert "Отчет сохранен" in report

    @pytest.mark.asyncio
    async def test_report_empty_items(self, base_state):
        """Пустые items → отчёт с предупреждением."""
        with patch.dict(os.environ, {"UPLOADS_DIR": tempfile.mkdtemp()}):
            result = await generate_equipment_report_node(base_state)

        report = result["final_report"]
        assert "Не удалось извлечь" in report

    @pytest.mark.asyncio
    async def test_report_smeta_mode_title(self, base_state):
        base_state["mode"] = "smeta_vs_smeta"
        base_state["items_1"] = [{"name": "X"}]
        base_state["items_2"] = [{"name": "Y"}]
        base_state["analysis_results"] = [
            {"item_1": {"name": "X"}, "item_2": {"name": "Y"}, "result": "SAME", "reason": "Без изменений", "type": "MODIFIED"}
        ]

        with patch.dict(os.environ, {"UPLOADS_DIR": tempfile.mkdtemp()}):
            result = await generate_equipment_report_node(base_state)

        assert "Сравнение смет" in result["final_report"]

    @pytest.mark.asyncio
    async def test_report_includes_errors(self, base_state):
        base_state["errors"] = ["Table extraction failed: timeout"]
        base_state["items_1"] = [{"name": "X"}]
        base_state["items_2"] = [{"name": "Y"}]
        base_state["analysis_results"] = []

        with patch.dict(os.environ, {"UPLOADS_DIR": tempfile.mkdtemp()}):
            result = await generate_equipment_report_node(base_state)

        assert "Предупреждения" in result["final_report"]
        assert "timeout" in result["final_report"]

    @pytest.mark.asyncio
    async def test_report_uses_uploads_dir(self, base_state):
        """Отчёт сохраняется в UPLOADS_DIR."""
        tmpdir = tempfile.mkdtemp()
        base_state["items_1"] = [{"name": "Test"}]
        base_state["items_2"] = [{"name": "Test2"}]
        base_state["analysis_results"] = [
            {"item_1": {"name": "Test"}, "item_2": {"name": "Test2"}, "result": "PASS", "reason": "OK", "type": "MODIFIED"}
        ]

        with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
            result = await generate_equipment_report_node(base_state)

        # Файл должен быть сохранён в tmpdir
        import glob
        reports = glob.glob(os.path.join(tmpdir, "Report_Equipment_*.pdf"))
        assert len(reports) == 1

    @pytest.mark.asyncio
    async def test_report_dedup(self, base_state):
        """Дедупликация: повторный вызов не создаёт дубль."""
        tmpdir = tempfile.mkdtemp()
        base_state["items_1"] = [{"name": "Test"}]
        base_state["items_2"] = [{"name": "Test2"}]
        base_state["analysis_results"] = [
            {"item_1": {"name": "Test"}, "item_2": {"name": "Test2"}, "result": "PASS", "reason": "OK", "type": "MODIFIED"}
        ]

        with patch.dict(os.environ, {"UPLOADS_DIR": tmpdir}):
            result1 = await generate_equipment_report_node(base_state)
            result2 = await generate_equipment_report_node(base_state)

        assert "уже сохранен" in result2["final_report"]

        import glob
        reports = glob.glob(os.path.join(tmpdir, "Report_Equipment_*.pdf"))
        assert len(reports) == 1  # Только один файл

    @pytest.mark.asyncio
    async def test_report_contains_full_details_without_truncation(self, base_state):
        long_reason = (
            "Предложение не соответствует всем требованиям ТЗ: "
            "не хватает резервирования БП, не указан тип RAID, не указан интерфейс PCIe для NVMe."
        )
        base_state["items_1"] = [{"name": "Сервер | Lenovo", "specs": "CPU: 2x Xeon\nRAM: 256GB"}]
        base_state["items_2"] = [{"name": "Сервер DELL", "specs": "CPU: 2x Xeon\nRAM: 128GB"}]
        base_state["analysis_results"] = [
            {
                "item_1": base_state["items_1"][0],
                "item_2": base_state["items_2"][0],
                "result": "PARTIAL",
                "reason": long_reason,
                "type": "MODIFIED",
            }
        ]

        with patch.dict(os.environ, {"UPLOADS_DIR": tempfile.mkdtemp()}):
            result = await generate_equipment_report_node(base_state)

        report = result["final_report"]
        assert "## Полные детали по позициям" in report
        assert long_reason in report
        assert "Сервер \\| Lenovo" in report
        assert "CPU: 2x Xeon\nRAM: 256GB" in report


# ============================================================================
# Tests: create_equipment_graph
# ============================================================================

class TestCreateEquipmentGraph:

    def test_graph_compiles(self):
        """Граф компилируется без ошибок."""
        graph = create_equipment_graph()
        assert graph is not None

    def test_graph_contains_conditional_extract_routing(self):
        """Граф сохраняет conditional routing extract -> match/report без runtime ainvoke."""
        graph = create_equipment_graph()
        compiled = graph.get_graph()

        edge_pairs = {(edge.source, edge.target, edge.conditional) for edge in compiled.edges}

        assert ("__start__", "extract", False) in edge_pairs
        assert ("extract", "match", True) in edge_pairs
        assert ("extract", "report", True) in edge_pairs
        assert ("match", "evaluate", False) in edge_pairs
        assert ("evaluate", "report", False) in edge_pairs
        assert ("report", "__end__", False) in edge_pairs


# ============================================================================
# Tests: _detect_equipment_mode (Chainlit helper)
# ============================================================================

class TestDetectEquipmentMode:

    def test_tz_in_filename(self):
        assert detect_equipment_mode("ТЗ_оборудование.pdf", "Смета_КП.xlsx", "") == "tz_vs_smeta"

    def test_both_smeta(self):
        assert detect_equipment_mode("Смета_2024.pdf", "Смета_2025.pdf", "") == "smeta_vs_smeta"

    def test_query_compare_smeta(self):
        assert detect_equipment_mode("doc1.pdf", "doc2.pdf", "Сравни сметы") == "smeta_vs_smeta"

    def test_default_tz_vs_smeta(self):
        assert detect_equipment_mode("doc1.pdf", "doc2.pdf", "Проверь соответствие") == "tz_vs_smeta"


# ============================================================================
# Tests: Document Server endpoints (unit)
# ============================================================================

class TestDocumentServerEndpoints:
    """Тесты новых endpoints Document Server (extract_tables_docx, extract_tables_excel)."""

    @pytest.mark.asyncio
    async def test_extract_tables_docx_file_not_found(self):
        """Несуществующий файл → error."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "document_server"))

        try:
            from mcp_document_server import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/extract_tables_docx", json={"path": "/nonexistent/file.docx"})
                data = resp.json()
            assert data["status"] == "error"
            assert "not found" in data["error"].lower()
        except ImportError:
            pytest.skip("Document server dependencies not available")

    @pytest.mark.asyncio
    async def test_extract_tables_excel_file_not_found(self):
        """Несуществующий файл → error."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "document_server"))

        try:
            from mcp_document_server import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/extract_tables_excel", json={"path": "/nonexistent/file.xlsx"})
                data = resp.json()
            assert data["status"] == "error"
            assert "not found" in data["error"].lower()
        except ImportError:
            pytest.skip("Document server dependencies not available")

    @pytest.mark.asyncio
    async def test_load_document_xlsx(self):
        """xlsx формат поддерживается в /load_document."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "document_server"))

        try:
            from openpyxl import Workbook
            from mcp_document_server import app

            # Создаём тестовый xlsx
            tmpdir = tempfile.mkdtemp()
            xlsx_path = os.path.join(tmpdir, "test.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.append(["Наименование", "Цена"])
            ws.append(["Коммутатор", "45000"])
            wb.save(xlsx_path)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/load_document", json={"path": xlsx_path})
                data = resp.json()
            assert data["status"] == "success"
            assert "Коммутатор" in data["text"]
            assert data["format"] == "excel"
        except ImportError:
            pytest.skip("openpyxl/fastapi not available")

    @pytest.mark.asyncio
    async def test_extract_tables_excel_real(self):
        """Реальный xlsx → таблицы извлекаются."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "document_server"))

        try:
            from openpyxl import Workbook
            from mcp_document_server import app

            tmpdir = tempfile.mkdtemp()
            xlsx_path = os.path.join(tmpdir, "test.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "Sheet1"
            ws.append(["Наименование", "Кол-во", "Цена"])
            ws.append(["Коммутатор", "10", "45000"])
            ws.append(["Сервер", "3", "350000"])
            wb.save(xlsx_path)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/extract_tables_excel", json={"path": xlsx_path})
                data = resp.json()
            assert data["status"] == "success"
            assert data["table_count"] == 1
            assert len(data["tables"][0]["data"]) == 3  # header + 2 rows
        except ImportError:
            pytest.skip("openpyxl/fastapi not available")


# ============================================================================
# Tests: _chunk_text (Task 1)
# ============================================================================

class TestChunkText:
    """Тесты для _chunk_text — чанкинг текста через Document Server."""

    @pytest.mark.asyncio
    async def test_short_text_no_chunking(self):
        """Текст <= MAX_TEXT_FOR_LLM — возвращается как есть, без HTTP."""
        short = "Коммутатор Cisco 2960. Сервер Dell R740."
        assert len(short) <= MAX_TEXT_FOR_LLM
    
        chunks = await _chunk_text(short)
        assert chunks == [short]

    @pytest.mark.asyncio
    async def test_long_text_calls_smart_chunk(self):
        """Текст > MAX_TEXT_FOR_LLM — вызывает /smart_chunk и возвращает чанки."""
        long_text = "A" * (MAX_TEXT_FOR_LLM + 100)
    
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "chunks": ["chunk_1_text", "chunk_2_text"],
            "chunk_count": 2,
        }
    
        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get_client.return_value = mock_client
            
            chunks = await _chunk_text(long_text)
            mock_client.post.assert_called_once()
            call_json = mock_client.post.call_args[1]["json"]
            assert call_json["max_tokens"] == 2000
            assert call_json["overlap"] == 150
            assert chunks == ["chunk_1_text", "chunk_2_text"]

    @pytest.mark.asyncio
    async def test_smart_chunk_fails_fallback(self):
        """Если /smart_chunk упал — fallback на _split_by_lines."""
        # Текст с переносами строк чтобы _split_by_lines мог разбить
        line = "B" * 100 + "\n"
        long_text = line * ((MAX_TEXT_FOR_LLM // 101) + 10)
        assert len(long_text) > MAX_TEXT_FOR_LLM
    
        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
            mock_get_client.return_value = mock_client
            
            chunks = await _chunk_text(long_text)
            assert len(chunks) >= 2
            for c in chunks:
                assert len(c) <= MAX_TEXT_FOR_LLM + 200  # допуск на последнюю строку

    @pytest.mark.asyncio
    async def test_oversized_smart_chunk_resplit(self):
        """smart_chunk вернул 1 oversized чанк → _chunk_text дробит локально."""
        line = "C" * 100 + "\n"
        long_text = line * ((MAX_TEXT_FOR_LLM // 101) + 10)
        assert len(long_text) > MAX_TEXT_FOR_LLM
    
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "chunks": [long_text],  # smart_chunk не смог разбить
            "chunk_count": 1,
        }
    
        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get_client.return_value = mock_client
            
            chunks = await _chunk_text(long_text)
            assert len(chunks) >= 2
            for c in chunks:
                assert len(c) <= MAX_TEXT_FOR_LLM + 200


# ============================================================================
# Tests: _extract_from_single_chunk (Task 2)
# ============================================================================

class TestExtractFromSingleChunk:
    """Тесты для _extract_from_single_chunk — один LLM вызов на один чанк."""

    @pytest.mark.asyncio
    async def test_extracts_items_from_chunk(self):
        """Извлекает позиции из текстового чанка."""
        mock_response = {
            "content": '[{"name": "Коммутатор Cisco", "specs": "48 портов", "quantity": "10", "price": "45000"}]'
        }
        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=mock_response)
            items = await _extract_from_single_chunk(
                chunk_text="Поставить коммутатор Cisco 48 портов 10 шт",
                chunk_idx=0,
                total_chunks=1,
                already_found=[],
            )
            assert len(items) == 1
            assert items[0]["name"] == "Коммутатор Cisco"
            assert items[0]["source"] == "text"

    @pytest.mark.asyncio
    async def test_passes_already_found_in_prompt(self):
        """already_found включается в промпт для дедупликации."""
        mock_response = {"content": "[]"}
        with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
            mock_ums.async_infer = AsyncMock(return_value=mock_response)
            await _extract_from_single_chunk(
                chunk_text="Текст без позиций",
                chunk_idx=1,
                total_chunks=3,
                already_found=["Коммутатор Cisco", "Сервер Dell"],
            )
            call_payload = mock_ums.async_infer.call_args[0][1]
            assert "Коммутатор Cisco" in call_payload["prompt"]
            assert "Сервер Dell" in call_payload["prompt"]
            assert "Фрагмент 2 из 3" in call_payload["prompt"]


# ============================================================================
# Tests: Chunked Extraction integration (Task 3)
# ============================================================================

class TestChunkedExtraction:
    """Интеграционные тесты Map-Reduce экстракции через _extract_items_llm."""

    @pytest.mark.asyncio
    async def test_multi_chunk_combines_results(self):
        """Длинный текст → 2 чанка → items из обоих объединены."""
        long_text = "X" * (MAX_TEXT_FOR_LLM + 100)

        # Мок /load_document
        load_resp = MagicMock()
        load_resp.status_code = 200
        load_resp.raise_for_status = MagicMock()
        load_resp.json.return_value = {"text": long_text}

        # Мок /smart_chunk
        chunk_resp = MagicMock()
        chunk_resp.status_code = 200
        chunk_resp.raise_for_status = MagicMock()
        chunk_resp.json.return_value = {
            "status": "success",
            "chunks": ["chunk_1 text here", "chunk_2 text here"],
            "chunk_count": 2,
        }

        async def route_post(url, **kwargs):
            if "/load_document" in url:
                return load_resp
            if "/smart_chunk" in url:
                return chunk_resp
            raise ValueError(f"Unexpected URL: {url}")

        # LLM: чанк 1 → 1 item, чанк 2 → 1 item
        llm_responses = [
            {"content": '[{"name": "Коммутатор", "specs": "48p", "quantity": "10", "price": "45000"}]'},
            {"content": '[{"name": "Сервер Dell", "specs": "Xeon", "quantity": "3", "price": "350000"}]'},
        ]
        call_count = {"n": 0}

        async def mock_infer(model, payload):
            idx = call_count["n"]
            call_count["n"] += 1
            return llm_responses[idx]

        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=route_post)
            mock_get_client.return_value = mock_client
            
            with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
                mock_ums.async_infer = mock_infer
                items = await _extract_items_llm("/fake/doc.pdf", [])
                assert len(items) == 2
                names = {it["name"] for it in items}
                assert "Коммутатор" in names
                assert "Сервер Dell" in names

    @pytest.mark.asyncio
    async def test_chunk_failure_partial_results(self):
        """Если один чанк упал — items из остальных всё равно возвращаются."""
        long_text = "Y" * (MAX_TEXT_FOR_LLM + 100)

        load_resp = MagicMock()
        load_resp.status_code = 200
        load_resp.raise_for_status = MagicMock()
        load_resp.json.return_value = {"text": long_text}

        chunk_resp = MagicMock()
        chunk_resp.status_code = 200
        chunk_resp.raise_for_status = MagicMock()
        chunk_resp.json.return_value = {
            "status": "success",
            "chunks": ["chunk_ok", "chunk_fail"],
            "chunk_count": 2,
        }

        async def route_post(url, **kwargs):
            if "/load_document" in url:
                return load_resp
            if "/smart_chunk" in url:
                return chunk_resp
            raise ValueError(f"Unexpected URL: {url}")

        call_count = {"n": 0}

        async def mock_infer(model, payload):
            idx = call_count["n"]
            call_count["n"] += 1
            if idx == 1:
                raise RuntimeError("LLM timeout")
            return {"content": '[{"name": "Item OK", "specs": "", "quantity": "1", "price": ""}]'}

        with patch("orchestrator.workflows.equipment.get_shared_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=route_post)
            mock_get_client.return_value = mock_client
            
            with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
                mock_ums.async_infer = mock_infer
                items = await _extract_items_llm("/fake/doc.pdf", [])
                assert len(items) == 1
                assert items[0]["name"] == "Item OK"
