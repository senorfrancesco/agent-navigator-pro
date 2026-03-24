from __future__ import annotations

from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_required_bundle_paths_exist() -> None:
    required_paths = [
        "README.md",
        "compose.offline.yaml",
        "manifest.template.json",
        "env.bundle.example",
        "Dockerfile.backend.offline",
        "Dockerfile.ums.offline",
        "Dockerfile.chainlit.offline",
        "requirements.ums.lock.txt",
        "vendor/README.md",
        "scripts/check_host.sh",
        "scripts/build_host_apt_bundle.sh",
        "scripts/install_host_apt_bundle.sh",
        "scripts/check_host_packages.py",
        "scripts/verify_host_runtime.py",
        "scripts/host_bundle_common.sh",
        "scripts/load_images.sh",
        "scripts/export_models.sh",
        "scripts/validate_bundle.py",
        "scripts/preflight_runtime.py",
        "scripts/restore_state.sh",
        "scripts/deploy.sh",
        "scripts/verify_runtime.sh",
        "scripts/launch_tmux_workspace.sh",
        "scripts/run_offline_bundle.sh",
        "scripts/stop_offline_bundle.sh",
        "scripts/build_bundle.sh",
        "docs/BUILD_AND_EXPORT.md",
        "docs/DEPLOY_OFFLINE.md",
        "tmux/tmux.conf",
        "tmux/tmux.conf.local",
        "monitoring/prometheus.yml",
        "monitoring/grafana/provisioning/datasources/prometheus.yml",
        "monitoring/grafana/provisioning/dashboards/dashboards.yml",
        "monitoring/grafana/dashboards/offline-bundle-overview.json",
        "state/host-install/.gitkeep",
        "state/legacy-orchestrator-files",
    ]

    for rel_path in required_paths:
        assert (BUNDLE_ROOT / rel_path).exists(), rel_path
