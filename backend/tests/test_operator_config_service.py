from pathlib import Path

from orchestrator.operator_config_service import OperatorConfigService
from orchestrator.operator_runtime_service import OperatorRuntimeService, parse_env_file


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_config_service_returns_path_aware_sources_and_values(tmp_path, monkeypatch):
    _write(
        tmp_path / "backend" / ".env",
        "BACKEND_MODE=llama-cpp-python\n"
        "MODEL_REGISTRY_CONFIG_PATH=backend/config/models.yaml\n"
        "DOC_SERVER_URL=http://127.0.0.1:8001\n"
        "MODEL_PATH_LLM=/models/custom.gguf\n"
        "UMS_RUNTIME_PROFILE=adaptive\n"
        "DEVICE_MODE=hybrid\n",
    )
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
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

    assert [item["path"] for item in state["native"]["sourceFiles"]] == [
        "backend/.env",
        "backend/.env.runtime",
    ]
    runtime_group = state["native"]["variants"][0]["groups"][0]
    assert runtime_group["fields"][0]["key"] == "UMS_RUNTIME_PROFILE"
    assert runtime_group["fields"][0]["applied"] == "adaptive"
    assert runtime_group["fields"][0]["control"] == "select"
    assert [option["value"] for option in runtime_group["fields"][0]["options"]] == [
        "default",
        "adaptive",
        "manual",
    ]
    assert runtime_group["fields"][0]["recommendedReasonEn"]
    assert runtime_group["fields"][0]["description"] != runtime_group["fields"][0]["recommendedReason"]
    assert "Рекомендуется" not in runtime_group["fields"][0]["description"]
    backend_mode_field = next(field for field in runtime_group["fields"] if field["key"] == "BACKEND_MODE")
    assert backend_mode_field["control"] == "select"
    assert [option["value"] for option in backend_mode_field["options"]] == [
        "llama-cpp-python",
        "llama-server",
        "vllm",
    ]
    device_mode_field = next(field for field in runtime_group["fields"] if field["key"] == "DEVICE_MODE")
    assert [option["value"] for option in device_mode_field["options"]] == ["cpu", "gpu", "hybrid"]
    assert state["native"]["variants"][0]["title"] == "Runtime / Profile"
    assert state["native"]["variants"][0]["titleEn"] == "Runtime / Profile"

    published_ports_variant = next(variant for variant in state["container"]["variants"] if variant["variantId"] == "published_ports")
    local_ports_group = published_ports_variant["groups"][0]
    assert local_ports_group["fields"][0]["key"] == "AGENT_API_PORT"
    assert local_ports_group["fields"][0]["applied"] == "8000"

    native_ports_variant = next(variant for variant in state["native"]["variants"] if variant["variantId"] == "ports")
    native_port_keys = [field["key"] for field in native_ports_variant["groups"][0]["fields"]]
    assert "DOC_SERVER_URL" in native_port_keys
    assert "AGENT_API_PORT" in native_port_keys


def test_config_service_apply_config_updates_selected_env_source(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env", "BACKEND_MODE=llama-cpp-python\nUMS_RUNTIME_PROFILE=adaptive\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "BUNDLE_RUNTIME_PROFILE=default\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    result = config_service.apply_config("native", {"UMS_RUNTIME_PROFILE": "manual"})

    assert result["updatedKeys"] == ["UMS_RUNTIME_PROFILE"]
    assert result["pathKey"] == "native"
    assert "UMS_RUNTIME_PROFILE=manual" in (tmp_path / "backend" / ".env").read_text(encoding="utf-8")
    assert result["config"]["variants"][0]["groups"][0]["fields"][0]["applied"] == "adaptive"


def test_config_service_apply_config_quotes_secret_values_and_preserves_round_trip(tmp_path, monkeypatch):
    _write(
        tmp_path / "backend" / ".env",
        "CHAINLIT_AUTH_SECRET='ok'\nCHAINLIT_ADMIN_PASSWORD='old'\n",
    )
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "BUNDLE_RUNTIME_PROFILE=default\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    dangerous_value = "pa$$w'rd $(echo hacked) #bang"
    result = config_service.apply_config("native", {"CHAINLIT_ADMIN_PASSWORD": dangerous_value})

    env_text = (tmp_path / "backend" / ".env").read_text(encoding="utf-8")
    assert "CHAINLIT_ADMIN_PASSWORD='" in env_text
    assert parse_env_file(tmp_path / "backend" / ".env")["CHAINLIT_ADMIN_PASSWORD"] == dangerous_value

    secret_fields = {
        field["key"]: field
        for variant in result["config"]["variants"]
        for group in variant["groups"]
        for field in group["fields"]
    }
    assert result["updatedKeys"] == ["CHAINLIT_ADMIN_PASSWORD"]
    assert secret_fields["CHAINLIT_ADMIN_PASSWORD"]["applied"] == dangerous_value


def test_config_service_apply_config_preserves_explicit_empty_native_model_paths(tmp_path, monkeypatch):
    _write(
        tmp_path / "backend" / ".env",
        "MODEL_REGISTRY_CONFIG_PATH=backend/config/models.yaml\n"
        "MODEL_PATH_VLM=/models/qwenvl.gguf\n"
        "MMPROJ_PATH=/models/mmproj.gguf\n",
    )
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "BUNDLE_RUNTIME_PROFILE=default\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    result = config_service.apply_config("native", {"MODEL_PATH_VLM": "", "MMPROJ_PATH": ""})

    env_text = (tmp_path / "backend" / ".env").read_text(encoding="utf-8")
    assert "MODEL_PATH_VLM=\n" in env_text
    assert "MMPROJ_PATH=\n" in env_text

    model_fields = {
        field["key"]: field
        for field in result["config"]["variants"][1]["groups"][0]["fields"]
    }
    assert result["updatedKeys"] == ["MODEL_PATH_VLM", "MMPROJ_PATH"]
    assert model_fields["MODEL_PATH_VLM"]["applied"] == ""
    assert model_fields["MMPROJ_PATH"]["applied"] == ""


def test_config_service_preview_preset_returns_changed_fields(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env", "BACKEND_MODE=llama-cpp-python\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
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

    assert preview["variantId"] == "published_ports"
    assert preview["updates"]["AGENT_API_PORT"] == "18000"
    assert any(item["key"] == "CHAINLIT_PORT" and item["staged"] == "13000" for item in preview["changedFields"])
    assert preview["titleEn"] == "Local Safe Ports"
    assert preview["reasonEn"]


def test_config_service_marks_boolean_parser_fields_as_selects(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env", "COMPARE_SINGLE_ITEM_STRICT_JSON=true\nCOMPARE_SINGLE_ITEM_RETRY_COUNT=1\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
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
    assert any(field["key"] == "COMPARE_SINGLE_ITEM_STRICT_JSON" and field["control"] == "toggle" for field in native_parsing_fields)
    assert any(field["key"] == "COMPARE_SINGLE_ITEM_RETRY_COUNT" and field["control"] == "select" for field in native_parsing_fields)

    container_parsing_fields = state["container"]["variants"][-1]["groups"][0]["fields"]
    assert all(field["control"] == "toggle" for field in container_parsing_fields)


def test_config_service_marks_native_model_paths_as_external_host_paths(tmp_path, monkeypatch):
    external_model = tmp_path / "external-models" / "qwen14b.gguf"
    _write(external_model, "weights")
    _write(tmp_path / "backend" / ".env", f"MODEL_REGISTRY_CONFIG_PATH=backend/config/models.yaml\nMODEL_PATH_LLM={external_model}\n")
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
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


def test_config_service_preserves_explicit_empty_native_model_path_values(tmp_path, monkeypatch):
    _write(
        tmp_path / "backend" / ".env",
        "MODEL_REGISTRY_CONFIG_PATH=backend/config/models.yaml\n"
        "MODEL_PATH_VLM=\n"
        "MMPROJ_PATH=\n",
    )
    _write(tmp_path / "backend" / ".env.runtime", "UMS_RUNTIME_PROFILE=adaptive\nDEVICE_MODE=hybrid\n")
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

    model_fields = {
        field["key"]: field
        for field in state["native"]["variants"][1]["groups"][0]["fields"]
    }

    assert model_fields["MODEL_PATH_VLM"]["applied"] == ""
    assert model_fields["MODEL_PATH_VLM"]["value"] == ""
    assert model_fields["MMPROJ_PATH"]["applied"] == ""
    assert model_fields["MMPROJ_PATH"]["value"] == ""
    assert model_fields["MODEL_PATH_VLM"]["suggested"] == "/models/qwenvl.gguf"


def test_config_service_exposes_intuitive_container_model_path_descriptions(tmp_path, monkeypatch):
    _write(
        tmp_path / "deploy" / "offline_bundle" / "env.bundle",
        "MODEL_SOURCE_MODE=external_host_mounts\n"
        "MODEL_PATH_LLM=/opt/llm-tools-platform/external/llm/model.gguf\n"
        "HOST_MODEL_PATH_LLM=/mnt/models/qwen14b.gguf\n",
    )
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")

    runtime_service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(runtime_service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_service, "_docker_socket_available", lambda: True)

    config_service = OperatorConfigService(repo_root=tmp_path)
    state = config_service.get_config_state(runtime_service.get_runtime_paths())

    artifact_variant = next(variant for variant in state["container"]["variants"] if variant["variantId"] == "artifact_mounts")
    runtime_fields = {field["key"]: field for field in artifact_variant["groups"][0]["fields"]}
    host_fields = {field["key"]: field for field in artifact_variant["groups"][1]["fields"]}

    assert "Путь внутри контейнера" in runtime_fields["MODEL_PATH_LLM"]["description"]
    assert "Файл на хосте" in host_fields["HOST_MODEL_PATH_LLM"]["description"]
    assert host_fields["HOST_MODEL_PATH_LLM"]["pickerKind"] == "file"
    assert host_fields["HOST_MODEL_PATH_LLM"]["validation"]["status"] == "error"


def test_config_service_exposes_native_runtime_gpu_and_context_knobs(tmp_path, monkeypatch):
    _write(
        tmp_path / "backend" / ".env",
        "CONTEXT_SIZE_QWEN14B=16384\n"
        "N_GPU_LAYERS_QWEN14B=-1\n"
        "UMS_LLM_GPU_INDICES=0,1\n",
    )
    _write(
        tmp_path / "backend" / ".env.runtime",
        "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS=8192\n"
        "UMS_RETRIEVED_CONTEXT_RATIO=0.6\n"
        "UMS_GENERATION_TOKENS_RESERVE=1024\n"
        "LLM_DEVICE_MODE=gpu\n"
        "VLM_DEVICE_MODE=gpu\n"
        "INTENT_EMBEDDER_DEVICE_MODE=cpu\n"
        "RETRIEVAL_EMBEDDER_DEVICE_MODE=cpu\n"
        "GPU_LAYERS_MODE=max\n"
        "N_GPU_LAYERS_OVERRIDE=-1\n",
    )
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

    variant_ids = [variant["variantId"] for variant in state["native"]["variants"]]
    assert "runtime_tuning" in variant_ids
    assert "model_runtime" in variant_ids
    assert "generation" in variant_ids

    gpu_variant = next(variant for variant in state["native"]["variants"] if variant["variantId"] == "gpu")
    assert gpu_variant["title"] == "GPU / Размещение"
    gpu_fields = gpu_variant["groups"][0]["fields"]
    assert any(field["key"] == "LLM_DEVICE_MODE" and field["control"] == "select" for field in gpu_fields)
    assert any(field["key"] == "GPU_LAYERS_MODE" and field["control"] == "select" for field in gpu_fields)

    tuning_variant = next(variant for variant in state["native"]["variants"] if variant["variantId"] == "runtime_tuning")
    tuning_keys = [field["key"] for field in tuning_variant["groups"][0]["fields"]]
    assert tuning_keys == [
        "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS",
        "UMS_RETRIEVED_CONTEXT_RATIO",
        "UMS_GENERATION_TOKENS_RESERVE",
    ]

    model_variant = next(variant for variant in state["native"]["variants"] if variant["variantId"] == "model_runtime")
    model_keys = [field["key"] for field in model_variant["groups"][0]["fields"]]
