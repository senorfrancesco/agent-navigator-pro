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
import contextlib
import copy
import json
import logging
import mimetypes
import os
import re
import sqlite3
import sys
import time
import shutil
import httpx
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import quote
import numpy as np

# Добавляем пути (оставляем для обратной совместимости, но используем абсолютные)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chainlit as cl
except ImportError:
    raise ImportError("chainlit not installed. Run: pip install chainlit")
from fastapi import HTTPException
from fastapi.responses import FileResponse

from services.model_manager.model_selection import resolve_model_selection
from services.model_manager.model_selection import resolve_execution_plan
from services.model_manager.ums_client import UMSBusyError, ums_client
from services.hardware.tier_selector import describe_rag_mode
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
    normalize_inference_device_mode,
    resolve_effective_settings,
    resolve_model_id,
)

logger = logging.getLogger("chainlit_app")

CHAINLIT_UI_TEST_HOOKS = (
    "login-form",
    "login-submit",
    "main-chat-input",
    "upload-trigger",
    "assistant-message-container",
    "thread-list",
    "current-thread-marker",
    "report-download-link",
)
CHAINLIT_UI_TEST_HOOK_ASSETS = {
    "custom_js": "/public/test-hooks.js",
    "custom_css": "/public/test-hooks.css",
}

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
COMPARE_APPENDIX_STEP_MIN_LINES = int(os.getenv("CHAINLIT_COMPARE_APPENDIX_STEP_MIN_LINES", "5"))
EXECUTION_PROGRESS_POLL_S = float(os.getenv("CHAINLIT_EXECUTION_PROGRESS_POLL_S", "2.5"))


def get_chainlit_ui_test_hook_contract() -> Dict[str, Any]:
    return {
        "assets": dict(CHAINLIT_UI_TEST_HOOK_ASSETS),
        "hooks": list(CHAINLIT_UI_TEST_HOOKS),
    }


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
        "command" TEXT,
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
        "defaultOpen" INTEGER,
        "autoCollapse" INTEGER,
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
    required_columns = {
        "threads": {
            "id": 'TEXT PRIMARY KEY',
            "createdAt": "TEXT",
            "name": "TEXT",
            "userId": "TEXT",
            "userIdentifier": "TEXT",
            "tags": "TEXT",
            "metadata": "TEXT",
        },
        "steps": {
            "id": 'TEXT PRIMARY KEY',
            "name": "TEXT",
            "type": "TEXT",
            "command": "TEXT",
            "threadId": "TEXT",
            "parentId": "TEXT",
            "streaming": "INTEGER",
            "waitForAnswer": "INTEGER",
            "isError": "INTEGER",
            "metadata": "TEXT",
            "tags": "TEXT",
            "input": "TEXT",
            "output": "TEXT",
            "createdAt": "TEXT",
            "start": "TEXT",
            "end": "TEXT",
            "defaultOpen": "INTEGER",
            "autoCollapse": "INTEGER",
            "generation": "TEXT",
            "showInput": "TEXT",
            "language": "TEXT",
        },
        "elements": {
            "id": 'TEXT PRIMARY KEY',
            "threadId": "TEXT",
            "type": "TEXT",
            "chainlitKey": "TEXT",
            "url": "TEXT",
            "objectKey": "TEXT",
            "name": "TEXT",
            "display": "TEXT",
            "size": "TEXT",
            "language": "TEXT",
            "page": "INTEGER",
            "autoPlay": "INTEGER",
            "playerConfig": "TEXT",
            "forId": "TEXT",
            "mime": "TEXT",
            "props": "TEXT",
        },
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(schema_sql)
        for table_name, columns in required_columns.items():
            existing_columns = {
                row[1]
                for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            }
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue
                conn.execute(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'
                )
        conn.commit()

if _ENABLE_DATA_LAYER:
    try:
        from chainlit.config import config as chainlit_config
        from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
        from chainlit.data.storage_clients.base import BaseStorageClient
        import chainlit.server as chainlit_server
        from sqlalchemy import event as sqlalchemy_event

        def _uploads_root() -> str:
            return str(globals().get("UPLOADS_DIR") or os.getenv("UPLOADS_DIR", "/app/uploads"))

        def _chainlit_elements_root() -> str:
            return os.path.join(_uploads_root(), "chainlit-elements")

        def _resolve_chainlit_element_fs_path(object_key: str) -> Path:
            normalized_key = object_key.strip().lstrip("/")
            if not normalized_key:
                raise HTTPException(status_code=404, detail="Element not found")
            key_path = Path(normalized_key)
            if key_path.is_absolute() or ".." in key_path.parts:
                raise HTTPException(status_code=400, detail="Invalid element path")
            root_path = Path(_chainlit_elements_root()).resolve()
            resolved_path = (root_path / key_path).resolve()
            try:
                resolved_path.relative_to(root_path)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid element path") from exc
            return resolved_path

        class _LocalChainlitStorageProvider(BaseStorageClient):
            """Persist Chainlit elements in a local file tree under UPLOADS_DIR."""

            def __init__(self, base_dir: Optional[str] = None):
                self.base_dir = os.path.abspath(base_dir or _chainlit_elements_root())
                os.makedirs(self.base_dir, exist_ok=True)

            async def upload_file(
                self,
                object_key: str,
                data: bytes | str,
                mime: str = "application/octet-stream",
                overwrite: bool = True,
                content_disposition: str | None = None,
            ) -> Dict[str, Any]:
                file_path = _resolve_chainlit_element_fs_path(object_key)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                if file_path.exists() and not overwrite:
                    raise FileExistsError(f"Object already exists: {object_key}")
                payload = data.encode("utf-8") if isinstance(data, str) else data
                file_path.write_bytes(payload)
                return {
                    "object_key": object_key,
                    "url": await self.get_read_url(object_key),
                    "mime": mime,
                    "content_disposition": content_disposition,
                }

            async def delete_file(self, object_key: str) -> bool:
                file_path = _resolve_chainlit_element_fs_path(object_key)
                if not file_path.exists():
                    return False
                file_path.unlink()
                parent = file_path.parent
                root_path = Path(self.base_dir).resolve()
                while parent != root_path and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
                return True

            async def get_read_url(self, object_key: str) -> str:
                encoded_key = quote(object_key.strip().lstrip("/"), safe="/")
                return f"{chainlit_config.run.root_path}/project/file/{encoded_key}"

            async def close(self) -> None:
                return None

        async def _serve_local_chainlit_element_file(
            object_key: str,
            current_user: Any,
        ) -> FileResponse:
            if not current_user:
                raise HTTPException(status_code=401, detail="Unauthorized")
            normalized_key = object_key.strip().lstrip("/")
            if not normalized_key.startswith(f"{current_user.identifier}/"):
                raise HTTPException(status_code=403, detail="Forbidden")
            file_path = _resolve_chainlit_element_fs_path(normalized_key)
            if not file_path.exists():
                raise HTTPException(status_code=404, detail="Element not found")
            media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            return FileResponse(file_path, media_type=media_type, filename=file_path.name)

        if not getattr(chainlit_server.app.state, "agent_nav_element_file_route_registered", False):

            @chainlit_server.app.get(f"{chainlit_config.run.root_path}/project/file/{{object_key:path}}")
            async def get_local_chainlit_element_file(
                object_key: str,
                current_user: chainlit_server.UserParam,
            ):
                return await _serve_local_chainlit_element_file(
                    object_key=object_key,
                    current_user=current_user,
                )

            chainlit_server.app.state.agent_nav_element_file_route_registered = True

        class _CompatibleSQLAlchemyDataLayer(SQLAlchemyDataLayer):
            """Compatibility wrapper for local SQLite bootstrap schema and tag serialization."""

            def __init__(self, conninfo: str, *args, **kwargs):
                connect_args = dict(kwargs.pop("connect_args", {}) or {})
                sqlite_timeout_s = float(os.getenv("CHAINLIT_SQLITE_TIMEOUT_S", "30"))
                if conninfo.startswith("sqlite"):
                    connect_args.setdefault("timeout", sqlite_timeout_s)
                super().__init__(conninfo=conninfo, *args, connect_args=connect_args, **kwargs)
                if conninfo.startswith("sqlite"):
                    busy_timeout_ms = int(sqlite_timeout_s * 1000)

                    @sqlalchemy_event.listens_for(self.engine.sync_engine, "connect")
                    def _configure_sqlite_connection(dbapi_connection, connection_record):
                        cursor = dbapi_connection.cursor()
                        cursor.execute("PRAGMA journal_mode=WAL")
                        cursor.execute("PRAGMA synchronous=NORMAL")
                        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
                        cursor.close()

            async def update_thread(
                self,
                thread_id: str,
                name: Optional[str] = None,
                user_id: Optional[str] = None,
                metadata: Optional[Dict] = None,
                tags: Optional[List[str]] = None,
            ):
                serialized_tags = json.dumps(tags) if isinstance(tags, list) else tags
                await super().update_thread(
                    thread_id=thread_id,
                    name=name,
                    user_id=user_id,
                    metadata=metadata,
                    tags=serialized_tags,
                )

            async def get_all_user_threads(
                self, user_id: Optional[str] = None, thread_id: Optional[str] = None
            ) -> Optional[List[dict]]:
                threads = await super().get_all_user_threads(user_id=user_id, thread_id=thread_id)
                if not threads:
                    return threads
                for thread in threads:
                    raw_tags = thread.get("tags")
                    if isinstance(raw_tags, str):
                        try:
                            decoded = json.loads(raw_tags)
                        except json.JSONDecodeError:
                            decoded = raw_tags
                        if isinstance(decoded, list):
                            thread["tags"] = decoded
                return threads

        @cl.data_layer
        def get_data_layer():
            _bootstrap_chainlit_sqlite_schema(_DB_URL)
            return _CompatibleSQLAlchemyDataLayer(
                conninfo=_DB_URL,
                storage_provider=_LocalChainlitStorageProvider(),
            )

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
    if cl.user_session.get("active_execution_task") is None:
        cl.user_session.set("active_execution_task", None)
    if cl.user_session.get("cancel_requested") is None:
        cl.user_session.set("cancel_requested", False)

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
    document_word = "документ" if len(labels) == 1 else "документа" if len(labels) in {2, 3, 4} else "документов"
    return f"Активный набор: {', '.join(labels)} ({len(labels)} {document_word})"


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
    if "model_execution" in response:
        cl.user_session.set("last_model_execution", copy.deepcopy(response.get("model_execution")))


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
        "last_model_execution": cl.user_session.get("last_model_execution"),
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
        "general_chat": "Общий чат",
        "coding": "Помощник по коду",
        "agentic": "Агентный режим (iterative)",
        "specific_tasks": "Специализированные задачи",
        "rag_qa": "Вопросы по документам (RAG)",
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
        f"- Режим ассистента: `{effective.get('assistant_mode')}`",
        f"- Режим выполнения: `{effective.get('runtime_mode')}`",
        f"- RAG-область: `{effective.get('rag_scope')}`",
        f"- Профиль модели: `{effective.get('model_profile')}`",
        f"- Разрешённая модель: `{effective.get('resolved_model_id')}`",
        f"- Intent-эмбеддер: `{effective.get('resolved_intent_embedder_model_id')}`",
        f"- Retrieval-эмбеддер: `{effective.get('resolved_retrieval_embedder_model_id')}`",
        f"- Device mode: `{effective.get('device_mode')}`",
        f"- Профиль бюджетов: `{effective.get('context_budget_profile')}`",
        f"- Активные документы: `{len(active_labels)}`",
        f"- Ожидает действия: `{'да' if pending_action else 'нет'}`",
    ]
    if active_labels:
        lines.append(f"- Активный набор: {', '.join(active_labels)}")
    else:
        lines.append("- Активный набор: пусто")
    if runtime_budget:
        lines.append(f"- Runtime-профиль: `{runtime_budget.get('runtime_profile')}`")
        lines.append(f"- RAG-режим: `{runtime_budget.get('rag_mode')}` ({runtime_budget.get('rag_mode_label')})")
    if effective.get("knowledge_collection_id"):
        lines.append(f"- Коллекция знаний: `{effective.get('knowledge_collection_id')}`")
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
    model_execution_events = cl.user_session.get("model_execution_events")
    if not isinstance(model_execution_events, list):
        model_execution_events = []
        cl.user_session.set("model_execution_events", model_execution_events)

    def _get_retrieval_embed_fn():
        effective = _get_effective_settings()
        rag_pipeline = cl.user_session.get("rag_pipeline")
        retriever = getattr(rag_pipeline, "retriever", None)
        embed_fn = getattr(retriever, "embed_fn", None)
        if embed_fn is not None:
            return embed_fn

        cache_entry = cl.user_session.get("retrieval_embed_fn")
        resolved_retrieval_model_id = str(effective.get("resolved_retrieval_embedder_model_id") or "")
        if isinstance(cache_entry, dict) and cache_entry.get("model_id") == resolved_retrieval_model_id:
            cached = cache_entry.get("embed_fn")
            if cached is not None:
                return cached

        retrieval_resolution = effective.get("resolved_retrieval_embedder_resolution")
        if retrieval_resolution is None and resolved_retrieval_model_id:
            retrieval_resolution = resolve_execution_plan(requested_model_id=resolved_retrieval_model_id)
        if retrieval_resolution is None:
            retrieval_resolution = resolve_model_selection("legal.embedder")
        embed_fn = _create_failover_embed_fn(retrieval_resolution, record_model_execution=_record_model_execution)
        cl.user_session.set(
            "retrieval_embed_fn",
            {"model_id": getattr(retrieval_resolution, "resolved_model_id", None), "embed_fn": embed_fn},
        )
        return embed_fn

    def _record_model_execution(event: Dict[str, Any]) -> None:
        if isinstance(event, dict):
            model_execution_events.append(dict(event))

    def _get_model_execution_events() -> List[Dict[str, Any]]:
        return [dict(event) for event in model_execution_events]

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
        update_progress_box=_update_progress_box,
        clear_progress_box=_clear_progress_box,
        is_cancelled=lambda: bool(cl.user_session.get("cancel_requested")),
        record_model_execution=_record_model_execution,
        get_model_execution_events=_get_model_execution_events,
    )


def _set_active_execution_task(task: Optional[asyncio.Task[Any]]) -> None:
    cl.user_session.set("active_execution_task", task)


def _reset_cancel_state() -> None:
    cl.user_session.set("cancel_requested", False)


def _is_compare_execution_request(request: Dict[str, Any]) -> bool:
    forced_route = str(request.get("forced_route") or "").strip()
    if forced_route == "compare_documents":
        return True
    session_docs = request.get("session_docs") or {}
    if len(session_docs) < 2:
        return False
    message = str(request.get("message") or "").lower()
    compare_markers = (
        "сравн",
        "compare",
        "отлич",
        "разниц",
        "diff",
    )
    return any(marker in message for marker in compare_markers)


def _build_execution_progress_stages(request: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    if _is_compare_execution_request(request):
        return [
            {
                "title": "Сравнение документов",
                "content": "Загружаю документы и подготавливаю текст для сравнения.",
            },
            {
                "title": "Сравнение документов",
                "content": "Сопоставляю смысловые фрагменты и ищу наиболее близкие нормы.",
            },
            {
                "title": "Сравнение документов",
                "content": "Анализирую различия по смыслу. Это может занять время на длинных документах.",
            },
            {
                "title": "Сравнение документов",
                "content": "Формирую юридический вывод и приложение с различиями.",
            },
        ]
    return None


async def _run_execution_progress(request: Dict[str, Any]) -> None:
    stages = _build_execution_progress_stages(request)
    if not stages:
        return
    for idx, stage in enumerate(stages):
        await _update_progress_box(
            key="execution_progress",
            title=stage["title"],
            content=stage["content"],
        )
        if idx < len(stages) - 1:
            await asyncio.sleep(EXECUTION_PROGRESS_POLL_S)


async def _await_backend_execution(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    _reset_cancel_state()
    cl.user_session.set("model_execution_events", [])
    progress_task: Optional[asyncio.Task[Any]] = None
    task = asyncio.create_task(
        _backend_execute_orchestration(request, deps=_build_execution_dependencies()),
        name=f"chainlit-exec:{request.get('trace_id', '-')}",
    )
    _set_active_execution_task(task)
    progress_stages = _build_execution_progress_stages(request)
    if progress_stages:
        progress_task = asyncio.create_task(
            _run_execution_progress(request),
            name=f"chainlit-progress:{request.get('trace_id', '-')}",
        )
    try:
        response = await task
    except asyncio.CancelledError:
        await _persist_current_backend_state(status="cancelled", last_error="cancelled-by-user")
        await cl.Message(content="Запрос остановлен пользователем.").send()
        return None
    finally:
        if progress_task is not None:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task
        if progress_stages:
            await _finalize_progress_box(key="execution_progress")
        _set_active_execution_task(None)
    return response


async def _update_progress_box(*, key: str, title: str, content: str) -> None:
    step = cl.user_session.get(key)
    rendered_title = str(title or "").strip() or "Прогресс"
    rendered_content = str(content or "").strip()
    if step is None:
        step = cl.Step(
            name=rendered_title,
            type="run",
            show_input=False,
            default_open=True,
        )
        step.output = rendered_content
        await step.send()
        cl.user_session.set(key, step)
        return
    step.name = rendered_title
    step.output = rendered_content
    update = getattr(step, "update", None)
    if callable(update):
        await update()


async def _clear_progress_box(*, key: str) -> None:
    step = cl.user_session.get(key)
    if step is None:
        return
    remove = getattr(step, "remove", None)
    if callable(remove):
        await remove()
    cl.user_session.set(key, None)


async def _finalize_progress_box(*, key: str) -> None:
    step = cl.user_session.get(key)
    if step is None:
        return
    update = getattr(step, "update", None)
    if callable(update):
        await update()
    cl.user_session.set(key, None)


async def _render_compare_appendix_step(split_result: Dict[str, Any]) -> None:
    appendix_body = str(split_result.get("appendix_body") or "").strip()
    if not appendix_body:
        return
    appendix_lines = int(split_result.get("appendix_lines") or 0)
    step = cl.Step(
        name=f"Приложение: различия по пунктам ({appendix_lines})",
        type="tool",
        show_input=False,
        default_open=False,
        autoCollapse=True,
    )
    step.output = appendix_body
    await step.send()


async def _render_execution_response(response: Dict[str, Any], history: List[Dict[str, str]]) -> None:
    _sync_run_metadata_from_response(response)
    _apply_session_state_patch(response.get("session_state_patch"))
    assistant_message = response.get("assistant_message")
    if assistant_message:
        await _finalize_progress_box(key="documents_summary_progress")
        rendered_message = assistant_message
        split_result = _split_compare_report_appendix(assistant_message)
        if _should_render_compare_appendix_in_step(split_result):
            rendered_message = split_result["main_body"]
            await cl.Message(content=rendered_message).send()
            await _render_compare_appendix_step(split_result)
        else:
            await cl.Message(content=rendered_message).send()
        history.append({"role": "assistant", "content": rendered_message})
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
        "trace_id": trace_id,
        "forced_route": forced_route,
        "effective_settings": effective,
        "runtime_budget_metadata": copy.deepcopy(cl.user_session.get("runtime_budget_metadata") or {}),
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


def _settings_payload_from_input(settings: Any) -> Dict[str, Any]:
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
                "tool_scope",
                "custom_system_prompt",
                "temperature",
                "top_p",
                "max_tokens",
            ):
                value = getter(key)
                if value is not None:
                    payload[key] = value
    return payload


def _assistant_mode_changed_via_modal(
    payload: Dict[str, Any],
    previous_effective: Dict[str, Any],
) -> bool:
    assistant_mode = payload.get("assistant_mode")
    if not isinstance(assistant_mode, str):
        return False
    assistant_mode = assistant_mode.strip()
    if assistant_mode not in _ASSISTANT_MODE_ITEMS:
        return False
    return assistant_mode != previous_effective.get("assistant_mode")


def _generation_value_changed(
    key: str,
    value: Any,
    previous_generation: Dict[str, Any],
) -> bool:
    normalized = clamp_generation_overrides({key: value}, base=previous_generation)
    return normalized.get(key) != previous_generation.get(key)


def _extract_control_plane_state_from_settings(
    settings: Any,
    *,
    previous_state: Optional[Dict[str, Any]] = None,
    previous_effective: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _settings_payload_from_input(settings)
    previous_state = previous_state or _get_control_plane_state()
    previous_effective = previous_effective or _get_effective_settings()

    if not _assistant_mode_changed_via_modal(payload, previous_effective):
        return merge_control_plane_state(previous_state, payload)

    assistant_mode = payload["assistant_mode"].strip()
    normalized_payload: Dict[str, Any] = {}

    for key in ("knowledge_collection_id", "custom_system_prompt"):
        if key in previous_state:
            normalized_payload[key] = previous_state.get(key)
        if key in payload:
            normalized_payload[key] = payload.get(key)

    for key in ("runtime_mode", "rag_scope", "model_profile", "prompt_profile", "tool_scope"):
        if key in payload and payload.get(key) != previous_effective.get(key):
            normalized_payload[key] = payload.get(key)

    previous_generation = dict(previous_effective.get("generation") or {})
    for key in ("temperature", "top_p", "max_tokens"):
        if key in payload and payload.get(key) is not None and _generation_value_changed(
            key,
            payload.get(key),
            previous_generation,
        ):
            normalized_payload[key] = payload.get(key)

    return merge_control_plane_state(build_preset_state(assistant_mode), normalized_payload)


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


def _ui_select_items(items: Dict[str, str]) -> Dict[str, str]:
    """Chainlit Select expects label -> value mapping on the frontend."""
    return {label: value for value, label in items.items()}


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
                        items=_ui_select_items(_ASSISTANT_MODE_ITEMS),
                        description="Крупный продуктовый сценарий без жёсткой смены chat profile.",
                    ),
                    cl.input_widget.Select(
                        id="runtime_mode",
                        label="Режим работы",
                        initial_value=effective.get("runtime_mode"),
                        items=_ui_select_items(_RUNTIME_MODE_ITEMS),
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
                        items=_ui_select_items(_RAG_SCOPE_ITEMS),
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
                        items=_ui_select_items(_MODEL_PROFILE_ITEMS),
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
                        items=_ui_select_items(_PROMPT_PROFILE_ITEMS),
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


def _get_intent_decision(
    query: str,
    file_count: int = 0,
    has_session_docs: bool = False,
    session_docs: Optional[Dict[str, Any]] = None,
    classifier_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session_docs = session_docs if session_docs is not None else (_get_session_docs() if has_session_docs else {})
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

# Пути для обмена файлами между Docker и хостом.
# Канонический runtime path: /app/backend/uploads.
UPLOADS_DIR = os.getenv("UPLOADS_DIR", "/app/backend/uploads")
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


def _is_model_failover_blocked(exc: Exception) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, UMSBusyError):
        return True
    message = str(exc).lower()
    return "429" in message or "busy" in message or "cancel" in message


def _build_model_execution_event(
    *,
    selection: Any,
    used_model_id: str,
    fallback_used: bool,
    fallback_reason: Optional[str],
    attempt_count: int,
) -> Dict[str, Any]:
    payload = dict(selection.to_dict() if hasattr(selection, "to_dict") else selection or {})
    payload.update(
        {
            "primary_model_id": payload.get("resolved_model_id") or payload.get("primary_model_id"),
            "fallback_model_id": payload.get("fallback_model_id") if payload.get("fallback_available") else None,
            "used_model_id": used_model_id,
            "fallback_used": bool(fallback_used),
            "fallback_reason": fallback_reason,
            "attempt_count": max(1, int(attempt_count)),
            "status": "fallback_completed" if fallback_used else "completed",
        }
    )
    return payload


def _infer_with_model_failover(
    selection: Any,
    payload: Dict[str, Any],
    device_mode: str,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    primary_model_id = str(getattr(selection, "resolved_model_id", "") or "")
    fallback_model_id = str(getattr(selection, "fallback_model_id", "") or "")
    if not fallback_model_id or fallback_model_id == primary_model_id:
        fallback_model_id = ""
    attempts = [primary_model_id] + ([fallback_model_id] if fallback_model_id else [])
    last_error: Optional[Exception] = None
    for attempt_idx, model_id in enumerate(attempts, start=1):
        try:
            request_payload = dict(payload)
            if prompt is not None:
                request_payload["prompt"] = prompt
            response = ums_client.infer(model_id, request_payload, device_mode)
            return {
                "response": response,
                "model_execution": _build_model_execution_event(
                    selection=selection,
                    used_model_id=model_id,
                    fallback_used=attempt_idx > 1,
                    fallback_reason=str(last_error) if attempt_idx > 1 and last_error is not None else None,
                    attempt_count=attempt_idx,
                ),
            }
        except Exception as exc:
            if _is_model_failover_blocked(exc):
                raise
            last_error = exc
            if attempt_idx < len(attempts):
                logger.warning(
                    "Model failover retry model=%s fallback=%s error=%s",
                    primary_model_id,
                    fallback_model_id or None,
                    exc,
                    exc_info=True,
                )
                inc_metric_counter(
                    "agent_nav_fallback_events_total",
                    labels={
                        "component": "chainlit",
                        "fallback": "model_failover_retry",
                        "source": "ui",
                    },
                )
                continue
            raise


def _create_failover_embed_fn(
    selection: Any,
    *,
    record_model_execution: Optional[Any] = None,
) -> Optional[Any]:
    primary_model_id = str(getattr(selection, "resolved_model_id", "") or "")
    fallback_model_id = str(getattr(selection, "fallback_model_id", "") or "")
    if not primary_model_id:
        return None
    attempts = [primary_model_id] + ([fallback_model_id] if fallback_model_id and fallback_model_id != primary_model_id else [])

    def _record(event: Dict[str, Any]) -> None:
        if callable(record_model_execution):
            try:
                record_model_execution(event)
            except Exception:
                logger.debug("Model execution recorder failed", exc_info=True)

    def _embed(texts: List[str]) -> np.ndarray:
        if not texts:
            return np.array([], dtype=np.float32)
        batch_size = int(os.getenv("UMS_EMBED_BATCH_SIZE", "10"))
        all_embeddings: List[List[float]] = []
        for batch_start in range(0, len(texts), batch_size):
            batch = texts[batch_start : batch_start + batch_size]
            last_error: Optional[Exception] = None
            for attempt_idx, model_id in enumerate(attempts, start=1):
                try:
                    response = ums_client.infer(model_id, {"input": batch, "normalize": True}, device_mode="cpu")
                    if "data" in response:
                        batch_embs = [item["embedding"] for item in response["data"]]
                    elif "embedding" in response:
                        embedding = response["embedding"]
                        batch_embs = embedding if isinstance(embedding[0], list) else [embedding]
                    else:
                        raise RuntimeError("Embedding response missing data")
                    all_embeddings.extend(batch_embs)
                    _record(
                        _build_model_execution_event(
                            selection=selection,
                            used_model_id=model_id,
                            fallback_used=attempt_idx > 1,
                            fallback_reason=str(last_error) if attempt_idx > 1 and last_error is not None else None,
                            attempt_count=attempt_idx,
                        )
                    )
                    break
                except Exception as exc:
                    if _is_model_failover_blocked(exc):
                        raise
                    last_error = exc
                    if attempt_idx < len(attempts):
                        logger.warning(
                            "Model failover retry embedding model=%s fallback=%s error=%s",
                            primary_model_id,
                            fallback_model_id or None,
                            exc,
                            exc_info=True,
                        )
                        continue
                    raise
        return np.array(all_embeddings, dtype=np.float32)

    try:
        _embed(["probe"])
    except Exception:
        return None
    return _embed


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
        "device_mode": normalize_inference_device_mode(effective.get("device_mode")),
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
    device_mode: Optional[str] = None,
    allow_sync_retry: bool = True,
    raise_on_error: bool = False,
    summary_stage: Optional[str] = None,
) -> str:
    model_execution_events = cl.user_session.get("model_execution_events")
    if not isinstance(model_execution_events, list):
        model_execution_events = []
        cl.user_session.set("model_execution_events", model_execution_events)
    request = _build_inference_request(
        default_temperature=temperature,
        default_top_p=default_top_p,
        default_max_tokens=default_max_tokens,
        enforced_overrides=enforced_overrides,
    )
    payload = dict(request["payload"])
    payload["prompt"] = prompt
    effective = _get_effective_settings()
    model_id = str(
        effective.get("resolved_model_id")
        or resolve_model_selection("llm.default_chat").resolved_model_id
    )
    resolved_device_mode = normalize_inference_device_mode(device_mode or request.get("device_mode"))
    try:
        response_payload = await asyncio.to_thread(ums_client.infer, model_id, payload, resolved_device_mode)
        if isinstance(response_payload, dict) and isinstance(response_payload.get("model_execution"), dict):
            model_execution_events.append(dict(response_payload["model_execution"]))
            cl.user_session.set("last_model_execution", dict(response_payload["model_execution"]))
        return _sanitize_assistant_output(response_payload.get("choices", [{}])[0].get("text", str(response_payload)))
    except Exception as exc:
        logger.warning(
            "Assistant infer failed stage=%s model=%s device_mode=%s retry=%s",
            summary_stage or "default",
            model_id,
            resolved_device_mode,
            allow_sync_retry,
            exc_info=True,
        )
        if not allow_sync_retry:
            if raise_on_error:
                raise
            return f"Ошибка генерации: {exc}"
        try:
            logger.warning("Direct chat infer failed, retrying sync inference", exc_info=True)
            response_payload = ums_client.infer(model_id, payload, resolved_device_mode)
            if isinstance(response_payload, dict) and isinstance(response_payload.get("model_execution"), dict):
                model_execution_events.append(dict(response_payload["model_execution"]))
                cl.user_session.set("last_model_execution", dict(response_payload["model_execution"]))
            return _sanitize_assistant_output(response_payload.get("choices", [{}])[0].get("text", str(response_payload)))
        except Exception as e:
            if raise_on_error:
                raise
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


_LEAKED_SYSTEM_LINE_PREFIXES = (
    "используй краткий ответ",
    "не повторяйся",
    "profile instructions:",
    "custom system override:",
    "catalog of sources:",
    "<|im_start|>",
    "<|im_end|>",
)


def _strip_leaked_system_instructions(answer_text: str) -> str:
    text = (answer_text or "").strip()
    if not text:
        return text

    kept_lines: List[str] = []
    removed_any = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if any(lowered.startswith(prefix) for prefix in _LEAKED_SYSTEM_LINE_PREFIXES):
            removed_any = True
            continue
        kept_lines.append(raw_line)

    cleaned = "\n".join(kept_lines).strip()
    if cleaned:
        return cleaned
    if removed_any:
        return "Чем могу помочь?"
    return text


def _sanitize_assistant_output(answer_text: str) -> str:
    text = _strip_model_source_sections(answer_text)
    return _strip_leaked_system_instructions(text)


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


def _split_compare_report_appendix(report_text: str) -> Dict[str, Any]:
    canonical_header = "## Приложение: различия по пунктам"
    text = str(report_text or "")
    marker_index = text.find(canonical_header)
    if marker_index < 0:
        return {
            "main_body": text,
            "appendix_body": None,
            "appendix_lines": 0,
        }

    main_body = text[:marker_index].rstrip()
    appendix_body = text[marker_index:].strip()
    if not main_body or not appendix_body.startswith(canonical_header):
        return {
            "main_body": text,
            "appendix_body": None,
            "appendix_lines": 0,
        }

    appendix_lines = sum(
        1
        for line in appendix_body.splitlines()[1:]
        if re.match(r"^\s*\d+\.\s+", line)
    )
    return {
        "main_body": main_body,
        "appendix_body": appendix_body,
        "appendix_lines": appendix_lines,
    }


def _should_render_compare_appendix_in_step(split_result: Dict[str, Any]) -> bool:
    appendix_body = split_result.get("appendix_body")
    appendix_lines = int(split_result.get("appendix_lines") or 0)
    return bool(appendix_body) and appendix_lines >= COMPARE_APPENDIX_STEP_MIN_LINES


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

    async def _reindex() -> tuple[AdaptiveRAGPipeline, int, Dict[str, Any]]:
        nonlocal rag
        runtime_budget = await _get_runtime_budget_metadata()
        effective = _get_effective_settings()
        retrieval_resolution = effective.get("resolved_retrieval_embedder_resolution")
        if retrieval_resolution is None and effective.get("resolved_retrieval_embedder_model_id"):
            retrieval_resolution = resolve_execution_plan(
                requested_model_id=str(effective.get("resolved_retrieval_embedder_model_id"))
            )
        if retrieval_resolution is None:
            retrieval_resolution = resolve_model_selection("legal.embedder")
        embed_fn = _create_failover_embed_fn(retrieval_resolution)
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
            mode_label = {
                "simple": "простой",
                "corrective": "корректирующий",
                "agentic": "агентный",
                "multi-agent": "мультиагентный",
            }.get(str(runtime_budget["rag_mode"]), str(runtime_budget["rag_mode"]))
            search_type = "BM25 + dense (гибридный)" if rag.embed_fn else "BM25 (только sparse)"
            profile_label = {
                "default": "стандартный",
                "adaptive": "адаптивный",
                "manual": "ручной",
            }.get(str(runtime_budget["runtime_profile"]), str(runtime_budget["runtime_profile"]))
            document_word = "документ" if indexed_count == 1 else "документа" if indexed_count in {2, 3, 4} else "документов"
            step.output = (
                f"RAG: режим={mode_label} ({runtime_budget['rag_mode']}; {runtime_budget.get('rag_mode_label')}), "
                f"поиск={search_type}, "
                f"профиль={profile_label} ({runtime_budget['runtime_profile']}), "
                f"бюджет={runtime_budget['retrieved_context_tokens_budget']} токенов, "
                f"проиндексирован {indexed_count} {document_word}\n"
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
    await _persist_current_backend_state(status="initialized")
    await _send_control_plane_settings()
    await _sync_thread_presentation()

def _default_starters() -> List[cl.Starter]:
    return [
        cl.Starter(
            label="Общий чат",
            message="Объясни простыми словами, что такое облака на небе.",
            command="preset:general_chat",
        ),
        cl.Starter(
            label="Помощник по коду",
            message="Помоги спроектировать небольшой FastAPI endpoint с понятным контрактом.",
            command="preset:coding",
        ),
        cl.Starter(
            label="Агентный режим (iterative)",
            message="Разложи задачу на шаги и предложи план исполнения с проверками.",
            command="preset:agentic",
        ),
        cl.Starter(
            label="Специализированные задачи",
            message="Сравни два загруженных документа и выдели ключевые различия.",
            command="preset:specific_tasks",
        ),
        cl.Starter(
            label="Вопросы по документам (RAG)",
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
                "общий чат, помощь по коду, агентный режим, специализированные задачи и RAG-вопросы по документам."
            ),
            icon="/public/logo_dark.svg",
            starters=_default_starters(),
        )
    ]


@cl.on_settings_update
async def on_settings_update(settings: Dict[str, Any]):
    _ensure_session_state()
    effective = _store_control_plane_state(
        _extract_control_plane_state_from_settings(
            settings,
            previous_state=_get_control_plane_state(),
            previous_effective=_get_effective_settings(),
        )
    )
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
                execution_response = await _await_backend_execution(
                    _build_execution_request(
                        message=pending_choice["query"],
                        trace_id=trace_id,
                        new_files=pending_choice.get("new_files", []),
                        session_docs=active_session_docs,
                        forced_route=selected_route,
                    ),
                )
                if execution_response is not None:
                    await _render_execution_response(execution_response, history)
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

    response = await _await_backend_execution(
        _build_execution_request(
            message=query,
            trace_id=trace_id,
            new_files=new_files,
            session_docs=active_session_docs,
        ),
    )
    if response is None:
        await _sync_thread_presentation(user_message=query)
        history.append({"role": "user", "content": query})
        return
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
                execution_response = await _await_backend_execution(
                    _build_execution_request(
                        message=query,
                        trace_id=trace_id,
                        new_files=new_files,
                        session_docs=_get_active_session_docs(),
                        forced_route=chosen_route,
                    ),
                )
                if execution_response is not None:
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


@cl.on_stop
async def on_stop():
    _ensure_session_state()
    cl.user_session.set("cancel_requested", True)
    task = cl.user_session.get("active_execution_task")
    if task is not None and not task.done():
        task.cancel()
    progress_step = cl.user_session.get("documents_summary_progress")
    if progress_step is not None:
        progress_step.name = "Остановка запроса"
        progress_step.output = "Останавливаю текущую обработку и освобождаю слот модели."
        update = getattr(progress_step, "update", None)
        if callable(update):
            await update()


def _detect_equipment_mode(file1_name: str, file2_name: str, query: str,
                           text_1: str = "", text_2: str = "") -> str:
    """Эвристика: tz_vs_smeta или smeta_vs_smeta."""
    from orchestrator.workflows.equipment import detect_equipment_mode
    return detect_equipment_mode(file1_name, file2_name, query, text_1, text_2)
