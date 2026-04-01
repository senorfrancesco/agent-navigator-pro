from __future__ import annotations

import copy
import threading
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_CURRENT_COLLECTOR: ContextVar["ExecutionTelemetryCollector | None"] = ContextVar(
    "execution_telemetry_collector",
    default=None,
)
_SUMMARY_LOCK = threading.Lock()
_LATEST_TELEMETRY: Dict[str, Any] = {}
_SUMMARY_BY_EXECUTOR: Dict[str, Dict[str, Any]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round_ms(seconds: float) -> int:
    return max(0, int(round(float(seconds) * 1000.0)))


def _append_signal(parts: List[str], label: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        parts.append(f"{label}: {'да' if value else 'нет'}")
        return
    if value == "":
        return
    parts.append(f"{label}: {value}")


@dataclass
class ExecutionTelemetryCollector:
    request_started_at: str = field(default_factory=_utc_now_iso)
    request_started_monotonic: float = field(default_factory=time.monotonic)
    request_completed_at: Optional[str] = None
    request_completed_monotonic: Optional[float] = None
    executor: str = ""
    route: str = ""
    status: str = "completed"
    stage_timings: List[Dict[str, Any]] = field(default_factory=list)
    tool_timings: List[Dict[str, Any]] = field(default_factory=list)
    quality_signals: Dict[str, Any] = field(default_factory=dict)

    def start_span(
        self,
        *,
        name: str,
        kind: str,
        category: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "name": name,
            "kind": kind,
            "category": category or "",
            "meta": dict(meta or {}),
            "started_at": _utc_now_iso(),
            "started_monotonic": time.monotonic(),
        }

    def finish_span(
        self,
        span: Dict[str, Any],
        *,
        status: str = "ok",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        completed_monotonic = time.monotonic()
        payload = {
            "name": str(span.get("name") or ""),
            "kind": str(span.get("kind") or "stage"),
            "category": str(span.get("category") or ""),
            "status": status,
            "started_at": span.get("started_at") or _utc_now_iso(),
            "completed_at": _utc_now_iso(),
            "elapsed_ms": _round_ms(completed_monotonic - float(span.get("started_monotonic") or completed_monotonic)),
            "meta": {**dict(span.get("meta") or {}), **dict(meta or {})},
        }
        self._append_timing(payload)
        return payload

    def record_duration(
        self,
        *,
        name: str,
        elapsed_seconds: float,
        kind: str,
        category: Optional[str] = None,
        status: str = "ok",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "name": name,
            "kind": kind,
            "category": category or "",
            "status": status,
            "started_at": None,
            "completed_at": _utc_now_iso(),
            "elapsed_ms": _round_ms(elapsed_seconds),
            "meta": dict(meta or {}),
        }
        self._append_timing(payload)
        return payload

    def _append_timing(self, payload: Dict[str, Any]) -> None:
        target = self.stage_timings if payload.get("kind") == "stage" else self.tool_timings
        target.append(payload)

    def record_quality(self, **signals: Any) -> None:
        for key, value in signals.items():
            if value is not None:
                self.quality_signals[key] = value

    def finalize(
        self,
        *,
        executor: str,
        route: str,
        response: Dict[str, Any],
        quality_signals: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.executor = executor
        self.route = route
        self.status = str(((response.get("execution_metadata") or {}).get("status")) or "completed")
        self.request_completed_at = _utc_now_iso()
        self.request_completed_monotonic = time.monotonic()
        if quality_signals:
            self.record_quality(**quality_signals)
        self.record_quality(
            used_llm=bool(response.get("model_execution")) or self._sum_category_ms("llm") > 0,
            used_rag=bool(response.get("sources")),
            sources_count=len(response.get("sources") or []),
            report_generated=bool(response.get("generated_report")),
            fallback_used=bool((response.get("model_execution") or {}).get("fallback_used")),
            degraded=bool((response.get("execution_metadata") or {}).get("degraded")),
        )
        payload = {
            "started_at": self.request_started_at,
            "completed_at": self.request_completed_at,
            "elapsed_ms": _round_ms((self.request_completed_monotonic or time.monotonic()) - self.request_started_monotonic),
            "queue_ms": 0,
            "service_ms": self._sum_category_ms("service"),
            "tool_ms": self._sum_category_ms("tool"),
            "llm_ms": self._sum_category_ms("llm"),
            "embedding_ms": self._sum_category_ms("embedding"),
            "report_ms": self._sum_category_ms("report"),
            "executor": executor,
            "route": route,
            "status": self.status,
            "stage_timings": copy.deepcopy(self.stage_timings),
            "tool_timings": copy.deepcopy(self.tool_timings),
            "quality_signals": copy.deepcopy(self.quality_signals),
            "quality_summary": build_quality_summary(self.quality_signals),
        }
        record_telemetry_summary(payload)
        return payload

    def _sum_category_ms(self, category: str) -> int:
        total = 0
        for item in self.stage_timings + self.tool_timings:
            if item.get("category") == category:
                total += int(item.get("elapsed_ms") or 0)
        return total


def begin_execution_telemetry() -> tuple[ExecutionTelemetryCollector, Token]:
    collector = ExecutionTelemetryCollector()
    token = _CURRENT_COLLECTOR.set(collector)
    return collector, token


def reset_execution_telemetry(token: Token) -> None:
    _CURRENT_COLLECTOR.reset(token)


def get_current_collector() -> Optional[ExecutionTelemetryCollector]:
    return _CURRENT_COLLECTOR.get()


def start_current_span(
    *,
    name: str,
    kind: str,
    category: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    collector = get_current_collector()
    if collector is None:
        return None
    return collector.start_span(name=name, kind=kind, category=category, meta=meta)


def finish_current_span(
    span: Optional[Dict[str, Any]],
    *,
    status: str = "ok",
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    collector = get_current_collector()
    if collector is None or span is None:
        return None
    return collector.finish_span(span, status=status, meta=meta)


def record_current_duration(
    *,
    name: str,
    elapsed_seconds: float,
    kind: str,
    category: Optional[str] = None,
    status: str = "ok",
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    collector = get_current_collector()
    if collector is None:
        return None
    return collector.record_duration(
        name=name,
        elapsed_seconds=elapsed_seconds,
        kind=kind,
        category=category,
        status=status,
        meta=meta,
    )


def record_quality_signals(**signals: Any) -> None:
    collector = get_current_collector()
    if collector is None:
        return
    collector.record_quality(**signals)


def build_quality_summary(signals: Dict[str, Any]) -> str:
    parts: List[str] = []
    _append_signal(parts, "LLM", signals.get("used_llm"))
    _append_signal(parts, "RAG", signals.get("used_rag"))
    _append_signal(parts, "Источников", signals.get("sources_count"))
    _append_signal(parts, "Цитат", signals.get("citations_count"))
    _append_signal(parts, "Fallback", signals.get("fallback_used"))
    _append_signal(parts, "Структура", signals.get("structured_output_ok"))
    _append_signal(parts, "Покрытие", signals.get("coverage_signals"))
    return " · ".join(parts) if parts else "Нет дополнительных сигналов качества."


def format_telemetry_footer(telemetry: Dict[str, Any]) -> str:
    if not telemetry:
        return ""
    lines = [
        "---",
        "Timing / Quality",
        f"- Полный ответ: {int(telemetry.get('elapsed_ms') or 0)} мс",
    ]
    parts = []
    for label, key in (
        ("LLM", "llm_ms"),
        ("Embeddings", "embedding_ms"),
        ("Сервисы", "service_ms"),
        ("Отчёт", "report_ms"),
    ):
        value = int(telemetry.get(key) or 0)
        if value > 0:
            parts.append(f"{label}: {value} мс")
    if parts:
        lines.append(f"- {' · '.join(parts)}")
    summary = str(telemetry.get("quality_summary") or "").strip()
    if summary:
        lines.append(f"- Качество: {summary}")
    return "\n".join(lines)


def append_telemetry_footer(text: str, telemetry: Dict[str, Any]) -> str:
    footer = format_telemetry_footer(telemetry)
    if not footer:
        return text
    base = str(text or "").rstrip()
    return f"{base}\n\n{footer}".strip()


def record_telemetry_summary(telemetry: Dict[str, Any]) -> None:
    executor = str(telemetry.get("executor") or "unknown")
    elapsed_ms = int(telemetry.get("elapsed_ms") or 0)
    with _SUMMARY_LOCK:
        global _LATEST_TELEMETRY
        _LATEST_TELEMETRY = copy.deepcopy(telemetry)
        bucket = _SUMMARY_BY_EXECUTOR.setdefault(
            executor,
            {
                "executor": executor,
                "count": 0,
                "avg_elapsed_ms": 0,
                "last_elapsed_ms": 0,
                "last_quality_summary": "",
                "last_status": "",
            },
        )
        bucket["count"] = int(bucket.get("count") or 0) + 1
        previous_avg = float(bucket.get("avg_elapsed_ms") or 0.0)
        count = int(bucket["count"])
        bucket["avg_elapsed_ms"] = int(round(((previous_avg * (count - 1)) + elapsed_ms) / max(1, count)))
        bucket["last_elapsed_ms"] = elapsed_ms
        bucket["last_quality_summary"] = str(telemetry.get("quality_summary") or "")
        bucket["last_status"] = str(telemetry.get("status") or "")


def get_timing_summary() -> Dict[str, Any]:
    with _SUMMARY_LOCK:
        return {
            "latest": copy.deepcopy(_LATEST_TELEMETRY),
            "by_executor": [copy.deepcopy(_SUMMARY_BY_EXECUTOR[key]) for key in sorted(_SUMMARY_BY_EXECUTOR.keys())],
        }
