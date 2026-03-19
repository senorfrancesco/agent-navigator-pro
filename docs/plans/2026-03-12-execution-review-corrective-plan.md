# Execution Review: Corrective Plan vs TASKS

**Reviewed sources:**
- [Unified Recovery And UI Plan](./2026-03-11-unified-recovery-and-ui-plan.md)
- [TASKS.md](../../TASKS.md)

## Executive Verdict

Текущий corrective plan в [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md)
остаётся корректным как основной execution-order документ.

План [2026-03-11-orchestration-stabilization-plan.md](./2026-03-11-orchestration-stabilization-plan.md)
нужно трактовать как более узкий stabilization-subset, а не как отдельный competing canon.

Главное:

1. Порядок из плана в целом правильный: сначала orchestration boundary, потом classifier quality,
   потом RAG/retrieval split, затем runtime budgeting, и только после этого UI/Ops расширения.
2. `TASKS.md` в целом согласован с этим планом, но backlog теперь нужно читать не как flat list,
   а как dependency graph относительно unified-plan.
3. На текущем этапе главный риск не в отсутствии задач, а в расползании по фронтам:
   classifier, retrieval, KB-RAG, tiers, UI, runtime budgeting и resume нельзя вести как
   равноправные параллельные линии.

## Что уже можно считать validated

По состоянию репо и последних решений уже зафиксировано:

1. Backend-first orchestration — правильное направление.
   Основание: [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md)
   и [TASKS.md](../../TASKS.md) `B3.31`.
2. Default classifier contour сейчас должен быть:
   - `intent`: `Qwen3-Embedding-0.6B`
   - `legal/doc similarity`: `LaBSE`
   Основание: [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md)
   и [TASKS.md](../../TASKS.md) `B3.21`.
3. `Session RAG` и `Knowledge Base RAG` нужно вести как разные продуктовые режимы.
   Основание: [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md)
   и [TASKS.md](../../TASKS.md) `B3.33`.
   Уточнение:
   - это отдельная ось `rag_scope`, а не расширение `runtime_mode`;
   - `knowledge_base_rag` должен уметь делать `knowledge base + session overlay`
     с явным provenance источников.
4. Названия `tiers` нельзя больше трактовать как fully implemented `agentic/multi-agent`.
   Основание: [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md)
   и [TASKS.md](../../TASKS.md) `B3.35`.

## Реальный порядок исполнения

### Block A — Закрыть orchestration boundary

**Primary:** `B3.31`

Почему first:

- все остальные блоки зависят от того, что UI перестанет быть вторым оркестратором;
- без этого classifier, runtime modes, citations и KB-RAG будут снова частично жить в UI.

Что считать finish condition:

- backend является единственным source of truth для `route`, `executor`, `action_required`;
- backend является единственным source of truth для `rag_scope`,
  `knowledge_collection_id` и `source_scope_summary`;
- workflow-слой не зависит жёстко от `Chainlit` runtime;
- `Chainlit` работает как thin renderer.

Уточнение по persistence ownership:

- temporary pragmatic step больше не актуален;
- отдельная фаза [B3.31a](../../TASKS.md) уже внедрила backend-owned `orchestrator_runs`
  для `run_id/state_ref/pending_action_id/resume_state_blob/checkpoint_blob`;
- `Chainlit` остался presentation/control-plane слоем и использует backend snapshot как
  primary resume source.

Статус execution-review:

- этот блок уже начат и частично реализован;
- его нельзя считать “закрытым концептуально и забытым”, пока не убраны оставшиеся UI-local
  routing/presentation leakage и не закрыт progress adapter path.
- legacy `/v1/chat/completions` должен быть доведён до compatibility adapter над unified backend core,
  без отдельного `sessions/run_workflow_stream/session-RAG` мира;
- hanging `test_chainlit_streaming.py` должен быть либо исправлен, либо иметь доказанный root cause
  и зафиксированный workaround.
- legacy upload discovery через `open_webui_uploads` не должен silently влиять на любой
  внешний `/v1/chat/completions` client; допустим только явный compatibility opt-in.

### Block B — Зафиксировать classifier contour

**Primary:** `B3.21`

Этот блок уже перешёл из research в decision:

- default classifier = `embedder`
- default intent embedder = `Qwen3-Embedding-0.6B`
- pure `llm` и текущий `hybrid` не являются production default

Что здесь ещё реально осталось:

- `abstain / unsure / needs_confirmation`
- calibration thresholds
- policy integration в orchestration runtime

Execution verdict:

- блок нельзя считать завершённым до внедрения runtime policy,
  но модельный выбор для intent-routing уже достаточно стабилен,
  чтобы не спорить о нём параллельно с другими задачами.

### Block C — Retrieval decision before RAG unification

**Primary:** `B3.34`

Это следующий обязательный gate перед любым переносом dense retrieval с `LaBSE` на `Qwen3`.

Почему:

- intent benchmark не равен retrieval benchmark;
- в legal/doc retrieval ошибка модели проявляется иначе, чем в routing.

Execution verdict:

- до завершения `B3.34` retrieval embedder не унифицировать;
- `LaBSE` остаётся retrieval/legal baseline;
- `Qwen3-Embedding-0.6B` уже accepted только для intent contour.
- eval не должен ограничиваться retrieval-only метриками:
  нужны также `source_origin` accuracy, mixed-scope cases и answer faithfulness.

Status update 2026-03-13:
- initial curated retrieval eval harness implemented;
- first CPU run on the curated matrix produced parity between `LaBSE` and `Qwen3-Embedding-0.6B`;
- decision remains conservative: keep `LaBSE` as retrieval/legal baseline and expand dataset before reconsidering migration;
- harness is now fail-fast on invalid dataset entries and reports deterministic JSON metrics (`recall_at_k`, `mrr`, `ndcg_at_k`, `evidence_hit_rate`, `source_origin_accuracy`, lexical `answer_faithfulness`).

### Block D — Product split: Session RAG vs Knowledge Base

**Primary:** `B3.33`

Почему не раньше `B3.34`:

- сначала нужно понимать retrieval contour и evidence UX;
- иначе можно начать строить persistent KB на неустоявшемся retrieval contract.

Что реально должно войти:

- отдельная backend-ось `rag_scope`, а не перегрузка `runtime_mode`
- source registry
- schema/versioning поля для retrieval contour:
  - `embedding_model_id`
  - `retrieval_embedder_profile`
  - `chunking_version`
- upload policy
- scope model
- merged retrieval policy:
  - `session_rag` -> только `active_doc_ids`
  - `knowledge_base_rag` -> `knowledge base + session overlay`, если есть активные документы
- shared doc-QA route:
  - `route=document_question` остаётся общим
  - меняется retrieval contour и evidence/provenance contract
- UX разделение `Session RAG` / `Knowledge Base` / `General Chat`

Дополнительное UX-правило:

- `ChatProfile` лучше оставлять coarse entrypoint;
- mutable выбор `session_rag` / `knowledge_base_rag` держать в `rag_scope` selector / tabs / workspace mode,
  а не делать его основным profile-state.

Execution verdict:

- это следующий крупный продуктовый шаг после classifier/retrieval gates;
- это не “мелкий UI task”, а отдельный data/model boundary.
- Status on 2026-03-13:
  - выполнено как V1 через backend-owned KB source registry и unified merged retrieval;
  - `knowledge_base_rag` больше не только control-plane vocabulary;
  - persisted embeddings / vector index / reranker вынесены в follow-up hardening phase.

### Block E — Evidence UX и honest tiers

**Primary:** `B3.35`, `B3.36`

Почему вместе:

- честные tiers влияют на ожидания от системы;
- citations/evidence UX влияют на доверие к ответам.

Execution verdict:

- эти задачи надо делать до широких UI/Ops улучшений;
- иначе UI начнёт красиво упаковывать неправильно названные режимы и не до конца grounded answers.
- выполнено:
  - public-facing tiers переведены на honest wording без смены runtime keys;
  - evidence UX v2 теперь показывает `source_scope_summary`, `source_origin`, `collection_id`, `section/page` там, где они доступны;
  - verification: `cd backend && pytest tests/ -q -m "not integration"` -> `388 passed, 4 deselected`.

### Block F — Runtime budgeting и hardware adaptation

**Primary:** `T4.13`, `T4.14`

Почему после classifier/retrieval gates:

- budgeting нужен обязательно;
- но он не должен подменять собой product/data decisions по RAG.

Execution verdict:

- реализовывать как infrastructure follow-up;
- держать native-first dev path;
- container path использовать как packaging/prod validation.

Статус на 2026-03-13:

- `T4.13` реализован;
- runtime budget теперь публикуется из `UMS /status` и используется downstream в `Chainlit`/RAG;
- retrieved context budget вычисляется от `effective_context_tokens`, а не от fixed char caps;
- `T4.14` тоже реализован: canonical runtime path теперь `launcher.sh + runtime_preflight.py`, а старые launch scripts оставлены как compatibility wrappers.

### Block G — UX controls и profiles

**Primary:** `T4.2` implemented, `T4.3` implemented

Что можно делать только после предыдущих блоков:

- вкладки `Session RAG` / `Knowledge Base` / `General Chat`
- runtime mode selector
- model/profile selectors
- control-plane settings layer между `Chainlit` и backend:
  - `assistant_mode`
  - `runtime_mode`
  - `rag_scope`
  - `model_profile`
  - `prompt_profile`
  - `generation_overrides`
  - `custom_system_prompt`
- richer citations/evidence presentation
- welcome screen / starter cards для use-case entrypoints

Execution verdict:

- UI уже не должен определять поведение системы;
- он должен только раскрывать backend/runtime/product modes, которые уже стабилизированы.
- code-defined defaults и UX overrides должны сходиться в backend-resolved effective config,
  а не применяться напрямую в UI.

## Что сейчас нельзя распараллеливать без вреда

Нельзя одновременно считать приоритетом:

1. полноценный `Knowledge Base` режим
2. перенос retrieval на `Qwen3-Embedding-0.6B`
3. новый UI layer с profiles/tabs/settings
4. resume/session recovery cluster
5. multi-agent RAG

Причина:

- это пять разных классов задач с разными источниками риска;
- их смешивание снова размоет execution order, ради которого и создавался unified-plan.

## Что отложить без сожаления

По отношению к [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md)
и [TASKS.md](../../TASKS.md) сейчас можно уверенно отложить:

- `B3.31a` как backend-authoritative persistence migration beyond current pragmatic boundary step
- `B3.32` как full-scale concern beyond ADR-level clarification
- `T4.4` / `T4.5` / `T4.6`
- `T4.8` / `T4.9` / `T4.10`
- любой реальный `multi-agent` runtime

Это не значит, что задачи плохие. Это значит, что они не разблокируют текущий corrective path.

## Рекомендуемый execution order на практике

1. Дожать `B3.31` до конца.
2. Завершить runtime policy для `B3.21`.
3. Сделать `B3.34` retrieval eval.
4. На основе `B3.34` принять решение по retrieval embedder.
5. Довести `B3.33` как product/data split поверх уже введённого в `B3.31` backend contract
   (`rag_scope`, `knowledge_collection_id`, `source_scope_summary`).
6. Довести `B3.35` и `B3.36`.
7. Только потом идти в `T4.13`, `T4.14`, `T4.2`, `T4.3`.
   Статус на сейчас: `T4.2` implemented, `T4.3` implemented.

## Короткий управленческий вывод

Если читать [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md)
как главный execution document, то `TASKS.md` сейчас уже хорошо ему подчинён.

Правильное чтение backlog теперь такое:

- `B3.31` — boundary
- `B3.21` — routing quality
- `B3.34` — retrieval truth test
- `B3.33` — product/data split поверх отдельной оси `rag_scope`
- `B3.35/B3.36` — trust and honesty layer
- `T4.*` — only after the above

Именно этот порядок сейчас лучше всего сохраняет corrective strategy, а не распыляет проект обратно.
