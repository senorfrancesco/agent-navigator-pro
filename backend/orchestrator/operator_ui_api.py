from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

try:
    from orchestrator.operator_ui_actions import cancel_action_job, get_action, get_job, list_actions, start_action_job
    from orchestrator.operator_config_service import OperatorConfigService
    from orchestrator.operator_deploy_service import OperatorDeployService
    from orchestrator.operator_observability_service import OperatorObservabilityService
    from orchestrator.operator_runtime_service import OperatorRuntimeService, env_value, file_freshness, parse_env_file
    from orchestrator.telemetry_runtime import get_timing_summary
except ModuleNotFoundError:  # pragma: no cover - direct module import fallback
    from backend.orchestrator.operator_ui_actions import cancel_action_job, get_action, get_job, list_actions, start_action_job
    from backend.orchestrator.operator_config_service import OperatorConfigService
    from backend.orchestrator.operator_deploy_service import OperatorDeployService
    from backend.orchestrator.operator_observability_service import OperatorObservabilityService
    from backend.orchestrator.operator_runtime_service import OperatorRuntimeService, env_value, file_freshness, parse_env_file
    from backend.orchestrator.telemetry_runtime import get_timing_summary


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
DEPLOY_ROOT = REPO_ROOT / "deploy" / "offline_bundle"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
OFFLINE_SCRIPTS_ROOT = DEPLOY_ROOT / "scripts"
PATH_BROWSER_ROOTS = [
    REPO_ROOT,
    Path("/home/seral"),
    Path("/mnt"),
    Path("/media"),
]
OPERATOR_SURFACE_PREFIXES = ("/operator", "/operator-ui", "/operator-assets")


def _operator_localhost_dependency(request: Request) -> None:
    enforce_operator_localhost_only(request)


router = APIRouter(
    prefix="/operator",
    tags=["operator-ui"],
    dependencies=[Depends(_operator_localhost_dependency)],
)


def _http_probe(url: str, timeout: float = 0.75) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "agent-operator-ui/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 0) < 500
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _operator_localhost_only_enabled() -> bool:
    raw_value = os.getenv("OPERATOR_UI_LOCALHOST_ONLY")
    if raw_value is None:
        backend_env_path = BACKEND_ROOT / ".env"
        if backend_env_path.exists():
            raw_value = parse_env_file(backend_env_path).get("OPERATOR_UI_LOCALHOST_ONLY")
    normalized = str(raw_value or "true").strip().lower()
    return normalized not in {"0", "false", "no", "off"}


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = str(host).strip()
    if normalized.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_operator_surface_path(path: str | None) -> bool:
    normalized = str(path or "").strip()
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in OPERATOR_SURFACE_PREFIXES
    )


def enforce_operator_localhost_only(request: Request) -> None:
    if not _operator_localhost_only_enabled():
        return
    client = getattr(request, "client", None)
    client_host = getattr(client, "host", None)
    if _is_loopback_host(client_host):
        return
    raise HTTPException(
        status_code=403,
        detail="operator-ui-localhost-only",
    )


def _probe_row(
    *,
    name: str,
    name_en: str,
    path: str,
    url: str,
    note: str,
    note_en: str,
    primary: bool = False,
) -> Dict[str, Any]:
    is_running = _http_probe(url)
    return {
        "name": name,
        "nameEn": name_en,
        "status": "running" if is_running else "blocked",
        "note": note,
        "noteEn": note_en,
        "path": path,
        "stage": "Готово" if is_running else "Не отвечает",
        "stageEn": "Ready" if is_running else "Unavailable",
        "endpoint": url,
        "freshness": "живой HTTP probe" if is_running else "живой HTTP probe не пройден",
        "freshnessEn": "live HTTP probe" if is_running else "live HTTP probe failed",
        "primary": primary,
    }


def _offline_bundle_running_services(bundle_root: Path) -> set[str]:
    compose_file = bundle_root / "compose.offline.yaml"
    env_file = bundle_root / "env.bundle"
    if not compose_file.exists() or not env_file.exists():
        return set()
    if not shutil.which("docker"):
        return set()
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "--env-file",
                str(env_file),
                "ps",
                "--services",
                "--status",
                "running",
            ],
            cwd=str(bundle_root),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


class OperatorActionRequest(BaseModel):
    action_id: str
    allow_privileged: bool = False


class OperatorConfigApplyRequest(BaseModel):
    updates: Dict[str, str]


class OperatorConfigPresetPreviewRequest(BaseModel):
    preset_id: str


def _resolve_allowed_path(raw_path: str) -> Path:
    raw_candidate = Path(raw_path).expanduser()
    candidate = raw_candidate.resolve() if raw_candidate.is_absolute() else (REPO_ROOT / raw_candidate).resolve()
    for root in PATH_BROWSER_ROOTS:
        try:
            candidate.relative_to(root.resolve())
            return candidate
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail=f"path-not-allowed:{candidate}")


def _path_kind_matches(candidate: Path, kind: str) -> bool:
    if kind == "file":
        return candidate.is_file()
    if kind == "directory":
        return candidate.is_dir()
    raise HTTPException(status_code=400, detail=f"invalid-path-kind:{kind}")


def _describe_path_validation(candidate: Path, kind: str, field_key: str | None) -> Dict[str, Any]:
    if not candidate.exists():
        return {
            "status": "error",
            "valid": False,
            "message": f"Path does not exist on the host yet: {candidate}",
            "checks": [],
        }

    if not _path_kind_matches(candidate, kind):
        expected = "file" if kind == "file" else "directory"
        actual = "directory" if candidate.is_dir() else "file"
        return {
            "status": "error",
            "valid": False,
            "message": f"Expected a {expected}, but got a {actual}: {candidate}",
            "checks": [],
        }

    checks: List[str] = []
    status = "ok"
    message = f"Path looks valid for this field: {candidate}"

    if field_key in {"HOST_MODEL_PATH_LLM", "HOST_MODEL_PATH_VLM", "HOST_MMPROJ_PATH"}:
        if candidate.suffix.lower() != ".gguf":
            return {
                "status": "error",
                "valid": False,
                "message": f"Expected a `.gguf` model file for {field_key}: {candidate}",
                "checks": checks,
            }
        checks.append("GGUF extension detected")
        if candidate.stat().st_size <= 0:
            return {
                "status": "error",
                "valid": False,
                "message": f"The selected `.gguf` file is empty: {candidate}",
                "checks": checks,
            }
        if field_key == "HOST_MMPROJ_PATH" and "mmproj" not in candidate.name.lower():
            status = "warning"
            message = f"The file exists, but its name does not look like an mmproj artifact: {candidate.name}"
        else:
            message = f"Host model file looks valid: {candidate.name}"

    if field_key in {"HOST_MODEL_PATH_EMBEDDING_INTENT", "HOST_MODEL_PATH_EMBEDDING_RETRIEVAL"}:
        markers = [
            "config.json",
            "modules.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "sentence_bert_config.json",
        ]
        found_markers = [marker for marker in markers if (candidate / marker).exists()]
        if found_markers:
            checks.append(f"Found model markers: {', '.join(found_markers)}")
            message = f"Model directory looks plausible: {candidate.name}"
        else:
            status = "warning"
            message = f"Directory exists, but common model marker files were not found yet: {candidate.name}"

    return {
        "status": status,
        "valid": status != "error",
        "message": message,
        "checks": checks,
    }


def _build_path_browser_payload(path: str | None, kind: str) -> Dict[str, Any]:
    if not path:
        return {
            "kind": kind,
            "cwd": "",
            "cwdDisplay": "",
            "parentPath": None,
            "entries": [
                {
                    "name": root.name or str(root),
                    "path": str(root),
                    "type": "directory",
                    "selectable": kind == "directory",
                }
                for root in PATH_BROWSER_ROOTS
                if root.exists()
            ],
        }

    current = _resolve_allowed_path(path)
    if not current.exists():
        raise HTTPException(status_code=404, detail=f"path-not-found:{current}")
    if current.is_file():
        current = current.parent

    entries = []
    for child in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        entries.append(
            {
                "name": child.name,
                "path": str(child.resolve()),
                "type": "directory" if child.is_dir() else "file",
                "selectable": child.is_dir() if kind == "directory" else child.is_file(),
            }
        )

    parent_path = None
    for root in PATH_BROWSER_ROOTS:
        root_resolved = root.resolve()
        try:
            current.relative_to(root_resolved)
        except ValueError:
            continue
        if current != root_resolved:
            parent_path = str(current.parent.resolve())
        break

    return {
        "kind": kind,
        "cwd": str(current),
        "cwdDisplay": str(current),
        "parentPath": parent_path,
        "entries": entries,
    }


def _build_delivery_contract() -> Dict[str, str]:
    return {
        "control_plane": "python",
        "ui_mode": "web-first",
        "shell_role": "compatibility",
        "actions_backend": "python_operator_runner",
    }


def _bundle_runtime_port_conflict(request_port: int | None) -> Dict[str, str] | None:
    if request_port is None:
        return None

    bundle_env = parse_env_file(DEPLOY_ROOT / "env.bundle")
    agent_api_port = env_value(bundle_env, "AGENT_API_PORT", default="8000")
    chainlit_port = env_value(bundle_env, "CHAINLIT_PORT", default="3000")

    if str(request_port) == str(agent_api_port):
        return {
            "service": "agent-api",
            "port": str(agent_api_port),
            "detail": (
                "Контейнерный деплой пытается поднять `agent-api` на том же порту, "
                "что и текущая operator UI (`{port}`). Это self-conflict: UI потеряет backend, "
                "если запускать bundle поверх текущего локального сервера."
            ).format(port=agent_api_port),
        }

    if str(request_port) == str(chainlit_port):
        return {
            "service": "chainlit",
            "port": str(chainlit_port),
            "detail": (
                "Контейнерный деплой пытается поднять `chainlit` на том же порту, "
                "что и текущий UI (`{port}`). Сначала разведи порты или запускай bundle в detached окружении."
            ).format(port=chainlit_port),
        }

    return None


def build_operator_state(request_port: int | None = None) -> Dict[str, Any]:
    runtime_service = OperatorRuntimeService(REPO_ROOT)
    config_service = OperatorConfigService(REPO_ROOT)
    deploy_service = OperatorDeployService(REPO_ROOT)
    observability_service = OperatorObservabilityService(REPO_ROOT)
    runtime_paths = runtime_service.get_runtime_paths()
    envs = runtime_service.load_env_payloads()
    hardware_metrics = runtime_service.get_hardware_metrics()
    archive_name = runtime_service.get_archive_name()
    docker_socket = runtime_service.docker_socket_path
    docker_binary = runtime_service.find_binary("docker")
    backend_env = envs["backend_env"]
    bundle_env = envs["bundle_env"]
    offline_running_services = _offline_bundle_running_services(DEPLOY_ROOT)

    warnings = []
    if not docker_binary:
        warnings.append(
            {
            "title": "Контейнерный запуск заблокирован: Docker не найден",
                "titleEn": "Container runtime blocked: Docker binary was not found",
                "body": "Файлы офлайн-бандла на месте, но действия через compose останутся заблокированными, пока Docker не появится на хосте.",
                "bodyEn": "Offline bundle files are present, but compose-driven actions stay blocked until Docker is available on the host.",
            }
        )
    elif not docker_socket.exists():
        warnings.append(
            {
                "title": "Контейнерный запуск заблокирован: нет доступа к Docker socket",
                "titleEn": "Container runtime blocked: no access to the Docker socket",
                "body": "Файлы офлайн-бандла на месте, но действия через compose останутся заблокированными, пока не появится доступ к сокету Docker.",
                "bodyEn": "Offline bundle files are present, but compose-driven actions stay blocked until the Docker socket becomes reachable.",
            }
        )

    native_agent_api_port = env_value(backend_env, "AGENT_API_PORT", default="8000")
    native_chainlit_port = env_value(backend_env, "CHAINLIT_PORT", default="3000")
    native_doc_port = env_value(backend_env, "DOC_PORT", "DOCUMENT_SERVER_PORT", default="8001")
    native_legal_port = env_value(backend_env, "LEGAL_PORT", "LEGAL_SERVER_PORT", default="8002")
    native_ums_port = env_value(backend_env, "UMS_PORT", default="8090")

    container_agent_api_port = env_value(bundle_env, "AGENT_API_PORT", default="8000")
    container_chainlit_port = env_value(bundle_env, "CHAINLIT_PORT", default="3000")
    container_doc_port = env_value(bundle_env, "DOCUMENT_SERVER_PORT", default="8001")
    container_legal_port = env_value(bundle_env, "LEGAL_SERVER_PORT", default="8002")
    container_ums_port = env_value(bundle_env, "UMS_PORT", default="8090")

    service_rows = [
        _probe_row(
            name="Agent API",
            name_en="Agent API",
            path="native",
            url=f"http://127.0.0.1:{native_agent_api_port}/health",
            note="Живой backend API нативного пути запуска.",
            note_en="Live backend API for the native runtime path.",
            primary=True,
        ),
        _probe_row(
            name="Chainlit UI",
            name_en="Chainlit UI",
            path="native",
            url=f"http://127.0.0.1:{native_chainlit_port}",
            note="Основной UI нативного запуска.",
            note_en="Primary UI for the native runtime path.",
            primary=True,
        ),
        _probe_row(
            name="Document Server",
            name_en="Document Server",
            path="native",
            url=f"http://127.0.0.1:{native_doc_port}/health",
            note="Сервис разбора документов для нативного пути.",
            note_en="Document parsing service for the native runtime path.",
        ),
        _probe_row(
            name="Legal Server",
            name_en="Legal Server",
            path="native",
            url=f"http://127.0.0.1:{native_legal_port}/health",
            note="Сервис юридического сопоставления для нативного пути.",
            note_en="Legal matching service for the native runtime path.",
        ),
        _probe_row(
            name="UMS",
            name_en="UMS",
            path="native",
            url=f"http://127.0.0.1:{native_ums_port}/health",
            note="Unified Model Server нативного пути запуска.",
            note_en="Unified Model Server for the native runtime path.",
            primary=True,
        ),
        _probe_row(
            name="Agent API",
            name_en="Agent API",
            path="container",
            url=f"http://127.0.0.1:{container_agent_api_port}/health",
            note="Опубликованный backend API офлайн-бандла.",
            note_en="Published backend API for the offline bundle runtime.",
            primary=True,
        ),
        _probe_row(
            name="Chainlit UI",
            name_en="Chainlit UI",
            path="container",
            url=f"http://127.0.0.1:{container_chainlit_port}",
            note="Опубликованный UI офлайн-бандла.",
            note_en="Published UI for the offline bundle runtime.",
            primary=True,
        ),
        _probe_row(
            name="Document Server",
            name_en="Document Server",
            path="container",
            url=f"http://127.0.0.1:{container_doc_port}/health",
            note="Опубликованный document-service офлайн-бандла.",
            note_en="Published document service for the offline bundle runtime.",
        ),
        _probe_row(
            name="Legal Server",
            name_en="Legal Server",
            path="container",
            url=f"http://127.0.0.1:{container_legal_port}/health",
            note="Опубликованный legal-service офлайн-бандла.",
            note_en="Published legal service for the offline bundle runtime.",
        ),
        _probe_row(
            name="UMS",
            name_en="UMS",
            path="container",
            url=f"http://127.0.0.1:{container_ums_port}/health",
            note="Опубликованный Unified Model Server офлайн-бандла.",
            note_en="Published Unified Model Server for the offline bundle runtime.",
            primary=True,
        ),
    ]

    if not offline_running_services:
        for row in service_rows:
            if row["path"] != "container":
                continue
            row["status"] = "not_started"
            row["stage"] = "Не запущено"
            row["stageEn"] = "Not started"
            row["freshness"] = "offline bundle ещё не поднят через compose"
            row["freshnessEn"] = "offline bundle is not up through compose yet"
    else:
        compose_service_map = {
            "Agent API": "agent-api",
            "Document Server": "document-server",
            "Legal Server": "legal-server",
            "UMS": "ums",
            "Chainlit UI": "chainlit",
        }
        for row in service_rows:
            if row["path"] != "container":
                continue
            compose_service = compose_service_map.get(row["nameEn"], "")
            if compose_service and compose_service not in offline_running_services:
                row["status"] = "degraded"
                row["stage"] = "Частично"
                row["stageEn"] = "Partial"
                row["freshness"] = "compose-service не в running"
                row["freshnessEn"] = "compose service is not running"

    config_state = config_service.get_config_state(runtime_paths)

    diagnostics: List[Dict[str, str]] = []
    if not docker_binary:
        diagnostics.append(
            {
                "path": "container",
                "title": "Контейнерный путь заблокирован: Docker отсутствует",
                "titleEn": "Container runtime blocked: Docker binary is missing",
                "body": "Контейнерный запуск не сможет перейти от чтения офлайн-бандла к сборке и деплою, пока Docker не установлен на хосте.",
                "bodyEn": "The container launch cannot move from bundle inspection to build/deploy until Docker is installed on the host.",
                "tone": "orange",
            }
        )
    if not docker_socket.exists():
        diagnostics.append(
            {
                "path": "container",
                "title": "Контейнерный путь заблокирован: сокет Docker недоступен",
                "titleEn": "Container runtime blocked: Docker socket is unavailable",
                "body": "Поверхность офлайн-бандла можно читать, но `docker compose` и загрузка образов не завершатся без доступа к `/var/run/docker.sock`.",
                "bodyEn": "The bundle surface remains readable, but `docker compose` and image loading cannot complete without access to `/var/run/docker.sock`.",
                "tone": "orange",
            }
        )
    bundle_port_conflict = _bundle_runtime_port_conflict(request_port)
    if bundle_port_conflict:
        diagnostics.append(
            {
                "path": "container",
                "title": "Нужен безопасный профиль портов перед локальным deploy",
                "titleEn": "A safe port profile is needed before local deploy",
                "body": (
                    "Текущий `{service}` публикуется на порту `{port}` и конфликтует с этой панелью на том же хосте. "
                    "Для локального запуска сначала подставь локальные безопасные порты."
                ).format(service=bundle_port_conflict["service"], port=bundle_port_conflict["port"]),
                "bodyEn": (
                    "The current `{service}` is published on port `{port}` and conflicts with the operator UI on this host. "
                    "Stage safe local ports before local smoke or deploy."
                ).format(service=bundle_port_conflict["service"], port=bundle_port_conflict["port"]),
                "tone": "cyan",
            }
        )

    log_lines = [
        {"path": "native", "service": "operator", "text": "[operator][repository-derived] обнаружены env-источники: backend/.env, backend/.env.runtime"},
        {"path": "native", "service": "launcher", "text": "[launcher][repository-derived] scripts/launcher.sh найден в корне репозитория"},
        {"path": "container", "service": "bundle", "text": "[bundle][repository-derived] обнаружены manifest и env-источники deploy/offline_bundle"},
        {"path": "container", "service": "docker", "text": f"[docker][host-probe] socket {'available' if docker_socket.exists() else 'missing'} at /var/run/docker.sock"},
    ]

    maintenance_actions = [
        {"title": "Перезагрузить источники конфига", "titleEn": "Reload Config Sources", "body": "Перечитать текущее состояние оператора из env-файлов и layout репозитория.", "bodyEn": "Reload the current operator state from env files and repository layout.", "action": "Перезагрузить источники конфига", "actionEn": "Reload Config Sources"},
        {"title": "Пересчитать пути запуска", "titleEn": "Recompute Runtime Paths", "body": "Заново вычислить доступность нативного и контейнерного путей запуска.", "bodyEn": "Recompute availability for the native and container runtime paths.", "action": "Пересчитать пути запуска", "actionEn": "Recompute Runtime Paths"},
        {"title": "Перезагрузить deploy surface", "titleEn": "Reload Deploy Surface", "body": "Перечитать каноническое отображение build/deploy scripts.", "bodyEn": "Reload the canonical build/deploy script mapping.", "action": "Перезагрузить deploy surface", "actionEn": "Reload Deploy Surface"},
    ]

    blockers = []
    if not docker_binary:
        blockers.append({"title": "Docker отсутствует", "titleEn": "Docker binary is missing", "body": "Сценарий сборки и деплоя контейнеров останется частично заблокированным, пока Docker не будет установлен.", "bodyEn": "The container build/deploy flow stays partially blocked until Docker is installed."})
    if not docker_socket.exists():
        blockers.append({"title": "Сокет Docker недоступен", "titleEn": "Docker socket is unavailable", "body": "Действия деплоя контейнеров не смогут завершиться без доступа к сокету Docker на хосте.", "bodyEn": "Container deploy actions cannot finish without Docker socket access on the host."})
    if bundle_port_conflict:
        blockers.append(
            {
                "title": "Контейнерный деплой делит порт с текущим operator UI",
                "titleEn": "Container deploy shares a port with the current operator UI",
                "body": (
                    "Если запускать офлайн-бандл из этого же локального backend, контейнерный `{service}` попытается "
                    "занять тот же порт `{port}`. Для безопасного запуска нужен другой порт или отдельное окружение."
                ).format(service=bundle_port_conflict["service"], port=bundle_port_conflict["port"]),
                "bodyEn": (
                    "If the bundle starts from this same local backend, its container `{service}` will try to take "
                    "the same port `{port}`. Use a different port profile or a separate environment."
                ).format(service=bundle_port_conflict["service"], port=bundle_port_conflict["port"]),
            }
        )

    activity_feed: List[str] = []

    deploy_surface = deploy_service.get_deploy_surface(
        archive_name=archive_name,
        docker_socket_available=docker_socket.exists(),
    )
    deploy_log_lines = deploy_service.get_deploy_log_lines(archive_name)
    metrics_summary = observability_service.get_metrics_summary()
    grafana_links = observability_service.get_grafana_links()
    timing_summary = get_timing_summary()

    return {
        "delivery": _build_delivery_contract(),
        "runtimePaths": runtime_paths,
        "hardwareMetrics": hardware_metrics,
        "warnings": warnings,
        "serviceRows": service_rows,
        "configState": config_state,
        "diagnostics": diagnostics,
        "logLines": log_lines,
        "maintenanceActions": maintenance_actions,
        "blockers": blockers,
        "activityFeed": activity_feed,
        "deploySurface": deploy_surface,
        "deployLogLines": deploy_log_lines,
        "metricsSummary": metrics_summary,
        "timingSummary": timing_summary,
        "grafanaLinks": grafana_links,
    }


def _build_runtime_health(state: Dict[str, Any], path_key: str) -> Dict[str, Any]:
    runtime_paths = state["runtimePaths"]
    if path_key not in runtime_paths:
        raise KeyError(path_key)

    path = runtime_paths[path_key]
    rows = [item for item in state["serviceRows"] if item["path"] == path_key]
    primary_rows = [item for item in rows if item.get("primary")]
    running = sum(1 for item in rows if item["status"] == "running")
    blocked = sum(1 for item in rows if item["status"] == "blocked")
    degraded = sum(1 for item in rows if item["status"] == "degraded")
    running_primary = sum(1 for item in primary_rows if item["status"] == "running")
    blocked_primary = sum(1 for item in primary_rows if item["status"] == "blocked")
    degraded_primary = sum(1 for item in primary_rows if item["status"] == "degraded")
    active_warnings = sum(1 for item in state["warnings"] if path_key == "container" or "runtime" in item["title"].lower())
    diagnostics = [item for item in state.get("diagnostics", []) if item.get("path") == path_key]
    warnings = state.get("warnings", [])
    blockers = state.get("blockers", [])

    status = "unknown"
    summary = "Runtime health is not available yet."
    summary_en = "Runtime health is not available yet."
    reason = ""
    reason_en = ""
    next_action = "Проверь путь запуска и журнал последних действий."
    next_action_en = "Check the runtime path and the recent activity log."

    if path_key == "container":
        docker_binary_missing = any("Docker binary" in item["titleEn"] for item in warnings) or any("Docker binary" in item["titleEn"] for item in blockers)
        docker_socket_missing = any("Docker socket" in item["titleEn"] for item in warnings) or any("Docker socket" in item["titleEn"] for item in blockers)
        port_conflict = any("shares a port" in item.get("titleEn", "") for item in blockers)

        if docker_binary_missing:
            status = "blocked"
            reason = "Docker отсутствует."
            reason_en = "The Docker binary is missing."
            summary = "Bundle читается, но сборка и деплой контейнеров заблокированы до установки Docker."
            summary_en = "The bundle surface is readable, but container build and deploy stay blocked until Docker is installed."
            next_action = "Установи Docker на хост и затем снова проверь контейнерный путь запуска."
            next_action_en = "Install Docker on the host and then check the container launch again."
        elif docker_socket_missing:
            status = "blocked"
            reason = "Сокет Docker недоступен."
            reason_en = "The Docker socket is unavailable."
            summary = "Bundle готов, но `docker compose` не сможет завершиться без доступа к сокету."
            summary_en = "The bundle surface is ready, but `docker compose` cannot complete without socket access."
            next_action = "Дай доступ к сокету Docker или запускай bundle в окружении, где он доступен."
            next_action_en = "Provide Docker socket access or run the bundle in an environment where the socket is available."
        elif port_conflict:
            status = "blocked"
            reason = "Порт `agent-api` конфликтует с текущим operator UI."
            reason_en = "The `agent-api` port conflicts with the current operator UI."
            summary = "Контейнерный путь не должен стартовать поверх локального backend на том же порту."
            summary_en = "The container runtime should not start on top of the local backend on the same port."
            next_action = "Во вкладке Конфиг подставь безопасные локальные порты перед локальным деплоем или запуском."
            next_action_en = "Stage safe local ports in Config before a local deploy or run."
        elif running_primary and blocked_primary == 0 and degraded_primary == 0:
            status = "running"
            reason = "Опубликованные сервисы офлайн-бандла отвечают на ожидаемых портах."
            reason_en = "Published offline bundle services respond on the expected ports."
            summary = "Контейнерный путь выглядит поднятым: основные сервисы офлайн-бандла доступны по опубликованным endpoint."
            summary_en = "The container runtime looks live: the primary offline bundle services respond on their published endpoints."
            next_action = "Проверь логи и сервисы, если менялись конфиг, образы или mount-пути."
            next_action_en = "Check logs and services if config, images, or mount paths changed."
        elif running_primary or degraded or degraded_primary or any(item.get("tone") == "orange" for item in diagnostics):
            status = "degraded"
            reason = "Есть сигналы среды выполнения и диагностики, требующие внимания."
            reason_en = "Runtime and diagnostics signals require attention."
            summary = "Bundle читается и проверки хоста доступны, но часть контейнерных проверок остаётся в сниженном состоянии."
            summary_en = "The bundle is readable and host probes are available, but some container checks remain degraded."
            next_action = "Открой Сервисы и Диагностику, затем смотри журнал запуска и панель деплоя."
            next_action_en = "Open Services and Diagnostics, then inspect the launch log and deploy surface."
        else:
            status = "not_started"
            reason = "Bundle и проверки хоста готовы, но контейнерный путь ещё не запускался."
            reason_en = "The bundle and host probes are ready, but the container runtime has not been started yet."
            summary = "Offline bundle готов к проверке оператором, но активная контейнерная среда ещё не поднята."
            summary_en = "The offline bundle is ready for operator review, but the active container runtime is not up yet."
            next_action = "Запусти Офлайн-бандл / Контейнеры из вкладки Запуск или перейди в Сборка / Деплой для подготовки окружения."
            next_action_en = "Start Offline Bundle / Containers from Launch or open Build / Deploy to prepare the environment."
    else:
        if path.get("status") == "unavailable":
            status = "blocked"
            reason = "Нативный путь запуска недоступен."
            reason_en = "The native runtime path is unavailable."
            summary = "Не хватает обязательных файлов нативного пути запуска."
            summary_en = "Required files for the native runtime path are missing."
            next_action = "Проверь `scripts/launcher.sh`, `scripts/runtime_preflight.py` и цепочку backend env-файлов."
            next_action_en = "Check `scripts/launcher.sh`, `scripts/runtime_preflight.py`, and the backend env chain."
        elif running_primary:
            status = "running"
            reason = "Живые проверки нативного пути отвечают на ожидаемых портах."
            reason_en = "Live native probes respond on the expected ports."
            summary = "Нативный путь выглядит рабочим по живым проверкам сервисов."
            summary_en = "The native runtime path looks healthy according to live service probes."
            next_action = "Проверь сервисы и логи, если менялся профиль запуска или режим устройства."
            next_action_en = "Check services and logs if the runtime profile or device mode changed."
        elif degraded or degraded_primary:
            status = "degraded"
            reason = "Нативный путь запуска виден, но часть проверок находится в сниженном состоянии."
            reason_en = "The native runtime path is visible, but some probes are degraded."
            summary = "Нативная среда доступна, но часть операторских проверок требует внимания."
            summary_en = "The native runtime is available, but some operator checks require attention."
            next_action = "Проверь Сервисы и журнал запуска перед повторным запуском."
            next_action_en = "Check Services and the launch log before retrying."
        else:
            status = "not_started"
            reason = "Живые сервисы нативного пути пока не отвечают на ожидаемых портах."
            reason_en = "Live native services do not respond on the expected ports yet."
            summary = "Нативный путь доступен как сценарий запуска, но сама среда ещё не поднята."
            summary_en = "The native runtime path is available as a launch path, but the runtime itself is not up yet."
            next_action = "Запусти нативный путь и затем проверь журнал запуска."
            next_action_en = "Start the native path and then inspect the launch log."

    return {
        "pathKey": path_key,
        "pathLabel": path["name"],
        "pathLabelEn": path.get("nameEn", path["name"]),
        "status": status,
        "summary": summary,
        "summaryEn": summary_en,
        "reason": reason,
        "reasonEn": reason_en,
        "nextAction": next_action,
        "nextActionEn": next_action_en,
        "runningChecks": running,
        "blockedChecks": blocked,
        "degradedChecks": degraded,
        "warningCount": active_warnings,
        "checkCount": len(rows),
    }


@router.get("/health")
def operator_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "operator_ui_served": True,
        "prototype_path": str(REPO_ROOT / "prototype" / "operator-ui"),
        "delivery": _build_delivery_contract(),
    }


@router.get("/state")
def operator_state(request: Request) -> Dict[str, Any]:
    return build_operator_state(getattr(request.url, "port", None))


@router.get("/runtime/paths")
def operator_runtime_paths(request: Request) -> Dict[str, Any]:
    return {"runtimePaths": build_operator_state(getattr(request.url, "port", None))["runtimePaths"]}


@router.get("/runtime/summary")
def operator_runtime_summary(request: Request) -> Dict[str, Any]:
    state = build_operator_state(getattr(request.url, "port", None))
    return {
        "hardwareMetrics": state["hardwareMetrics"],
        "warnings": state["warnings"],
        "activityFeed": state["activityFeed"],
    }


@router.get("/runtime/health/{path_key}")
def operator_runtime_health(path_key: str, request: Request) -> Dict[str, Any]:
    state = build_operator_state(getattr(request.url, "port", None))
    try:
        return _build_runtime_health(state, path_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-path:{path_key}") from exc


@router.get("/metrics/summary")
def operator_metrics_summary(request: Request) -> Dict[str, Any]:
    state = build_operator_state(getattr(request.url, "port", None))
    payload = dict(state["metricsSummary"])
    payload["timingSummary"] = state["timingSummary"]
    return payload


@router.get("/metrics/runtime")
def operator_metrics_runtime(request: Request) -> Dict[str, Any]:
    return {"overview": build_operator_state(getattr(request.url, "port", None))["metricsSummary"]["overview"]}


@router.get("/metrics/services")
def operator_metrics_services(request: Request) -> Dict[str, Any]:
    return {"services": build_operator_state(getattr(request.url, "port", None))["metricsSummary"]["services"]}


@router.get("/metrics/deploy")
def operator_metrics_deploy(request: Request) -> Dict[str, Any]:
    return {"deploy": build_operator_state(getattr(request.url, "port", None))["metricsSummary"]["deploy"]}


@router.get("/links/grafana")
def operator_grafana_links(request: Request) -> Dict[str, Any]:
    return build_operator_state(getattr(request.url, "port", None))["grafanaLinks"]


@router.get("/config/{path_key}")
def operator_config(path_key: str, request: Request) -> Dict[str, Any]:
    state = build_operator_state(getattr(request.url, "port", None))
    if path_key not in state["configState"]:
        raise HTTPException(status_code=404, detail=f"unknown-path:{path_key}")
    return state["configState"][path_key]


@router.get("/config/{path_key}/variants")
def operator_config_variants(path_key: str) -> Dict[str, Any]:
    config_service = OperatorConfigService(REPO_ROOT)
    try:
        return config_service.get_config_variants(path_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-path:{path_key}") from exc


@router.post("/config/{path_key}/preset/preview")
def operator_config_preset_preview(path_key: str, request: OperatorConfigPresetPreviewRequest) -> Dict[str, Any]:
    config_service = OperatorConfigService(REPO_ROOT)
    try:
        return config_service.preview_preset(path_key, request.preset_id)
    except KeyError as exc:
        detail = f"unknown-path:{path_key}" if str(exc) == f"'{path_key}'" else f"unknown-preset:{request.preset_id}"
        raise HTTPException(status_code=404, detail=detail) from exc


@router.post("/config/{path_key}/apply")
def operator_config_apply(path_key: str, request: OperatorConfigApplyRequest) -> Dict[str, Any]:
    config_service = OperatorConfigService(REPO_ROOT)
    try:
        return config_service.apply_config(path_key, request.updates)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-path:{path_key}") from exc


@router.get("/path-browser")
def operator_path_browser(path: str | None = None, kind: str = "file") -> Dict[str, Any]:
    return _build_path_browser_payload(path=path, kind=kind)


@router.get("/path-browser/validate")
def operator_path_browser_validate(path: str, kind: str = "file", field_key: str | None = None) -> Dict[str, Any]:
    candidate = _resolve_allowed_path(path)
    validation = _describe_path_validation(candidate, kind, field_key)
    return {
        "path": str(candidate),
        "exists": candidate.exists(),
        "kind": kind,
        "fieldKey": field_key,
        "valid": validation["valid"],
        "status": validation["status"],
        "message": validation["message"],
        "checks": validation["checks"],
    }


@router.get("/services/{path_key}")
def operator_services(path_key: str, request: Request) -> Dict[str, Any]:
    state = build_operator_state(getattr(request.url, "port", None))
    return {
        "services": [item for item in state["serviceRows"] if item["path"] == path_key],
        "diagnostics": [item for item in state["diagnostics"] if item["path"] == path_key],
    }


@router.get("/deploy/{mode}")
def operator_deploy_mode(mode: str, request: Request) -> Dict[str, Any]:
    state = build_operator_state(getattr(request.url, "port", None))
    if mode not in state["deploySurface"]:
        raise HTTPException(status_code=404, detail=f"unknown-mode:{mode}")
    return state["deploySurface"][mode]


@router.get("/actions/catalog")
def operator_action_catalog() -> Dict[str, Any]:
    return {"actions": list_actions()}


@router.get("/actions/{action_id}")
def operator_action_preview(action_id: str) -> Dict[str, Any]:
    try:
        return get_action(action_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-action:{action_id}") from exc


@router.post("/actions/run")
async def operator_action_run(http_request: Request, request: OperatorActionRequest) -> Dict[str, Any]:
    if request.action_id in {"deploy.runtime.deploy", "deploy.runtime.run", "runtime.container.launch"}:
        conflict = _bundle_runtime_port_conflict(http_request.url.port)
        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail=f"port-conflict:{conflict['service']}:{conflict['port']}:{conflict['detail']}",
            )
    try:
        job = await start_action_job(request.action_id, allow_privileged=request.allow_privileged)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-action:{request.action_id}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"privileged-action-blocked:{request.action_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive API guard
        raise HTTPException(status_code=500, detail=f"action-run-internal-error:{request.action_id}") from exc
    return job.to_dict()


@router.get("/jobs/{job_id}")
def operator_job(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown-job:{job_id}")
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
async def operator_job_cancel(job_id: str) -> Dict[str, Any]:
    try:
        job = await cancel_action_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-job:{job_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()
