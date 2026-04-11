from pathlib import Path

from orchestrator.operator_deploy_service import OperatorDeployService


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_deploy_service_exposes_build_and_import_stage_catalog(tmp_path):
    deploy_root = tmp_path / "deploy" / "offline_bundle"
    scripts_root = deploy_root / "scripts"

    _write(scripts_root / "build_bundle.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "build_host_apt_bundle.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "export_images.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "build_wheelhouse.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "export_models.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "export_state.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "generate_manifest.py", "print('ok')\n")
    _write(scripts_root / "validate_bundle.py", "print('ok')\n")
    _write(scripts_root / "install_host_apt_bundle.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "deploy.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "load_images.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "restore_state.sh", "#!/usr/bin/env bash\n")
    _write(scripts_root / "run_offline_bundle.sh", "#!/usr/bin/env bash\n")
    _write(deploy_root / "manifest.json", "{}\n")
    _write(deploy_root / "compose.offline.yaml", "services: {}\n")
    _write(deploy_root / "env.bundle", "BUNDLE_DEPLOY_MODE=offline\n")
    _write(deploy_root / "host_packages" / "ubuntu-24.04" / "versions.lock.json", "{}\n")
    _write(deploy_root / "images" / "ums.tar", "")

    service = OperatorDeployService(repo_root=tmp_path)
    surface = service.get_deploy_surface(docker_socket_available=True)

    assert set(surface.keys()) == {"build", "import"}
    assert surface["build"]["labelEn"] == "Build Bundle"
    assert surface["build"]["summaryTitleEn"] == "Build Bundle Status"
    assert any(stage["title"] == "Wheelhouse" for stage in surface["build"]["stages"])
    assert any(stage["titleEn"] == "Archive Intake" for stage in surface["import"]["stages"])
    assert any(item["labelEn"] == "Canonical Entrypoint" and item["value"] == "deploy.sh" for item in surface["import"]["summary"])
    assert any(artifact["titleEn"] == "Manifest" and artifact["badge"] == "ready" for artifact in surface["build"]["artifacts"])


def test_deploy_service_exposes_stage_scoped_log_lines(tmp_path):
    _write(tmp_path / "deploy" / "offline_bundle" / "scripts" / "validate_bundle.py", "print('ok')\n")

    service = OperatorDeployService(repo_root=tmp_path)
    logs = service.get_deploy_log_lines("bundle.tar.gz")

    assert any(line["mode"] == "build" and line["stage"] == "manifest" for line in logs)
    assert any(line["mode"] == "import" and line["stage"] == "deploy" for line in logs)
