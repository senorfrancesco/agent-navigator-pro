from __future__ import annotations

import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_flags_reference_contains_runtime_env_matrix():
    text = _read("docs/flags-reference.md")

    assert "backend/.env" in text
    assert "backend/.env.runtime" in text
    assert "руками не редактировать" in text
    assert "deprecated" in text


def test_flags_reference_covers_timeout_flags():
    text = _read("docs/flags-reference.md")

    assert "UMS_INFER_TIMEOUT_S" in text
    assert "UMS_CLIENT_TIMEOUT_S" in text
    assert "UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S" in text


def test_deploy_guide_points_to_launcher_as_canonical_entrypoint():
    text = _read("docs/deploy-guide.md")

    assert "./scripts/launcher.sh --target container --profile default --no-attach" in text
    assert "canonical entrypoint остаётся `launcher.sh`" in text


def test_docs_distinguish_runtime_and_hardware_override_files():
    readme = _read("README.md")
    deploy = _read("docs/deploy-guide.md")
    scripts_readme = _read("docs/scripts/README.md")

    assert "backend/.env.runtime" in readme
    assert "generated applied env" in readme
    assert "backend/.env.native" in readme
    assert "deprecated" in readme

    assert "backend/.env.runtime" in deploy
    assert "generated applied env" in deploy
    assert "deprecated" in deploy

    assert "backend/.env.runtime" in scripts_readme
    assert "backend/.env.native" in scripts_readme
    assert "deprecated" in scripts_readme
