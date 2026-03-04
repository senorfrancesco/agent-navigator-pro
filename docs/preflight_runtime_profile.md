# Preflight Runtime Profile (каркас)

Этот документ фиксирует единый каркас preflight-конфигуратора перед запуском стека.

## Цели

- Убрать зависимость от статичных лимитов контекста.
- Рассчитывать эффективный per-request budget, а не «сырой максимум модели».
- Разделить режимы запуска: `default`, `adaptive`, `manual`.
- Сохранять итоговые параметры в `backend/.env.runtime`.

## Новый entrypoint

- Скрипт: `scripts/preflight.py`
- Пример запуска:
  - `python scripts/preflight.py --mode adaptive`
  - `python scripts/preflight.py --mode default --hard-cap 8192`
  - `python scripts/preflight.py --mode manual`

## Формула budget

Используется защитный расчёт:

- `effective_context_tokens = min(floor(ctx_size / parallel_slots), hard_cap)`
- `parallel_slots >= 1`
- нижняя граница: `1024` токенов

Это снижает риск OOM и неверных ожиданий при параллельной обработке.

## Пайплайн preflight

1. **Detect**
   - HardwareProfiler (CPU/RAM/GPU/VRAM).
   - TierSelector (базовый tier-кандидат).
2. **Plan**
   - `default`: безопасные значения.
   - `adaptive`: значения из tier + guardrails.
   - `manual`: интерактивный выбор tier и лимитов.
3. **Apply**
   - Генерация `backend/.env.runtime`.
4. **Report**
   - Вывод итоговых параметров и пути сохранения.

## Интеграция в запуск

`scripts/run_all.sh` теперь загружает два файла окружения в порядке:

1. `backend/.env`
2. `backend/.env.runtime` (если существует, имеет приоритет)

## Рекомендуемые следующие шаги

1. Расширить UMS `/status` полем `effective_context_tokens`.
2. Добавить в preflight check доступности ключевых бинарей (`tmux`, `docker`, `llama-server`) с мягкими подсказками.
3. Сделать `scripts/restart_all.sh` с optional preflight-фазой (флаг `--skip-preflight`).
4. Добавить unit-тесты для `scripts/preflight.py` через выделение расчетов в отдельный модуль.
