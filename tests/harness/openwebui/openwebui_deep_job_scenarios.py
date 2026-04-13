from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS_DIR = REPO_ROOT / "documents"


@dataclass(frozen=True)
class DeepJobScenario:
    scenario_id: str
    pair_key: str
    prompt_variant: str
    document_path: Path
    prompt: str
    expected_focus_fragments: tuple[str, ...]
    expected_mode: str = "positive"
    architecture_probe: bool = False


SCENARIOS = (
    DeepJobScenario(
        scenario_id="requirements-p1",
        pair_key="requirements",
        prompt_variant="p1",
        document_path=DOCUMENTS_DIR / "Requirements.pdf",
        prompt="Определи тип документа, ключевые категории оборудования и обязательные характеристики.",
        expected_focus_fragments=("тип документа", "категории оборудования", "обязательные характеристики"),
        architecture_probe=True,
    ),
    DeepJobScenario(
        scenario_id="requirements-p2",
        pair_key="requirements",
        prompt_variant="p2",
        document_path=DOCUMENTS_DIR / "Requirements.pdf",
        prompt="Сфокусируйся только на процессорах, памяти, дисках и обязательности формулировок.",
        expected_focus_fragments=("процессорах", "памяти", "дисках", "обязательности"),
    ),
    DeepJobScenario(
        scenario_id="quotation12-p1",
        pair_key="quotation12",
        prompt_variant="p1",
        document_path=DOCUMENTS_DIR / "Quotation_12.pdf",
        prompt="Определи, что именно предлагается и какие позиции выглядят серверными, а какие пользовательскими.",
        expected_focus_fragments=("что именно предлагается", "серверными", "пользовательскими"),
    ),
    DeepJobScenario(
        scenario_id="quotation12-p2",
        pair_key="quotation12",
        prompt_variant="p2",
        document_path=DOCUMENTS_DIR / "Quotation_12.pdf",
        prompt="Сфокусируйся на рисках неполного соответствия требованиям и на недостающих параметрах.",
        expected_focus_fragments=("рисках", "неполного соответствия", "недостающих параметрах"),
    ),
    DeepJobScenario(
        scenario_id="ksu-docx-p1",
        pair_key="ksu-docx",
        prompt_variant="p1",
        document_path=DOCUMENTS_DIR / "2._KSU_1_4_24_tz-V2.docx",
        prompt="Кратко определи тип документа и ключевые блоки оборудования.",
        expected_focus_fragments=("тип документа", "ключевые блоки оборудования"),
    ),
    DeepJobScenario(
        scenario_id="ksu-docx-p2",
        pair_key="ksu-docx",
        prompt_variant="p2",
        document_path=DOCUMENTS_DIR / "2._KSU_1_4_24_tz-V2.docx",
        prompt="Сфокусируйся на сетевом оборудовании, интерфейсах и критичных параметрах совместимости.",
        expected_focus_fragments=("сетевом оборудовании", "интерфейсах", "совместимости"),
    ),
    DeepJobScenario(
        scenario_id="commercial-offer-docx-p1",
        pair_key="commercial-offer-docx",
        prompt_variant="p1",
        document_path=DOCUMENTS_DIR / "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ.docx",
        prompt="Определи структуру предложения и основные товарные группы.",
        expected_focus_fragments=("структуру предложения", "товарные группы"),
    ),
    DeepJobScenario(
        scenario_id="commercial-offer-docx-p2",
        pair_key="commercial-offer-docx",
        prompt_variant="p2",
        document_path=DOCUMENTS_DIR / "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ.docx",
        prompt="Сфокусируйся на том, что можно использовать для проверки соответствия ТЗ, а что остаётся неясным.",
        expected_focus_fragments=("проверки соответствия тз", "остаётся неясным"),
    ),
    DeepJobScenario(
        scenario_id="f5-p1",
        pair_key="f5",
        prompt_variant="p1",
        document_path=DOCUMENTS_DIR / "f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf",
        prompt="Выдели ключевые позиции оборудования и их обязательные характеристики.",
        expected_focus_fragments=("ключевые позиции оборудования", "обязательные характеристики"),
        architecture_probe=True,
    ),
    DeepJobScenario(
        scenario_id="f5-p2",
        pair_key="f5",
        prompt_variant="p2",
        document_path=DOCUMENTS_DIR / "f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf",
        prompt="Сфокусируйся на таблицах, количественных параметрах и возможных неоднозначностях извлечения.",
        expected_focus_fragments=("таблицах", "количественных параметрах", "неоднозначностях извлечения"),
    ),
    DeepJobScenario(
        scenario_id="qw23-p1",
        pair_key="qw23",
        prompt_variant="p1",
        document_path=DOCUMENTS_DIR / "qw23.pdf",
        prompt="Определи, удалось ли извлечь содержимое, и если нет — скажи это явно.",
        expected_focus_fragments=("удалось ли извлечь", "скажи это явно"),
        expected_mode="negative",
    ),
)


def get_scenarios(selected_ids: list[str] | None = None) -> list[DeepJobScenario]:
    if not selected_ids:
        return list(SCENARIOS)
    wanted = {item.strip() for item in selected_ids if item.strip()}
    return [scenario for scenario in SCENARIOS if scenario.scenario_id in wanted]
