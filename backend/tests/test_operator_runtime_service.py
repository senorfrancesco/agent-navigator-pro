from pathlib import Path
import subprocess

from orchestrator.operator_runtime_service import OperatorRuntimeService


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_runtime_service_discovers_native_and_bundle_paths(tmp_path, monkeypatch):
    _write(tmp_path / "scripts" / "launcher.sh", "#!/usr/bin/env bash\n")
    _write(tmp_path / "scripts" / "runtime_preflight.py", "print('ok')\n")
    _write(tmp_path / "backend" / ".env", "BACKEND_MODE=llama-cpp-python\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "compose.offline.yaml", "services: {}\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "env.bundle", "BUNDLE_DEPLOY_MODE=offline\n")
    _write(tmp_path / "deploy" / "offline_bundle" / "manifest.json", "{}\n")

    service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(service, "_find_binary", lambda name: "/usr/bin/docker" if name == "docker" else f"/usr/bin/{name}")
    monkeypatch.setattr(service, "_docker_socket_available", lambda: True)
    monkeypatch.setattr(service, "_detect_gpu", lambda: "Test GPU")
    monkeypatch.setattr(service, "_detect_memory_gb", lambda: 64)

    summary = service.get_runtime_paths()

    assert "native" in summary
    assert "container" in summary
    assert summary["native"]["status"] == "available"
    assert summary["container"]["status"] == "available"
    assert summary["native"]["configSources"][0]["path"] == "backend/.env"
    assert [item["path"] for item in summary["native"]["configSources"]] == [
        "backend/.env",
        "backend/.env.runtime",
    ]
    assert summary["native"]["nameEn"] == "Native Runtime"
    assert summary["native"]["configSources"][0]["roleEn"] == "canonical env for runtime and services"
    assert summary["container"]["profiles"][0]["titleEn"] == "Bundle image loading"
    assert summary["container"]["launchSource"] == "deploy/offline_bundle/scripts/run_offline_bundle.sh"


def test_runtime_service_hardware_metrics_use_runtime_context(tmp_path, monkeypatch):
    _write(tmp_path / "backend" / ".env.runtime", "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS=32768\n")

    service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(
        service,
        "_detect_gpu_inventory",
        lambda: [
            {"index": 0, "name": "NVIDIA GeForce RTX 4090", "memory_mib": 24564},
            {"index": 1, "name": "NVIDIA GeForce RTX 2070", "memory_mib": 8192},
        ],
    )
    monkeypatch.setattr(service, "_detect_memory_gb", lambda: 48)
    monkeypatch.setattr(service, "_detect_bundle_archive", lambda: "bundle.tar.gz")

    metrics = service.get_hardware_metrics()

    assert any(item["labelEn"] == "Suggested Context Budget" and item["value"] == "32768" for item in metrics)
    assert any(item["labelEn"] == "Bundle Archive Target" and item["value"] == "bundle.tar.gz" for item in metrics)
    assert any(item["labelEn"] == "GPU Visibility" and item["valueEn"] == "2x NVIDIA GPU detected" for item in metrics)
    assert any(item["labelEn"] == "GPU Inventory" and "GPU0: NVIDIA GeForce RTX 4090" in item["value"] and "GPU1: NVIDIA GeForce RTX 2070" in item["value"] for item in metrics)


def test_runtime_service_parses_all_visible_gpus(tmp_path, monkeypatch):
    service = OperatorRuntimeService(repo_root=tmp_path)
    monkeypatch.setattr(service, "_find_binary", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="0, NVIDIA GeForce RTX 2070, 8192 MiB\n1, NVIDIA GeForce RTX 2070, 8192 MiB\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    inventory = service._detect_gpu_inventory()

    assert inventory == [
        {"index": 0, "name": "NVIDIA GeForce RTX 2070", "memory_mib": 8192},
        {"index": 1, "name": "NVIDIA GeForce RTX 2070", "memory_mib": 8192},
    ]
    assert service._detect_gpu() == "2x NVIDIA GPU detected"
