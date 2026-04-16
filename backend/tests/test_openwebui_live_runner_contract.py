from __future__ import annotations

import json
import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_package_json_exposes_openwebui_live_runner():
    package_json = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package_json.get("scripts") or {}

    assert (
        scripts.get("test:e2e:openwebui:live")
        == "OPENWEBUI_LIVE_MODE=1 playwright test --headed --workers=1 tests/e2e/openwebui/deep-job.spec.ts"
    )


def test_playwright_config_supports_live_mode_flags():
    config = (PROJECT_ROOT / "playwright.config.ts").read_text(encoding="utf-8")

    assert "OPENWEBUI_LIVE_MODE" in config
    assert "slowMo" in config
    assert "fullyParallel: !liveMode" in config
    assert "workers: liveMode ? 1 : undefined" in config


def test_openwebui_deep_job_spec_contains_live_playback_helpers():
    spec = (PROJECT_ROOT / "tests" / "e2e" / "openwebui" / "deep-job.spec.ts").read_text(encoding="utf-8")

    assert "const LIVE_MODE" in spec
    assert "await livePause(" in spec
    assert "previewPromptInComposer" in spec


def test_openwebui_deep_job_spec_supports_live_fallback_without_manifest():
    spec = (PROJECT_ROOT / "tests" / "e2e" / "openwebui" / "deep-job.spec.ts").read_text(encoding="utf-8")

    assert "loadManifestForExecution" in spec
    assert "buildLiveModeManifestFallback" in spec
    assert "Requirements.pdf" in spec


def test_openwebui_deep_job_spec_runs_preflight_and_bootstrap_sync():
    spec = (PROJECT_ROOT / "tests" / "e2e" / "openwebui" / "deep-job.spec.ts").read_text(encoding="utf-8")

    assert "runOpenWebUIRuntimePreflight" in spec
    assert "runOpenWebUIBootstrapSync" in spec
    assert "assertInstalledActionFunctionsSynced" in spec
    assert "bootstrap_openwebui.py" in spec
