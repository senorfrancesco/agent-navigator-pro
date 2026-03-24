from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BUNDLE_ROOT = SCRIPT_DIR.parent
ENV_PATH = BUNDLE_ROOT / "env.bundle"

REQUIRED_ENV_KEYS = [
    "BACKEND_MODE",
    "UMS_RUNTIME_PROFILE",
    "DEVICE_MODE",
    "GPU_LAYERS_MODE",
    "MODEL_PATH_LLM",
    "MODEL_PATH_EMBEDDING_INTENT",
    "MODEL_PATH_EMBEDDING_RETRIEVAL",
    "ORCHESTRATOR_STATE_DB_URL",
    "ORCHESTRATOR_KB_DB_URL",
    "CHAINLIT_DB_URL",
]

WRITEABLE_DIRS = [
    "state/backend-data",
    "state/chainlit-data",
    "state/uploads",
]

SUPPORTED_BACKEND_MODES = {"llama-cpp-python", "llama-server", "vllm"}
SUPPORTED_RUNTIME_PROFILES = {"default", "adaptive", "manual"}
SUPPORTED_DEVICE_MODES = {"cpu", "gpu", "hybrid"}
SUPPORTED_GPU_LAYERS_MODES = {"auto", "max", "manual"}
PLACEHOLDER_SECRET_VALUES = {
    "change-me-before-deploy",
    "agent-navigator-secret-key-change-me",
    "change-me-grafana",
    "admin",
}
RUNTIME_PLAN_KEYS = [
    "UMS_RUNTIME_PROFILE",
    "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS",
    "UMS_RETRIEVED_CONTEXT_RATIO",
    "UMS_GENERATION_TOKENS_RESERVE",
    "DEVICE_MODE",
    "GPU_LAYERS_MODE",
    "UMS_SELECTED_GPU_LAYERS",
    "LLM_DEVICE_MODE",
    "VLM_DEVICE_MODE",
    "INTENT_EMBEDDER_DEVICE_MODE",
    "RETRIEVAL_EMBEDDER_DEVICE_MODE",
    "N_GPU_LAYERS_OVERRIDE",
    "RAG_MODE_OVERRIDE",
]


def parse_env(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip().strip("\"'")
    return payload


def read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip().strip("\"'")
    return payload


def run_command(args: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return 127, ""
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    return completed.returncode, stdout or stderr


def detect_host_snapshot() -> dict[str, str]:
    os_release = read_os_release()
    snapshot = {
        "os_id": os_release.get("ID", ""),
        "os_version": os_release.get("VERSION_ID", ""),
        "os_pretty_name": os_release.get("PRETTY_NAME", ""),
        "kernel": platform.release(),
        "architecture": os.environ.get("TEST_ARCHITECTURE", platform.machine()),
        "docker_version": "",
        "docker_compose_version": "",
        "nvidia_driver_version": "",
        "gpu_count": "0",
        "gpu_names": "",
        "gpu_compute_caps": "",
        "gpu_detection_status": "not-run",
        "gpu_detection_error": "",
    }

    docker_version = os.environ.get("TEST_DOCKER_VERSION", "")
    if not docker_version and shutil.which("docker"):
        _, docker_version = run_command(["docker", "--version"])
    snapshot["docker_version"] = docker_version

    docker_compose_version = os.environ.get("TEST_DOCKER_COMPOSE_VERSION", "")
    if not docker_compose_version and shutil.which("docker"):
        _, docker_compose_version = run_command(["docker", "compose", "version"])
    snapshot["docker_compose_version"] = docker_compose_version

    gpu_query = os.environ.get("TEST_GPU_QUERY", "")
    gpu_query_code = 0 if gpu_query else 127
    if not gpu_query and shutil.which("nvidia-smi"):
        gpu_query_code, gpu_query = run_command(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,compute_cap",
                "--format=csv,noheader",
            ]
        )
    if gpu_query and gpu_query_code == 0:
        snapshot["gpu_detection_status"] = "ok"
    if gpu_query and gpu_query_code == 0:
        gpu_lines = [line.strip() for line in gpu_query.splitlines() if line.strip()]
        snapshot["gpu_count"] = str(len(gpu_lines))
        names: list[str] = []
        caps: list[str] = []
        driver = ""
        for line in gpu_lines:
            parts = [part.strip() for part in line.split(",")]
            if parts:
                names.append(parts[0])
            if len(parts) > 1 and not driver:
                driver = parts[1]
            if len(parts) > 2:
                caps.append(parts[2])
        snapshot["gpu_names"] = "; ".join(names)
        snapshot["gpu_compute_caps"] = "; ".join(caps)
        snapshot["nvidia_driver_version"] = driver
    elif shutil.which("nvidia-smi"):
        snapshot["gpu_detection_status"] = "failed"
        snapshot["gpu_detection_error"] = gpu_query or "nvidia-smi-query-failed"
    else:
        snapshot["gpu_detection_status"] = "nvidia-smi-unavailable"

    return snapshot


def build_recommended_runtime_plan(snapshot: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    has_gpu = int(snapshot.get("gpu_count") or "0") > 0
    detection_failed = snapshot.get("gpu_detection_status") == "failed"
    plan = {
        "UMS_RUNTIME_PROFILE": current.get("UMS_RUNTIME_PROFILE", "adaptive") or "adaptive",
        "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS": current.get("UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", "13926") or "13926",
        "UMS_RETRIEVED_CONTEXT_RATIO": current.get("UMS_RETRIEVED_CONTEXT_RATIO", "0.6") or "0.6",
        "UMS_GENERATION_TOKENS_RESERVE": current.get("UMS_GENERATION_TOKENS_RESERVE", "1024") or "1024",
        "DEVICE_MODE": "gpu" if has_gpu else ("hybrid" if detection_failed else "cpu"),
        "GPU_LAYERS_MODE": "max" if has_gpu else current.get("GPU_LAYERS_MODE", "auto") if detection_failed else "auto",
        "UMS_SELECTED_GPU_LAYERS": "-1" if has_gpu else current.get("UMS_SELECTED_GPU_LAYERS", "") if detection_failed else "0",
        "LLM_DEVICE_MODE": "gpu" if has_gpu else current.get("LLM_DEVICE_MODE", "hybrid") if detection_failed else "cpu",
        "VLM_DEVICE_MODE": "gpu" if has_gpu else current.get("VLM_DEVICE_MODE", "hybrid") if detection_failed else "cpu",
        "INTENT_EMBEDDER_DEVICE_MODE": "cpu",
        "RETRIEVAL_EMBEDDER_DEVICE_MODE": "cpu",
        "N_GPU_LAYERS_OVERRIDE": "-1" if has_gpu else current.get("N_GPU_LAYERS_OVERRIDE", "") if detection_failed else "",
        "RAG_MODE_OVERRIDE": current.get("RAG_MODE_OVERRIDE", "auto") or "auto",
    }
    return plan


def extract_runtime_plan(payload: dict[str, str]) -> dict[str, str]:
    return {key: payload.get(key, "") for key in RUNTIME_PLAN_KEYS}


def print_kv_section(title: str, values: dict[str, str]) -> None:
    print(title)
    for key, value in values.items():
        print(f"  {key}={value}")


def ensure_writeable_dir(path: Path, errors: list[str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".bundle_write_check_{Path(__file__).stem}"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError:
        errors.append(f"write-check-failed:{path.relative_to(BUNDLE_ROOT)}")
        return
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def validate_env(payload: dict[str, str], errors: list[str], warnings: list[str]) -> None:
    for key in REQUIRED_ENV_KEYS:
        if not payload.get(key, "").strip():
            errors.append(f"missing-env:{key}")

    vlm_path = payload.get("MODEL_PATH_VLM", "").strip()
    mmproj_path = payload.get("MMPROJ_PATH", "").strip()
    if bool(vlm_path) != bool(mmproj_path):
        errors.append("incomplete-vlm-config:MODEL_PATH_VLM-and-MMPROJ_PATH-must-both-be-set-or-empty")

    backend_mode = payload.get("BACKEND_MODE", "").strip().lower()
    if backend_mode and backend_mode not in SUPPORTED_BACKEND_MODES:
        errors.append(f"invalid-backend-mode:{backend_mode}")

    runtime_profile = payload.get("UMS_RUNTIME_PROFILE", "").strip().lower()
    if runtime_profile and runtime_profile not in SUPPORTED_RUNTIME_PROFILES:
        errors.append(f"invalid-runtime-profile:{runtime_profile}")

    gpu_layers_mode = payload.get("GPU_LAYERS_MODE", "").strip().lower()
    if gpu_layers_mode and gpu_layers_mode not in SUPPORTED_GPU_LAYERS_MODES:
        errors.append(f"invalid-gpu-layers-mode:{gpu_layers_mode}")

    for key in [
        "DEVICE_MODE",
        "LLM_DEVICE_MODE",
        "VLM_DEVICE_MODE",
        "INTENT_EMBEDDER_DEVICE_MODE",
        "RETRIEVAL_EMBEDDER_DEVICE_MODE",
    ]:
        value = payload.get(key, "").strip().lower()
        if value and value not in SUPPORTED_DEVICE_MODES:
            errors.append(f"invalid-device-mode:{key}:{value}")

    for key in [
        "CHAINLIT_AUTH_SECRET",
        "CHAINLIT_ADMIN_PASSWORD",
        "GF_SECURITY_ADMIN_PASSWORD",
    ]:
        if payload.get(key, "").strip() in PLACEHOLDER_SECRET_VALUES:
            warnings.append(f"placeholder-secret:{key}")


def print_summary(payload: dict[str, str]) -> None:
    print("runtime-summary")
    print(f"  backend_mode={payload.get('BACKEND_MODE', '')}")
    print(f"  runtime_profile={payload.get('UMS_RUNTIME_PROFILE', '')}")
    print(f"  device_mode={payload.get('DEVICE_MODE', '')}")
    print(f"  llm_device_mode={payload.get('LLM_DEVICE_MODE', '')}")
    print(f"  vlm_device_mode={payload.get('VLM_DEVICE_MODE', '')}")
    print(f"  intent_embedder_device_mode={payload.get('INTENT_EMBEDDER_DEVICE_MODE', '')}")
    print(f"  retrieval_embedder_device_mode={payload.get('RETRIEVAL_EMBEDDER_DEVICE_MODE', '')}")
    print(f"  gpu_layers_mode={payload.get('GPU_LAYERS_MODE', '')}")
    print(f"  n_gpu_layers_override={payload.get('N_GPU_LAYERS_OVERRIDE', '')}")
    print(f"  rag_mode_override={payload.get('RAG_MODE_OVERRIDE', '')}")
    print(f"  intent_classifier_mode={payload.get('INTENT_CLASSIFIER_MODE', '')}")
    print(f"  active_model_id={payload.get('ACTIVE_MODEL_ID', '')}")


def print_runtime_plan_report(payload: dict[str, str]) -> None:
    current = extract_runtime_plan(payload)
    snapshot = detect_host_snapshot()
    recommended = build_recommended_runtime_plan(snapshot, current)

    print_kv_section("host-detected", snapshot)
    print_kv_section("runtime-current", current)
    print_kv_section("runtime-recommended", recommended)

    diff = {
        key: f"{current.get(key, '')} -> {recommended.get(key, '')}"
        for key in RUNTIME_PLAN_KEYS
        if current.get(key, "") != recommended.get(key, "")
    }
    if diff:
        print_kv_section("runtime-diff", diff)
    else:
        print("runtime-diff")
        print("  none")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validates writable state paths, env sanity, and operator summary for the offline bundle runtime."
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
        help="Path to env bundle file (default: deploy/offline_bundle/env.bundle).",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print summary and warnings without failing on validation errors.",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print("missing:env.bundle")
        return 1

    payload = parse_env(env_path)
    errors: list[str] = []
    warnings: list[str] = []

    validate_env(payload, errors, warnings)
    for rel_path in WRITEABLE_DIRS:
        ensure_writeable_dir(BUNDLE_ROOT / rel_path, errors)

    print_summary(payload)
    print_runtime_plan_report(payload)
    for warning in warnings:
        print(f"warning:{warning}")

    if errors and not args.report_only:
        for error in errors:
            print(error)
        return 1

    if errors:
        for error in errors:
            print(f"report-only:{error}")

    print("runtime-preflight:ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
