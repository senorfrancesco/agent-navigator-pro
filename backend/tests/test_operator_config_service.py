from pathlib import Path

from orchestrator.operator_config_service import OperatorConfigService
from orchestrator.operator_runtime_service import OperatorRuntimeService


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_config_service_returns_path_aware_sources_and_values(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env", "BACKEND_MODE=llama-cpp-python\nMODEL_REGISTRY_CONFIG_PATH=backend/config/models.yaml\n")
    _write(tmp_path / "backend" / ".env.native", "MODEL_PATH_LLM=/models/custom.gguf\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=cpu+gpu hybrid\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "BUNDLE_RUNTIME_PROFILE=default\nCHAINLIT_PORT=3000\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    state = config_service.get_config_state(runtime_service.get_runtime_paths())

    assert state["native"]["sourceFiles"][0]["path"] == "backend/.env"
    runtime_group = state["native"]["variants"][0]["groups"][0]
    assert runtime_group["fields"][0]["key"] == "UMS_RUNTIME_PROFILE"
    assert runtime_group["fields"][0]["applied"] == "adaptive"
    assert runtime_group["fields"][0]["control"] == "select"
    assert runtime_group["fields"][0]["options"][0]["value"] == "adaptive"
    assert runtime_group["fields"][0]["recommendedReasonEn"]
    backend_mode_field = next(field for field in runtime_group["fields"] if field["key"] == "BACKEND_MODE")
    assert backend_mode_field["control"] == "select"
    assert [option["value"] for option in backend_mode_field["options"]] == [
        "llama-cpp-python",
        "llama-server",
        "vllm",
    ]
    assert state["native"]["variants"][0]["titleEn"] == "Runtime / Profile"

    local_ports_group = state["container"]["variants"][0]["groups"][0]
    assert local_ports_group["fields"][0]["key"] == "AGENT_API_PORT"
    assert local_ports_group["fields"][0]["applied"] == "8000"


def test_config_service_apply_config_updates_selected_env_source(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env", "BACKEND_MODE=llama-cpp-python\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=cpu+gpu hybrid\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "BUNDLE_RUNTIME_PROFILE=default\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    result = config_service.apply_config("native", {"UMS_RUNTIME_PROFILE": "conservative"})

    assert result["updatedKeys"] == ["UMS_RUNTIME_PROFILE"]
    assert result["pathKey"] == "native"
    assert "UMS_RUNTIME_PROFILE=conservative" in (tmp_path / "backend" / ".env.runtime").read_text(encoding="utf-8")
    assert result["config"]["variants"][0]["groups"][0]["fields"][0]["applied"] == "conservative"


def test_config_service_preview_preset_returns_changed_fields(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env", "BACKEND_MODE=llama-cpp-python\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=cpu+gpu hybrid\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(
        tmp_path / "deploy" / "offline_bundle" / "env.bundle",
        "AGENT_API_PORT=8000\nCHAINLIT_PORT=3000\nUMS_PORT=8090\nPROMETHEUS_PORT=9090\nGRAFANA_PORT=3002\n",
    )
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    preview = config_service.preview_preset("container", "bundle-local-safe-ports")

    assert preview["variantId"] == "local_safe_ports"
    assert preview["updates"]["AGENT_API_PORT"] == "18000"
    assert any(item["key"] == "CHAINLIT_PORT" and item["staged"] == "13000" for item in preview["changedFields"])
    assert preview["titleEn"] == "Local Safe Ports"
    assert preview["reasonEn"]


def test_config_service_marks_boolean_parser_fields_as_selects(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env", "COMPARE_SINGLE_ITEM_STRICT_JSON=true\nCOMPARE_SINGLE_ITEM_RETRY_COUNT=1\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=cpu+gpu hybrid\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "OFFLINE_COMPARE_STRICT_JSON=true\nBUNDLE_PREFLIGHT_REQUIRED=true\nBUNDLE_PARITY_SMOKE_REQUIRED=true\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    state = config_service.get_config_state(runtime_service.get_runtime_paths())

    native_parsing_fields = state["native"]["variants"][-1]["groups"][0]["fields"]
    assert any(field["key"] == "COMPARE_SINGLE_ITEM_STRICT_JSON" and field["control"] == "select" for field in native_parsing_fields)
    assert any(field["key"] == "COMPARE_SINGLE_ITEM_RETRY_COUNT" and field["control"] == "select" for field in native_parsing_fields)

    container_parsing_fields = state["container"]["variants"][-1]["groups"][0]["fields"]
    assert all(field["control"] == "select" for field in container_parsing_fields)


def test_config_service_marks_native_model_paths_as_external_host_paths(tmp_path, monkeypatch):
    external_model = tmp_path / "external-models" / "qwen14b.gguf"
    _write(external_model, "weights")
    _write(tmp_path / "backend" / ".env", "MODEL_REGISTRY_CONFIG_PATH=backend/config/models.yaml\n")
    _write(tmp_path / "backend" / ".env.native", f"MODEL_PATH_LLM={external_model}\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=cpu+gpu hybrid\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "BUNDLE_RUNTIME_PROFILE=default\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    state = config_service.get_config_state(runtime_service.get_runtime_paths())
    model_fields = state["native"]["variants"][1]["groups"][0]["fields"]
    llm_field = next(field for field in model_fields if field["key"] == "MODEL_PATH_LLM")

    assert llm_field["pathPolicy"] == "host_path_flexible"
    assert llm_field["validation"]["status"] == "ok"
    assert str(external_model) in llm_field["validation"]["message"]
