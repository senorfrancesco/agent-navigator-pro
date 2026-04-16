from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import pytest
from starlette.testclient import TestClient

from orchestrator.operator_ui_api import (
    OperatorConfigApplyRequest,
    OperatorConfigPresetPreviewRequest,
    build_operator_state,
    enforce_operator_localhost_only,
    is_operator_surface_path,
    operator_path_browser,
    operator_path_browser_validate,
    operator_config_apply,
    operator_config_preset_preview,
    operator_config_variants,
    operator_action_run,
    operator_health,
    OperatorActionRequest,
    operator_runtime_health,
    operator_qdrant_summary,
    operator_metrics_summary,
    operator_grafana_links,
    operator_job_cancel,
    operator_tool_action_catalog,
    operator_tool_bindings,
    operator_tool_bindings_export_openwebui,
    router,
)


class _FakeQdrantCollectionsResponse:
    def __init__(self, names):
        self.collections = [SimpleNamespace(name=name) for name in names]


class _FakeQdrantClient:
    collections_payloads = {}

    def __init__(self, url=None):
        self.url = url

    def get_collections(self):
        return _FakeQdrantCollectionsResponse(list(self.collections_payloads.keys()))

    def scroll(self, collection_name, limit, offset=None, with_payload=True, with_vectors=False):
        payloads = list(self.collections_payloads.get(collection_name, []))
        start = int(offset or 0)
        end = start + int(limit)
        chunk = payloads[start:end]
        next_offset = end if end < len(payloads) else None
        return [SimpleNamespace(payload=item) for item in chunk], next_offset

    def close(self):
        return None


def test_operator_ui_state_exposes_python_control_plane_contract():
    state = build_operator_state()

    assert state["delivery"]["control_plane"] == "python"
    assert state["delivery"]["ui_mode"] == "web-first"
    assert state["delivery"]["shell_role"] == "compatibility"
    assert state["delivery"]["actions_backend"] == "python_operator_runner"
    assert state["toolUx"]["directActionCount"] == 2
    assert state["toolUx"]["promptShortcutCount"] == 2


def test_operator_health_exposes_delivery_contract():
    payload = operator_health()

    assert payload["status"] == "ok"
    assert payload["operator_ui_served"] is True
    assert payload["delivery"]["control_plane"] == "python"
    assert payload["delivery"]["shell_role"] == "compatibility"


def _make_request(path: str, client_host: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": [],
            "client": (client_host, 40000),
            "server": ("testserver", 80),
        }
    )


def test_operator_access_guard_allows_loopback(monkeypatch):
    monkeypatch.setenv("OPERATOR_UI_LOCALHOST_ONLY", "true")

    enforce_operator_localhost_only(_make_request("/operator/state", "127.0.0.1"))
    enforce_operator_localhost_only(_make_request("/operator-ui/", "::1"))


def test_operator_access_guard_blocks_remote_host(monkeypatch):
    monkeypatch.setenv("OPERATOR_UI_LOCALHOST_ONLY", "true")

    with pytest.raises(Exception) as exc_info:
        enforce_operator_localhost_only(_make_request("/operator/state", "203.0.113.10"))

    assert getattr(exc_info.value, "status_code", None) == 403
    assert getattr(exc_info.value, "detail", "") == "operator-ui-localhost-only"


def test_operator_access_guard_can_be_disabled(monkeypatch):
    monkeypatch.setenv("OPERATOR_UI_LOCALHOST_ONLY", "false")

    enforce_operator_localhost_only(_make_request("/operator/state", "203.0.113.10"))


def test_operator_surface_path_matches_api_and_static_paths():
    assert is_operator_surface_path("/operator/state") is True
    assert is_operator_surface_path("/operator-ui/") is True
    assert is_operator_surface_path("/operator-assets/app.css") is True
    assert is_operator_surface_path("/health") is False


def test_operator_ui_state_exposes_observability_contract():
    state = build_operator_state()

    assert "metricsSummary" in state
    assert "grafanaLinks" in state
    assert {"overview", "services", "deploy"} <= set(state["metricsSummary"].keys())
    assert state["warnings"] == [] or "titleEn" in state["warnings"][0]
    assert "noteEn" in state["serviceRows"][0]
    assert "titleEn" in state["maintenanceActions"][0]
    assert "labelEn" in state["deploySurface"]["build"]


def test_operator_metrics_and_grafana_endpoints_return_payloads():
    request = SimpleNamespace(url=SimpleNamespace(port=18000))
    metrics_payload = operator_metrics_summary(request)
    grafana_payload = operator_grafana_links(request)

    assert {"overview", "services", "deploy"} <= set(metrics_payload.keys())
    assert "links" in grafana_payload


def test_operator_metrics_summary_exposes_timing_summary():
    metrics_payload = operator_metrics_summary(SimpleNamespace(url=SimpleNamespace(port=18000)))

    for section in ("overview", "services", "deploy"):
        assert isinstance(metrics_payload[section], list)
    assert "timingSummary" in metrics_payload
    assert {"latest", "by_executor"} <= set(metrics_payload["timingSummary"].keys())


def test_operator_qdrant_summary_reports_namespace_separation(monkeypatch):
    monkeypatch.setenv("KB_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://fake-qdrant:6333")
    monkeypatch.setenv("QDRANT_COLLECTION_NAME", "rag_chunks_v1")
    monkeypatch.setenv("OPENWEBUI_SESSION_RAG_HANDOFF", "preferred")
    monkeypatch.setattr("orchestrator.operator_ui_api._load_qdrant_client_class", lambda: _FakeQdrantClient)
    _FakeQdrantClient.collections_payloads = {
        "rag_chunks_v1": [
            {"source_scope": "knowledge", "document_id": "kb-1"},
            {"source_scope": "session", "document_id": "doc-1", "thread_id": "thread-1", "expires_at": 4102444800.0},
            {"source_scope": "session", "document_id": "doc-2", "thread_id": "thread-2", "expires_at": 946684800.0},
        ],
        "anp-openwebui-team-a": [],
        "anp-openwebui-team-b": [],
    }

    payload = operator_qdrant_summary()

    assert payload["backend"]["mode"] == "qdrant"
    assert payload["backend"]["collectionName"] == "rag_chunks_v1"
    assert payload["server"]["reachable"] is True
    assert payload["server"]["collections"] == [
        "anp-openwebui-team-a",
        "anp-openwebui-team-b",
        "rag_chunks_v1",
    ]
    assert payload["sessionHandoff"] == {"mode": "preferred", "enabled": True}
    assert payload["openWebUI"]["collections"] == ["anp-openwebui-team-a", "anp-openwebui-team-b"]
    assert payload["namespaces"]["separationOk"] is True
    assert payload["namespaces"]["issues"] == []
    assert payload["payloads"] == {
        "knowledgeCount": 1,
        "sessionCount": 2,
        "unknownCount": 0,
        "activeSessionCount": 1,
        "expiredSessionCount": 1,
        "sessionMetadataOk": True,
    }


def test_operator_qdrant_summary_flags_namespace_collision(monkeypatch):
    monkeypatch.setenv("KB_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://fake-qdrant:6333")
    monkeypatch.setenv("QDRANT_COLLECTION_NAME", "anp-openwebui-shared")
    monkeypatch.setenv("OPENWEBUI_SESSION_RAG_HANDOFF", "off")
    monkeypatch.setattr("orchestrator.operator_ui_api._load_qdrant_client_class", lambda: _FakeQdrantClient)
    _FakeQdrantClient.collections_payloads = {
        "anp-openwebui-shared": [],
        "anp-openwebui-tenant-a": [],
    }

    payload = operator_qdrant_summary()

    assert payload["namespaces"]["separationOk"] is False
    assert payload["sessionHandoff"] == {"mode": "off", "enabled": False}
    assert any("backend collection name overlaps" in issue for issue in payload["namespaces"]["issues"])


def test_operator_qdrant_summary_route_returns_payload(monkeypatch):
    monkeypatch.setenv("OPERATOR_UI_LOCALHOST_ONLY", "false")
    monkeypatch.setattr(
        "orchestrator.operator_ui_api._build_qdrant_summary",
        lambda *_args, **_kwargs: {"backend": {"mode": "qdrant"}, "namespaces": {"separationOk": True}},
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/operator/rag/qdrant/summary", headers={"host": "127.0.0.1"})

    assert response.status_code == 200
    assert response.json() == {
        "backend": {"mode": "qdrant"},
        "namespaces": {"separationOk": True},
    }


def test_build_operator_state_includes_qdrant_summary(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.operator_ui_api._build_qdrant_summary",
        lambda *_args, **_kwargs: {"backend": {"mode": "qdrant"}, "namespaces": {"separationOk": True}},
    )

    state = build_operator_state()

    assert state["qdrantSummary"] == {
        "backend": {"mode": "qdrant"},
        "namespaces": {"separationOk": True},
    }


def test_operator_tool_bindings_expose_enabled_and_document_dependent_entries():
    payload = operator_tool_bindings()

    assert payload["summary"]["directActionCount"] == 2
    assert payload["summary"]["disabledDocumentDependentCount"] >= 4
    binding_ids = {item["binding_id"] for item in payload["bindings"]}
    assert "equipment.fast.direct" in binding_ids
    assert "document.ask.direct" in binding_ids


def test_operator_tool_action_catalog_splits_direct_actions_and_prompts():
    payload = operator_tool_action_catalog()

    direct_ids = {item["binding_id"] for item in payload["directActions"]}
    prompt_ids = {item["binding_id"] for item in payload["promptShortcuts"]}
    blocked_ids = {item["binding_id"] for item in payload["blockedBindings"]}

    assert direct_ids == {"equipment.fast.direct", "equipment.deep.direct"}
    assert prompt_ids == {"equipment.fast.prompt", "equipment.deep.prompt"}
    assert "document.ask.direct" in blocked_ids


def test_operator_tool_bindings_export_openwebui_returns_manual_import_bundle():
    payload = operator_tool_bindings_export_openwebui("http://127.0.0.1:18000")

    assert payload["toolServer"]["baseUrl"] == "http://127.0.0.1:18000/tool-server"
    assert payload["toolServer"]["browserReachableBaseUrl"] == "http://127.0.0.1:18000/tool-server"
    assert payload["toolServer"]["containerReachableBaseUrl"] == "http://host.docker.internal:18000/tool-server"
    assert payload["toolServer"]["manualEnableRequired"] is False
    assert payload["toolServer"]["pickerVisible"] is False
    assert payload["toolServer"]["defaultBootstrapManaged"] is False
    assert len(payload["workspaceTools"]) == 2
    assert payload["workspaceTools"][0]["tool_id"] == "equipment_fast_tool"
    assert "class Tools" in payload["workspaceTools"][0]["pythonCode"]
    assert "/tools/{target_tool_name}" in payload["workspaceTools"][0]["pythonCode"]
    assert "analyze_document_fast" in payload["workspaceTools"][0]["pythonCode"]
    assert "analyze_equipment_fast" in payload["workspaceTools"][0]["pythonCode"]
    assert payload["directActions"][0]["binding_id"] == "equipment.fast.direct"
    assert payload["workspacePrompts"][0]["slash_command"] == "/hw_fast"
    assert payload["workspacePrompts"][0]["manualImportRequired"] is True
    assert payload["workspacePrompts"][0]["openwebui"]["command"] == "/hw_fast"
    assert payload["toolCatalog"]["enabled"][0]["name"] == "analyze_equipment_fast"
    assert payload["toolCatalog"]["enabled"][1]["name"] == "analyze_equipment_deep"
    deferred_names = {item["name"] for item in payload["toolCatalog"]["deferred"]}
    assert deferred_names == {
        "ask_document",
        "analyze_document_fast",
        "analyze_document_deep",
        "compare_documents_fast",
        "compare_documents_deep",
    }
    assert len(payload["actionFunctions"]) == 4
    action_ids = {item["action_id"] for item in payload["actionFunctions"]}
    assert action_ids == {
        "equipment_fast_action",
        "equipment_deep_action",
        "tool_job_refresh_action",
        "tool_job_cancel_action",
    }
    assert payload["runtimeConfig"]["rag"] == {
        "vectorDb": "qdrant",
        "embeddingEngine": "openai",
        "embeddingModel": "labse-embedding",
        "embeddingOpenAIBaseUrl": "http://host.docker.internal:8090/v1",
        "rerankingEngine": "",
    }
    assert payload["knowledgeConfig"]["bootstrapMode"] == "manual_checklist"
    assert payload["knowledgeConfig"]["sessionFlow"] == "backend_owned_qdrant"
    assert payload["qdrantConfig"] == {
        "provider": "qdrant",
        "uri": "http://qdrant:6333",
        "collectionPrefix": "anp-openwebui",
        "multitenancy": True,
        "backendCollectionNameSource": "backend_env:QDRANT_COLLECTION_NAME",
        "ownership": "shared_server_separate_namespaces",
    }
    assert payload["manualChecklist"]["sessionRag"][0].startswith("Проверьте, что backend запущен")
    assert payload["manualChecklist"]["knowledgeQdrant"][0].startswith("Откройте `Admin Settings -> Documents`")
    assert payload["preflightRequirements"]["requiredServices"] == ["agent-api", "open-webui", "qdrant", "ums"]
    assert payload["preflightRequirements"]["backendEnv"] == [
        "OPENAPI_TOOL_SERVER_TOKEN",
        "KB_BACKEND",
        "QDRANT_URL",
        "QDRANT_COLLECTION_NAME",
    ]
    equipment_fast_action = next(item for item in payload["actionFunctions"] if item["action_id"] == "equipment_fast_action")
    assert equipment_fast_action["manualImportRequired"] is True
    assert equipment_fast_action["targetModels"] == ["raw.*"]
    assert equipment_fast_action["isActive"] is True
    assert equipment_fast_action["isGlobal"] is True
    assert "Authorization" in equipment_fast_action["pythonCode"]
    assert "/tools/{target_tool_name}" in equipment_fast_action["pythonCode"]
    assert "analyze_document_fast" in equipment_fast_action["pythonCode"]
    assert "analyze_equipment_fast" in equipment_fast_action["pythonCode"]
    assert payload["importChecklist"][0].startswith("1. Создайте только именованные tools")


def test_operator_config_apply_rejects_unknown_path():
    try:
        operator_config_apply(
            "missing",
            OperatorConfigApplyRequest(updates={"FOO": "bar"}),
        )
    except Exception as exc:  # FastAPI raises HTTPException directly in function tests
        assert getattr(exc, "status_code", None) == 404
    else:  # pragma: no cover - should not happen
        raise AssertionError("unknown path should raise HTTPException")


def test_operator_config_variants_and_preset_preview_return_payloads():
    variants = operator_config_variants("container")
    preview = operator_config_preset_preview(
        "container",
        OperatorConfigPresetPreviewRequest(preset_id="bundle-local-safe-ports"),
    )

    assert "variants" in variants
    assert preview["presetId"] == "bundle-local-safe-ports"
    assert preview["updates"]["AGENT_API_PORT"] == "18000"


def test_operator_runtime_health_returns_summary_for_path():
    payload = operator_runtime_health("native", SimpleNamespace(url=SimpleNamespace(port=18000)))

    assert payload["pathKey"] == "native"
    assert "status" in payload
    assert "checkCount" in payload
    assert "summary" in payload
    assert "reason" in payload
    assert "nextAction" in payload


def test_operator_runtime_health_marks_container_as_blocked_with_actionable_summary():
    payload = operator_runtime_health("container", SimpleNamespace(url=SimpleNamespace(port=8000)))

    assert payload["pathKey"] == "container"
    assert payload["status"] in {"blocked", "not_started"}
    assert payload["reason"]
    assert payload["summary"]
    assert payload["nextAction"]


def test_operator_runtime_health_does_not_mark_native_running_from_static_files(monkeypatch):
    def fake_probe(url: str, timeout: float = 0.75) -> bool:
        return False

    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", fake_probe)

    payload = operator_runtime_health("native", SimpleNamespace(url=SimpleNamespace(port=18000)))

    assert payload["pathKey"] == "native"
    assert payload["status"] == "not_started"
    assert payload["runningChecks"] == 0


def test_operator_runtime_health_marks_native_running_from_live_primary_probes(monkeypatch):
    def fake_probe(url: str, timeout: float = 0.75) -> bool:
        return any(port in url for port in (":8000", ":3000", ":8090"))

    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", fake_probe)

    payload = operator_runtime_health("native", SimpleNamespace(url=SimpleNamespace(port=18000)))

    assert payload["pathKey"] == "native"
    assert payload["status"] == "running"


def test_operator_state_marks_container_rows_not_started_without_bundle_runtime(monkeypatch):
    monkeypatch.setattr("orchestrator.operator_ui_api._offline_bundle_running_services", lambda _root: set())
    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", lambda _url, timeout=0.75: True)

    state = build_operator_state(18000)
    container_rows = [row for row in state["serviceRows"] if row["path"] == "container"]

    assert container_rows
    assert all(row["status"] == "not_started" for row in container_rows)


def test_operator_state_marks_partial_container_runtime_when_compose_services_missing(monkeypatch):
    monkeypatch.setattr("orchestrator.operator_ui_api._offline_bundle_running_services", lambda _root: {"agent-api", "chainlit"})
    monkeypatch.setattr("orchestrator.operator_ui_api._http_probe", lambda _url, timeout=0.75: True)

    state = build_operator_state(18000)
    container_rows = [row for row in state["serviceRows"] if row["path"] == "container"]

    degraded = [row for row in container_rows if row["status"] == "degraded"]
    assert degraded


@pytest.mark.asyncio
async def test_operator_action_run_returns_job_snapshot(monkeypatch):
    fake_job = SimpleNamespace(
        to_dict=lambda: {
            "job_id": "job-123",
            "action_id": "runtime.native.launch",
            "status": "queued",
        }
    )
    monkeypatch.setattr(
        "orchestrator.operator_ui_api.start_action_job",
        AsyncMock(return_value=fake_job),
    )

    payload = await operator_action_run(
        SimpleNamespace(url=SimpleNamespace(port=9000)),
        OperatorActionRequest(action_id="runtime.native.launch"),
    )

    assert payload["job_id"] == "job-123"
    assert payload["status"] == "queued"


@pytest.mark.asyncio
async def test_operator_action_run_accepts_runtime_stop_action(monkeypatch):
    fake_job = SimpleNamespace(
        to_dict=lambda: {
            "job_id": "job-stop-123",
            "action_id": "runtime.native.stop",
            "status": "queued",
        }
    )
    monkeypatch.setattr(
        "orchestrator.operator_ui_api.start_action_job",
        AsyncMock(return_value=fake_job),
    )

    payload = await operator_action_run(
        SimpleNamespace(url=SimpleNamespace(port=9000)),
        OperatorActionRequest(action_id="runtime.native.stop"),
    )

    assert payload["job_id"] == "job-stop-123"
    assert payload["action_id"] == "runtime.native.stop"


@pytest.mark.asyncio
async def test_operator_job_cancel_returns_job_snapshot(monkeypatch):
    fake_job = SimpleNamespace(
        to_dict=lambda: {
            "job_id": "job-cancel-123",
            "action_id": "deploy.runtime.run",
            "status": "cancelling",
        }
    )
    monkeypatch.setattr(
        "orchestrator.operator_ui_api.cancel_action_job",
        AsyncMock(return_value=fake_job),
    )

    payload = await operator_job_cancel("job-cancel-123")

    assert payload["job_id"] == "job-cancel-123"
    assert payload["status"] == "cancelling"


def test_operator_path_browser_lists_allowed_roots():
    payload = operator_path_browser()

    assert payload["kind"] == "file"
    assert payload["cwd"] == ""
    assert any(entry["type"] == "directory" for entry in payload["entries"])


def test_operator_path_browser_validate_accepts_repo_path():
    payload = operator_path_browser_validate(path=str(Path.cwd()), kind="directory")

    assert payload["exists"] is True
    assert payload["valid"] is True


def test_operator_path_browser_validate_reports_model_file_semantics(tmp_path, monkeypatch):
    candidate = tmp_path / "model.gguf"
    candidate.write_text("weights", encoding="utf-8")
    monkeypatch.setattr("orchestrator.operator_ui_api.PATH_BROWSER_ROOTS", [tmp_path])

    payload = operator_path_browser_validate(
        path=str(candidate),
        kind="file",
        field_key="HOST_MODEL_PATH_LLM",
    )

    assert payload["valid"] is True
    assert payload["status"] == "ok"
    assert "GGUF extension detected" in payload["checks"]


def test_operator_path_browser_resolves_repo_relative_paths_from_repo_root(tmp_path, monkeypatch):
    bundle_model = tmp_path / "deploy" / "offline_bundle" / "models" / "gguf" / "qwen.gguf"
    bundle_model.parent.mkdir(parents=True, exist_ok=True)
    bundle_model.write_text("weights", encoding="utf-8")
    monkeypatch.setattr("orchestrator.operator_ui_api.REPO_ROOT", tmp_path)
    monkeypatch.setattr("orchestrator.operator_ui_api.PATH_BROWSER_ROOTS", [tmp_path])

    payload = operator_path_browser(path="./deploy/offline_bundle/models/gguf/qwen.gguf", kind="file")

    assert payload["cwd"] == str(bundle_model.parent.resolve())
    assert any(entry["path"] == str(bundle_model.resolve()) for entry in payload["entries"])


@pytest.mark.asyncio
async def test_operator_action_run_blocks_bundle_port_conflict(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.operator_ui_api.parse_env_file",
        lambda _path: {"AGENT_API_PORT": "8000", "CHAINLIT_PORT": "3000"},
    )

    with pytest.raises(Exception) as exc_info:
        await operator_action_run(
            SimpleNamespace(url=SimpleNamespace(port=8000)),
            OperatorActionRequest(action_id="deploy.runtime.run"),
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert "port-conflict:agent-api:8000" in str(getattr(exc_info.value, "detail", ""))


def test_operator_surface_middleware_blocks_remote_static_access(monkeypatch, tmp_path):
    monkeypatch.setenv("OPERATOR_UI_LOCALHOST_ONLY", "true")
    static_root = tmp_path / "operator-ui"
    static_root.mkdir(parents=True, exist_ok=True)
    (static_root / "index.html").write_text("<html>operator</html>", encoding="utf-8")

    app = FastAPI()

    @app.middleware("http")
    async def restrict_operator_surface(request: Request, call_next):
        if is_operator_surface_path(request.url.path):
            try:
                enforce_operator_localhost_only(request)
            except Exception as exc:
                return JSONResponse(
                    status_code=getattr(exc, "status_code", 403),
                    content={"detail": getattr(exc, "detail", "operator-ui-access-denied")},
                )
        return await call_next(request)

    app.include_router(router)
    app.mount("/operator-ui", StaticFiles(directory=str(static_root), html=True), name="operator-ui")

    remote_client = TestClient(app, client=("203.0.113.10", 50000))
    local_client = TestClient(app, client=("127.0.0.1", 50001))

    remote_response = remote_client.get("/operator-ui/")
    local_response = local_client.get("/operator-ui/")

    assert remote_response.status_code == 403
    assert remote_response.json()["detail"] == "operator-ui-localhost-only"
    assert local_response.status_code == 200
