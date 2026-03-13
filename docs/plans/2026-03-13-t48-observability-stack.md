# T4.8 Observability stack

## Goal

Собрать минимальный production-safe observability слой для `UMS` и `Agent API` без full APM platform rewrite.

## Safe scope

### In scope

- Prometheus-compatible `/metrics` для:
  - `backend/orchestrator/agent_api.py`
  - `backend/services/model_manager/unified_model_server.py`
- HTTP request telemetry:
  - request counters
  - latency histogram
  - in-flight gauge
- trace-aware request logging:
  - `trace_id`
  - `method`
  - `path`
  - `status_code`
  - `duration_ms`
- response header propagation:
  - `X-Trace-Id`
- minimal operator gauges для `UMS`:
  - running models
  - active heavy model
- docker compose profile:
  - `prometheus`
  - `grafana`
- Grafana provisioning:
  - datasource
  - basic dashboard
- targeted tests + docs/TASKS sync

### Out of scope

- distributed tracing backend (`Jaeger`/`Tempo`)
- OpenTelemetry exporter stack
- Loki / centralized log shipping
- alertmanager / on-call routing
- per-user cost analytics

## Implementation slice

1. Add shared observability module:
   - metrics registry
   - HTTP counters/histograms/gauges
   - helper for metrics exposition
2. Instrument `UMS`:
   - `/metrics`
   - middleware for request telemetry
   - update gauges from runtime state
3. Instrument `Agent API`:
   - `/metrics`
   - middleware for request telemetry
   - response `X-Trace-Id`
4. Add compose profile and monitoring assets:
   - `monitoring/prometheus.yml`
   - `monitoring/grafana/provisioning/...`
   - baseline dashboard JSON
5. Add targeted tests:
   - `/metrics` endpoint reachable
   - expected metric names present
   - `X-Trace-Id` propagation on health endpoints
6. Run targeted and full backend verification.

## Verification

- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_agent_api_metrics.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/observability.py backend/services/model_manager/unified_model_server.py backend/orchestrator/agent_api.py backend/tests/test_agent_api_metrics.py backend/tests/test_unified_model_server_startup.py`
- `bash -n scripts/run_all.sh scripts/run_native.sh scripts/run_container.sh`
- `git diff --check`

## Status

Implemented as a safe slice:
- shared Prometheus-compatible metrics exporter without adding a new runtime dependency
- `/metrics` for `UMS` and `Agent API`
- ASGI request middleware with `X-Trace-Id` propagation and request-completion logging
- `monitoring` docker compose profile with Prometheus + Grafana provisioning

Deferred follow-up:
- full distributed tracing backend and centralized log stack stay out of scope
- tmux window rename from `monitor` to `syswatch` is tracked as separate ops cleanup
- health monitoring for non-metrics services (`document_server`, `legal_server`, `chainlit`) should use a blackbox/synthetic probe block rather than scraping JSON `/health` with Prometheus
