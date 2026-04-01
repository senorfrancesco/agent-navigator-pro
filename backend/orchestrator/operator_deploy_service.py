from __future__ import annotations

from pathlib import Path
from typing import Dict, List

try:
    from orchestrator.operator_runtime_service import file_freshness
except ModuleNotFoundError:  # pragma: no cover - direct module import fallback
    from backend.orchestrator.operator_runtime_service import file_freshness


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


class OperatorDeployService:
    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = Path(repo_root or DEFAULT_REPO_ROOT)
        self.deploy_root = self.repo_root / "deploy" / "offline_bundle"
        self.offline_scripts_root = self.deploy_root / "scripts"

    def get_deploy_surface(self, *, archive_name: str | None = None, docker_socket_available: bool = False) -> Dict[str, Dict[str, object]]:
        archive_name = archive_name or "agent-nav-offline-bundle_v1.0.tar.gz"
        return {
            "build": {
                "label": "Сборка офлайн-бандла",
                "labelEn": "Build Bundle",
                "summaryTitle": "Состояние сборки офлайн-бандла",
                "summaryTitleEn": "Build Bundle Status",
                "stagesTitle": "Стадии на хосте сборки",
                "stagesTitleEn": "Build-Host Stages",
                "actionsTitle": "Действия сборки",
                "actionsTitleEn": "Build Actions",
                "artifactsTitle": "Выходные артефакты и упаковка архива",
                "artifactsTitleEn": "Output Artifacts and Archive Packaging",
                "logsTitle": "Лог сборки и экспорта",
                "logsTitleEn": "Build and Export Log",
                "summary": [
                    {"label": "Корень бандла", "labelEn": "Bundle Root", "value": "deploy/offline_bundle", "note": "Канонический рабочий каталог сборки.", "noteEn": "Canonical workspace for connected bundle builds.", "tone": "cyan"},
                    {"label": "Целевой архив", "labelEn": "Bundle Archive Target", "value": archive_name, "note": "Единый переносимый артефакт для передачи.", "noteEn": "Single portable artifact for transfer.", "tone": "lime"},
                    {"label": "Матрица системных пакетов", "labelEn": "Host APT Matrix", "value": "ubuntu-24.04 готов" if (self.deploy_root / "host_packages" / "ubuntu-24.04").exists() else "ожидает", "valueEn": "ubuntu-24.04 ready" if (self.deploy_root / "host_packages" / "ubuntu-24.04").exists() else "waiting", "note": "Собирается из локального сценария APT-бандла.", "noteEn": "Built from the local apt bundle flow.", "tone": "lime" if (self.deploy_root / "host_packages" / "ubuntu-24.04").exists() else "orange"},
                    {"label": "Состояние манифеста", "labelEn": "Manifest Status", "value": "проверен" if (self.deploy_root / "manifest.json").exists() else "отсутствует", "valueEn": "validated" if (self.deploy_root / "manifest.json").exists() else "missing", "note": "Выходное состояние, готовое к деплою.", "noteEn": "Output state ready for deploy.", "tone": "lime" if (self.deploy_root / "manifest.json").exists() else "orange"},
                ],
                "sources": [
                    {"path": "deploy/offline_bundle/scripts/build_bundle.sh", "role": "верхнеуровневый сценарий сборки и экспорта", "roleEn": "top-level build and export orchestrator", "freshness": file_freshness(self.offline_scripts_root / "build_bundle.sh")},
                    {"path": "deploy/offline_bundle/scripts/build_host_apt_bundle.sh", "role": "сборка APT-бандла системных пакетов", "roleEn": "host APT bundle build", "freshness": file_freshness(self.offline_scripts_root / "build_host_apt_bundle.sh")},
                    {"path": "deploy/offline_bundle/scripts/export_images.sh", "role": "сборка и экспорт Docker-образов", "roleEn": "Docker image build and export", "freshness": file_freshness(self.offline_scripts_root / "export_images.sh")},
                    {"path": "deploy/offline_bundle/scripts/generate_manifest.py", "role": "генерация манифеста", "roleEn": "manifest generation", "freshness": file_freshness(self.offline_scripts_root / "generate_manifest.py")},
                    {"path": "deploy/offline_bundle/scripts/validate_bundle.py", "role": "проверка готовности к деплою", "roleEn": "deploy-readiness validation", "freshness": file_freshness(self.offline_scripts_root / "validate_bundle.py")},
                ],
                "stages": [
                    {"title": "Wheelhouse", "titleEn": "Wheelhouse", "status": "running" if (self.offline_scripts_root / "build_wheelhouse.sh").exists() else "blocked", "body": "Подготовить офлайн-набор Python wheel перед экспортом образов.", "bodyEn": "Prepare the offline Python wheelhouse before exporting images.", "script": "build_wheelhouse.sh"},
                    {"title": "Образы", "titleEn": "Images", "status": "running" if (self.offline_scripts_root / "export_images.sh").exists() else "blocked", "body": "Собрать backend, UMS и Chainlit как Docker-образы и экспортировать их в tar-архивы.", "bodyEn": "Build backend, UMS, and Chainlit images and export them as tar archives.", "script": "export_images.sh"},
                    {"title": "Модели", "titleEn": "Models", "status": "running" if (self.offline_scripts_root / "export_models.sh").exists() else "blocked", "body": "Экспортировать layout моделей для env.bundle и манифеста.", "bodyEn": "Export the model layout for env.bundle and the manifest.", "script": "export_models.sh"},
                    {"title": "Состояние", "titleEn": "State", "status": "running" if (self.offline_scripts_root / "export_state.sh").exists() else "blocked", "body": "Экспортировать env среды и сохранённое состояние в bundle/state.", "bodyEn": "Export runtime env and persisted state into bundle/state.", "script": "export_state.sh"},
                    {"title": "Системные пакеты", "titleEn": "Host packages", "status": "running" if (self.offline_scripts_root / "build_host_apt_bundle.sh").exists() else "blocked", "body": "Собрать APT-бандл точных версий для целевой Ubuntu.", "bodyEn": "Build an exact-version apt bundle for the target Ubuntu host.", "script": "build_host_apt_bundle.sh"},
                    {"title": "Упаковка архива", "titleEn": "Archive packaging", "status": "partial", "body": "Упаковать весь deploy/offline_bundle в единый tar.gz артефакт.", "bodyEn": "Package all of deploy/offline_bundle into a single tar.gz artifact.", "script": "tar czf ..."},
                ],
                "actions": [
                    {"title": "Собрать офлайн-бандл", "titleEn": "Build Bundle", "body": "Запустить канонический сценарий сборки и экспорта офлайн-бандла.", "bodyEn": "Run the canonical build and export orchestration for the bundle.", "action": "Собрать bundle", "actionEn": "Build Bundle"},
                    {"title": "Собрать APT-бандл", "titleEn": "Build Host APT Bundle", "body": "Подготовить системные пакеты Ubuntu и lock-файл версий для офлайн-установки.", "bodyEn": "Prepare Ubuntu host packages and the versions lock for offline install.", "action": "Собрать host APT bundle", "actionEn": "Build Host APT Bundle"},
                    {"title": "Экспортировать образы", "titleEn": "Export Images", "body": "Собрать офлайн-образы и сохранить их как архивы образов.", "bodyEn": "Build offline images and save them as image archives.", "action": "Экспортировать images", "actionEn": "Export Images"},
                    {"title": "Сгенерировать манифест", "titleEn": "Generate Manifest", "body": "Сгенерировать манифест и проверить полноту готовности к деплою.", "bodyEn": "Generate the manifest and verify deploy readiness completeness.", "action": "Сгенерировать manifest", "actionEn": "Generate Manifest"},
                    {"title": "Упаковать tar.gz", "titleEn": "Pack tar.gz", "body": "Создать единый переносимый архив из deploy/offline_bundle.", "bodyEn": "Create a single portable archive from deploy/offline_bundle.", "action": "Упаковать tar.gz", "actionEn": "Pack tar.gz"},
                    {"title": "Открыть логи сборки", "titleEn": "Open Build Logs", "body": "Открыть поэтапный вывод сборки и экспорта.", "bodyEn": "Open stage-oriented build and export output.", "action": "Посмотреть логи сборки", "actionEn": "Open Build Logs"},
                ],
                "artifacts": [
                    {"title": "Архив бандла", "titleEn": "Bundle Archive", "body": archive_name, "bodyEn": archive_name, "badge": "ready"},
                    {"title": "Архивы образов", "titleEn": "Image Archives", "body": "deploy/offline_bundle/images/*.tar", "bodyEn": "deploy/offline_bundle/images/*.tar", "badge": "ready" if (self.deploy_root / "images").exists() else "partial"},
                    {"title": "Пакет системных пакетов", "titleEn": "Host Packages", "body": "deploy/offline_bundle/host_packages", "bodyEn": "deploy/offline_bundle/host_packages", "badge": "ready" if (self.deploy_root / "host_packages").exists() else "partial"},
                    {"title": "Манифест", "titleEn": "Manifest", "body": "deploy/offline_bundle/manifest.json", "bodyEn": "deploy/offline_bundle/manifest.json", "badge": "ready" if (self.deploy_root / "manifest.json").exists() else "partial"},
                ],
            },
            "import": {
                "label": "Импорт / Деплой",
                "labelEn": "Import / Deploy",
                "summaryTitle": "Состояние импорта / деплоя",
                "summaryTitleEn": "Import / Deploy Status",
                "stagesTitle": "Стадии на целевом host",
                "stagesTitleEn": "Target-Host Stages",
                "actionsTitle": "Действия импорта и деплоя",
                "actionsTitleEn": "Import and Deploy Actions",
                "artifactsTitle": "Приём архива и распакованное состояние бандла",
                "artifactsTitleEn": "Archive Intake and Unpacked Bundle State",
                "logsTitle": "Лог импорта / деплоя",
                "logsTitleEn": "Import / Deploy Log",
                "summary": [
                    {"label": "Приём архива", "labelEn": "Archive Intake", "value": archive_name, "valueEn": archive_name, "note": "Один tar.gz остаётся каноническим переносимым артефактом.", "noteEn": "A single tar.gz remains the canonical portable artifact.", "tone": "lime"},
                    {"label": "Корень распаковки", "labelEn": "Unpack Root", "value": "deploy/offline_bundle", "valueEn": "deploy/offline_bundle", "note": "Бандл распаковывается в целевой корень среды.", "noteEn": "The bundle is unpacked into the target runtime root.", "tone": "cyan"},
                    {"label": "Состояние деплоя", "labelEn": "Deploy Status", "value": "частично" if docker_socket_available else "заблокировано", "valueEn": "partial" if docker_socket_available else "blocked", "note": "Бандл проходит проверку, но целевая среда всё ещё зависит от доступности Docker.", "noteEn": "The bundle passes validation, but the host runtime still depends on Docker availability.", "tone": "cyan" if docker_socket_available else "orange"},
                    {"label": "Каноническая точка входа", "labelEn": "Canonical Entrypoint", "value": "deploy.sh", "valueEn": "deploy.sh", "note": "run_offline_bundle.sh остаётся верхнеуровневой обёрткой.", "noteEn": "run_offline_bundle.sh remains the top-level convenience wrapper.", "tone": "cyan"},
                ],
                "sources": [
                    {"path": "deploy/offline_bundle/scripts/install_host_apt_bundle.sh", "role": "офлайн-установка и проверка системных пакетов", "roleEn": "offline install and host package checks", "freshness": file_freshness(self.offline_scripts_root / "install_host_apt_bundle.sh")},
                    {"path": "deploy/offline_bundle/scripts/deploy.sh", "role": "канонический сценарий деплоя", "roleEn": "canonical deploy orchestration", "freshness": file_freshness(self.offline_scripts_root / "deploy.sh")},
                    {"path": "deploy/offline_bundle/scripts/load_images.sh", "role": "импорт Docker-образов", "roleEn": "Docker image import", "freshness": file_freshness(self.offline_scripts_root / "load_images.sh")},
                    {"path": "deploy/offline_bundle/scripts/restore_state.sh", "role": "восстановление состояния бандла", "roleEn": "bundle state restore", "freshness": file_freshness(self.offline_scripts_root / "restore_state.sh")},
                    {"path": "deploy/offline_bundle/scripts/run_offline_bundle.sh", "role": "обёртка верхнего уровня для удобного запуска", "roleEn": "top-level convenience wrapper", "freshness": file_freshness(self.offline_scripts_root / "run_offline_bundle.sh")},
                ],
                "stages": [
                    {"title": "Приём архива", "titleEn": "Archive Intake", "status": "running", "body": "Принять tar.gz артефакт и зарегистрировать метаданные импорта.", "bodyEn": "Accept the tar.gz artifact and register import metadata.", "script": "archive selector"},
                    {"title": "Распаковка", "titleEn": "Unpack", "status": "running", "body": "Распаковать бандл в целевой корень офлайн-бандла.", "bodyEn": "Unpack the bundle into the target offline bundle root.", "script": "tar xzf ..."},
                    {"title": "Проверка бандла", "titleEn": "Bundle Validation", "status": "running", "body": "Проверить env, модели, манифест, образы и системные пакеты.", "bodyEn": "Validate env, models, manifest, images, and host packages.", "script": "validate_bundle.py --mode deploy"},
                    {"title": "Установка APT-бандла", "titleEn": "Install Host APT Bundle", "status": "partial", "body": "Установить или проверить офлайн-системные пакеты для обнаруженного дистрибутива.", "bodyEn": "Install or verify offline host packages for the detected distro.", "script": "install_host_apt_bundle.sh"},
                    {"title": "Загрузка образов", "titleEn": "Load Images", "status": "partial", "body": "Загрузить Docker-образы из архивов бандла.", "bodyEn": "Load Docker images from the bundle image archives.", "script": "load_images.sh"},
                    {"title": "Деплой и проверка", "titleEn": "Deploy and Verify", "status": "partial", "body": "Восстановить состояние, запустить deploy.sh и затем проверить среду выполнения.", "bodyEn": "Restore state, run deploy.sh, and then verify the runtime.", "script": "deploy.sh + verify_runtime.sh"},
                ],
                "actions": [
                    {"title": "Выбрать архив", "titleEn": "Choose Archive", "body": "Выбрать один tar.gz архив офлайн-бандла.", "bodyEn": "Choose a single offline bundle tar.gz artifact.", "action": "Выбрать архив", "actionEn": "Choose Archive"},
                    {"title": "Распаковать бандл", "titleEn": "Unpack Bundle", "body": "Распаковать архив и подготовить целевой корень бандла.", "bodyEn": "Unpack the archive and prepare the target bundle root.", "action": "Распаковать бандл", "actionEn": "Unpack Bundle"},
                    {"title": "Проверить системные пакеты", "titleEn": "Check Host Packages", "body": "Запустить проверку системных пакетов перед установкой.", "bodyEn": "Run host package checks before installation.", "action": "Проверить системные пакеты", "actionEn": "Check Host Packages"},
                    {"title": "Установить APT-бандл", "titleEn": "Install Host APT Bundle", "body": "Установить офлайн-системные пакеты точной версии и конфигурацию среды.", "bodyEn": "Install offline host packages with exact versions and runtime configuration.", "action": "Установить APT-бандл", "actionEn": "Install Host APT Bundle"},
                    {"title": "Деплоить среду", "titleEn": "Deploy Runtime", "body": "Запустить канонический сценарий деплоя: загрузка образов, восстановление состояния, compose up и проверка.", "bodyEn": "Run the canonical deploy flow: image load, state restore, compose up, and verification.", "action": "Деплоить среду", "actionEn": "Deploy Runtime"},
                    {"title": "Запустить офлайн-бандл", "titleEn": "Run Offline Bundle", "body": "Использовать верхнеуровневый launcher после прохождения обязательного деплоя.", "bodyEn": "Use the top-level launcher after the deploy prerequisite is satisfied.", "action": "Запустить офлайн-бандл", "actionEn": "Run Offline Bundle"},
                ],
                "artifacts": [
                    {"title": "Приём архива", "titleEn": "Archive Intake", "body": archive_name, "bodyEn": archive_name, "badge": "ready"},
                    {"title": "Распакованный бандл", "titleEn": "Unpacked Bundle", "body": "compose.offline.yaml, env.bundle, manifest.json, images/, models/, state/", "bodyEn": "compose.offline.yaml, env.bundle, manifest.json, images/, models/, state/", "badge": "ready" if self.deploy_root.exists() else "partial"},
                    {"title": "Установка системных пакетов", "titleEn": "Host Package Install", "body": "versions.lock.json + матрица APT-бандла", "bodyEn": "versions.lock.json + apt bundle matrix", "badge": "ready" if (self.deploy_root / "host_packages").exists() else "partial"},
                    {"title": "Деплой среды", "titleEn": "Runtime Deploy", "body": "deploy.sh + verify_runtime.sh", "bodyEn": "deploy.sh + verify_runtime.sh", "badge": "partial"},
                ],
            },
        }

    def get_deploy_log_lines(self, archive_name: str | None = None) -> List[Dict[str, str]]:
        archive_name = archive_name or "agent-nav-offline-bundle_v1.0.tar.gz"
        return [
            {"mode": "build", "stage": "build", "text": "[build_bundle] deploy/offline_bundle build/export orchestration is available"},
            {"mode": "build", "stage": "wheelhouse", "text": "[build_wheelhouse] script detected and ready for offline Python wheel preparation"},
            {"mode": "build", "stage": "images", "text": "[export_images] image build/export path maps to deploy/offline_bundle/images/*.tar"},
            {"mode": "build", "stage": "host", "text": "[build_host_apt_bundle] host apt bundle builder detected for ubuntu package matrices"},
            {"mode": "build", "stage": "manifest", "text": "[generate_manifest] manifest and validate_bundle scripts present in offline bundle toolchain"},
            {"mode": "import", "stage": "archive", "text": f"[archive] preferred portable artifact is {archive_name}"},
            {"mode": "import", "stage": "validate", "text": "[validate_bundle] deploy-side validation script detected"},
            {"mode": "import", "stage": "host", "text": "[install_host_apt_bundle] host install/check script detected"},
            {"mode": "import", "stage": "images", "text": "[load_images] docker archive import path available"},
            {"mode": "import", "stage": "deploy", "text": "[deploy.sh] canonical offline deploy orchestration is available"},
        ]
