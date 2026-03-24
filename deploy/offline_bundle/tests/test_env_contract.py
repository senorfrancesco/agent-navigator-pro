from __future__ import annotations

import importlib.util
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BUNDLE_ROOT / "scripts" / "preflight_runtime.py"


def _parse_env_keys(path: Path) -> list[str]:
    keys: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        keys.append(key)
    return keys


def _parse_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _load_preflight_module():
    spec = importlib.util.spec_from_file_location("offline_preflight_runtime", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_bundle_example_has_unique_key_set() -> None:
    example_keys = _parse_env_keys(BUNDLE_ROOT / "env.bundle.example")

    assert example_keys
    assert len(example_keys) == len(set(example_keys))


def test_env_bundle_example_contains_runtime_parity_knobs() -> None:
    keys = set(_parse_env_keys(BUNDLE_ROOT / "env.bundle.example"))

    expected = {
        "AGENT_API_HOST",
        "UMS_URL",
        "DOC_SERVER_URL",
        "LEGAL_SERVER_URL",
        "BACKEND_APP_IMAGE",
        "UMS_IMAGE",
        "UMS_LLAMA_CACHE_PROMPT",
        "CHAINLIT_ENABLE_DATA_LAYER",
        "CHAINLIT_MODEL_PROFILE_DEFAULT_CHAT_MODEL",
        "CHAINLIT_MODEL_PROFILE_LONG_CONTEXT_MODEL",
        "INTENT_CLASSIFIER_MODE",
        "RAG_MODE_OVERRIDE",
        "UMS_LLM_MAX_CONCURRENCY",
        "REPORT_PDF_FONT_PATH",
    }

    assert expected.issubset(keys)


def test_env_bundle_example_defaults_to_llama_server_backend() -> None:
    values = _parse_env_values(BUNDLE_ROOT / "env.bundle.example")

    assert values["BACKEND_MODE"] == "llama-server"


def test_env_bundle_example_passes_preflight_contract_validation() -> None:
    module = _load_preflight_module()
    payload = module.parse_env(BUNDLE_ROOT / "env.bundle.example")

    errors: list[str] = []
    warnings: list[str] = []
    module.validate_env(payload, errors, warnings)

    assert errors == []
    assert "placeholder-secret:CHAINLIT_AUTH_SECRET" in warnings
    assert "placeholder-secret:CHAINLIT_ADMIN_PASSWORD" in warnings
    assert "placeholder-secret:GF_SECURITY_ADMIN_PASSWORD" in warnings
