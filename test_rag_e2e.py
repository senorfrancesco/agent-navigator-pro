"""
RAG End-to-End Test — без внешних файлов.

Загружает тестовые юридические тексты в AdaptiveRAGPipeline,
задаёт вопросы, получает ответы через LLM (UMS), выводит evaluation.

Запуск:
    conda activate diploma_llm
    python test_rag_e2e.py

Требования: UMS запущен (8090), LaBSE загружена.
"""

import sys
import os
import time
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from orchestrator.rag.pipeline import AdaptiveRAGPipeline
from services.model_manager.ums_client import create_ums_embed_fn

# ---------------------------------------------------------------------------
# Тестовые документы (встроенные)
# ---------------------------------------------------------------------------

DOCUMENTS = {
    "contract_001.txt": """
ДОГОВОР ПОСТАВКИ № 142-П

г. Москва, 15 января 2025 года

Общество с ограниченной ответственностью «ТехноСтрой» (далее — Поставщик),
в лице директора Иванова А.В., и ООО «СтройМонтаж» (далее — Покупатель),
в лице генерального директора Петрова С.Н., заключили настоящий договор.

1. ПРЕДМЕТ ДОГОВОРА
1.1. Поставщик обязуется поставить строительное оборудование согласно
спецификации (Приложение № 1), а Покупатель обязуется принять и оплатить товар.
1.2. Общая стоимость поставки составляет 4 500 000 рублей (четыре миллиона
пятьсот тысяч рублей), включая НДС 20%.

2. СРОКИ ПОСТАВКИ
2.1. Поставщик обязуется осуществить поставку в течение 30 (тридцати)
календарных дней с момента поступления авансового платежа.
2.2. Датой поставки считается дата подписания товарной накладной обеими сторонами.

3. ОПЛАТА
3.1. Покупатель вносит аванс в размере 30% от стоимости договора в течение
5 рабочих дней с момента подписания договора.
3.2. Оставшиеся 70% оплачиваются в течение 10 рабочих дней после поставки.

4. ГАРАНТИИ
4.1. Гарантийный срок на оборудование составляет 24 месяца с даты поставки.
4.2. Поставщик обязуется устранить выявленные дефекты в течение 15 рабочих дней.

5. ШТРАФНЫЕ САНКЦИИ
5.1. За просрочку поставки Поставщик уплачивает пеню в размере 0.1% от стоимости
недопоставленного товара за каждый день просрочки, но не более 10% от суммы договора.
5.2. За просрочку оплаты Покупатель уплачивает пеню 0.05% от просроченной суммы
за каждый день просрочки.

6. ФОРС-МАЖОР
6.1. Стороны освобождаются от ответственности при наступлении обстоятельств
непреодолимой силы: стихийные бедствия, военные действия, решения органов власти.
6.2. Сторона, для которой наступил форс-мажор, обязана уведомить другую сторону
в течение 3 рабочих дней.

7. РАСТОРЖЕНИЕ ДОГОВОРА
7.1. Договор может быть расторгнут по соглашению сторон.
7.2. Покупатель вправе расторгнуть договор в одностороннем порядке при просрочке
поставки более 30 дней с уведомлением за 10 рабочих дней.
""",

    "specification.txt": """
СПЕЦИФИКАЦИЯ К ДОГОВОРУ № 142-П (Приложение № 1)

Наименование оборудования и технические характеристики:

1. Компрессор воздушный К-250
   - Производительность: 250 м³/час
   - Рабочее давление: до 10 атм
   - Мощность двигателя: 45 кВт
   - Количество: 2 шт.
   - Цена за единицу: 850 000 руб.
   - Срок поставки: 20 дней

2. Сварочный аппарат СА-500И
   - Сварочный ток: до 500 А
   - Напряжение сети: 380 В
   - КПД: 85%
   - Количество: 5 шт.
   - Цена за единицу: 180 000 руб.
   - Срок поставки: 15 дней

3. Кран-балка электрическая КБ-3.2
   - Грузоподъёмность: 3.2 тонны
   - Длина пролёта: 12 м
   - Высота подъёма: 6 м
   - Количество: 1 шт.
   - Цена: 950 000 руб.
   - Срок поставки: 25 дней

Стоимость без НДС: 3 750 000 руб.
НДС 20%: 750 000 руб.
Итого с НДС: 4 500 000 руб.

Условия гарантии: 24 месяца с даты подписания акта приёмки.
Доставка: силами и за счёт Поставщика до склада Покупателя.
Адрес доставки: г. Москва, ул. Промышленная, д. 15, стр. 2.
""",
}

# ---------------------------------------------------------------------------
# Вопросы и ключевые слова для оценки
# ---------------------------------------------------------------------------

QUESTIONS = [
    {
        "q": "Какова общая стоимость договора?",
        "expected_keywords": ["4 500 000", "4500000", "четыре миллиона"],
        "label": "Стоимость договора",
    },
    {
        "q": "Какой срок поставки по договору?",
        "expected_keywords": ["30", "тридцати", "календарных", "авансов", "аванс"],
        "label": "Срок поставки",
    },
    {
        "q": "Каков размер штрафа за просрочку поставки?",
        "expected_keywords": ["0.1", "0,1", "пеню", "пени", "10%", "просрочк"],
        "label": "Штраф за просрочку",
    },
    {
        "q": "Какой гарантийный срок на оборудование?",
        "expected_keywords": ["24", "месяца", "двадцать"],
        "label": "Гарантийный срок",
    },
    {
        "q": "Сколько компрессоров К-250 нужно поставить и по какой цене?",
        "expected_keywords": ["2", "850", "250"],
        "label": "Компрессоры в спецификации",
    },
    {
        "q": "Какой размер авансового платежа?",
        "expected_keywords": ["30%", "30 процент", "аванс"],
        "label": "Аванс",
    },
    {
        "q": "Какова грузоподъёмность крана-балки?",
        "expected_keywords": ["3.2", "3,2", "тонн"],
        "label": "Кран-балка грузоподъёмность",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")


def build_llm_prompt(query: str, context: str) -> str:
    return (
        "<|im_start|>system\n"
        "Ты помощник по юридическим документам. Отвечай кратко и точно "
        "на основе предоставленного контекста. Если информации нет — скажи об этом.\n\n"
        f"Контекст:\n{context}<|im_end|>\n"
        f"<|im_start|>user\n{query}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def call_llm(prompt: str) -> str:
    try:
        resp = requests.post(
            f"{UMS_URL}/infer",
            json={
                "model_id": "qwen-14b-llm",
                "payload": {"prompt": prompt, "max_tokens": 256, "temperature": 0.1},
                "device_mode": "hybrid",
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        choices = result.get("choices", [])
        if choices:
            return choices[0].get("text", "").strip()
        return str(result)
    except Exception as e:
        return f"[LLM ERROR: {e}]"


def evaluate(answer: str, keywords: list[str]) -> bool:
    answer_lower = answer.lower()
    return any(kw.lower() in answer_lower for kw in keywords)


def separator(char="-", width=70):
    print(char * width)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print()
    separator("=")
    print("  RAG End-to-End Test — Agent Navigator Pro")
    separator("=")

    # 1. Embed function
    print("\n[1/4] Подключение к UMS LaBSE...")
    embed_fn = create_ums_embed_fn(UMS_URL)
    if embed_fn:
        test_emb = embed_fn(["тест"])
        print(f"      ✓ LaBSE доступен: dim={test_emb.shape[1]}, mode=BM25+Dense (hybrid)")
    else:
        print("      ⚠ LaBSE недоступен — работаем в режиме BM25-only")

    # 2. Pipeline init + indexing
    print("\n[2/4] Инициализация RAG pipeline...")
    rag = AdaptiveRAGPipeline(embed_fn=embed_fn, rag_mode="corrective", top_k=5)

    texts = list(DOCUMENTS.values())
    names = list(DOCUMENTS.keys())

    t0 = time.perf_counter()
    chunks = rag.index_documents(texts, doc_names=names)
    dt = time.perf_counter() - t0

    print(f"      ✓ Проиндексировано: {len(texts)} документов → {len(chunks)} чанков за {dt:.2f}с")
    print(f"      ✓ Classifier: {'инициализирован' if rag._classifier_initialized else 'недоступен (BM25-only)'}")

    # 3. Q&A loop
    print(f"\n[3/4] Тест: {len(QUESTIONS)} вопросов...\n")
    separator()

    results = []
    for i, item in enumerate(QUESTIONS, 1):
        print(f"\nВопрос {i}/{len(QUESTIONS)}: {item['label']}")
        print(f"  Q: {item['q']}")

        t0 = time.perf_counter()

        # RAG retrieve
        rag_result = rag.retrieve(item["q"])
        context = rag_result.context_text
        n_chunks = len(rag_result.chunks)
        intent_info = rag_result.intent or {}
        retrieve_ms = (time.perf_counter() - t0) * 1000

        if not context:
            print(f"  ⚠ RAG не нашёл релевантных фрагментов")
            answer = "[RAG EMPTY]"
            passed = False
        else:
            # LLM generate
            prompt = build_llm_prompt(item["q"], context)
            t1 = time.perf_counter()
            answer = call_llm(prompt)
            llm_ms = (time.perf_counter() - t1) * 1000

            passed = evaluate(answer, item["expected_keywords"])

            print(f"  A: {answer}")
            print(f"  Retrieve: {n_chunks} чанков за {retrieve_ms:.0f}ms | LLM: {llm_ms:.0f}ms")
            if intent_info:
                print(f"  Intent: {intent_info.get('intent','?')} "
                      f"(conf={intent_info.get('confidence',0):.2f}, "
                      f"needs_rag={intent_info.get('needs_rag','?')})")

        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}")
        results.append({"label": item["label"], "passed": passed, "answer": answer})

    # 4. Summary
    print()
    separator("=")
    print("  РЕЗУЛЬТАТЫ")
    separator("=")

    passed_count = sum(1 for r in results if r["passed"])
    total = len(results)
    accuracy = passed_count / total * 100

    for r in results:
        mark = "✓" if r["passed"] else "✗"
        print(f"  {mark}  {r['label']}")

    separator()
    print(f"  Accuracy: {passed_count}/{total} = {accuracy:.0f}%")
    separator("=")

    if accuracy >= 70:
        print("  ✓ RAG pipeline работает корректно")
    elif accuracy >= 50:
        print("  ⚠ Частичная работоспособность, проверьте LLM и embeddings")
    else:
        print("  ✗ Проблемы с pipeline, проверьте UMS и индексацию")
    print()

    return 0 if accuracy >= 70 else 1


if __name__ == "__main__":
    sys.exit(main())
