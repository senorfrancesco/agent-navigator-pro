import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "tests" / "harness" / "openwebui" / "qdrant_namespace_smoke.py"
SPEC = importlib.util.spec_from_file_location("qdrant_namespace_smoke", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_evaluate_summary_accepts_clean_namespace_state():
    result = MODULE.evaluate_summary(
        {
            "namespaces": {"separationOk": True, "issues": []},
            "openWebUI": {"collections": ["anp-openwebui-tenant-a"]},
            "sessionHandoff": {"enabled": True},
            "payloads": {"sessionCount": 2},
        },
        expect_openwebui_collections=True,
        expect_session_points=True,
    )

    assert result["ok"] is True
    assert result["issues"] == []


def test_evaluate_summary_rejects_missing_openwebui_collections_when_expected():
    result = MODULE.evaluate_summary(
        {
            "namespaces": {"separationOk": True, "issues": []},
            "openWebUI": {"collections": []},
            "sessionHandoff": {"enabled": True},
            "payloads": {"sessionCount": 0},
        },
        expect_openwebui_collections=True,
        expect_session_points=False,
    )

    assert result["ok"] is False
    assert "native Knowledge collections were expected but not detected" in result["issues"]


def test_evaluate_summary_rejects_missing_session_points_when_expected():
    result = MODULE.evaluate_summary(
        {
            "namespaces": {"separationOk": True, "issues": []},
            "openWebUI": {"collections": ["anp-openwebui-tenant-a"]},
            "sessionHandoff": {"enabled": True},
            "payloads": {"sessionCount": 0},
        },
        expect_openwebui_collections=False,
        expect_session_points=True,
    )

    assert result["ok"] is False
    assert "backend session points were expected but not detected" in result["issues"]


def test_evaluate_summary_rejects_disabled_session_handoff_when_session_points_expected():
    result = MODULE.evaluate_summary(
        {
            "namespaces": {"separationOk": True, "issues": []},
            "openWebUI": {"collections": ["anp-openwebui-tenant-a"]},
            "sessionHandoff": {"enabled": False},
            "payloads": {"sessionCount": 2},
        },
        expect_openwebui_collections=False,
        expect_session_points=True,
    )

    assert result["ok"] is False
    assert "backend session handoff is disabled in Open WebUI runtime" in result["issues"]
