from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    payload: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip().strip("\"'")
    return payload


def file_freshness(path: Path, *, generated: bool = False) -> str:
    if not path.exists():
        return "missing"
    if generated:
        return "generated"
    return "present"


def env_value(env: Dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        value = str(env.get(key, "")).strip()
        if value:
            return value
    return default


class OperatorRuntimeService:
    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = Path(repo_root or DEFAULT_REPO_ROOT)
        self.backend_root = self.repo_root / "backend"
        self.deploy_root = self.repo_root / "deploy" / "offline_bundle"
        self.scripts_root = self.repo_root / "scripts"
        self.offline_scripts_root = self.deploy_root / "scripts"
        self.docker_socket_path = Path("/var/run/docker.sock")

    def _find_binary(self, name: str) -> str | None:
        return shutil.which(name)

    def find_binary(self, name: str) -> str | None:
        return self._find_binary(name)

    def _docker_socket_available(self) -> bool:
        return self.docker_socket_path.exists()

    def docker_socket_available(self) -> bool:
        return self._docker_socket_available()

    def _build_path_status(self, required_paths: List[Path]) -> str:
        present = sum(1 for path in required_paths if path.exists())
        if present == len(required_paths):
            return "available"
        if present == 0:
            return "unavailable"
        return "partial"

    def _detect_gpu(self) -> str:
        inventory = self._detect_gpu_inventory()
        if not inventory:
            return "No NVIDIA GPU detected"
        if len(inventory) == 1:
            gpu = inventory[0]
            return f"{gpu['name']}, {gpu['memory_mib']} MiB"
        return f"{len(inventory)}x NVIDIA GPU detected"

    def _detect_gpu_inventory(self) -> List[Dict[str, object]]:
        if not self._find_binary("nvidia-smi"):
            return []
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            inventory: List[Dict[str, object]] = []
            for raw_line in result.stdout.strip().splitlines():
                parts = [part.strip() for part in raw_line.split(",", 2)]
                if len(parts) != 3:
                    continue
                index_raw, name, memory_raw = parts
                memory_mib = int(memory_raw.replace("MiB", "").strip())
                inventory.append(
                    {
                        "index": int(index_raw),
                        "name": name,
                        "memory_mib": memory_mib,
                    }
                )
            return inventory
        except Exception:
            return []

    def _detect_memory_gb(self) -> int:
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024 / 1024)
        except Exception:
            return 0
        return 0

    def _detect_bundle_archive(self) -> str:
        candidates = sorted(self.deploy_root.parent.glob("*.tar.gz")) + sorted(self.repo_root.glob("*.tar.gz"))
        if candidates:
            return candidates[0].name
        return "agent-nav-offline-bundle_v1.0.tar.gz"

    def get_archive_name(self) -> str:
        return self._detect_bundle_archive()

    def load_env_payloads(self) -> Dict[str, Dict[str, str]]:
        return {
            "backend_env": parse_env_file(self.backend_root / ".env"),
            "backend_env_native": parse_env_file(self.backend_root / ".env.native"),
            "backend_env_runtime": parse_env_file(self.backend_root / ".env.runtime"),
            "backend_env_override": parse_env_file(self.backend_root / ".env.hardware.override"),
            "bundle_env": parse_env_file(self.deploy_root / "env.bundle"),
        }

    def get_runtime_paths(self) -> Dict[str, Dict[str, object]]:
        native_required = [
            self.scripts_root / "launcher.sh",
            self.scripts_root / "runtime_preflight.py",
            self.backend_root / ".env",
        ]
        native_status = self._build_path_status(native_required)

        bundle_required = [
            self.deploy_root,
            self.deploy_root / "compose.offline.yaml",
            self.deploy_root / "env.bundle",
            self.deploy_root / "manifest.json",
        ]
        bundle_status = self._build_path_status(bundle_required)
        docker_binary = self._find_binary("docker")
        docker_socket_ready = self._docker_socket_available()
        if bundle_status == "available" and (not docker_binary or not docker_socket_ready):
            bundle_status = "partial"

        return {
            "native": {
                "key": "native",
                "name": "Нативный запуск",
                "nameEn": "Native Runtime",
                "status": native_status,
                "description": "Локальный developer-runtime с orchestration через launcher, прямым контролем путей моделей и видимостью сервисов.",
                "descriptionEn": "Local developer runtime with launcher orchestration, direct model-path control, and visible service state.",
                "launchSource": "scripts/launcher.sh --target native --profile adaptive",
                "configSources": [
                    {"path": "backend/.env", "role": "канонический env runtime и сервисов", "roleEn": "canonical env for runtime and services", "freshness": file_freshness(self.backend_root / ".env")},
                    {"path": "backend/.env.native", "role": "переопределения для нативного пути запуска", "roleEn": "overrides for the native runtime path", "freshness": file_freshness(self.backend_root / ".env.native")},
                    {"path": "backend/.env.runtime", "role": "сгенерированный применённый план runtime", "roleEn": "generated applied runtime plan", "freshness": file_freshness(self.backend_root / ".env.runtime", generated=True)},
                    {"path": "backend/.env.hardware.override", "role": "постоянные overrides размещения", "roleEn": "persistent placement overrides", "freshness": file_freshness(self.backend_root / ".env.hardware.override")},
                ],
                "summary": [
                    {"label": "Профили запуска", "labelEn": "Launch Profiles", "value": "4 готовы", "valueEn": "4 ready", "tone": "lime"},
                    {"label": "Цепочка env", "labelEn": "env Chain", "value": "разрешена" if (self.backend_root / ".env").exists() else "отсутствует", "valueEn": "resolved" if (self.backend_root / ".env").exists() else "missing", "tone": "cyan" if (self.backend_root / ".env").exists() else "orange"},
                    {"label": "Состояние runtime", "labelEn": "Runtime State", "value": "стабильно" if native_status == "available" else native_status, "valueEn": "stable" if native_status == "available" else native_status, "tone": "lime" if native_status == "available" else "orange"},
                ],
                "profiles": [
                    {"title": "Только backend", "titleEn": "Backend only", "status": "available", "body": "Минимальный API + orchestration flow для workflow и endpoint checks.", "bodyEn": "Minimal API and orchestration flow for workflow and endpoint checks."},
                    {"title": "Chainlit dev", "titleEn": "Chainlit dev", "status": "available", "body": "Путь operator UI с backend orchestration и локальным session state.", "bodyEn": "Operator UI path with backend orchestration and local session state."},
                    {"title": "Нативный runtime", "titleEn": "Native runtime", "status": native_status, "body": "Полный native launcher flow с hardware-aware планом.", "bodyEn": "Full native launcher flow with a hardware-aware plan."},
                    {"title": "Полный локальный стек", "titleEn": "Full local stack", "status": "partial", "body": "Запускается, но warmup и готовность сервисов могут отставать на один цикл проверки.", "bodyEn": "Can start, but warmup and service readiness may lag by one probe cycle."},
                ],
                "whyUnavailable": {
                    "title": "Доступность нативного запуска",
                    "titleEn": "Native runtime availability",
                    "checks": [
                        ["Python toolchain", "present" if self._find_binary("python3") else "missing"],
                        ["launcher.sh", "present" if (self.scripts_root / "launcher.sh").exists() else "missing"],
                        ["runtime_preflight.py", "present" if (self.scripts_root / "runtime_preflight.py").exists() else "missing"],
                        ["backend/.env chain", "configured" if (self.backend_root / ".env").exists() else "missing"],
                        ["tmux support", "present" if self._find_binary("tmux") else "missing"],
                    ],
                    "blockers": [],
                    "blockersEn": [],
                    "remediation": "Нативный путь запуска использует backend/.env, .env.native, .env.runtime и .env.hardware.override как видимые operator-источники.",
                    "remediationEn": "The native runtime path uses backend/.env, .env.native, .env.runtime, and .env.hardware.override as visible operator-side sources.",
                },
            },
            "container": {
                "key": "container",
                "name": "Offline Bundle / Контейнеры",
                "nameEn": "Offline Bundle / Containers",
                "status": bundle_status,
                "description": "Путь через offline bundle с загрузкой образов, деплоем, проверками parity и artifact-based запуском на сервере.",
                "descriptionEn": "Offline bundle path for image loading, deploy, parity checks, and artifact-based server startup.",
                "launchSource": "deploy/offline_bundle/scripts/run_offline_bundle.sh",
                "configSources": [
                    {"path": "deploy/offline_bundle/env.bundle", "role": "контракт env для bundle", "roleEn": "env contract for the bundle", "freshness": file_freshness(self.deploy_root / "env.bundle")},
                    {"path": "deploy/offline_bundle/compose.offline.yaml", "role": "топология контейнеров", "roleEn": "container topology", "freshness": file_freshness(self.deploy_root / "compose.offline.yaml")},
                    {"path": "deploy/offline_bundle/manifest.json", "role": "manifest артефактов", "roleEn": "artifact manifest", "freshness": file_freshness(self.deploy_root / "manifest.json")},
                    {"path": "deploy/offline_bundle/state/exported-env", "role": "экспортированное env-состояние runtime", "roleEn": "exported runtime env state", "freshness": file_freshness(self.deploy_root / "state" / "exported-env")},
                ],
                "summary": [
                    {"label": "Файлы bundle", "labelEn": "Bundle Files", "value": "есть" if self.deploy_root.exists() else "отсутствуют", "valueEn": "present" if self.deploy_root.exists() else "missing", "tone": "lime" if self.deploy_root.exists() else "orange"},
                    {"label": "Docker binary", "labelEn": "Docker binary", "value": "доступен" if docker_binary else "отсутствует", "valueEn": "available" if docker_binary else "missing", "tone": "cyan" if docker_binary else "orange"},
                    {"label": "Доступ к socket", "labelEn": "Socket access", "value": "есть" if docker_socket_ready else "заблокирован", "valueEn": "present" if docker_socket_ready else "blocked", "tone": "lime" if docker_socket_ready else "orange"},
                ],
                "profiles": [
                    {"title": "Загрузка образов bundle", "titleEn": "Bundle image loading", "status": "available" if docker_binary and docker_socket_ready else "unavailable", "body": "Архивы образов можно загружать в Docker только когда доступны engine и socket.", "bodyEn": "Bundle image archives can be loaded into Docker only when the engine and socket are available."},
                    {"title": "Offline bundle runtime", "titleEn": "Offline bundle runtime", "status": bundle_status, "body": "Основной пользовательский путь идёт через run_offline_bundle.sh и deploy.sh, а не через dev compose launcher checkout-репозитория.", "bodyEn": "The main user path goes through run_offline_bundle.sh and deploy.sh, not through the dev compose launcher for the checkout repository."},
                    {"title": "Импорт и parity-проверка", "titleEn": "Import and parity validation", "status": "partial", "body": "Manifest, env.bundle и deploy surface читаются даже когда сам runtime ещё не поднят.", "bodyEn": "Manifest, env.bundle, and the deploy surface remain readable even before the runtime is up."},
                ],
                "whyUnavailable": {
                    "title": "Состояние offline bundle / контейнерного запуска",
                    "titleEn": "Offline bundle and container runtime state",
                    "checks": [
                        ["deploy/offline_bundle/", "present" if self.deploy_root.exists() else "missing"],
                        ["compose.offline.yaml", "present" if (self.deploy_root / "compose.offline.yaml").exists() else "missing"],
                        ["env.bundle", "present" if (self.deploy_root / "env.bundle").exists() else "missing"],
                        ["manifest.json", "present" if (self.deploy_root / "manifest.json").exists() else "missing"],
                        ["Docker engine binary", "present" if docker_binary else "missing"],
                        ["Docker socket", "present" if docker_socket_ready else "missing"],
                    ],
                    "blockers": [] if (docker_binary and docker_socket_ready) else [
                        "Bundle-файлы есть, но Docker на текущем host доступен не полностью.",
                    ],
                    "blockersEn": [] if (docker_binary and docker_socket_ready) else [
                        "Bundle files are present, but Docker is not fully available on this host.",
                    ],
                    "remediation": "Контейнерный путь запуска ведёт в deploy/offline_bundle/scripts: сначала можно загрузить образы bundle, затем выполнить deploy или run offline bundle без dev-сборки checkout-репозитория.",
                    "remediationEn": "The container runtime path points to deploy/offline_bundle/scripts: first load bundle images, then deploy or run the offline bundle without a dev build from the checkout repository.",
                },
            },
        }

    def get_hardware_metrics(self) -> List[Dict[str, str]]:
        envs = self.load_env_payloads()
        mem_gb = self._detect_memory_gb()
        cpu_label = f"{platform.processor() or platform.machine()} / {mem_gb} GB RAM".strip()
        gpu_inventory = self._detect_gpu_inventory()
        gpu_visibility = self._detect_gpu()
        gpu_inventory_text = "No NVIDIA GPU detected"
        if gpu_inventory:
            gpu_inventory_text = "; ".join(
                f"GPU{gpu['index']}: {gpu['name']} ({gpu['memory_mib']} MiB)"
                for gpu in gpu_inventory
            )
        return [
            {"label": "Detected host OS", "labelEn": "Detected Host OS", "value": platform.platform(), "valueEn": platform.platform(), "note": "Host facts are gathered server-side for the operator UI.", "noteEn": "Host facts are gathered server-side for the operator UI."},
            {"label": "GPU visibility", "labelEn": "GPU Visibility", "value": gpu_visibility, "valueEn": gpu_visibility, "note": "Used to explain runtime capabilities and suggested defaults.", "noteEn": "Used to explain runtime capabilities and suggested defaults."},
            {"label": "GPU inventory", "labelEn": "GPU Inventory", "value": gpu_inventory_text, "valueEn": gpu_inventory_text, "note": "All detected NVIDIA devices are listed for multi-GPU aware planning.", "noteEn": "All detected NVIDIA devices are listed for multi-GPU aware planning."},
            {"label": "CPU / memory", "labelEn": "CPU / Memory", "value": cpu_label, "valueEn": cpu_label, "note": "Used for runtime and bundle sizing context.", "noteEn": "Used for runtime and bundle sizing context."},
            {"label": "Suggested runtime profile", "labelEn": "Suggested Runtime Profile", "value": "adaptive", "valueEn": "adaptive", "note": "Matches the canonical launcher flow for generated applied plans.", "noteEn": "Matches the canonical launcher flow for generated applied plans."},
            {"label": "Suggested context budget", "labelEn": "Suggested Context Budget", "value": env_value(envs["backend_env_runtime"], "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", default="16384"), "valueEn": env_value(envs["backend_env_runtime"], "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", default="16384"), "note": "Mirrors the current applied or suggested operator value.", "noteEn": "Mirrors the current applied or suggested operator value."},
            {"label": "Bundle archive target", "labelEn": "Bundle Archive Target", "value": self._detect_bundle_archive(), "valueEn": self._detect_bundle_archive(), "note": "Single portable tar.gz is the preferred build artifact.", "noteEn": "Single portable tar.gz is the preferred build artifact."},
        ]
