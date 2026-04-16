import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = ROOT / "scripts"
SCRIPT_PATH = ROOT / "tests" / "harness" / "openwebui" / "openwebui_followup_payload_harness.py"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
SPEC = importlib.util.spec_from_file_location("openwebui_followup_payload_harness", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_parse_args_accepts_session_mode():
    args = MODULE.parse_args(
        [
            "--mode",
            "session",
            "--session-file",
            "/tmp/contract.txt",
            "--session-prompt",
            "Что сказано про штраф?",
            "--session-result-marker",
            "17 процентов",
        ]
    )

    assert args.mode == "session"
    assert args.session_file == "/tmp/contract.txt"
    assert args.session_prompt == "Что сказано про штраф?"
    assert args.session_result_marker == "17 процентов"


def test_parse_args_accepts_diagnostic_mode():
    args = MODULE.parse_args(
        [
            "--mode",
            "diagnostic",
            "--diagnostic-dir",
            "/tmp/harness-artifacts",
        ]
    )

    assert args.mode == "diagnostic"
    assert args.diagnostic_dir == "/tmp/harness-artifacts"


def test_analyze_session_result_accepts_backend_owned_handoff():
    result = MODULE.analyze_session_result(
        payload={
            "firstRequest": {
                "rawBody": '{"model":"raw.qwen-14b-llm","files":[{"id":"file-1"}]}',
                "parsedBody": {
                    "model": "raw.qwen-14b-llm",
                    "files": [{"id": "file-1"}],
                },
            },
            "visibleTextSample": "По договору штраф составляет 17 процентов от суммы обязательства [1]",
            "resultSeen": True,
            "capturedCount": 1,
            "pageUrl": "http://127.0.0.1:3001/?temporary-chat=true",
        },
        operator_summary={
            "payloads": {"sessionCount": 2},
            "namespaces": {"separationOk": True},
        },
        session_result_marker="17 процентов",
    )

    assert result["classification"] == "success"
    assert result["issues"] == []
    assert result["requestModel"] == "raw.qwen-14b-llm"
    assert result["sessionCount"] == 2
    assert result["namespaceSeparationOk"] is True


def test_analyze_knowledge_result_accepts_existing_native_namespace():
    result = MODULE.analyze_knowledge_result(
        operator_summary={
            "server": {"reachable": True},
            "openWebUI": {"collections": ["anp-openwebui_files", "anp-openwebui_knowledge"]},
            "namespaces": {"separationOk": True},
        }
    )

    assert result["classification"] == "success"
    assert result["collections"] == ["anp-openwebui_files", "anp-openwebui_knowledge"]
    assert result["issues"] == []


def test_analyze_knowledge_result_reports_missing_namespace():
    result = MODULE.analyze_knowledge_result(
        operator_summary={
            "server": {"reachable": False},
            "openWebUI": {"collections": []},
            "namespaces": {"separationOk": False},
        }
    )

    assert result["classification"] == "knowledge_namespace_regression"
    assert "operator summary cannot reach the Qdrant server" in result["issues"]
    assert "native Knowledge collections were not detected in Qdrant" in result["issues"]
    assert "Qdrant namespace separation check failed for native Knowledge" in result["issues"]


def test_analyze_session_result_reports_missing_backend_signals():
    result = MODULE.analyze_session_result(
        payload={
            "firstRequest": {
                "rawBody": "",
                "parsedBody": {"model": "llm-tools-platform", "files": []},
            },
            "visibleTextSample": "Ответ без цитаты",
            "resultSeen": False,
            "capturedCount": 0,
            "pageUrl": "http://127.0.0.1:3001/?temporary-chat=true",
        },
        operator_summary={
            "payloads": {"sessionCount": 0},
            "namespaces": {"separationOk": False},
        },
        session_result_marker="17 процентов",
    )

    assert result["classification"] == "session_handoff_regression"
    assert "ordinary chat upload request was not captured" in result["issues"]
    assert "ordinary chat upload request does not contain top-level files" in result["issues"]
    assert "ordinary chat upload request is not using the expected raw client-side model" in result["issues"]
    assert "backend session points were not detected after the chat upload" in result["issues"]
    assert "Qdrant namespace separation check failed after the chat upload" in result["issues"]


def test_build_combined_report_keeps_both_paths_and_success_flag():
    result = MODULE.build_combined_report(
        mode="full",
        session_report={"classification": "success", "issues": []},
        knowledge_report={"classification": "success", "issues": []},
        operator_summary={"namespaces": {"separationOk": True}},
    )

    assert result["ok"] is True
    assert result["classification"] == "success"
    assert result["failureKind"] == "none"
    assert result["namespaceSeparationOk"] is True


def test_collect_diagnostic_artifacts_writes_expected_files(monkeypatch, tmp_path):
    responses = [
        {"ok": True, "returncode": 0, "stdout": "openwebui log", "stderr": "", "command": ["docker"]},
        {"ok": False, "returncode": 1, "stdout": "", "stderr": "no agent-api container", "command": ["docker"]},
        {"ok": True, "returncode": 0, "stdout": "tmux tail", "stderr": "", "command": ["tmux"]},
    ]

    def fake_run_best_effort(*command):
        return responses.pop(0)

    monkeypatch.setattr(MODULE, "_run_best_effort_command", fake_run_best_effort)

    artifacts = MODULE.collect_diagnostic_artifacts(
        diagnostic_dir=str(tmp_path / "diag"),
        report={"classification": "session_handoff_regression"},
        operator_summary={"payloads": {"sessionCount": 0}},
    )

    assert Path(artifacts["report"]).is_file()
    assert Path(artifacts["operatorSummary"]).is_file()
    assert Path(artifacts["openWebUIDockerLogs"]["path"]).read_text(encoding="utf-8") == "openwebui log"
    assert Path(artifacts["agentApiDockerLogs"]["path"]).read_text(encoding="utf-8") == "no agent-api container"
    assert Path(artifacts["agentApiTmuxTail"]["path"]).read_text(encoding="utf-8") == "tmux tail"


def test_run_playwright_invokes_wrapper_via_bash(monkeypatch, tmp_path):
    script_path = tmp_path / "playwright_cli.sh"
    script_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    captured = {}

    class _FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, input=None, text=None, capture_output=None):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(MODULE, "PLAYWRIGHT_CLI", script_path)
    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    result = MODULE.run_playwright("session-1", "snapshot")

    assert result == "ok"
    assert captured["cmd"] == ["bash", str(script_path), "-s=session-1", "snapshot"]


def test_build_session_run_code_uses_page_url_based_navigation():
    script = MODULE.build_session_run_code(
        openwebui_base_url="http://127.0.0.1:3001",
        session_file="/tmp/contract.txt",
        session_prompt="Что сказано про штраф?",
        session_result_marker="17 процентов",
        timeout_ms=120000,
    )

    assert 'const openwebuiBaseUrl = "http://127.0.0.1:3001";' in script
    assert 'await page.goto(openwebuiBaseUrl + "/?temporary-chat=true");' in script
    assert 'page.on("console"' in script
    assert 'page.on("pageerror"' in script
