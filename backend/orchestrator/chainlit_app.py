"""
Chainlit App — замена Open WebUI для Agent Navigator Pro.

Преимущества:
- cl.Step → каждая LangGraph нода видна как шаг с прогрессом
- Нет встроенного RAG → нет конфликтов
- Файлы не дублируются при follow-up
- Python-native → тот же стек что FastAPI/LangGraph
- Docker-ready: chainlit run app.py --host 0.0.0.0 --port 3000
- Auth: password-based authentication (env-driven)
- History: SQLAlchemy data layer (SQLite) for chat persistence
"""

import asyncio
import copy
import logging
import os
import re
import sqlite3
import sys
import time
import shutil
import httpx
import uuid
from typing import Dict, List, Optional, Any

# Добавляем пути (оставляем для обратной совместимости, но используем абсолютные)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chainlit as cl
except ImportError:
    raise ImportError("chainlit not installed. Run: pip install chainlit")

from services.model_manager.ums_client import ums_client
from services.hardware.tier_selector import describe_rag_mode
from orchestrator.rag.classifier import LLMIntentClassifier, select_classifier_result
from orchestrator.shared.http_client import get_shared_client
from orchestrator.orchestration_runtime import (
    ROUTE_CHOICE_TIMEOUT_S,
    decide_orchestration as _backend_decide_orchestration,
    normalize_runtime_mode as _backend_normalize_runtime_mode,
    resolve_pending_action_selection as _backend_resolve_pending_action_selection,
)
from orchestrator.execution_runtime import (
    ExecutionDependencies,
    execute_orchestration as _backend_execute_orchestration,
)
from orchestrator.doc_question_heuristics import (
    DocQuestionResponse,
    SourceRef,
    build_doc_question_deterministic_fallback as _build_doc_question_deterministic_fallback,
    citations_are_valid as _citations_are_valid,
    compute_confidence_v1 as _compute_confidence_v1,
    extract_citation_ids as _extract_citation_ids,
    has_sufficient_evidence_v1 as _has_sufficient_evidence,
)
from orchestrator.knowledge_base_store import get_knowledge_base_store
from orchestrator.state_store import get_orchestration_state_store
from orchestrator.ui_control_plane import (
    ASSISTANT_MODE_ITEMS as _ASSISTANT_MODE_ITEMS,
    MODEL_PROFILE_ITEMS as _MODEL_PROFILE_ITEMS,
    PROMPT_PROFILE_ITEMS as _PROMPT_PROFILE_ITEMS,
    RAG_SCOPE_ITEMS as _RAG_SCOPE_ITEMS,
    RUNTIME_MODE_ITEMS as _RUNTIME_MODE_ITEMS,
    build_initial_control_plane_state,
    build_preset_state,
    clamp_generation_overrides,
    get_prompt_profile_system_message,
    merge_control_plane_state,
    resolve_effective_settings,
    resolve_model_id,
)

logger = logging.getLogger("chainlit_app")

INTENT_CLASSIFIER_MODE = os.getenv("INTENT_CLASSIFIER_MODE", "embedder").strip().lower()
INTENT_CLASSIFIER_EMBEDDER_MODEL = os.getenv(
    "INTENT_CLASSIFIER_EMBEDDER_MODEL",
    "qwen3-embedding-0.6b",
)
INTENT_CLASSIFIER_LLM_MODEL = os.getenv("INTENT_CLASSIFIER_LLM_MODEL", "qwen-14b-llm")
INTENT_CLASSIFIER_LLM_CONFIDENCE_THRESHOLD = float(
    os.getenv("INTENT_CLASSIFIER_LLM_CONFIDENCE_THRESHOLD", "0.75")
)
INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD = float(
    os.getenv("INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD", "0.60")
)
INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD = float(
    os.getenv("INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD", "0.10")
)
LEGAL_EMBEDDER_MODEL = os.getenv("LEGAL_EMBEDDER_MODEL", "labse-embedding")

_DOC_QUESTION_UPLOAD_REQUEST_PHRASES = [
    "предоставьте тексты",
    "предоставьте текст",
    "пришлите текст",
    "загрузите тексты",
    "загрузите текст",
    "нужно увидеть тексты",
    "мне нужно увидеть тексты",
    "предоставьте содержание",
    "нужно содержание",
]
RAG_INDEX_CACHE_MAX = int(os.getenv("RAG_INDEX_CACHE_MAX", "8"))
RAG_INDEX_CACHE_TTL_S = int(os.getenv("RAG_INDEX_CACHE_TTL_S", "1800"))


# === RAG Mode Helper ===

def _default_runtime_budget_metadata() -> Dict[str, Any]:
    effective_context_tokens = int(os.getenv("CHAINLIT_DEFAULT_EFFECTIVE_CONTEXT_TOKENS", "8192"))
    context_budget_ratio = float(os.getenv("CHAINLIT_RETRIEVED_CONTEXT_RATIO", "0.60"))
    retrieved_context_tokens_budget = int(effective_context_tokens * context_budget_ratio)
    return {
        "runtime_profile": "default",
        "effective_context_tokens": effective_context_tokens,
        "retrieved_context_tokens_budget": retrieved_context_tokens_budget,
        "generation_tokens_reserve": max(256, effective_context_tokens - retrieved_context_tokens_budget),
        "context_budget_ratio": context_budget_ratio,
        "rag_mode": "simple",
        "rag_mode_label": describe_rag_mode("simple"),
    }


async def _get_runtime_budget_metadata() -> Dict[str, Any]:
    override = os.getenv("RAG_MODE_OVERRIDE", "auto")
    fallback = _default_runtime_budget_metadata()
    if override != "auto":
        fallback["rag_mode"] = override
        fallback["rag_mode_label"] = describe_rag_mode(override)
        return fallback
    try:
        ums_url = os.getenv("UMS_URL", "http://localhost:8090")
        client = await get_shared_client()
        resp = await client.get(f"{ums_url}/status", timeout=5.0)
        data = resp.json()
        budget = {
            "runtime_profile": data.get("runtime_profile") or fallback["runtime_profile"],
            "effective_context_tokens": int(data.get("effective_context_tokens") or fallback["effective_context_tokens"]),
            "retrieved_context_tokens_budget": int(
                data.get("retrieved_context_tokens_budget") or fallback["retrieved_context_tokens_budget"]
            ),
            "generation_tokens_reserve": int(
                data.get("generation_tokens_reserve") or fallback["generation_tokens_reserve"]
            ),
            "context_budget_ratio": float(data.get("context_budget_ratio") or fallback["context_budget_ratio"]),
            "rag_mode": data.get("tier", {}).get("rag_mode", fallback["rag_mode"]),
            "rag_mode_label": data.get("tier", {}).get("rag_mode_label")
            or describe_rag_mode(data.get("tier", {}).get("rag_mode", fallback["rag_mode"])),
        }
        cl.user_session.set("runtime_budget_metadata", budget)
        return budget
    except Exception:
        cl.user_session.set("runtime_budget_metadata", fallback)
        return fallback


# === Authentication ===

ADMIN_USER = os.getenv("CHAINLIT_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("CHAINLIT_ADMIN_PASSWORD", "admin")


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> Optional[cl.User]:
    if (username, password) == (ADMIN_USER, ADMIN_PASSWORD):
        return cl.User(
            identifier=username,
            metadata={"role": "admin", "provider": "credentials"},
        )
    return None


# === Data Layer (SQLite persistence, optional) ===

_DB_URL = os.getenv(
    "CHAINLIT_DB_URL",
    "sqlite+aiosqlite:///.data/chainlit.db",
)
_ENABLE_DATA_LAYER = os.getenv("CHAINLIT_ENABLE_DATA_LAYER", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _sqlite_db_path_from_conninfo(conninfo: str) -> Optional[str]:
    if not conninfo.startswith("sqlite"):
        return None
    if ":///" not in conninfo:
        return None
    raw_path = conninfo.split(":///", 1)[1]
    if not raw_path:
        return None
    if raw_path.startswith("/"):
        return raw_path
    return os.path.join(os.getcwd(), raw_path)


def _bootstrap_chainlit_sqlite_schema(conninfo: str) -> None:
    db_path = _sqlite_db_path_from_conninfo(conninfo)
    if not db_path:
        return
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    schema_sql = """
    CREATE TABLE IF NOT EXISTS "users" (
        "id" TEXT PRIMARY KEY,
        "identifier" TEXT NOT NULL UNIQUE,
        "createdAt" TEXT NOT NULL,
        "metadata" TEXT
    );

    CREATE TABLE IF NOT EXISTS "threads" (
        "id" TEXT PRIMARY KEY,
        "createdAt" TEXT,
        "name" TEXT,
        "userId" TEXT,
        "userIdentifier" TEXT,
        "tags" TEXT,
        "metadata" TEXT
    );

    CREATE TABLE IF NOT EXISTS "steps" (
        "id" TEXT PRIMARY KEY,
        "name" TEXT,
        "type" TEXT,
        "threadId" TEXT,
        "parentId" TEXT,
        "streaming" INTEGER,
        "waitForAnswer" INTEGER,
        "isError" INTEGER,
        "metadata" TEXT,
        "tags" TEXT,
        "input" TEXT,
        "output" TEXT,
        "createdAt" TEXT,
        "start" TEXT,
        "end" TEXT,
        "generation" TEXT,
        "showInput" TEXT,
        "language" TEXT
    );

    CREATE TABLE IF NOT EXISTS "feedbacks" (
        "id" TEXT PRIMARY KEY,
        "forId" TEXT,
        "value" INTEGER,
        "comment" TEXT
    );

    CREATE TABLE IF NOT EXISTS "elements" (
        "id" TEXT PRIMARY KEY,
        "threadId" TEXT,
        "type" TEXT,
        "chainlitKey" TEXT,
        "url" TEXT,
        "objectKey" TEXT,
        "name" TEXT,
        "display" TEXT,
        "size" TEXT,
        "language" TEXT,
        "page" INTEGER,
        "autoPlay" INTEGER,
        "playerConfig" TEXT,
        "forId" TEXT,
        "mime" TEXT,
        "props" TEXT
    );
    """
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
        conn.commit()

if _ENABLE_DATA_LAYER:
    try:
        from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

        @cl.data_layer
        def get_data_layer():
            _bootstrap_chainlit_sqlite_schema(_DB_URL)
            return SQLAlchemyDataLayer(conninfo=_DB_URL)

    except ImportError:
        logger.warning("SQLAlchemy data layer unavailable, continuing without persistence")
else:
    logger.info("CHAINLIT_ENABLE_DATA_LAYER=false, running without persistence")


# === Session Storage ===

def _ensure_session_state() -> None:
    if cl.user_session.get("history") is None:
        cl.user_session.set("history", [])
    if cl.user_session.get("documents_by_id") is None:
        cl.user_session.set("documents_by_id", {})
    if cl.user_session.get("documents_by_name") is None:
        cl.user_session.set("documents_by_name", {})
    if cl.user_session.get("active_doc_ids") is None:
        cl.user_session.set("active_doc_ids", [])
    if cl.user_session.get("rag_pipeline_cache") is None:
        cl.user_session.set("rag_pipeline_cache", {})
    if cl.user_session.get("documents") is None:
        cl.user_session.set("documents", {})
    if cl.user_session.get("pending_action") is None:
        cl.user_session.set("pending_action", None)
    if cl.user_session.get("pending_route_choice") is None:
        cl.user_session.set("pending_route_choice", None)
    if cl.user_session.get("runtime_mode") is None:
        cl.user_session.set("runtime_mode", "auto")
    if cl.user_session.get("control_plane_state") is None:
        cl.user_session.set(
            "control_plane_state",
            build_initial_control_plane_state(cl.user_session.get("runtime_mode")),
        )
    if cl.user_session.get("effective_settings") is None:
        cl.user_session.set(
            "effective_settings",
            resolve_effective_settings(cl.user_session.get("control_plane_state") or {}),
        )
    if cl.user_session.get("last_route") is None:
        cl.user_session.set("last_route", None)
    if cl.user_session.get("last_executor") is None:
        cl.user_session.set("last_executor", None)
    if cl.user_session.get("last_trace_id") is None:
        cl.user_session.set("last_trace_id", None)
    if cl.user_session.get("active_mode") is None:
        cl.user_session.set("active_mode", None)
    if cl.user_session.get("runtime_budget_metadata") is None:
        cl.user_session.set("runtime_budget_metadata", _default_runtime_budget_metadata())
    if cl.user_session.get("run_id") is None:
        cl.user_session.set("run_id", None)
    if cl.user_session.get("state_ref") is None:
        cl.user_session.set("state_ref", None)
    if cl.user_session.get("state_version") is None:
        cl.user_session.set("state_version", None)
    if cl.user_session.get("thread_name") is None:
        cl.user_session.set("thread_name", None)
    if cl.user_session.get("thread_name_locked") is None:
        cl.user_session.set("thread_name_locked", False)

    # Legacy migration: {name: {text, path}} -> documents_by_id/documents_by_name
    docs_by_id = cl.user_session.get("documents_by_id") or {}
    legacy_docs = cl.user_session.get("documents") or {}
    if legacy_docs and not docs_by_id:
        migrated_by_id: Dict[str, Any] = {}
        migrated_by_name: Dict[str, List[str]] = {}
        ts_base = time.time()
        for idx, (name, info) in enumerate(legacy_docs.items(), start=1):
            doc_id = f"legacy-{idx}"
            migrated_by_id[doc_id] = {
                "document_id": doc_id,
                "display_name": name,
                "version": 1,
                "path": info.get("path", ""),
                "text": info.get("text", ""),
                "uploaded_at": ts_base + idx / 1000.0,
                "source_message_id": None,
            }
            migrated_by_name.setdefault(name, []).append(doc_id)
        cl.user_session.set("documents_by_id", migrated_by_id)
        cl.user_session.set("documents_by_name", migrated_by_name)
        cl.user_session.set("active_doc_ids", list(migrated_by_id.keys())[-2:])

    effective = cl.user_session.get("effective_settings") or resolve_effective_settings(
        cl.user_session.get("control_plane_state") or {}
    )
    cl.user_session.set("effective_settings", effective)
    cl.user_session.set("runtime_mode", _backend_normalize_runtime_mode(effective.get("runtime_mode")))


def _get_documents_by_id() -> Dict[str, Any]:
    _ensure_session_state()
    return cl.user_session.get("documents_by_id") or {}


def _get_documents_by_name() -> Dict[str, List[str]]:
    _ensure_session_state()
    docs_by_id = _get_documents_by_id()
    rebuilt: Dict[str, List[str]] = {}
    docs_sorted = sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0)))
    for doc in docs_sorted:
        name = str(doc.get("display_name", "")).strip()
        doc_id = str(doc.get("document_id", "")).strip()
        if not name or not doc_id:
            continue
        rebuilt.setdefault(name, []).append(doc_id)
    cl.user_session.set("documents_by_name", rebuilt)
    return rebuilt


def _doc_label(display_name: str, version: int) -> str:
    return f"{display_name} (v{version})" if int(version) > 1 else display_name


def _sync_legacy_documents_cache() -> Dict[str, Any]:
    docs_by_id = _get_documents_by_id()
    latest_by_name: Dict[str, Any] = {}
    for doc in sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0))):
        name = str(doc.get("display_name", "")).strip()
        if not name:
            continue
        existing = latest_by_name.get(name)
        if existing is None or int(doc.get("version", 1)) >= int(existing.get("version", 1)):
            latest_by_name[name] = doc

    legacy: Dict[str, Any] = {}
    for doc in sorted(latest_by_name.values(), key=lambda d: float(d.get("uploaded_at", 0.0))):
        legacy[doc["display_name"]] = {
            "text": doc.get("text", ""),
            "path": doc.get("path", ""),
            "document_id": doc.get("document_id"),
            "version": int(doc.get("version", 1)),
        }
    cl.user_session.set("documents", legacy)
    return legacy


def _get_session_docs() -> Dict[str, Any]:
    # Backward-compatible view: latest version per display_name.
    _ensure_session_state()
    return _sync_legacy_documents_cache()


def _get_active_doc_ids() -> List[str]:
    _ensure_session_state()
    active_ids = list(cl.user_session.get("active_doc_ids") or [])
    docs_by_id = _get_documents_by_id()
    active_ids = [doc_id for doc_id in active_ids if doc_id in docs_by_id]
    if active_ids:
        cl.user_session.set("active_doc_ids", active_ids)
        return active_ids

    if not docs_by_id:
        return []

    docs_sorted = sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0)))
    default_ids = [str(docs_sorted[-1]["document_id"])]
    if len(docs_sorted) >= 2:
        default_ids = [str(docs_sorted[-2]["document_id"]), str(docs_sorted[-1]["document_id"])]
    cl.user_session.set("active_doc_ids", default_ids)
    return default_ids


def _set_active_doc_ids(doc_ids: List[str]) -> List[str]:
    docs_by_id = _get_documents_by_id()
    unique: List[str] = []
    seen = set()
    for doc_id in doc_ids:
        if doc_id in docs_by_id and doc_id not in seen:
            seen.add(doc_id)
            unique.append(doc_id)
    cl.user_session.set("active_doc_ids", unique)
    # При смене активного набора старый pending-choice становится невалидным.
    _set_pending_route_choice(None)
    return unique


def _get_active_docs() -> List[Dict[str, Any]]:
    docs_by_id = _get_documents_by_id()
    active_ids = _get_active_doc_ids()
    return [docs_by_id[doc_id] for doc_id in active_ids if doc_id in docs_by_id]


def _get_all_docs() -> List[Dict[str, Any]]:
    docs_by_id = _get_documents_by_id()
    return sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0)))


def _get_report_docs() -> List[Dict[str, Any]]:
    return [
        d
        for d in _get_all_docs()
        if str(d.get("display_name", "")).lower().startswith("report_")
    ]


def _get_active_session_docs() -> Dict[str, Any]:
    active_docs = _get_active_docs()
    result: Dict[str, Any] = {}
    for doc in active_docs:
        key = str(doc.get("display_name", "")).strip()
        if key in result:
            key = _doc_label(key, int(doc.get("version", 1)))
        result[key] = {
            "text": doc.get("text", ""),
            "path": doc.get("path", ""),
            "document_id": doc.get("document_id", ""),
            "display_name": doc.get("display_name", key),
            "version": int(doc.get("version", 1)),
        }
    return result


def _active_set_status_line() -> str:
    active_docs = _get_active_docs()
    if not active_docs:
        return "Активный набор: пусто"
    labels = [_doc_label(str(d["display_name"]), int(d.get("version", 1))) for d in active_docs]
    return f"Активный набор: {', '.join(labels)} ({len(labels)} docs)"


def _get_active_doc_labels() -> List[str]:
    active_docs = _get_active_docs()
    return [_doc_label(str(d["display_name"]), int(d.get("version", 1))) for d in active_docs]


def _next_doc_version(display_name: str) -> int:
    docs_by_name = _get_documents_by_name()
    docs_by_id = _get_documents_by_id()
    versions = [
        int(docs_by_id[doc_id].get("version", 1))
        for doc_id in docs_by_name.get(display_name, [])
        if doc_id in docs_by_id
    ]
    return (max(versions) + 1) if versions else 1


def _register_loaded_document(
    *,
    display_name: str,
    path: str,
    text: str,
    source_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    docs_by_id = _get_documents_by_id()
    docs_by_name = _get_documents_by_name()
    doc_id = str(uuid.uuid4())
    version = _next_doc_version(display_name)
    record = {
        "document_id": doc_id,
        "display_name": display_name,
        "version": version,
        "path": path,
        "text": text,
        "uploaded_at": time.time(),
        "source_message_id": source_message_id,
    }
    docs_by_id[doc_id] = record
    docs_by_name.setdefault(display_name, []).append(doc_id)
    cl.user_session.set("documents_by_id", docs_by_id)
    cl.user_session.set("documents_by_name", docs_by_name)
    _sync_legacy_documents_cache()
    return record


def _get_session_history() -> List[Dict[str, str]]:
    _ensure_session_state()
    history = cl.user_session.get("history")
    if history is None:
        history = []
        cl.user_session.set("history", history)
    return history


def _get_pending_route_choice() -> Optional[Dict[str, Any]]:
    _ensure_session_state()
    return cl.user_session.get("pending_action") or cl.user_session.get("pending_route_choice")


def _set_pending_route_choice(data: Optional[Dict[str, Any]]) -> None:
    cl.user_session.set("pending_action", data)
    cl.user_session.set("pending_route_choice", data)


def _apply_session_state_patch(patch: Optional[Dict[str, Any]]) -> None:
    if not patch:
        return
    if "pending_action" in patch:
        _set_pending_route_choice(patch.get("pending_action"))
    if patch.get("preserve_active_mode"):
        pass  # intentionally empty — handled by backend orchestration decision
    elif "active_mode" in patch:
        cl.user_session.set("active_mode", patch.get("active_mode"))
    for key in ("last_route", "last_executor", "last_trace_id", "runtime_mode"):
        if key in patch:
            cl.user_session.set(key, patch.get(key))
    if "runtime_mode" in patch:
        merged_state = merge_control_plane_state(
            cl.user_session.get("control_plane_state") or {},
            {"runtime_mode": patch.get("runtime_mode")},
        )
        cl.user_session.set("control_plane_state", merged_state)
        cl.user_session.set("effective_settings", resolve_effective_settings(merged_state))


def _sync_run_metadata_from_response(response: Optional[Dict[str, Any]]) -> None:
    if not response:
        return
    if "run_id" in response:
        cl.user_session.set("run_id", response.get("run_id"))
    if "state_ref" in response:
        cl.user_session.set("state_ref", response.get("state_ref"))
    if "state_version" in response:
        cl.user_session.set("state_version", response.get("state_version"))


def _build_backend_resume_snapshot() -> Dict[str, Any]:
    _ensure_session_state()
    return {
        "history": copy.deepcopy(_get_session_history()),
        "document_refs": [
            {
                "document_id": str(doc.get("document_id")),
                "display_name": str(doc.get("display_name")),
                "version": int(doc.get("version", 1)),
                "path": doc.get("path"),
                "uploaded_at": doc.get("uploaded_at"),
                "source_message_id": doc.get("source_message_id"),
                "source_origin": doc.get("source_origin"),
                "collection_id": doc.get("collection_id"),
            }
            for doc in _get_all_docs()
            if doc.get("document_id") and doc.get("display_name")
        ],
        "active_doc_ids": list(_get_active_doc_ids()),
        "pending_action": copy.deepcopy(_get_pending_route_choice()),
        "runtime_mode": _get_runtime_mode(),
        "control_plane_state": copy.deepcopy(_get_control_plane_state()),
        "effective_settings": copy.deepcopy(_get_effective_settings()),
        "last_route": cl.user_session.get("last_route"),
        "last_executor": cl.user_session.get("last_executor"),
        "last_trace_id": cl.user_session.get("last_trace_id"),
        "active_mode": cl.user_session.get("active_mode"),
        "runtime_budget_metadata": copy.deepcopy(cl.user_session.get("runtime_budget_metadata") or {}),
        "thread_name": cl.user_session.get("thread_name"),
        "thread_name_locked": bool(cl.user_session.get("thread_name_locked")),
    }


def _restore_backend_resume_snapshot(snapshot: Dict[str, Any]) -> None:
    _ensure_session_state()
    docs_by_id = copy.deepcopy(snapshot.get("documents_by_id") or {})
    if not docs_by_id:
        for ref in snapshot.get("document_refs") or []:
            document_id = str(ref.get("document_id") or "").strip()
            display_name = str(ref.get("display_name") or "").strip()
            if not document_id or not display_name:
                continue
            docs_by_id[document_id] = {
                "document_id": document_id,
                "display_name": display_name,
                "version": int(ref.get("version", 1)),
                "path": ref.get("path", ""),
                "text": "",
                "uploaded_at": float(ref.get("uploaded_at", 0.0) or 0.0),
                "source_message_id": ref.get("source_message_id"),
                "source_origin": ref.get("source_origin"),
                "collection_id": ref.get("collection_id"),
            }
    cl.user_session.set("history", copy.deepcopy(snapshot.get("history") or []))
    cl.user_session.set("documents_by_id", docs_by_id)
    cl.user_session.set("documents_by_name", {})
    _get_documents_by_name()
    cl.user_session.set("active_doc_ids", list(snapshot.get("active_doc_ids") or []))
    _set_pending_route_choice(copy.deepcopy(snapshot.get("pending_action")))

    runtime_mode = _backend_normalize_runtime_mode(snapshot.get("runtime_mode"))
    control_plane_state = copy.deepcopy(snapshot.get("control_plane_state") or build_initial_control_plane_state(runtime_mode))
    effective_settings = copy.deepcopy(snapshot.get("effective_settings") or resolve_effective_settings(control_plane_state))
    cl.user_session.set("runtime_mode", _backend_normalize_runtime_mode(effective_settings.get("runtime_mode")))
    cl.user_session.set("control_plane_state", control_plane_state)
    cl.user_session.set("effective_settings", effective_settings)

    for key in ("last_route", "last_executor", "last_trace_id", "active_mode"):
        cl.user_session.set(key, snapshot.get(key))
    cl.user_session.set(
        "runtime_budget_metadata",
        copy.deepcopy(snapshot.get("runtime_budget_metadata") or _default_runtime_budget_metadata()),
    )
    cl.user_session.set("thread_name", snapshot.get("thread_name"))
    cl.user_session.set("thread_name_locked", bool(snapshot.get("thread_name_locked")))

    _sync_legacy_documents_cache()


def _build_thread_metadata() -> Dict[str, Any]:
    effective = _get_effective_settings()
    active_doc_ids = _get_active_doc_ids()
    active_doc_labels = _get_active_doc_labels()
    pending_action = _get_pending_route_choice()
    return {
        "assistant_mode": effective.get("assistant_mode"),
        "runtime_mode": effective.get("runtime_mode"),
        "rag_scope": effective.get("rag_scope"),
        "model_profile": effective.get("model_profile"),
        "resolved_model_id": effective.get("resolved_model_id"),
        "device_mode": effective.get("device_mode"),
        "context_budget_profile": effective.get("context_budget_profile"),
        "knowledge_collection_id": effective.get("knowledge_collection_id"),
        "active_doc_ids": active_doc_ids,
        "active_doc_labels": active_doc_labels,
        "active_doc_count": len(active_doc_labels),
        "has_pending_action": bool(pending_action),
        "last_route": cl.user_session.get("last_route"),
        "last_executor": cl.user_session.get("last_executor"),
    }


def _derive_thread_name_from_message(user_message: Optional[str]) -> Optional[str]:
    text = " ".join((user_message or "").split())
    if not text:
        return None
    if len(text) <= 80:
        return text
    return text[:77].rsplit(" ", 1)[0] + "..."


def _derive_default_thread_name() -> str:
    effective = _get_effective_settings()
    active_labels = _get_active_doc_labels()
    assistant_mode = effective.get("assistant_mode") or "general_chat"
    mode_titles = {
        "general_chat": "General Chat",
        "coding": "Coding Assistant",
        "agentic": "Agentic (iterative)",
        "specific_tasks": "Specific Tasks",
        "rag_qa": "RAG Q&A",
    }
    base = mode_titles.get(str(assistant_mode), "Agent Navigator")
    if active_labels:
        return f"{base} — {active_labels[0]}"
    return base


def _build_context_status_markdown(title: str = "Текущий контекст") -> str:
    effective = _get_effective_settings()
    runtime_budget = cl.user_session.get("runtime_budget_metadata") or {}
    active_labels = _get_active_doc_labels()
    pending_action = _get_pending_route_choice()
    lines = [
        f"### {title}",
        f"- assistant_mode: `{effective.get('assistant_mode')}`",
        f"- runtime_mode: `{effective.get('runtime_mode')}`",
        f"- rag_scope: `{effective.get('rag_scope')}`",
        f"- model_profile: `{effective.get('model_profile')}`",
        f"- resolved_model_id: `{effective.get('resolved_model_id')}`",
        f"- intent_embedder: `{effective.get('resolved_intent_embedder_model_id')}`",
        f"- retrieval_embedder: `{effective.get('resolved_retrieval_embedder_model_id')}`",
        f"- device_mode: `{effective.get('device_mode')}`",
        f"- context_budget_profile: `{effective.get('context_budget_profile')}`",
        f"- Active docs: `{len(active_labels)}`",
        f"- pending_action: `{'yes' if pending_action else 'no'}`",
    ]
    if active_labels:
        lines.append(f"- active_set: {', '.join(active_labels)}")
    else:
        lines.append("- active_set: пусто")
    if runtime_budget:
        lines.append(f"- runtime_profile: `{runtime_budget.get('runtime_profile')}`")
        lines.append(f"- rag_mode: `{runtime_budget.get('rag_mode')}` ({runtime_budget.get('rag_mode_label')})")
    if effective.get("knowledge_collection_id"):
        lines.append(f"- knowledge_collection_id: `{effective.get('knowledge_collection_id')}`")
    return "\n".join(lines)


def _build_welcome_markdown() -> str:
    return (
        "## Добро пожаловать в Agent Navigator\n\n"
        "Используйте starter cards или настройки справа, чтобы быстро переключить сценарий.\n\n"
        f"{_build_context_status_markdown(title='Стартовый контекст')}"
    )


def _build_resume_markdown(*, restored_via: str) -> str:
    subtitles = {
        "backend_snapshot": "Контекст восстановлен из backend snapshot.",
        "legacy_history": "Контекст восстановлен из сохранённой истории Chainlit.",
        "empty_thread": "Тред найден, но активный контекст отсутствовал.",
    }
    subtitle = subtitles.get(restored_via, "Контекст чата восстановлен.")
    return f"## Контекст восстановлен\n\n{subtitle}\n\n{_build_context_status_markdown()}"


async def _sync_thread_presentation(user_message: Optional[str] = None) -> None:
    ids = _get_current_chainlit_session_ids()
    thread_id = ids.get("thread_id")
    if not thread_id:
        return

    current_name = cl.user_session.get("thread_name")
    locked = bool(cl.user_session.get("thread_name_locked"))
    proposed_name = None
    next_locked = locked
    if user_message and not locked:
        proposed_name = _derive_thread_name_from_message(user_message)
        next_locked = bool(proposed_name)
    elif not current_name:
        proposed_name = _derive_default_thread_name()
        next_locked = False

    if proposed_name:
        cl.user_session.set("thread_name", proposed_name)
        cl.user_session.set("thread_name_locked", next_locked)
    metadata = _build_thread_metadata()

    try:
        from chainlit.data import get_data_layer

        data_layer = get_data_layer()
        if data_layer is not None:
            await data_layer.update_thread(
                thread_id=thread_id,
                name=proposed_name,
                metadata=metadata,
            )
    except Exception:
        logger.debug("Thread presentation sync skipped", exc_info=True)


async def _persist_current_backend_state(*, status: str = "active", last_error: Optional[str] = None) -> None:
    _ensure_session_state()
    ids = _get_current_chainlit_session_ids()
    store = get_orchestration_state_store()
    run_record = await store.get_or_create_run(
        thread_id=ids.get("thread_id"),
        session_id=ids.get("session_id"),
        workflow_type="chainlit",
    )
    record = await store.save_run(
        run_id=run_record.run_id,
        status=status,
        pending_action_id=(_get_pending_route_choice() or {}).get("pending_action_id")
        or (_get_pending_route_choice() or {}).get("route_choice_id"),
        resume_state_blob=_build_backend_resume_snapshot(),
        checkpoint_blob={
            "route": cl.user_session.get("last_route"),
            "executor": cl.user_session.get("last_executor"),
            "trace_id": cl.user_session.get("last_trace_id"),
            "runtime_mode": _get_runtime_mode(),
            "assistant_mode": _get_effective_settings().get("assistant_mode"),
            "rag_scope": _get_effective_settings().get("rag_scope"),
            "knowledge_collection_id": _get_effective_settings().get("knowledge_collection_id"),
        },
        last_error=last_error,
    )
    cl.user_session.set("run_id", record.run_id)
    cl.user_session.set("state_ref", record.state_ref)
    cl.user_session.set("state_version", record.version)


def _build_execution_dependencies() -> ExecutionDependencies:
    def _get_retrieval_embed_fn():
        effective = _get_effective_settings()
        retrieval_embedder_model_id = str(
            effective.get("resolved_retrieval_embedder_model_id") or LEGAL_EMBEDDER_MODEL
        )
        rag_pipeline = cl.user_session.get("rag_pipeline")
        retriever = getattr(rag_pipeline, "retriever", None)
        embed_fn = getattr(retriever, "embed_fn", None)
        if embed_fn is not None:
            return embed_fn

        cache_entry = cl.user_session.get("retrieval_embed_fn")
        if isinstance(cache_entry, dict) and cache_entry.get("model_id") == retrieval_embedder_model_id:
            cached = cache_entry.get("embed_fn")
            if cached is not None:
                return cached
        elif cache_entry is not None and retrieval_embedder_model_id == LEGAL_EMBEDDER_MODEL:
            return cache_entry

        try:
            from services.model_manager.ums_client import create_ums_embed_fn

            embed_fn = create_ums_embed_fn(model_id=retrieval_embedder_model_id)
        except Exception:
            embed_fn = None
        cl.user_session.set(
            "retrieval_embed_fn",
            {"model_id": retrieval_embedder_model_id, "embed_fn": embed_fn},
        )
        return embed_fn

    return ExecutionDependencies(
        infer_assistant_text=_infer_assistant_text,
        build_prompt=_build_prompt,
        get_profile_system_prompt=_get_profile_system_prompt,
        has_retrieval_adapter=lambda: (_get_retrieval_embed_fn() is not None) or bool(
            getattr(cl.user_session.get("rag_pipeline"), "_indexed", False)
        ),
        get_retrieval_embed_fn=_get_retrieval_embed_fn,
        get_knowledge_base_store=get_knowledge_base_store,
        get_active_doc_ids=_get_active_doc_ids,
        get_all_docs=_get_all_docs,
        get_active_docs=_get_active_docs,
        get_report_docs=_get_report_docs,
        resolve_target_doc_name=_resolve_target_doc_name,
        is_report_query=_is_report_query,
        ensure_rag_index_for_doc_ids=_ensure_rag_index_for_doc_ids,
        get_rag_pipeline=lambda: cl.user_session.get("rag_pipeline"),
        build_sources_from_rag_result=_build_sources_from_rag_result,
        reindex_sources=_reindex_sources,
        build_doc_question_deterministic_fallback=_build_doc_question_deterministic_fallback,
        render_doc_question_markdown=_render_doc_question_markdown,
        build_doc_question_prompt_with_sources=_build_doc_question_prompt_with_sources,
        citations_are_valid=_citations_are_valid,
        needs_doc_question_regen=_needs_doc_question_regen,
        extract_citation_ids=_extract_citation_ids,
        has_sufficient_evidence=_has_sufficient_evidence,
        compute_confidence_v1=_compute_confidence_v1,
        strip_model_source_sections=_strip_model_source_sections,
        to_host_path=_to_host_path,
        active_set_status_line=_active_set_status_line,
        attach_and_register_report=_attach_and_register_report,
    )


async def _render_execution_response(response: Dict[str, Any], history: List[Dict[str, str]]) -> None:
    _sync_run_metadata_from_response(response)
    _apply_session_state_patch(response.get("session_state_patch"))
    assistant_message = response.get("assistant_message")
    if assistant_message:
        await cl.Message(content=assistant_message).send()
        history.append({"role": "assistant", "content": assistant_message})
    await _persist_current_backend_state(
        status="waiting_action" if response.get("action_required") else "completed",
        last_error=assistant_message if str(assistant_message or "").lower().startswith("ошибка") else None,
    )


def _get_current_chainlit_session_ids() -> Dict[str, Optional[str]]:
    def _normalize(value: Any) -> Optional[str]:
        return value if isinstance(value, str) and value.strip() else None

    thread_id = _normalize(cl.user_session.get("thread_id"))
    session_id = _normalize(cl.user_session.get("id"))
    try:
        session = getattr(cl, "context").session
        thread_id = thread_id or _normalize(getattr(session, "thread_id", None))
        session_id = session_id or _normalize(getattr(session, "id", None))
    except Exception:
        pass
    return {"thread_id": thread_id, "session_id": session_id}


def _build_execution_request(
    *,
    message: str,
    trace_id: str,
    new_files: List[Dict[str, Any]],
    session_docs: Dict[str, Any],
    classifier_result: Optional[Dict[str, Any]],
    forced_route: Optional[str] = None,
) -> Dict[str, Any]:
    effective = _get_effective_settings()
    ids = _get_current_chainlit_session_ids()
    return {
        "message": message,
        "run_id": cl.user_session.get("run_id"),
        "state_ref": cl.user_session.get("state_ref"),
        "state_version": cl.user_session.get("state_version"),
        "session_id": ids["session_id"],
        "thread_id": ids["thread_id"],
        "history": list(_get_session_history()),
        "pending_action": copy.deepcopy(_get_pending_route_choice()),
        "active_doc_ids": _get_active_doc_ids(),
        "attachments_meta": list(new_files),
        "runtime_mode": _get_runtime_mode(),
        "assistant_mode": effective.get("assistant_mode"),
        "rag_scope": effective.get("rag_scope"),
        "knowledge_collection_id": effective.get("knowledge_collection_id"),
        "model_profile": effective.get("model_profile"),
        "prompt_profile": effective.get("prompt_profile"),
        "generation_overrides": dict(effective.get("generation") or {}),
        "custom_system_prompt": effective.get("custom_system_prompt"),
        "tool_scope": effective.get("tool_scope"),
        "ui_state": _build_backend_resume_snapshot(),
        "control_plane_state": copy.deepcopy(_get_control_plane_state()),
        "file_count": len(new_files),
        "has_session_docs": bool(session_docs),
        "session_docs": session_docs,
        "classifier_result": classifier_result,
        "trace_id": trace_id,
        "forced_route": forced_route,
        "effective_settings": effective,
    }


def _get_runtime_mode() -> str:
    return _backend_normalize_runtime_mode(_get_effective_settings().get("runtime_mode"))


def _get_control_plane_state() -> Dict[str, Any]:
    _ensure_session_state()
    state = cl.user_session.get("control_plane_state")
    if not isinstance(state, dict):
        state = build_initial_control_plane_state(cl.user_session.get("runtime_mode"))
        cl.user_session.set("control_plane_state", state)
    return state


def _get_effective_settings() -> Dict[str, Any]:
    _ensure_session_state()
    effective = cl.user_session.get("effective_settings")
    if not isinstance(effective, dict):
        effective = resolve_effective_settings(_get_control_plane_state())
        cl.user_session.set("effective_settings", effective)
    return effective


def _store_control_plane_state(state: Dict[str, Any]) -> Dict[str, Any]:
    cl.user_session.set("control_plane_state", state)
    effective = resolve_effective_settings(state)
    cl.user_session.set("effective_settings", effective)
    cl.user_session.set("runtime_mode", _backend_normalize_runtime_mode(effective.get("runtime_mode")))
    return effective


def _extract_control_plane_state_from_settings(settings: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if isinstance(settings, dict):
        payload = dict(settings)
    else:
        getter = getattr(settings, "get", None)
        if callable(getter):
            for key in (
                "assistant_mode",
                "runtime_mode",
                "rag_scope",
                "knowledge_collection_id",
                "model_profile",
                "prompt_profile",
                "custom_system_prompt",
                "temperature",
                "top_p",
                "max_tokens",
            ):
                value = getter(key)
                if value is not None:
                    payload[key] = value
    return merge_control_plane_state(_get_control_plane_state(), payload)


def _apply_control_plane_preset(command: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(command, str) or not command.startswith("preset:"):
        return None
    assistant_mode = command.split(":", 1)[1].strip()
    if assistant_mode not in _ASSISTANT_MODE_ITEMS:
        return None
    return _store_control_plane_state(build_preset_state(assistant_mode))


def _format_effective_settings_summary(effective: Optional[Dict[str, Any]] = None) -> str:
    effective = effective or _get_effective_settings()
    runtime_budget = cl.user_session.get("runtime_budget_metadata") or {}
    generation = effective.get("generation") or {}
    lines = [
        "### Активная конфигурация",
        f"- assistant_mode: `{effective.get('assistant_mode')}`",
        f"- runtime_mode: `{effective.get('runtime_mode')}`",
        f"- rag_scope: `{effective.get('rag_scope')}`",
        f"- model_profile: `{effective.get('model_profile')}`",
        f"- resolved_model_id: `{effective.get('resolved_model_id')}`",
        f"- intent_embedder: `{effective.get('resolved_intent_embedder_model_id')}`",
        f"- retrieval_embedder: `{effective.get('resolved_retrieval_embedder_model_id')}`",
        f"- device_mode: `{effective.get('device_mode')}`",
        f"- context_budget_profile: `{effective.get('context_budget_profile')}`",
        f"- prompt_profile: `{effective.get('prompt_profile')}`",
        f"- temperature: `{generation.get('temperature')}`",
        f"- top_p: `{generation.get('top_p')}`",
        f"- max_tokens: `{generation.get('max_tokens')}`",
    ]
    if runtime_budget:
        lines.append(f"- runtime_profile: `{runtime_budget.get('runtime_profile')}`")
        lines.append(f"- rag_mode: `{runtime_budget.get('rag_mode')}` ({runtime_budget.get('rag_mode_label')})")
        lines.append(f"- effective_context_tokens: `{runtime_budget.get('effective_context_tokens')}`")
        lines.append(
            f"- retrieved_context_tokens_budget: `{runtime_budget.get('retrieved_context_tokens_budget')}`"
        )
    if effective.get("knowledge_collection_id"):
        lines.append(f"- knowledge_collection_id: `{effective.get('knowledge_collection_id')}`")
    if effective.get("custom_system_prompt"):
        lines.append("- custom_system_prompt: задан")
    return "\n".join(lines)


async def _send_control_plane_settings() -> None:
    effective = _get_effective_settings()
    settings = cl.ChatSettings(
        [
            cl.input_widget.Tab(
                id="use_case",
                label="Use Case",
                inputs=[
                    cl.input_widget.Select(
                        id="assistant_mode",
                        label="Сценарий",
                        initial_value=effective.get("assistant_mode"),
                        items=_ASSISTANT_MODE_ITEMS,
                        description="Крупный продуктовый сценарий без жёсткой смены chat profile.",
                    ),
                    cl.input_widget.Select(
                        id="runtime_mode",
                        label="Режим работы",
                        initial_value=effective.get("runtime_mode"),
                        items=_RUNTIME_MODE_ITEMS,
                        description="Policy роутинга backend для текущего чата.",
                    ),
                ],
            ),
            cl.input_widget.Tab(
                id="rag",
                label="RAG",
                inputs=[
                    cl.input_widget.Select(
                        id="rag_scope",
                        label="RAG scope",
                        initial_value=effective.get("rag_scope"),
                        items=_RAG_SCOPE_ITEMS,
                        description="Выбирает session RAG или knowledge-base контур.",
                    ),
                    cl.input_widget.TextInput(
                        id="knowledge_collection_id",
                        label="Knowledge Collection",
                        initial=effective.get("knowledge_collection_id") or "",
                        placeholder="Например: legal",
                        description="Идентификатор коллекции knowledge base для document Q&A.",
                    ),
                ],
            ),
            cl.input_widget.Tab(
                id="model",
                label="Model",
                inputs=[
                    cl.input_widget.Select(
                        id="model_profile",
                        label="Model Profile",
                        initial_value=effective.get("model_profile"),
                        items=_MODEL_PROFILE_ITEMS,
                        description="Логический профиль модели. Backend сам резолвит физическую модель.",
                    ),
                ],
            ),
            cl.input_widget.Tab(
                id="prompt",
                label="Prompt",
                inputs=[
                    cl.input_widget.Select(
                        id="prompt_profile",
                        label="Prompt Profile",
                        initial_value=effective.get("prompt_profile"),
                        items=_PROMPT_PROFILE_ITEMS,
                        description="Профиль системного промпта до применения пользовательского override.",
                    ),
                    cl.input_widget.TextInput(
                        id="custom_system_prompt",
                        label="Custom System Prompt",
                        initial=effective.get("custom_system_prompt") or "",
                        multiline=True,
                        placeholder="Опционально переопределяет системный промпт.",
                    ),
                ],
            ),
            cl.input_widget.Tab(
                id="generation",
                label="Generation",
                inputs=[
                    cl.input_widget.Slider(
                        id="temperature",
                        label="Temperature",
                        initial=float((effective.get("generation") or {}).get("temperature", 0.7)),
                        min=0.0,
                        max=2.0,
                        step=0.05,
                    ),
                    cl.input_widget.Slider(
                        id="top_p",
                        label="Top P",
                        initial=float((effective.get("generation") or {}).get("top_p", 0.9)),
                        min=0.0,
                        max=1.0,
                        step=0.05,
                    ),
                    cl.input_widget.NumberInput(
                        id="max_tokens",
                        label="Max Tokens",
                        initial=int((effective.get("generation") or {}).get("max_tokens", 2048)),
                    ),
                ],
            ),
        ]
    )
    await settings.send()

def _is_report_query(query: str) -> bool:
    q = (query or "").lower()
    return ("отчет" in q) or ("отчёт" in q) or ("report_" in q) or ("по отчету" in q) or ("по отчёту" in q)


def _get_classifier_result(query: str) -> Optional[Dict[str, Any]]:
    embedder_result = None
    rag = cl.user_session.get("rag_pipeline")
    if rag and rag._classifier_initialized:
        embedder_result = rag.classify_intent(query)
    elif cl.user_session.get("intent_classifier"):
        standalone = cl.user_session.get("intent_classifier")
        try:
            embedder_result = standalone.classify(query)
        except Exception as e:
            logger.warning(f"Standalone classifier error: {e}")
    llm_result = None
    if INTENT_CLASSIFIER_MODE in {"llm", "hybrid"}:
        llm_classifier = cl.user_session.get("intent_classifier_llm")
        if llm_classifier is None:
            llm_classifier = LLMIntentClassifier(infer_text_fn=_infer_intent_via_llm)
            cl.user_session.set("intent_classifier_llm", llm_classifier)
        try:
            llm_result = llm_classifier.classify(query)
        except Exception as e:
            logger.warning(f"LLM classifier error: {e}")

    return select_classifier_result(
        INTENT_CLASSIFIER_MODE,
        embedder_result=embedder_result,
        llm_result=llm_result,
        llm_confidence_threshold=INTENT_CLASSIFIER_LLM_CONFIDENCE_THRESHOLD,
        embedder_confidence_threshold=INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD,
        embedder_margin_threshold=INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD,
    )


def _infer_intent_via_llm(prompt: str) -> str:
    payload = {
        "prompt": prompt,
        "temperature": 0.0,
        "top_p": 0.1,
        "max_tokens": 96,
    }
    response = ums_client.infer(INTENT_CLASSIFIER_LLM_MODEL, payload)
    return response.get("choices", [{}])[0].get("text", str(response))


def _get_intent_decision(
    query: str,
    file_count: int = 0,
    has_session_docs: bool = False,
    session_docs: Optional[Dict[str, Any]] = None,
    classifier_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session_docs = session_docs if session_docs is not None else (_get_session_docs() if has_session_docs else {})
    classifier_result = classifier_result if classifier_result is not None else _get_classifier_result(query)
    response = _backend_decide_orchestration(
        query=query,
        trace_id=cl.user_session.get("request_trace_id"),
        runtime_mode=_get_runtime_mode(),
        rag_scope=str(_get_effective_settings().get("rag_scope") or "off"),
        knowledge_collection_id=_get_effective_settings().get("knowledge_collection_id"),
        file_count=file_count,
        has_session_docs=has_session_docs,
        session_docs=session_docs,
        classifier_result=classifier_result,
        active_doc_ids=_get_active_doc_ids(),
    )
    action_required = response.get("action_required")
    return {
        "intent": response.get("route"),
        "requires_choice": bool(action_required and action_required.get("type") == "choose_route"),
        "recommended_route": (action_required or {}).get("recommended_route"),
        "confidence": response.get("confidence", 0.0),
        "margin": response.get("margin", 0.0),
        "reason": response.get("reason"),
        "mode": (action_required or {}).get("mode"),
        "action_required": action_required,
        "executor": response.get("executor"),
        "session_state_patch": response.get("session_state_patch"),
    }


# === File Loading ===

# Пути для обмена файлами между Docker и хостом
# Внутри контейнера: /app/uploads → на хосте: backend/open_webui_uploads/
UPLOADS_DIR = os.getenv("UPLOADS_DIR", "/app/uploads")
# Хостовый путь, который doc-server на хосте может прочитать
HOST_UPLOADS_DIR = os.getenv("HOST_UPLOADS_DIR", "")


def _to_host_path(container_path: str) -> str:
    """Конвертирует контейнерный путь в хостовый для сервисов на хосте."""
    if HOST_UPLOADS_DIR and container_path.startswith(UPLOADS_DIR):
        return container_path.replace(UPLOADS_DIR, HOST_UPLOADS_DIR, 1)
    return container_path


def _save_to_uploads(src_path: str, filename: str) -> str:
    """Копирует файл в shared uploads директорию, возвращает путь внутри контейнера.

    Если одноимённый файл уже существует и не может быть перезаписан
    (например, остался root-owned после контейнера), сохраняет новую версию
    с суффиксом _vN.
    """
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    base_name, ext = os.path.splitext(filename)
    candidate = os.path.join(UPLOADS_DIR, filename)

    # Если путь совпадает, копировать не нужно.
    if os.path.abspath(src_path) == os.path.abspath(candidate):
        return candidate

    def _is_writable(path: str) -> bool:
        return (not os.path.exists(path)) or os.access(path, os.W_OK)

    dst_path = candidate
    if not _is_writable(dst_path):
        version = 2
        while True:
            alt_name = f"{base_name}_v{version}{ext}"
            alt_path = os.path.join(UPLOADS_DIR, alt_name)
            if _is_writable(alt_path):
                dst_path = alt_path
                break
            version += 1

    shutil.copy2(src_path, dst_path)
    return dst_path


async def _load_files(files: List[Dict]) -> List[Dict]:
    """Копирует файлы в shared uploads, загружает через Document Server."""
    doc_server = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
    loaded = []
    client = await get_shared_client()

    for f in files:
        try:
            # Копируем файл в shared директорию
            container_path = _save_to_uploads(f["path"], f["name"])
            host_path = _to_host_path(container_path)

            resp = await client.post(f"{doc_server}/load_document", json={"path": host_path}, timeout=60.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "error":
                    loaded.append({**f, "text": "", "path": container_path, "error": data.get("error")})
                else:
                    text = data.get("text", "")
                    loaded.append({**f, "text": text, "path": container_path})
            else:
                loaded.append({**f, "text": "", "path": container_path, "error": f"HTTP {resp.status_code}"})
        except Exception as e:
            loaded.append({**f, "text": "", "error": str(e)})

    return loaded


# === Prompt Builder ===

MAX_HISTORY_MESSAGES = 10


def _build_prompt(query: str, history: List, system_msg: str = "") -> str:
    if not system_msg:
        system_msg = "Ты помощник Agent Navigator. Помогай пользователю."
    prompt = f"<|im_start|>system\n{system_msg}<|im_end|>\n"
    for msg in history[-MAX_HISTORY_MESSAGES:]:
        prompt += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    prompt += f"<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n"
    return prompt


def _get_profile_system_prompt() -> str:
    effective = _get_effective_settings()
    custom_system_prompt = (effective.get("custom_system_prompt") or "").strip()
    if custom_system_prompt:
        return custom_system_prompt
    return get_prompt_profile_system_message(effective.get("prompt_profile"))


def _compose_doc_question_system_prompt(catalog: str) -> str:
    effective = _get_effective_settings()
    system_parts = [
        "Ты помощник Agent Navigator.\n"
        "Отвечай только на основе CATALOG OF SOURCES.\n"
        "Каждое фактическое утверждение помечай ссылками [n] из каталога.\n"
        "Запрещено использовать ссылки вне диапазона каталога.\n"
        "Если данных недостаточно, прямо скажи это и укажи ограничения, не выдумывай.\n"
        "Не проси повторно загрузить документы/тексты.\n\n"
        "Не добавляй отдельные разделы 'Источники' и 'Надёжность' — "
        "система добавляет их автоматически.\n\n"
        f"CATALOG OF SOURCES:\n{catalog}"
    ]

    prompt_profile = effective.get("prompt_profile")
    if prompt_profile and prompt_profile != "default-assistant":
        system_parts.append(f"PROFILE INSTRUCTIONS:\n{get_prompt_profile_system_message(prompt_profile)}")

    custom_system_prompt = (effective.get("custom_system_prompt") or "").strip()
    if custom_system_prompt:
        system_parts.append(f"CUSTOM SYSTEM OVERRIDE:\n{custom_system_prompt}")

    return "\n\n".join(system_parts)


def _build_inference_request(
    *,
    default_temperature: float = 0.7,
    default_top_p: float = 0.9,
    default_max_tokens: int = 2048,
    enforced_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective = _get_effective_settings()
    generation = clamp_generation_overrides(
        (effective.get("generation") or {}),
        base={
            "temperature": default_temperature,
            "top_p": default_top_p,
            "max_tokens": default_max_tokens,
        },
    )
    if enforced_overrides:
        generation = clamp_generation_overrides(enforced_overrides, base=generation)
    return {
        "model_id": effective.get("resolved_model_id") or resolve_model_id(effective.get("model_profile")),
        "payload": {
            "prompt": None,
            "temperature": generation["temperature"],
            "top_p": generation["top_p"],
            "max_tokens": generation["max_tokens"],
        },
    }


# === Stream Response ===

async def _stream_response(prompt: str, msg: cl.Message, history: List):
    """Генерация ответа для direct chat без SSE streaming.

    Временный production workaround: direct-chat streaming через
    Chainlit -> UMS -> llama-server даёт GeneratorExit/cancel-scope
    ошибки в httpcore/anyio. Для UI-чата используем обычный infer.
    """
    msg.content = await _infer_assistant_text(prompt)

    await msg.send()
    history.append({"role": "assistant", "content": msg.content})


async def _infer_assistant_text(
    prompt: str,
    temperature: float = 0.7,
    *,
    default_top_p: float = 0.9,
    default_max_tokens: int = 2048,
    enforced_overrides: Optional[Dict[str, Any]] = None,
) -> str:
    request = _build_inference_request(
        default_temperature=temperature,
        default_top_p=default_top_p,
        default_max_tokens=default_max_tokens,
        enforced_overrides=enforced_overrides,
    )
    payload = dict(request["payload"])
    payload["prompt"] = prompt
    model_id = request["model_id"]
    try:
        response = await asyncio.to_thread(
            ums_client.infer,
            model_id,
            payload,
        )
        return response.get("choices", [{}])[0].get("text", str(response))
    except Exception:
        try:
            logger.warning("Direct chat infer failed, retrying sync inference", exc_info=True)
            response = ums_client.infer(model_id, payload)
            return response.get("choices", [{}])[0].get("text", str(response))
        except Exception as e:
            return f"Ошибка генерации: {e}"


def _needs_doc_question_regen(answer_text: str, has_session_docs: bool) -> bool:
    if not has_session_docs:
        return False
    text = (answer_text or "").lower()
    if any(phrase in text for phrase in _DOC_QUESTION_UPLOAD_REQUEST_PHRASES):
        return True
    # Более общий guard: модель уходит в "уточните/пришлите содержание", хотя контекст уже передан.
    if "уточните" in text and ("содержан" in text or "текст" in text or "требован" in text):
        return True
    if "не могу предоставить точный ответ" in text and ("уточните" in text or "предостав" in text):
        return True
    return False


def _normalize_doc_match_key(text: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", (text or "").lower())


def _resolve_target_doc_name(query: str, active_docs: List[Dict[str, Any]]) -> Optional[str]:
    query_norm = _normalize_doc_match_key(query)
    if not query_norm:
        return None

    best: Optional[tuple[int, str]] = None
    for doc in active_docs:
        display_name = str(doc.get("display_name", "")).strip()
        if not display_name:
            continue
        stem = os.path.splitext(display_name)[0]
        variants = {_normalize_doc_match_key(display_name), _normalize_doc_match_key(stem)}
        for variant in variants:
            if not variant:
                continue
            if variant in query_norm:
                score = len(variant)
                if best is None or score > best[0]:
                    best = (score, display_name)
    return best[1] if best else None


def _reindex_sources(sources: List[SourceRef]) -> List[SourceRef]:
    reindexed: List[SourceRef] = []
    for idx, src in enumerate(sources, start=1):
        copied = dict(src)
        copied["source_id"] = idx
        reindexed.append(copied)
    return reindexed


def _normalize_quote(text: str, max_len: int = 320) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= max_len:
        return flat
    return flat[:max_len].rsplit(" ", 1)[0] + "..."


def _normalize_score_minmax(raw: float, min_score: float, max_score: float) -> float:
    spread = max_score - min_score
    if spread <= 1e-9:
        return 0.5
    normalized = max(0.0, min(1.0, (raw - min_score) / spread))
    if spread < 0.05:
        # На малой выборке score часто очень близки; сжимаем шкалу для честного UI.
        return 0.35 + 0.30 * normalized
    return normalized


def _build_sources_from_rag_result(rag_result: Any, rag_pipeline: Any, max_sources: int = 5) -> List[SourceRef]:
    chunks = list(getattr(rag_result, "chunks", []) or [])[:max_sources]
    if not chunks:
        return []

    all_raw_scores = [float(c.score) for c in chunks]
    min_score = min(all_raw_scores)
    max_score = max(all_raw_scores)
    all_chunk_meta = list(getattr(rag_pipeline, "_chunks", []) or [])

    sources: List[SourceRef] = []
    for idx, c in enumerate(chunks, 1):
        chunk_id = int(getattr(c, "index", -1))
        chunk_obj = all_chunk_meta[chunk_id] if 0 <= chunk_id < len(all_chunk_meta) else None
        meta = getattr(chunk_obj, "metadata", {}) if chunk_obj is not None else {}
        start_char = getattr(chunk_obj, "start_char", None) if chunk_obj is not None else None
        end_char = getattr(chunk_obj, "end_char", None) if chunk_obj is not None else None
        sources.append(
            {
                "source_id": idx,
                "document_id": str(meta.get("doc_name", f"doc_{chunk_id}" if chunk_id >= 0 else "unknown")),
                "display_name": str(meta.get("doc_name", f"doc_{chunk_id}" if chunk_id >= 0 else "unknown")),
                "chunk_id": chunk_id,
                "collection_id": None,
                "source_origin": "session",
                "section": meta.get("section"),
                "char_span": {"start_char": start_char, "end_char": end_char},
                "page": None,
                "quote": _normalize_quote(getattr(c, "text", "")),
                "raw_score": float(getattr(c, "score", 0.0)),
                "normalized_score": _normalize_score_minmax(float(getattr(c, "score", 0.0)), min_score, max_score),
                "grade": getattr(c, "metadata", {}).get("grade"),
                "z_score": getattr(c, "metadata", {}).get("z_score"),
            }
        )
    return sources


def _build_doc_question_prompt_with_sources(query: str, history: List, sources: List[SourceRef]) -> str:
    lines = []
    for s in sources:
        span = s["char_span"]
        section = f" section={s.get('section')}" if s.get("section") else ""
        page = f" page={s.get('page')}" if s.get("page") is not None else ""
        origin = f" origin={s.get('source_origin')}" if s.get("source_origin") else ""
        lines.append(
            f"[{s['source_id']}] doc={s['document_id']} chunk={s['chunk_id']} "
            f"span=({span.get('start_char')},{span.get('end_char')}){section}{page}{origin} quote={s['quote']}"
        )
    catalog = "\n".join(lines)
    system_msg = _compose_doc_question_system_prompt(catalog)
    return _build_prompt(query, history, system_msg)


def _strip_model_source_sections(answer_text: str) -> str:
    text = (answer_text or "").strip()
    if not text:
        return text

    patterns = [
        r"(?im)^\s*#{0,3}\s*источники\s*$",
        r"(?im)^\s*источники\s*$",
        r"(?im)^\s*#{0,3}\s*надежность\s*$",
        r"(?im)^\s*#{0,3}\s*надёжность\s*$",
        r"(?im)^\s*надежность\s*$",
        r"(?im)^\s*надёжность\s*$",
    ]

    cut_pos = len(text)
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            cut_pos = min(cut_pos, match.start())

    cleaned = text[:cut_pos].rstrip()
    return cleaned if cleaned else text


def _render_doc_question_markdown(resp: DocQuestionResponse) -> str:
    lines = [resp["answer_text"].strip(), "", "### Источники"]
    lines.append(f"- retrieval_scope: `{resp.get('source_scope_summary', 'off')}`")
    if resp["sources"]:
        for s in resp["sources"]:
            score_pct = int(round(float(s["normalized_score"]) * 100))
            origin = str(s.get("source_origin") or "session")
            scope = f"{origin}"
            if s.get("collection_id"):
                scope += f":{s['collection_id']}"
            location_parts = []
            if s.get("section"):
                location_parts.append(f"section={s['section']}")
            if s.get("page") is not None:
                location_parts.append(f"page={s['page']}")
            location = f" {' '.join(location_parts)}" if location_parts else ""
            lines.append(
                f"- [{s['source_id']}] `{s.get('display_name') or s['document_id']}` "
                f"(origin={scope}) chunk={s['chunk_id']} "
                f"span=({s['char_span'].get('start_char')},{s['char_span'].get('end_char')}){location} "
                f"relevance={score_pct}% raw={s['raw_score']:.4f}"
            )
            lines.append(f"  Цитата: {s['quote']}")
    else:
        lines.append("- Источники не найдены.")
    lines.extend(
        [
            "",
            "### Надёжность",
            f"- confidence: {resp['confidence']:.2f} ({resp['confidence_label']})",
            f"- method: {resp['confidence_method']} v{resp['confidence_version']}",
            f"- mode: {resp['answer_mode']}",
            f"- fallback: {resp['fallback_type']}",
        ]
    )
    if resp.get("fallback_reason"):
        lines.append(f"- reason: {resp['fallback_reason']}")
    return "\n".join(lines)


def _extract_saved_report_filename(report_text: str) -> Optional[str]:
    match = re.search(r"\*\*Отчет (?:сохранен|уже сохранен):\*\*\s*`([^`]+)`", report_text or "")
    if not match:
        return None
    return match.group(1).strip()


async def _attach_and_register_report(report_text: str) -> None:
    filename = _extract_saved_report_filename(report_text)
    if not filename:
        return

    report_path = os.path.join(UPLOADS_DIR, filename)
    if not os.path.exists(report_path):
        return

    try:
        if filename.lower().endswith(".pdf"):
            pdf_display = (os.getenv("CHAINLIT_REPORT_PDF_DISPLAY", "inline") or "inline").strip().lower()
            if pdf_display not in {"inline", "side", "page"}:
                pdf_display = "inline"
            report_el = cl.Pdf(
                name=filename,
                path=report_path,
                display=pdf_display,  # inline | side | page
                page=1,
            )
        else:
            report_el = cl.File(name=filename, path=report_path, display="inline")
        await cl.Message(content=f"Файл отчёта: `{filename}`", elements=[report_el]).send()
    except Exception:
        logger.warning("Failed to attach report file element for %s", filename, exc_info=True)

    try:
        if filename.lower().endswith(".pdf"):
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(report_path)
                parts = [page.get_text("text") for page in doc]
                doc.close()
                report_text_content = "\n".join(parts)
            except Exception:
                logger.warning("Failed to extract text from PDF report %s", filename, exc_info=True)
                report_text_content = ""
        else:
            with open(report_path, "r", encoding="utf-8") as f:
                report_text_content = f.read()
        _register_loaded_document(
            display_name=filename,
            path=report_path,
            text=report_text_content,
            source_message_id=cl.user_session.get("request_trace_id"),
        )
    except Exception:
        logger.warning("Failed to register report in session docs: %s", filename, exc_info=True)


def _build_rag_index_key(doc_ids: List[str]) -> str:
    return "|".join(sorted(doc_ids))


async def _ensure_rag_index_for_active_docs(step_name: Optional[str] = None) -> bool:
    """Индексирует RAG только по active_docs. Возвращает True, если была переиндексация."""
    active_docs = _get_active_docs()
    if not active_docs:
        cl.user_session.set("rag_index_key", "")
        cl.user_session.set("rag_index_doc_ids", [])
        return False

    active_ids = [str(d["document_id"]) for d in active_docs]
    index_key = _build_rag_index_key(active_ids)
    now_ts = time.time()

    cache: Dict[str, Any] = cl.user_session.get("rag_pipeline_cache") or {}
    for key, entry in list(cache.items()):
        last_used = float(entry.get("last_used", entry.get("indexed_at", 0.0)))
        if RAG_INDEX_CACHE_TTL_S > 0 and (now_ts - last_used) > RAG_INDEX_CACHE_TTL_S:
            cache.pop(key, None)

    cached_entry = cache.get(index_key)
    if cached_entry and cached_entry.get("pipeline") is not None:
        cached_entry["last_used"] = now_ts
        cache[index_key] = cached_entry
        cl.user_session.set("rag_pipeline_cache", cache)
        cl.user_session.set("rag_pipeline", cached_entry["pipeline"])
        cl.user_session.set("rag_index_key", index_key)
        cl.user_session.set("rag_index_doc_ids", active_ids)
        return False

    rag = cl.user_session.get("rag_pipeline")
    cached_key = cl.user_session.get("rag_index_key") or ""
    if rag is not None and cached_key == index_key:
        cache[index_key] = {
            "pipeline": rag,
            "doc_ids": active_ids,
            "indexed_at": now_ts,
            "last_used": now_ts,
        }
        cl.user_session.set("rag_pipeline_cache", cache)
        return False

    from orchestrator.rag.pipeline import AdaptiveRAGPipeline
    from services.model_manager.ums_client import create_ums_embed_fn

    async def _reindex() -> tuple[AdaptiveRAGPipeline, int, Dict[str, Any]]:
        nonlocal rag
        embed_fn = create_ums_embed_fn()
        runtime_budget = await _get_runtime_budget_metadata()
        rag_mode = runtime_budget["rag_mode"]
        if rag is None:
            rag = AdaptiveRAGPipeline(
                embed_fn=embed_fn,
                rag_mode=rag_mode,
                effective_context_tokens=runtime_budget.get("effective_context_tokens"),
                retrieved_context_ratio=runtime_budget.get("context_budget_ratio", 0.60),
            )
        else:
            rag.rag_mode = rag_mode
            rag.configure_runtime_budget(
                effective_context_tokens=runtime_budget.get("effective_context_tokens"),
                retrieved_context_ratio=runtime_budget.get("context_budget_ratio", 0.60),
            )
        texts = [str(d.get("text", "")) for d in active_docs if d.get("text")]
        names = [str(d.get("display_name", "")) for d in active_docs if d.get("text")]
        await asyncio.to_thread(rag.index_documents, texts, doc_names=names)
        return rag, len(texts), runtime_budget

    if step_name:
        async with cl.Step(name=step_name, type="tool") as step:
            rag, indexed_count, runtime_budget = await _reindex()
            search_type = "BM25+Dense (hybrid)" if rag.embed_fn else "BM25-only"
            step.output = (
                f"RAG: mode={runtime_budget['rag_mode']} ({runtime_budget.get('rag_mode_label')}), search={search_type}, "
                f"profile={runtime_budget['runtime_profile']}, "
                f"budget={runtime_budget['retrieved_context_tokens_budget']} tokens, indexed {indexed_count} docs\n"
                f"{_active_set_status_line()}"
            )
    else:
        rag, _, runtime_budget = await _reindex()

    cache[index_key] = {
        "pipeline": rag,
        "doc_ids": active_ids,
        "indexed_at": now_ts,
        "last_used": now_ts,
        "runtime_budget": runtime_budget,
    }
    if RAG_INDEX_CACHE_MAX > 0 and len(cache) > RAG_INDEX_CACHE_MAX:
        sorted_keys = sorted(
            cache.keys(),
            key=lambda k: float((cache.get(k) or {}).get("last_used", 0.0)),
        )
        for key in sorted_keys[:-RAG_INDEX_CACHE_MAX]:
            cache.pop(key, None)

    cl.user_session.set("rag_pipeline", rag)
    cl.user_session.set("rag_pipeline_cache", cache)
    cl.user_session.set("rag_index_key", index_key)
    cl.user_session.set("rag_index_doc_ids", active_ids)
    return True


async def _ensure_rag_index_for_doc_ids(doc_ids: List[str], step_name: Optional[str] = None) -> bool:
    docs_by_id = _get_documents_by_id()
    scoped_ids = [doc_id for doc_id in doc_ids if doc_id in docs_by_id]
    if not scoped_ids:
        return False

    previous_active = _get_active_doc_ids()
    cl.user_session.set("active_doc_ids", scoped_ids)
    try:
        return await _ensure_rag_index_for_active_docs(step_name=step_name)
    finally:
        cl.user_session.set("active_doc_ids", previous_active)


# === Chainlit Handlers ===

async def _init_classifier():
    """Фоновая инициализация EmbeddingIntentClassifier до загрузки файлов."""
    try:
        if INTENT_CLASSIFIER_MODE == "llm":
            logger.info("Skipping embedder classifier pre-init: INTENT_CLASSIFIER_MODE=llm")
            return
        from orchestrator.rag.classifier import EmbeddingIntentClassifier
        from services.model_manager.ums_client import create_ums_embed_fn

        retries = int(os.getenv("CHAINLIT_CLASSIFIER_PREINIT_RETRIES", "6"))
        delay_s = float(os.getenv("CHAINLIT_CLASSIFIER_PREINIT_DELAY_S", "2.0"))
        embed_fn = None
        effective = _get_effective_settings()
        intent_embedder_model_id = str(
            effective.get("resolved_intent_embedder_model_id") or INTENT_CLASSIFIER_EMBEDDER_MODEL
        )

        for attempt in range(1, retries + 1):
            embed_fn = await asyncio.to_thread(
                create_ums_embed_fn,
                model_id=intent_embedder_model_id,
            )
            if embed_fn:
                break
            if attempt < retries:
                logger.info(
                    f"Classifier pre-init: UMS unavailable, retrying ({attempt}/{retries})"
                )
                await asyncio.sleep(delay_s)

        if not embed_fn:
            logger.info("Classifier pre-init skipped: UMS unavailable after retries")
            return
        classifier = EmbeddingIntentClassifier(embed_fn=embed_fn)
        await asyncio.to_thread(classifier.initialize)
        cl.user_session.set("intent_classifier", classifier)
        logger.info("Classifier pre-initialized successfully")
    except Exception as e:
        logger.warning(f"Classifier pre-init failed: {e}")


@cl.on_chat_start
async def on_chat_start():
    ids = _get_current_chainlit_session_ids()
    if ids["session_id"]:
        cl.user_session.set("id", ids["session_id"])
    if ids["thread_id"]:
        cl.user_session.set("thread_id", ids["thread_id"])
    cl.user_session.set("documents", {})
    cl.user_session.set("documents_by_id", {})
    cl.user_session.set("documents_by_name", {})
    cl.user_session.set("active_doc_ids", [])
    cl.user_session.set("history", [])
    cl.user_session.set("active_mode", None)
    cl.user_session.set("runtime_mode", _backend_normalize_runtime_mode(cl.user_session.get("runtime_mode")))
    cl.user_session.set(
        "control_plane_state",
        build_initial_control_plane_state(cl.user_session.get("runtime_mode")),
    )
    cl.user_session.set(
        "effective_settings",
        resolve_effective_settings(cl.user_session.get("control_plane_state") or {}),
    )
    cl.user_session.set("rag_index_key", "")
    cl.user_session.set("rag_index_doc_ids", [])
    cl.user_session.set("rag_pipeline_cache", {})

    # Фоновая инициализация classifier для раннего semantic routing
    asyncio.create_task(_init_classifier())
    await _persist_current_backend_state(status="initialized")
    await _send_control_plane_settings()
    await _sync_thread_presentation()
    await cl.Message(content=_build_welcome_markdown()).send()

def _default_starters() -> List[cl.Starter]:
    return [
        cl.Starter(
            label="General Chat",
            message="Объясни простыми словами, что такое облака на небе.",
            command="preset:general_chat",
        ),
        cl.Starter(
            label="Coding Assistant",
            message="Помоги спроектировать небольшой FastAPI endpoint с понятным контрактом.",
            command="preset:coding",
        ),
        cl.Starter(
            label="Agentic (iterative)",
            message="Разложи задачу на шаги и предложи план исполнения с проверками.",
            command="preset:agentic",
        ),
        cl.Starter(
            label="Specific Tasks",
            message="Сравни два загруженных документа и выдели ключевые различия.",
            command="preset:specific_tasks",
        ),
        cl.Starter(
            label="RAG Q&A",
            message="Ответь по базе знаний: найди, что сказано про штрафы и сроки уведомления.",
            command="preset:rag_qa",
        ),
    ]

@cl.set_starters
async def set_starters(user: Optional[cl.User] = None, language: Optional[str] = None):
    return _default_starters()


@cl.set_chat_profiles
async def set_chat_profiles(current_user: Optional[cl.User]):
    return [
        cl.ChatProfile(
            name="Agent Navigator",
            markdown_description=(
                "Основной Chainlit UI для Agent Navigator с use-case режимами: "
                "General Chat, Coding, Agentic, Specific Tasks и RAG Q&A."
            ),
            icon="/public/logo_dark.svg",
            starters=_default_starters(),
        )
    ]


@cl.on_settings_update
async def on_settings_update(settings: Dict[str, Any]):
    _ensure_session_state()
    effective = _store_control_plane_state(_extract_control_plane_state_from_settings(settings))
    cl.user_session.set("effective_settings_summary", _format_effective_settings_summary(effective))
    await _sync_thread_presentation()
    await _persist_current_backend_state()
    await _send_control_plane_settings()


@cl.on_chat_resume
async def on_chat_resume(thread):
    """
    Восстановление сессии из сохранённой истории (TD-4 Fix).
    Пытается восстановить список документов и RAG-индекс.
    """
    ids = _get_current_chainlit_session_ids()
    if ids["session_id"]:
        cl.user_session.set("id", ids["session_id"])
    if thread and thread.get("id"):
        cl.user_session.set("thread_id", str(thread.get("id")))
        cl.user_session.set("thread_name", thread.get("name"))
        cl.user_session.set("thread_name_locked", bool(thread.get("name")))
    elif ids["thread_id"]:
        cl.user_session.set("thread_id", ids["thread_id"])
    cl.user_session.set("documents", {})
    cl.user_session.set("documents_by_id", {})
    cl.user_session.set("documents_by_name", {})
    cl.user_session.set("active_doc_ids", [])
    cl.user_session.set("runtime_mode", _backend_normalize_runtime_mode(cl.user_session.get("runtime_mode")))
    cl.user_session.set(
        "control_plane_state",
        build_initial_control_plane_state(cl.user_session.get("runtime_mode")),
    )
    cl.user_session.set(
        "effective_settings",
        resolve_effective_settings(cl.user_session.get("control_plane_state") or {}),
    )
    cl.user_session.set("rag_index_key", "")
    cl.user_session.set("rag_index_doc_ids", [])
    cl.user_session.set("rag_pipeline_cache", {})
    store = get_orchestration_state_store()
    restored_run = await store.load_run(
        thread_id=cl.user_session.get("thread_id"),
        session_id=cl.user_session.get("id"),
    )
    if restored_run is not None:
        cl.user_session.set("run_id", restored_run.run_id)
        cl.user_session.set("state_ref", restored_run.state_ref)
        cl.user_session.set("state_version", restored_run.version)
        if restored_run.resume_state_blob:
            _restore_backend_resume_snapshot(restored_run.resume_state_blob)
            if _get_active_doc_ids():
                await _ensure_rag_index_for_active_docs(step_name="Восстановление индекса")
            await _send_control_plane_settings()
            await _sync_thread_presentation()
            await cl.Message(content=_build_resume_markdown(restored_via="backend_snapshot")).send()
            return
    history = []
    found_files = []

    if thread and thread.get("steps"):
        for step in thread["steps"]:
            # Восстанавливаем историю сообщений
            if step.get("type") == "user_message":
                history.append({"role": "user", "content": step.get("output", "")})
            elif step.get("type") == "assistant_message":
                history.append({"role": "assistant", "content": step.get("output", "")})
            
            # Ищем информацию о загруженных файлах в шагах
            if step.get("name") == "Загрузка документов" and step.get("output"):
                # Парсим строку "Загружено: file1.pdf, file2.docx"
                output = step.get("output", "")
                if "Загружено:" in output:
                    files_str = output.split("Загружено:")[1].strip()
                    fnames = []
                    for raw_name in files_str.split(","):
                        name = raw_name.strip()
                        if not name:
                            continue
                        name = re.sub(r"\s+\(v\d+\)$", "", name, flags=re.IGNORECASE)
                        if name:
                            fnames.append(name)
                    found_files.extend(fnames)

    cl.user_session.set("history", history)

    # Пытаемся восстановить документы и RAG только для восстановленного активного набора
    if found_files:
        unique_files = list(set(found_files))
        files_to_load = []

        for fname in unique_files:
            # Путь в контейнере
            c_path = os.path.join(UPLOADS_DIR, fname)
            if os.path.exists(c_path):
                files_to_load.append({"name": fname, "path": c_path})

        if files_to_load:
            async with cl.Step(name="Восстановление документов", type="tool") as step:
                loaded = await _load_files(files_to_load)
                names: List[str] = []
                restored_doc_ids: List[str] = []
                for f in loaded:
                    if f.get("text"):
                        rec = _register_loaded_document(
                            display_name=f["name"],
                            path=f["path"],
                            text=f["text"],
                            source_message_id=None,
                        )
                        restored_doc_ids.append(str(rec["document_id"]))
                        names.append(_doc_label(rec["display_name"], rec["version"]))
                if restored_doc_ids:
                    _set_active_doc_ids(restored_doc_ids)
                    await _ensure_rag_index_for_active_docs()
                    step.output = f"Восстановлено {len(names)} документов.\n{_active_set_status_line()}"
                else:
                    step.output = "Не удалось восстановить текст документов."

    await _send_control_plane_settings()
    await _sync_thread_presentation()
    await cl.Message(
        content=_build_resume_markdown(
            restored_via="legacy_history" if (history or found_files) else "empty_thread"
        )
    ).send()
    await _persist_current_backend_state()


@cl.on_message
async def on_message(message: cl.Message):
    _ensure_session_state()
    trace_id = str(uuid.uuid4())[:8]
    cl.user_session.set("request_trace_id", trace_id)

    preset_effective = _apply_control_plane_preset(getattr(message, "command", None))
    if preset_effective is not None:
        cl.user_session.set("effective_settings_summary", _format_effective_settings_summary(preset_effective))
        await _send_control_plane_settings()
        await _sync_thread_presentation()

    query = message.content
    history = _get_session_history()
    active_session_docs = _get_active_session_docs()
    normalized_query = (query or "").strip().lower()

    pending_choice = _get_pending_route_choice()
    if pending_choice:
        selected_route = _backend_resolve_pending_action_selection(query, pending_choice)
        if pending_choice.get("expires_at", 0) < time.time():
            _set_pending_route_choice(None)
        elif selected_route:
            _set_pending_route_choice(None)
            pending_doc_ids = pending_choice.get("active_doc_ids") or []
            if pending_doc_ids:
                _set_active_doc_ids(list(pending_doc_ids))
            active_session_docs = _get_active_session_docs()
            logger.info(
                "Route choice resolved trace=%s route_choice_id=%s selected=%s",
                trace_id,
                pending_choice.get("route_choice_id", "-"),
                selected_route,
            )
            if selected_route == "cancel":
                await cl.Message(content="Выбор отменён.").send()
            else:
                response = await _backend_execute_orchestration(
                    _build_execution_request(
                        message=pending_choice["query"],
                        trace_id=trace_id,
                        new_files=pending_choice.get("new_files", []),
                        session_docs=active_session_docs,
                        classifier_result=None,
                        forced_route=selected_route,
                    ),
                    deps=_build_execution_dependencies(),
                )
                await _render_execution_response(response, history)
            history.append({"role": "user", "content": query})
            return
        else:
            # Fail-closed: pending-choice одноразовый. Любой не-choice запрос его сбрасывает,
            # чтобы следующий вопрос не воспринимался как продолжение старого меню.
            logger.info(
                "Route choice dropped trace=%s route_choice_id=%s reason=non_choice_input",
                trace_id,
                pending_choice.get("route_choice_id", "-"),
            )
            _set_pending_route_choice(None)
    elif normalized_query in ("отмена", "cancel"):
        # Явная отмена без активного pending-choice не должна запускать роутинг.
        await cl.Message(content="Нет активного выбора для отмены.").send()
        history.append({"role": "user", "content": query})
        return

    # Обработка файлов
    new_files: List[Dict[str, Any]] = []
    file_inputs: List[Dict[str, str]] = []
    if message.elements:
        seen_inputs = set()
        for element in message.elements:
            if hasattr(element, 'path') and element.path:
                fname = element.name or os.path.basename(element.path)
                key = (fname, element.path)
                if key in seen_inputs:
                    continue
                seen_inputs.add(key)
                file_inputs.append({"name": fname, "path": element.path})

        if file_inputs:
            async with cl.Step(name="Загрузка документов", type="tool") as step:
                loaded = await _load_files(file_inputs)
                names: List[str] = []
                uploaded_doc_ids: List[str] = []
                for f in loaded:
                    if f.get("text"):
                        rec = _register_loaded_document(
                            display_name=f["name"],
                            path=f["path"],
                            text=f["text"],
                            source_message_id=trace_id,
                        )
                        uploaded_doc_ids.append(str(rec["document_id"]))
                        names.append(_doc_label(rec["display_name"], rec["version"]))
                        new_files.append(
                            {
                                "id": rec["document_id"],
                                "name": rec["display_name"],
                                "path": rec["path"],
                                "version": rec["version"],
                                "name_with_version": _doc_label(rec["display_name"], rec["version"]),
                            }
                        )
                    elif f.get("error"):
                        await cl.Message(content=f"Ошибка загрузки {f['name']}: {f['error']}").send()
                if uploaded_doc_ids:
                    _set_active_doc_ids(uploaded_doc_ids)
                    step.output = f"Загружено: {', '.join(names)}\n{_active_set_status_line()}"
                else:
                    step.output = "Нет новых файлов"

            if uploaded_doc_ids:
                step_name = "Инициализация RAG" if cl.user_session.get("rag_pipeline") is None else "Переиндексация"
                await _ensure_rag_index_for_active_docs(step_name=step_name)

    active_session_docs = _get_active_session_docs()
    classifier_result = _get_classifier_result(query)

    response = await _backend_execute_orchestration(
        _build_execution_request(
            message=query,
            trace_id=trace_id,
            new_files=new_files,
            session_docs=active_session_docs,
            classifier_result=classifier_result,
        ),
        deps=_build_execution_dependencies(),
    )
    logger.info(
        "Route decision trace=%s route=%s executor=%s action=%s reason=%s conf=%.2f margin=%.3f active_docs=%s",
        trace_id,
        response.get("route"),
        response.get("executor"),
        (response.get("action_required") or {}).get("type"),
        response.get("reason"),
        float(response.get("confidence", 0.0)),
        float(response.get("margin", 0.0)),
        len(_get_active_doc_ids()),
    )

    action_required = response.get("action_required")
    if action_required and action_required.get("type") == "choose_route":
        state = action_required
        prompt_text = action_required.get("title") or response.get("assistant_message") or "Выберите маршрут."
        logger.info(
            "Route choice required trace=%s route_choice_id=%s recommended=%s mode=%s",
            trace_id,
            state.get("route_choice_id"),
            state.get("recommended_route"),
            state.get("mode", "unknown"),
        )
        response = await cl.AskActionMessage(
            content=prompt_text,
            actions=[
                cl.Action(name="route_choice", payload={"route": option["route"]}, label=option["label"])
                for option in state.get("options", [])
            ],
            timeout=ROUTE_CHOICE_TIMEOUT_S,
            raise_on_timeout=False,
        ).send()
        if response and response.get("payload", {}).get("route"):
            chosen_route = response["payload"]["route"]
            _set_pending_route_choice(None)
            state_doc_ids = state.get("active_doc_ids") or []
            if state_doc_ids:
                _set_active_doc_ids(list(state_doc_ids))
            logger.info(
                "Route choice action trace=%s route_choice_id=%s selected=%s",
                trace_id,
                state.get("route_choice_id", "-"),
                chosen_route,
            )
            if chosen_route == "cancel":
                await cl.Message(content="Выбор отменён.").send()
            else:
                execution_response = await _backend_execute_orchestration(
                    _build_execution_request(
                        message=query,
                        trace_id=trace_id,
                        new_files=new_files,
                        session_docs=_get_active_session_docs(),
                        classifier_result=classifier_result,
                        forced_route=chosen_route,
                    ),
                    deps=_build_execution_dependencies(),
                )
                await _render_execution_response(execution_response, history)
            await _sync_thread_presentation(user_message=query)
        else:
            _set_pending_route_choice(state)
            await cl.Message(
                content=(
                    "Не дождался выбора. Ответьте сообщением: "
                    "1 — первый вариант, 2 — второй, 3 — третий, 4 — сводка, или 'отмена'.\n"
                    f"{_active_set_status_line()}"
                )
            ).send()
            await _sync_thread_presentation(user_message=query)
            history.append({"role": "user", "content": query})
            return
    elif action_required:
        _apply_session_state_patch(response.get("session_state_patch"))
        await cl.Message(content=response.get("assistant_message") or action_required.get("title") or "Нужно действие пользователя.").send()
        await _sync_thread_presentation(user_message=query)
        history.append({"role": "user", "content": query})
        return
    else:
        await _render_execution_response(response, history)

    await _sync_thread_presentation(user_message=query)

    history.append({"role": "user", "content": query})


def _detect_equipment_mode(file1_name: str, file2_name: str, query: str,
                           text_1: str = "", text_2: str = "") -> str:
    """Эвристика: tz_vs_smeta или smeta_vs_smeta."""
    from orchestrator.workflows.equipment import detect_equipment_mode
    return detect_equipment_mode(file1_name, file2_name, query, text_1, text_2)
