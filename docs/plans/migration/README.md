# Migration Plans Index

Эта папка является каноническим местом для migration-related планов по `Open WebUI`, `OpenAPI Tool Server`, `MCP`, `KnowledgeBaseStoreProtocol`, `Qdrant` и rollout-критериям.

## Current Recommended Reading Order

1. [2026-04-02-unified-openwebui-migration-master-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-02-unified-openwebui-migration-master-plan.md)
   Единый master-plan по migration contour.
2. [2026-04-07-openapi-tool-server-mvp-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-07-openapi-tool-server-mvp-plan.md)
   MVP-contract для backend-owned OpenAPI Tool Server.
3. [2026-04-08-openwebui-model-tool-split-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-model-tool-split-plan.md)
   Source-backed plan по разделению raw model provider и tool server в `Open WebUI`.
4. [2026-04-08-openwebui-named-tools-bootstrap-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-named-tools-bootstrap-plan.md)
   Execution-ready plan по named tools, thin Python wrappers и idempotent bootstrap в `Open WebUI`.
5. [2026-04-07-openwebui-mcp-tools-actions-architecture-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-07-openwebui-mcp-tools-actions-architecture-plan.md)
   Подробное ТЗ по `MCP`, `OpenAPI`, slash/prompts, actions и rich UI.

## Supporting Migration Plans

- [2026-04-12-single-env-runtime-convergence-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-12-single-env-runtime-convergence-plan.md)
  План по сведению runtime-контракта к одному `backend/.env` и выводу `backend/.env.runtime` из активного product/operator path.
- [2026-04-01-knowledge-base-store-protocol-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-knowledge-base-store-protocol-plan.md)
  Подготовка storage contract перед заменой backend.
- [2026-04-01-qdrant-knowledge-base-store-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md)
  План перехода на `Qdrant`.

## Historical Reference

Эти документы полезны как контекст ранних решений, но не являются операционными source-of-truth. Актуальные фазы и acceptance criteria живут в [TASKS_MIGRATION.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS_MIGRATION.md), а главный архитектурный narrative живёт в master-plan.

- [2026-04-01-openwebui-tool-server-integration-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-openwebui-tool-server-integration-plan.md)
  Более ранний integration-plan, перекрыт master-plan и `TASKS_MIGRATION.md`.
- [2026-04-01-openwebui-migration-sprint-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-openwebui-migration-sprint-plan.md)
  Sprint decomposition раннего migration-stage, перекрыт текущими `M0-M4` фазами.
- [2026-04-01-migration-readiness-pr-decision-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-migration-readiness-pr-decision-plan.md)
  Audit/decision документ по старым PR и historical branch material.

## Operational Rule

Если новый план напрямую касается migration на `Open WebUI`, `OpenAPI Tool Server`, `MCP`, backend tool contracts, upload/document binding или `KB/Qdrant` prerequisites для migration, его нужно класть именно в `docs/plans/migration/`, а не в общий корень `docs/plans/`.
