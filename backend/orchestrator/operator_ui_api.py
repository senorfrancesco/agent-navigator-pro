from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

try:
    from orchestrator.operator_ui_actions import get_action, get_job, list_actions, start_action_job
    from orchestrator.operator_config_service import OperatorConfigService
    from orchestrator.operator_deploy_service import OperatorDeployService
    from orchestrator.operator_observability_service import OperatorObservabilityService
    from orchestrator.operator_runtime_service import OperatorRuntimeService, env_value, file_freshness, parse_env_file
except ModuleNotFoundError:  # pragma: no cover - direct module import fallback
    from backend.orchestrator.operator_ui_actions import get_action, get_job, list_actions, start_action_job
    from backend.orchestrator.operator_config_service import OperatorConfigService
    from backend.orchestrator.operator_deploy_service import OperatorDeployService
    from backend.orchestrator.operator_observability_service import OperatorObservabilityService
    from backend.orchestrator.operator_runtime_service import OperatorRuntimeService, env_value, file_freshness, parse_env_file


router = APIRouter(prefix="/operator", tags=["operator-ui"])

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
DEPLOY_ROOT = REPO_ROOT / "deploy" / "offline_bundle"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
OFFLINE_SCRIPTS_ROOT = DEPLOY_ROOT / "scripts"


class OperatorActionRequest(BaseModel):
    action_id: str
    allow_privileged: bool = False


class OperatorConfigApplyRequest(BaseModel):
    updates: Dict[str, str]


class OperatorConfigPresetPreviewRequest(BaseModel):
    preset_id: str


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


def build_operator_state() -> Dict[str, Any]:
    runtime_service = OperatorRuntimeService(REPO_ROOT)
    config_service = OperatorConfigService(REPO_ROOT)
    deploy_service = OperatorDeployService(REPO_ROOT)
    observability_service = OperatorObservabilityService(REPO_ROOT)
    runtime_paths = runtime_service.get_runtime_paths()
    hardware_metrics = runtime_service.get_hardware_metrics()
    archive_name = runtime_service.get_archive_name()
    docker_socket = runtime_service.docker_socket_path
    docker_binary = runtime_service.find_binary("docker")
    bundle_env = parse_env_file(DEPLOY_ROOT / "env.bundle")

    warnings = []
    if not docker_binary:
        warnings.append(
            {
                "title": "Контейнерный запуск заблокирован: Docker binary не найден",
                "titleEn": "Container runtime blocked: Docker binary was not found",
                "body": "Файлы offline bundle на месте, но compose-driven действия останутся заблокированными, пока Docker не появится на host.",
                "bodyEn": "Offline bundle files are present, but compose-driven actions stay blocked until Docker is available on the host.",
            }
        )
    elif not docker_socket.exists():
        warnings.append(
            {
                "title": "Контейнерный запуск заблокирован: нет доступа к Docker socket",
                "titleEn": "Container runtime blocked: no access to the Docker socket",
                "body": "Файлы offline bundle на месте, но compose-driven действия останутся заблокированными, пока не появится доступ к Docker socket.",
                "bodyEn": "Offline bundle files are present, but compose-driven actions stay blocked until the Docker socket becomes reachable.",
            }
        )

    service_rows = [
        {
            "name": "Runtime Preflight",
            "nameEn": "Runtime Preflight",
            "status": "running" if (SCRIPTS_ROOT / "runtime_preflight.py").exists() else "blocked",
            "note": "Канонический вход для preflight и planning нативного запуска.",
            "noteEn": "Canonical entrypoint for native runtime preflight and planning.",
            "path": "native",
            "stage": "Готово" if (SCRIPTS_ROOT / "runtime_preflight.py").exists() else "Заблокировано",
            "stageEn": "Ready" if (SCRIPTS_ROOT / "runtime_preflight.py").exists() else "Blocked",
            "endpoint": "scripts/runtime_preflight.py",
            "freshness": "проверка файловой системы",
            "freshnessEn": "filesystem probe",
        },
        {
            "name": "Launcher",
            "nameEn": "Launcher",
            "status": "running" if (SCRIPTS_ROOT / "launcher.sh").exists() else "blocked",
            "note": "Канонический launcher для нативного и контейнерного путей запуска.",
            "noteEn": "Canonical launcher for native and container runtime paths.",
            "path": "native",
            "stage": "Готово" if (SCRIPTS_ROOT / "launcher.sh").exists() else "Заблокировано",
            "stageEn": "Ready" if (SCRIPTS_ROOT / "launcher.sh").exists() else "Blocked",
            "endpoint": "scripts/launcher.sh",
            "freshness": "проверка файловой системы",
            "freshnessEn": "filesystem probe",
        },
        {
            "name": "Bundle Manifest",
            "nameEn": "Bundle Manifest",
            "status": "running" if (DEPLOY_ROOT / "manifest.json").exists() else "blocked",
            "note": "Manifest артефактов для deploy/offline_bundle.",
            "noteEn": "Artifact manifest for deploy/offline_bundle.",
            "path": "container",
            "stage": "Читается" if (DEPLOY_ROOT / "manifest.json").exists() else "Отсутствует",
            "stageEn": "Readable" if (DEPLOY_ROOT / "manifest.json").exists() else "Missing",
            "endpoint": "deploy/offline_bundle/manifest.json",
            "freshness": "проверка файловой системы",
            "freshnessEn": "filesystem probe",
        },
        {
            "name": "Docker Socket",
            "nameEn": "Docker Socket",
            "status": "running" if docker_socket.exists() else "blocked",
            "note": "Запуск контейнеров и загрузка images зависят от доступа к Docker socket на host.",
            "noteEn": "Container startup and image loading depend on Docker socket access on the host.",
            "path": "container",
            "stage": "Готово" if docker_socket.exists() else "Заблокировано",
            "stageEn": "Ready" if docker_socket.exists() else "Blocked",
            "endpoint": "/var/run/docker.sock",
            "freshness": "проверка файловой системы",
            "freshnessEn": "filesystem probe",
        },
    ]

    config_state = config_service.get_config_state(runtime_paths)

    diagnostics: List[Dict[str, str]] = []

    log_lines = [
        {"path": "native", "service": "operator", "text": "[operator][repository-derived] обнаружены env-источники: backend/.env, backend/.env.native, backend/.env.runtime"},
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
        blockers.append({"title": "Docker binary отсутствует", "titleEn": "Docker binary is missing", "body": "Container build/deploy flow останется частично заблокированным, пока Docker не будет установлен.", "bodyEn": "The container build/deploy flow stays partially blocked until Docker is installed."})
    if not docker_socket.exists():
        blockers.append({"title": "Docker socket недоступен", "titleEn": "Docker socket is unavailable", "body": "Container deploy actions не смогут завершиться без доступа к socket на host.", "bodyEn": "Container deploy actions cannot finish without Docker socket access on the host."})
    if env_value(bundle_env, "AGENT_API_PORT", default="8000") == "8000":
        blockers.append(
            {
                "title": "Контейнерный деплой делит порт с текущим operator UI",
                "titleEn": "Container deploy shares a port with the current operator UI",
                "body": "Если запускать bundle из этого же локального backend на `8000`, контейнерный `agent-api` попытается занять тот же порт. Для безопасного запуска нужен другой порт или отдельное окружение.",
                "bodyEn": "If the bundle starts from this same local backend on `8000`, its container `agent-api` will try to take the same port. Use a different port profile or a separate environment.",
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
        "grafanaLinks": grafana_links,
    }


def _build_runtime_health(state: Dict[str, Any], path_key: str) -> Dict[str, Any]:
    runtime_paths = state["runtimePaths"]
    if path_key not in runtime_paths:
        raise KeyError(path_key)

    rows = [item for item in state["serviceRows"] if item["path"] == path_key]
    running = sum(1 for item in rows if item["status"] == "running")
    blocked = sum(1 for item in rows if item["status"] == "blocked")
    degraded = sum(1 for item in rows if item["status"] == "degraded")
    active_warnings = sum(1 for item in state["warnings"] if path_key == "container" or "runtime" in item["title"].lower())

    if blocked:
        status = "blocked"
    elif degraded:
        status = "degraded"
    elif running:
        status = "healthy"
    else:
        status = "unknown"

    return {
        "pathKey": path_key,
        "pathLabel": runtime_paths[path_key]["name"],
        "pathLabelEn": runtime_paths[path_key].get("nameEn", runtime_paths[path_key]["name"]),
        "status": status,
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
def operator_state() -> Dict[str, Any]:
    return build_operator_state()


@router.get("/runtime/paths")
def operator_runtime_paths() -> Dict[str, Any]:
    return {"runtimePaths": build_operator_state()["runtimePaths"]}


@router.get("/runtime/summary")
def operator_runtime_summary() -> Dict[str, Any]:
    state = build_operator_state()
    return {
        "hardwareMetrics": state["hardwareMetrics"],
        "warnings": state["warnings"],
        "activityFeed": state["activityFeed"],
    }


@router.get("/runtime/health/{path_key}")
def operator_runtime_health(path_key: str) -> Dict[str, Any]:
    state = build_operator_state()
    try:
        return _build_runtime_health(state, path_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown-path:{path_key}") from exc


@router.get("/metrics/summary")
def operator_metrics_summary() -> Dict[str, Any]:
    return build_operator_state()["metricsSummary"]


@router.get("/metrics/runtime")
def operator_metrics_runtime() -> Dict[str, Any]:
    return {"overview": build_operator_state()["metricsSummary"]["overview"]}


@router.get("/metrics/services")
def operator_metrics_services() -> Dict[str, Any]:
    return {"services": build_operator_state()["metricsSummary"]["services"]}


@router.get("/metrics/deploy")
def operator_metrics_deploy() -> Dict[str, Any]:
    return {"deploy": build_operator_state()["metricsSummary"]["deploy"]}


@router.get("/links/grafana")
def operator_grafana_links() -> Dict[str, Any]:
    return build_operator_state()["grafanaLinks"]


@router.get("/config/{path_key}")
def operator_config(path_key: str) -> Dict[str, Any]:
    state = build_operator_state()
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


@router.get("/services/{path_key}")
def operator_services(path_key: str) -> Dict[str, Any]:
    state = build_operator_state()
    return {
        "services": [item for item in state["serviceRows"] if item["path"] == path_key],
        "diagnostics": [item for item in state["diagnostics"] if item["path"] == path_key],
    }


@router.get("/deploy/{mode}")
def operator_deploy_mode(mode: str) -> Dict[str, Any]:
    state = build_operator_state()
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
