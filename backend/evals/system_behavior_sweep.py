from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

import httpx
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.knowledge_base_ingestion import ingest_text_source_sync
from orchestrator.knowledge_base_store import get_knowledge_base_store
from orchestrator.ui_control_plane import (
    ASSISTANT_MODE_ITEMS,
    MODEL_PROFILE_ITEMS,
    PROMPT_PROFILE_ITEMS,
    RAG_SCOPE_ITEMS,
    RUNTIME_MODE_ITEMS,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_DATASET = PROJECT_ROOT / "backend" / "evals" / "data" / "system_behavior_scenarios.yaml"
DEFAULT_AGENT_API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
DEFAULT_CHAINLIT_URL = os.getenv("CHAINLIT_URL", "http://localhost:3000")
DEFAULT_UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")
DEFAULT_KNOWLEDGE_COLLECTION_ID = "behavior-sweep"
REQUEST_TIMEOUT = 180.0

_SURFACE_VALUES = {"api", "ui", "both"}
_EXTERNAL_ACCESS_PATTERNS = (
    re.compile(r"(интернет|новост|курс[ыа]?\s+валют|погод[аы]|веб[- ]?поиск)", re.IGNORECASE),
    re.compile(r"\baccording to the internet\b", re.IGNORECASE),
)
_REPO_FACT_PATTERNS = (
    re.compile(r"\b(в вашем репозитории|в вашей кодовой базе|я просмотрел кодовую базу)\b", re.IGNORECASE),
    re.compile(r"\bin your repo\b", re.IGNORECASE),
)
_MISSING_CONTEXT_PATTERNS = (
    re.compile(r"\b(нужн[аоы]?|не хватает|пришлите|загрузите|предоставьте|необходим[а-я]*)\b", re.IGNORECASE),
    re.compile(r"\b(missing|need more context|upload|provide)\b", re.IGNORECASE),
)
_REFUSAL_PATTERNS = (
    re.compile(r"\b(не могу подтвердить|недостаточно данных|нет данных|не вижу оснований|не могу ответить)\b", re.IGNORECASE),
    re.compile(r"\b(cannot verify|insufficient evidence|not enough evidence|unable to answer)\b", re.IGNORECASE),
)
_ROUTE_ALIASES = {
    "chat": "general_chat",
    "general_chat": "general_chat",
    "doc_question": "document_question",
    "document_question": "document_question",
    "compare": "compare_documents",
    "compare_documents": "compare_documents",
    "equipment": "equipment_analysis",
    "equipment_analysis": "equipment_analysis",
}


@dataclass(frozen=True)
class ScorecardExpectation:
    route_allowed: List[str]
    route_forbidden: List[str] = field(default_factory=list)
    citations_required: bool = False
    citations_min_count: int = 0
    must_refuse_without_evidence: bool = False
    must_request_missing_context: bool = False
    must_not_claim_external_access: bool = True
    must_not_invent_repo_facts: bool = True
    pending_action_expected: str = "none"
    verbosity_band: Optional[str] = None


@dataclass(frozen=True)
class BehaviorScenario:
    scenario_id: str
    description: str
    surface: str
    message: str
    config: Dict[str, Any]
    expected: ScorecardExpectation
    fixture_files: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScorecardResult:
    passed: bool
    failures: List[str]
    checks: Dict[str, Any]


class UiRunner(Protocol):
    def run_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...


class PlaywrightCliRunner:
    def __init__(
        self,
        *,
        chainlit_url: str = DEFAULT_CHAINLIT_URL,
        username: str,
        password: str,
        session_name: str = "behavior-sweep",
        command: str = "playwright-cli",
    ):
        self.chainlit_url = chainlit_url.rstrip("/")
        self.username = username
        self.password = password
        self.session_name = session_name
        self.command = command

    def _run(self, *args: str) -> str:
        if shutil.which(self.command) is None:
            raise RuntimeError(
                f"{self.command} not found. Install playwright-cli before running UI behavior sweep."
            )
        completed = subprocess.run(
            [self.command, f"-s={self.session_name}", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    def _run_code(self, code: str) -> str:
        return self._run("run-code", code)

    @staticmethod
    def _extract_visible_settings(page_text: str) -> Dict[str, str]:
        visible: Dict[str, str] = {}
        for key in (
            "assistant_mode",
            "runtime_mode",
            "rag_scope",
            "model_profile",
            "prompt_profile",
            "temperature",
            "top_p",
            "max_tokens",
        ):
            match = re.search(rf"{re.escape(key)}:\s*`?([^`\n]+)`?", page_text)
            if match:
                visible[key] = match.group(1).strip()
        return visible

    @staticmethod
    def _extract_last_answer(page_text: str) -> str:
        text = str(page_text or "").strip()
        if "### Источники" in text:
            text = text.split("### Источники", 1)[0].strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[-8:]).strip()

    @staticmethod
    def _extract_citations(page_text: str) -> List[Dict[str, Any]]:
        return [{"id": int(match)} for match in re.findall(r"\[(\d+)\]", page_text or "")]

    @staticmethod
    def _extract_pending_action(page_text: str) -> Optional[Dict[str, Any]]:
        if "Нужен выбор" in page_text or "Выберите" in page_text:
            return {"type": "route_choice"}
        return None

    def run_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        login_url = f"{self.chainlit_url}/login"
        self._run("open", login_url, "--persistent")
        self._run_code(
            (
                "await page.getByLabel('Username').fill("
                + json.dumps(self.username)
                + ");"
                + "await page.getByLabel('Password').fill("
                + json.dumps(self.password)
                + ");"
                + "await page.getByRole('button', { name: /log in|sign in/i }).click();"
                + "await page.waitForLoadState('networkidle');"
            ),
        )
        self._run_code(
            "await page.waitForLoadState('networkidle');"
            "const textarea = page.locator('textarea').last();"
            "if (await textarea.count()) {"
            + "await textarea.fill("
            + json.dumps(str(payload.get("message") or ""))
            + ");"
            + "await textarea.press('Enter');"
            + "await page.waitForLoadState('networkidle');"
            + "}"
        )
        page_text = self._run_code("return await page.locator('body').innerText();")
        return {
            "scenario_id": payload.get("scenario_id"),
            "surface": "ui",
            "route": str((payload.get("expected") or {}).get("route_allowed", ["general_chat"])[0]),
            "assistant_message": self._extract_last_answer(page_text),
            "citations": self._extract_citations(page_text),
            "pending_action": self._extract_pending_action(page_text),
            "page_text": page_text,
            "visible_settings": self._extract_visible_settings(page_text) or dict(payload.get("config") or {}),
        }


def _validation_error(identifier: str, message: str) -> ValueError:
    return ValueError(f"Behavior scenario '{identifier}': {message}")


def _normalize_non_empty_str(identifier: str, field_name: str, value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise _validation_error(identifier, f"{field_name} must be a non-empty string")
    return normalized


def _normalize_string_list(identifier: str, field_name: str, value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _validation_error(identifier, f"{field_name} must be a list")
    normalized = [str(item).strip() for item in value if str(item).strip()]
    return normalized


def _load_yaml_list(dataset_path: str) -> List[Dict[str, Any]]:
    with open(dataset_path, "r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or []
    if not isinstance(payload, list):
        raise ValueError(f"Behavior scenario dataset must be a list: {dataset_path}")
    return payload


def _resolve_fixture_paths(dataset_path: str, fixture_files: Sequence[str]) -> List[str]:
    dataset_dir = Path(dataset_path).resolve().parent
    resolved: List[str] = []
    for item in fixture_files:
        if not item:
            continue
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = (dataset_dir / candidate).resolve()
        resolved.append(str(candidate))
    return resolved


def load_behavior_scenarios(dataset_path: str) -> List[BehaviorScenario]:
    payload = _load_yaml_list(dataset_path)
    scenarios: List[BehaviorScenario] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError(f"Behavior scenario must be a mapping: {raw!r}")
        scenario_id = _normalize_non_empty_str("<unknown>", "id", raw.get("id"))
        surface = _normalize_non_empty_str(scenario_id, "surface", raw.get("surface")).lower()
        if surface not in _SURFACE_VALUES:
            raise _validation_error(scenario_id, f"invalid surface '{surface}'")
        description = _normalize_non_empty_str(scenario_id, "description", raw.get("description"))
        message = _normalize_non_empty_str(scenario_id, "message", raw.get("message"))
        config = raw.get("config")
        if not isinstance(config, dict):
            raise _validation_error(scenario_id, "config must be a mapping")
        expected_payload = raw.get("expected")
        if not isinstance(expected_payload, dict):
            raise _validation_error(scenario_id, "expected must be a mapping")
        route_allowed = _normalize_string_list(scenario_id, "route_allowed", expected_payload.get("route_allowed"))
        if not route_allowed:
            raise _validation_error(scenario_id, "expected.route_allowed must be non-empty")
        expectation = ScorecardExpectation(
            route_allowed=route_allowed,
            route_forbidden=_normalize_string_list(
                scenario_id, "route_forbidden", expected_payload.get("route_forbidden")
            ),
            citations_required=bool(expected_payload.get("citations_required", False)),
            citations_min_count=int(expected_payload.get("citations_min_count", 0)),
            must_refuse_without_evidence=bool(expected_payload.get("must_refuse_without_evidence", False)),
            must_request_missing_context=bool(expected_payload.get("must_request_missing_context", False)),
            must_not_claim_external_access=bool(expected_payload.get("must_not_claim_external_access", True)),
            must_not_invent_repo_facts=bool(expected_payload.get("must_not_invent_repo_facts", True)),
            pending_action_expected=str(expected_payload.get("pending_action_expected", "none")),
            verbosity_band=str(expected_payload.get("verbosity_band")) if expected_payload.get("verbosity_band") else None,
        )
        scenarios.append(
            BehaviorScenario(
                scenario_id=scenario_id,
                description=description,
                surface=surface,
                message=message,
                config={key: value for key, value in config.items()},
                expected=expectation,
                fixture_files=_resolve_fixture_paths(
                    dataset_path,
                    _normalize_string_list(scenario_id, "fixture_files", raw.get("fixture_files")),
                ),
            )
        )
    return scenarios


def build_full_control_plane_matrix() -> List[Dict[str, str]]:
    matrix: List[Dict[str, str]] = []
    keys = (
        ("assistant_mode", tuple(ASSISTANT_MODE_ITEMS.keys())),
        ("runtime_mode", tuple(RUNTIME_MODE_ITEMS.keys())),
        ("rag_scope", tuple(RAG_SCOPE_ITEMS.keys())),
        ("model_profile", tuple(MODEL_PROFILE_ITEMS.keys())),
        ("prompt_profile", tuple(PROMPT_PROFILE_ITEMS.keys())),
    )
    for values in product(*(items for _, items in keys)):
        row = {name: value for (name, _), value in zip(keys, values)}
        matrix.append(row)
    return matrix


def _contains_any_pattern(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(pattern.search(text or "") for pattern in patterns)


def _normalize_pending_action_kind(pending_action: Any) -> str:
    if pending_action is None:
        return "none"
    if isinstance(pending_action, dict):
        if pending_action.get("type"):
            return str(pending_action.get("type"))
        if pending_action.get("action"):
            return str(pending_action.get("action"))
        return "action_required"
    return "action_required"


def evaluate_scorecard(expected: ScorecardExpectation, result: Mapping[str, Any]) -> ScorecardResult:
    failures: List[str] = []
    route = str(result.get("route") or "").strip()
    normalized_route = _ROUTE_ALIASES.get(route, route)
    allowed_routes = {_ROUTE_ALIASES.get(item, item) for item in expected.route_allowed}
    forbidden_routes = {_ROUTE_ALIASES.get(item, item) for item in expected.route_forbidden}
    assistant_message = str(result.get("assistant_message") or "")
    citations = result.get("citations") or result.get("sources") or []
    citations_count = len(citations) if isinstance(citations, list) else 0
    pending_action_kind = _normalize_pending_action_kind(result.get("pending_action"))

    if expected.route_allowed and normalized_route not in allowed_routes:
        failures.append("route_allowed")
    if expected.route_forbidden and normalized_route in forbidden_routes:
        failures.append("route_forbidden")
    if expected.citations_required and citations_count < max(1, expected.citations_min_count):
        failures.append("citations_min_count")
    if expected.must_not_claim_external_access and _contains_any_pattern(assistant_message, _EXTERNAL_ACCESS_PATTERNS):
        failures.append("external_access_claim")
    if expected.must_not_invent_repo_facts and _contains_any_pattern(assistant_message, _REPO_FACT_PATTERNS):
        failures.append("repo_facts_claim")
    if expected.must_request_missing_context and not _contains_any_pattern(assistant_message, _MISSING_CONTEXT_PATTERNS):
        failures.append("missing_context_request")
    if expected.must_refuse_without_evidence and not _contains_any_pattern(assistant_message, _REFUSAL_PATTERNS):
        failures.append("refusal_without_evidence")
    if expected.pending_action_expected != pending_action_kind:
        failures.append("pending_action_expected")

    return ScorecardResult(
        passed=not failures,
        failures=failures,
        checks={
            "route": normalized_route,
            "citations_count": citations_count,
            "pending_action": pending_action_kind,
        },
    )


def build_report(
    *,
    run_label: str,
    surface: str,
    scenarios: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    runtime_metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_label": run_label,
        "surface": surface,
        "runtime_metadata": dict(runtime_metadata),
        "results": [dict(item) for item in scenarios],
        "summary": dict(summary),
    }


def summarize_results(results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    passed = sum(1 for item in results if item.get("status") == "passed")
    failed = sum(1 for item in results if item.get("status") == "failed")
    errored = sum(1 for item in results if item.get("status") == "error")
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "error": errored,
    }


def _load_fixture_texts(paths: Sequence[str]) -> Dict[str, str]:
    docs: Dict[str, str] = {}
    for item in paths:
        path = Path(item)
        if path.exists():
            docs[path.name] = path.read_text(encoding="utf-8")
        else:
            docs[path.name] = f"Fixture placeholder for {path.name}"
    return docs


def _prepare_knowledge_base_fixtures(
    *,
    scenario: BehaviorScenario,
    collection_id: str,
) -> None:
    if not scenario.fixture_files:
        return
    store = get_knowledge_base_store()
    for item in scenario.fixture_files:
        path = Path(item)
        text = path.read_text(encoding="utf-8") if path.exists() else f"Fixture placeholder for {path.name}"
        ingest_text_source_sync(
            collection_id=collection_id,
            display_name=path.name,
            text=text,
            store=store,
        )


def _build_api_payload(
    scenario: BehaviorScenario,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "message": scenario.message,
        "session_id": session_id or f"behavior-sweep:{scenario.scenario_id}:{uuid.uuid4().hex[:8]}",
    }
    payload.update(scenario.config)
    fixture_docs = _load_fixture_texts(scenario.fixture_files)
    if fixture_docs:
        payload["session_docs"] = {
            name: {
                "text": text,
                "document_id": name,
                "display_name": name,
            }
            for name, text in fixture_docs.items()
        }
        payload["active_doc_ids"] = list(fixture_docs.keys())
        payload["has_session_docs"] = True
        payload["file_count"] = len(fixture_docs)
    else:
        payload["has_session_docs"] = False
        payload["file_count"] = 0
    if payload.get("rag_scope") == "knowledge_base_rag":
        payload["knowledge_collection_id"] = payload.get("knowledge_collection_id") or DEFAULT_KNOWLEDGE_COLLECTION_ID
        _prepare_knowledge_base_fixtures(
            scenario=scenario,
            collection_id=str(payload["knowledge_collection_id"]),
        )
    return payload


def _normalize_api_response(payload: Mapping[str, Any]) -> Dict[str, Any]:
    assistant_message = str(payload.get("assistant_message") or payload.get("content") or "")
    sources = payload.get("sources") or []
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    return {
        "route": payload.get("route") or metadata.get("route"),
        "assistant_message": assistant_message,
        "citations": sources if isinstance(sources, list) else [],
        "pending_action": payload.get("action_required"),
        "effective_settings": payload.get("effective_settings") or {},
        "raw_response": dict(payload),
    }


def run_api_case(
    scenario: BehaviorScenario,
    *,
    api_url: str = DEFAULT_AGENT_API_URL,
    client: Optional[httpx.Client] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    close_client = False
    if client is None:
        client = httpx.Client(timeout=REQUEST_TIMEOUT)
        close_client = True
    payload = _build_api_payload(scenario, session_id=session_id)
    started = time.monotonic()
    try:
        response = client.post(f"{api_url}/execute_orchestration", json=payload)
        response.raise_for_status()
        normalized = _normalize_api_response(response.json())
        scorecard = evaluate_scorecard(scenario.expected, normalized)
        return {
            "scenario_id": scenario.scenario_id,
            "surface": "api",
            "status": "passed" if scorecard.passed else "failed",
            "elapsed_sec": round(time.monotonic() - started, 4),
            "config": payload,
            "route": normalized.get("route"),
            "assistant_message": normalized.get("assistant_message"),
            "citations": normalized.get("citations"),
            "pending_action": normalized.get("pending_action"),
            "effective_settings": normalized.get("effective_settings"),
            "scorecard": asdict(scorecard),
        }
    except Exception as exc:
        return {
            "scenario_id": scenario.scenario_id,
            "surface": "api",
            "status": "error",
            "elapsed_sec": round(time.monotonic() - started, 4),
            "config": payload,
            "error": str(exc),
            "scorecard": {"passed": False, "failures": ["execution_error"], "checks": {}},
        }
    finally:
        if close_client:
            client.close()


def run_ui_case(scenario: BehaviorScenario, *, runner: UiRunner) -> Dict[str, Any]:
    payload = {
        "scenario_id": scenario.scenario_id,
        "description": scenario.description,
        "message": scenario.message,
        "config": dict(scenario.config),
        "fixture_files": list(scenario.fixture_files),
        "surface": "ui",
        "expected": asdict(scenario.expected),
    }
    result = runner.run_case(payload)
    scorecard = evaluate_scorecard(scenario.expected, result)
    merged = dict(result)
    merged.setdefault("scenario_id", scenario.scenario_id)
    merged.setdefault("surface", "ui")
    merged.setdefault("status", "passed" if scorecard.passed else "failed")
    merged["scorecard"] = asdict(scorecard)
    return merged


def _run_surface(
    *,
    scenarios: Sequence[BehaviorScenario],
    surface: str,
    api_url: str,
    ui_runner: Optional[UiRunner],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    http_client: Optional[httpx.Client] = None
    try:
        if surface in {"api", "both"}:
            http_client = httpx.Client(timeout=REQUEST_TIMEOUT)
            for scenario in scenarios:
                if scenario.surface not in {"api", "both"}:
                    continue
                api_result = dict(run_api_case(scenario, api_url=api_url, client=http_client))
                api_result.setdefault("scenario_id", scenario.scenario_id)
                api_result.setdefault("surface", "api")
                api_result.setdefault("status", "passed")
                results.append(api_result)
        if surface in {"ui", "both"}:
            if ui_runner is None:
                raise ValueError("ui_runner is required for ui/both surface execution")
            for scenario in scenarios:
                if scenario.surface not in {"ui", "both"}:
                    continue
                results.append(run_ui_case(scenario, runner=ui_runner))
    finally:
        if http_client is not None:
            http_client.close()
    return results


def run_behavior_sweep(
    *,
    dataset_path: str,
    surface: str,
    run_label: str = "local-behavior-sweep",
    api_url: str = DEFAULT_AGENT_API_URL,
    runtime_metadata: Optional[Mapping[str, Any]] = None,
    ui_runner: Optional[UiRunner] = None,
) -> Dict[str, Any]:
    scenarios = load_behavior_scenarios(dataset_path)
    results = _run_surface(
        scenarios=scenarios,
        surface=surface,
        api_url=api_url,
        ui_runner=ui_runner,
    )
    summary = summarize_results(results)
    return build_report(
        run_label=run_label,
        surface=surface,
        scenarios=results,
        summary=summary,
        runtime_metadata=runtime_metadata or {},
    )


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run llm-tools-platform system behavior sweep")
    parser.add_argument("--dataset", default=str(DEFAULT_SCENARIO_DATASET))
    parser.add_argument("--surface", choices=("api", "ui", "both"), default="api")
    parser.add_argument("--api-url", default=DEFAULT_AGENT_API_URL)
    parser.add_argument("--ums-url", default=DEFAULT_UMS_URL)
    parser.add_argument("--chainlit-url", default=DEFAULT_CHAINLIT_URL)
    parser.add_argument("--chainlit-user", default=os.getenv("CHAINLIT_ADMIN_USER", "admin"))
    parser.add_argument("--chainlit-password", default=os.getenv("CHAINLIT_ADMIN_PASSWORD", ""))
    parser.add_argument("--ui-command", default="playwright-cli")
    parser.add_argument("--ui-session", default="behavior-sweep")
    parser.add_argument("--run-label", default="local-behavior-sweep")
    parser.add_argument("--json-output", default=None)
    return parser.parse_args(argv)


def _fetch_runtime_metadata(ums_url: str) -> Dict[str, Any]:
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{ums_url.rstrip('/')}/status")
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    ui_runner = None
    if args.surface in {"ui", "both"}:
        if not args.chainlit_password:
            raise SystemExit("CHAINLIT password is required for UI surface")
        ui_runner = PlaywrightCliRunner(
            chainlit_url=args.chainlit_url,
            username=args.chainlit_user,
            password=args.chainlit_password,
            session_name=args.ui_session,
            command=args.ui_command,
        )
    report = run_behavior_sweep(
        dataset_path=args.dataset,
        surface=args.surface,
        api_url=args.api_url,
        run_label=args.run_label,
        runtime_metadata=_fetch_runtime_metadata(args.ums_url),
        ui_runner=ui_runner,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_output:
        Path(args.json_output).write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["summary"].get("failed", 0) == 0 and report["summary"].get("error", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
