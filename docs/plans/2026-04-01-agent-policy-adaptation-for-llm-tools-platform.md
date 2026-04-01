# Agent Policy Adaptation for llm-tools-platform

**Дата:** 2026-04-01  
**Источники:**  
- [/home/seral/HDD/clone_claude/CLAUDE.md](/home/seral/HDD/clone_claude/CLAUDE.md)  
- [/home/seral/HDD/clone_claude/CLAUDE_2.md](/home/seral/HDD/clone_claude/CLAUDE_2.md)  
**Целевые файлы адаптации:**  
- [AGENTS.md](/home/seral/HDD/proj/agent-navigator-pro/AGENTS.md)  
**Устаревшие policy-артефакты:**  
- [TASKS_NIGHT.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS_NIGHT.md)  
- [workflow.yaml](/home/seral/HDD/proj/agent-navigator-pro/workflow.yaml)

## 1. Назначение документа

- [ ] Выделить из `CLAUDE.md` и `CLAUDE_2.md` только те правила, которые реально полезны для `llm-tools-platform`.
- [ ] Отделить:
  - [ ] правила, которые стоит перенести почти дословно;
  - [ ] правила, которые надо адаптировать под наш стек;
  - [ ] правила, которые не стоит переносить.
- [ ] Зафиксировать, какие правила нужно переносить в `AGENTS.md`.
- [ ] Зафиксировать, что `TASKS_NIGHT.md` и `workflow.yaml` больше не являются актуальным policy-слоем и должны быть выведены из активного контура.

---

## 2. Общая оценка двух файлов

- [ ] `CLAUDE.md` и `CLAUDE_2.md` содержательно почти совпадают.
- [ ] `CLAUDE_2.md` является более аккуратной и строгой редакцией:
  - [ ] лучше нумерация;
  - [ ] яснее verification language;
  - [ ] жёстче сформулирован sub-agent режим.
- [ ] Для нашего проекта они полезны не как готовая policy “под копирование”, а как источник дисциплины:
  - [ ] phased execution;
  - [ ] forced verification;
  - [ ] context decay awareness;
  - [ ] edit safety;
  - [ ] search discipline.

**Нормативное решение:**  
Брать из этих документов **инженерные guardrails**, но не переносить их дословно вместе с чужими assumptions про стек, верификацию и веточную модель.

---

## 3. Что переносить в `AGENTS.md`

## 3.1 Переносить обязательно

### A. Phased execution для больших изменений

- [ ] Добавить правило:
  - [ ] не делать широкие multi-file refactor за один проход;
  - [ ] разбивать работу на явные фазы;
  - [ ] одна фаза = ограниченный набор файлов.

**Адаптация для нас:**
- [ ] вместо “max 5 files always” использовать:
  - [ ] `5 файлов` как default limit;
  - [ ] если больше, это должно быть явно оговорено в плане.

**Почему это полезно:**
- [ ] у нас большой `backend/orchestrator`;
- [ ] высокая связность runtime/retrieval/control-plane кода;
- [ ] слишком широкий дифф даёт дорогие регрессии.

### B. Forced verification before completion

- [ ] Добавить правило:
  - [ ] нельзя заявлять задачу завершённой без реальной проверки;
  - [ ] тип проверки зависит от затронутого слоя.

**Адаптация для нас:**
- [ ] вместо `npx tsc --noEmit` / `eslint` как универсального правила использовать project-aware verification:
  - [ ] Python:
    - [ ] `python -m py_compile`
    - [ ] targeted `pytest`
  - [ ] Frontend JS:
    - [ ] `node --check`
  - [ ] Shell/docs:
    - [ ] `bash -n`
    - [ ] `git diff --check`

### C. Re-read before edit / after edit

- [ ] Добавить правило:
  - [ ] перед каждым редактированием перечитывать файл;
  - [ ] после редактирования перечитывать файл и проверять, что правка применена корректно.

**Почему это полезно:**
- [ ] у нас длинные сессии;
- [ ] высокая вероятность stale context при работе в больших Python-модулях.

### D. Context decay awareness

- [ ] Добавить правило:
  - [ ] после длинной сессии или смены фокуса перечитывать связанные файлы;
  - [ ] не доверять памяти о старом состоянии файла.

### E. Search discipline for renames/refactors

- [ ] Добавить правило:
  - [ ] при rename/refactor не ограничиваться одним `rg`;
  - [ ] отдельно искать:
    - [ ] прямые вызовы;
    - [ ] type-level references;
    - [ ] string literals;
    - [ ] re-exports;
    - [ ] tests и mocks.

---

## 3.2 Переносить с адаптацией

### F. Step 0 cleanup

- [ ] Не переносить как “всегда вычищать dead code перед любой работой”.
- [ ] Перенести как мягкое правило:
  - [ ] перед большим структурным рефакторингом допустим отдельный preparatory cleanup slice;
  - [ ] cleanup не должен смешиваться с функциональным изменением без причины.

**Почему нужна адаптация:**
- [ ] в нашем проекте dead-code cleanup может затронуть слишком много файлов и сорвать scope.

### G. Senior dev override

- [ ] Не переносить в форме “всегда исправляй всё, что не понравится senior dev”.
- [ ] Перенести как правило:
  - [ ] если архитектурный дефект прямо мешает текущей задаче, его нужно явно назвать и либо исправить в рамках slice, либо записать в `TASKS.md`.

**Почему нужна адаптация:**
- [ ] в нашем проекте слишком агрессивное “улучшай всё вокруг” легко превращается в uncontrolled refactor.

### H. File read budget

- [ ] Перенести как техническое напоминание:
  - [ ] большие файлы читать чанками;
  - [ ] не предполагать, что один read показал весь файл.

**Почему это полезно:**
- [ ] особенно актуально для `backend/orchestrator/*`.

### I. Tool result blindness

- [ ] Перенести как правило:
  - [ ] если результат поиска/grep подозрительно мал, сузить запрос и повторить;
  - [ ] учитывать возможную усечённость вывода.

---

## 3.3 Не переносить в `AGENTS.md` дословно

### J. Универсальная верификация через `tsc` и `eslint`

- [ ] Не переносить.

**Причина:**
- [ ] наш стек не TypeScript-first;
- [ ] это создаст ложный стандарт и шум.

### K. Жёсткое обязательное sub-agent swarming

- [ ] Не переносить как безусловное правило.

**Причина:**
- [ ] у нас не каждая многокомпонентная задача подходит для параллельной декомпозиции;
- [ ] для tightly coupled backend slices это может только увеличить merge-conflict risk.

**Что оставить вместо этого:**
- [ ] правило “рассматривать декомпозицию на независимые slices при >5 независимых файлов”.

---

## 4. Что делать с `TASKS_NIGHT.md`

- [ ] Не адаптировать.
- [ ] Не развивать как текущий policy-layer.
- [ ] Рассматривать как устаревший ночной режим, который больше не соответствует актуальной модели работы.

### Почему

- [ ] текущая работа уже не опирается на отдельный “night autonomous mode” как канонический operating model;
- [ ] наличие отдельного `TASKS_NIGHT.md` размывает единый backlog/process contract;
- [ ] часть правил в нём уже либо дублирует `AGENTS.md`, либо закрепляет устаревшую branch/policy-модель.

### Нормативное решение

- [ ] нужные guardrails переносить в `AGENTS.md`, если они вообще полезны;
- [ ] сам `TASKS_NIGHT.md` пометить как неактуальный и подготовить к удалению;
- [ ] не создавать новые policy-правила в `TASKS_NIGHT.md`.
---

## 5. Что делать с `workflow.yaml`

- [ ] Не адаптировать как активный repo policy-файл.
- [ ] Не развивать как источник текущих рабочих правил.
- [ ] Рассматривать как устаревший экспериментальный execution-policy artifact.

### Почему

- [ ] файл уже содержит исторические assumptions, включая старую branch-модель;
- [ ] он дублирует policy, которая должна жить либо в `AGENTS.md`, либо в task/plans/docs;
- [ ] отдельный `workflow.yaml` повышает вероятность расхождения между “официальными” правилами в репо.

### Нормативное решение

- [ ] полезные guardrails из `workflow.yaml` переносить в `AGENTS.md`, если они всё ещё нужны;
- [ ] сам `workflow.yaml` пометить как неактуальный и подготовить к удалению;
- [ ] не использовать его как source-of-truth для дальнейших agent rules.

---

## 6. Что не стоит переносить вообще

- [ ] Обязательный `sub-agent swarming` как абсолютное правило.
- [ ] TypeScript-specific verification как основную модель верификации.
- [ ] Формулировку “всегда исправляй всё, что неидеально” без project-local scope guardrails.
- [ ] Правила, которые конфликтуют с нашим safe-night режимом и ограничением на risky operations.

---

## 7. Рекомендуемая итоговая policy-выжимка для `llm-tools-platform`

### 7.1 Для `AGENTS.md`

- [ ] phased execution for structural changes
- [ ] mandatory project-aware verification before completion
- [ ] re-read before edit / after edit
- [ ] context decay awareness
- [ ] search discipline for refactors
- [ ] optional preparatory cleanup as separate slice

### 7.2 Для устаревших policy-артефактов

- [ ] `TASKS_NIGHT.md` не развивать, а вывести из активного использования
- [ ] `workflow.yaml` не развивать, а вывести из активного использования
- [ ] нужные правила поднять в `AGENTS.md`

---

## 8. Дополнительное наблюдение по текущему состоянию репо

- [ ] В текущих [AGENTS.md](/home/seral/HDD/proj/agent-navigator-pro/AGENTS.md) и [workflow.yaml](/home/seral/HDD/proj/agent-navigator-pro/workflow.yaml) ещё остаются исторические упоминания `v3.0` как canonical base branch.
- [ ] Это уже не соответствует актуальной branch-модели репозитория.
- [ ] При policy refresh нужно:
  - [ ] обновить `AGENTS.md`;
  - [ ] не переносить эти assumptions в новые policy-документы;
  - [ ] удалить `workflow.yaml` вместо его дальнейшей поддержки.

---

## 9. Нормативное решение

- [ ] Взять из `CLAUDE.md` / `CLAUDE_2.md` только engineering discipline rules.
- [ ] Не переносить чужие стек-специфичные assumptions.
- [ ] Усилить наши существующие guardrails, а не заменять их.
- [ ] Делать policy refresh отдельным slice:
  - [ ] обновить `AGENTS.md`
  - [ ] пометить `TASKS_NIGHT.md` как obsolete
  - [ ] удалить `workflow.yaml`
