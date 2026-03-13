import threading
import time
from collections import defaultdict
import uuid
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from starlette.datastructures import Headers, MutableHeaders

HTTP_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

_METRIC_HELP = {
    "agent_nav_http_requests_total": "Total HTTP requests by service, method, path and status code.",
    "agent_nav_http_request_duration_seconds": "HTTP request latency in seconds.",
    "agent_nav_http_requests_in_progress": "HTTP requests currently in progress.",
    "agent_nav_ums_running_models": "Number of model processes currently running in UMS.",
    "agent_nav_ums_active_heavy_model": "Whether a heavy model is currently active in UMS.",
    "agent_nav_agent_api_orchestration_requests_total": "Agent API orchestration requests by endpoint and result.",
    "agent_nav_agent_api_openai_dedup_hits_total": "OpenAI-compatible deduplicated requests served without execution.",
    "agent_nav_ums_concurrency_saturation_total": "UMS requests rejected because concurrency policy was saturated.",
}

_METRIC_TYPE = {
    "agent_nav_http_requests_total": "counter",
    "agent_nav_http_request_duration_seconds": "histogram",
    "agent_nav_http_requests_in_progress": "gauge",
    "agent_nav_ums_running_models": "gauge",
    "agent_nav_ums_active_heavy_model": "gauge",
    "agent_nav_agent_api_orchestration_requests_total": "counter",
    "agent_nav_agent_api_openai_dedup_hits_total": "counter",
    "agent_nav_ums_concurrency_saturation_total": "counter",
}


def _normalize_labels(labels: Optional[Dict[str, Any]] = None) -> Tuple[Tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _format_labels(labels: Iterable[Tuple[str, str]]) -> str:
    items = list(labels)
    if not items:
        return ""
    rendered = []
    for key, value in items:
        escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        rendered.append(f'{key}="{escaped}"')
    return "{" + ",".join(rendered) + "}"


class _MetricsStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, Dict[Tuple[Tuple[str, str], ...], float]] = defaultdict(dict)
        self._gauges: Dict[str, Dict[Tuple[Tuple[str, str], ...], float]] = defaultdict(dict)
        self._histograms: Dict[str, Dict[Tuple[Tuple[str, str], ...], Dict[str, Any]]] = defaultdict(dict)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def inc_counter(self, name: str, value: float = 1.0, labels: Optional[Dict[str, Any]] = None) -> None:
        key = _normalize_labels(labels)
        with self._lock:
            current = float(self._counters[name].get(key, 0.0))
            self._counters[name][key] = current + value

    def set_gauge(self, name: str, value: float, labels: Optional[Dict[str, Any]] = None) -> None:
        key = _normalize_labels(labels)
        with self._lock:
            self._gauges[name][key] = float(value)

    def inc_gauge(self, name: str, value: float = 1.0, labels: Optional[Dict[str, Any]] = None) -> None:
        key = _normalize_labels(labels)
        with self._lock:
            current = float(self._gauges[name].get(key, 0.0))
            self._gauges[name][key] = current + value

    def dec_gauge(self, name: str, value: float = 1.0, labels: Optional[Dict[str, Any]] = None) -> None:
        self.inc_gauge(name=name, value=-value, labels=labels)

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: Optional[Dict[str, Any]] = None,
        buckets: Tuple[float, ...] = HTTP_DURATION_BUCKETS,
    ) -> None:
        key = _normalize_labels(labels)
        with self._lock:
            entry = self._histograms[name].get(key)
            if entry is None:
                entry = {
                    "buckets": list(buckets),
                    "counts": [0 for _ in buckets],
                    "count": 0,
                    "sum": 0.0,
                }
                self._histograms[name][key] = entry
            numeric_value = float(value)
            entry["count"] += 1
            entry["sum"] += numeric_value
            for index, bucket in enumerate(entry["buckets"]):
                if numeric_value <= bucket:
                    entry["counts"][index] += 1

    def render(self) -> str:
        lines = []
        with self._lock:
            metric_names = sorted(
                set(self._counters.keys()) | set(self._gauges.keys()) | set(self._histograms.keys())
            )
            for name in metric_names:
                lines.append(f"# HELP {name} {_METRIC_HELP.get(name, name)}")
                lines.append(f"# TYPE {name} {_METRIC_TYPE.get(name, 'gauge')}")
                if name in self._counters:
                    for labels, value in sorted(self._counters[name].items()):
                        lines.append(f"{name}{_format_labels(labels)} {value}")
                if name in self._gauges:
                    for labels, value in sorted(self._gauges[name].items()):
                        lines.append(f"{name}{_format_labels(labels)} {value}")
                if name in self._histograms:
                    for labels, entry in sorted(self._histograms[name].items()):
                        for bucket, count in zip(entry["buckets"], entry["counts"]):
                            bucket_labels = tuple(list(labels) + [("le", str(bucket))])
                            lines.append(
                                f"{name}_bucket{_format_labels(bucket_labels)} {count}"
                            )
                        inf_labels = tuple(list(labels) + [("le", "+Inf")])
                        lines.append(f"{name}_bucket{_format_labels(inf_labels)} {entry['count']}")
                        lines.append(f"{name}_count{_format_labels(labels)} {entry['count']}")
                        lines.append(f"{name}_sum{_format_labels(labels)} {entry['sum']}")
        return "\n".join(lines) + "\n"


_METRICS = _MetricsStore()


def reset_observability_metrics() -> None:
    _METRICS.reset()


def begin_http_request(*, service: str, method: str, path: str) -> float:
    _METRICS.inc_gauge(
        "agent_nav_http_requests_in_progress",
        labels={"service": service, "method": method},
    )
    return time.perf_counter()


def end_http_request(
    *,
    service: str,
    method: str,
    path: str,
    status_code: int,
    started_at: float,
) -> None:
    duration = max(0.0, time.perf_counter() - started_at)
    inflight_labels = {"service": service, "method": method}
    labels = {"service": service, "method": method, "path": path}
    _METRICS.dec_gauge("agent_nav_http_requests_in_progress", labels=inflight_labels)
    _METRICS.inc_counter(
        "agent_nav_http_requests_total",
        labels={**labels, "status_code": str(status_code)},
    )
    _METRICS.observe_histogram(
        "agent_nav_http_request_duration_seconds",
        duration,
        labels=labels,
    )


def set_ums_runtime_metrics(*, running_models: int, active_heavy_model_present: bool) -> None:
    labels = {"service": "ums"}
    _METRICS.set_gauge("agent_nav_ums_running_models", float(running_models), labels=labels)
    _METRICS.set_gauge(
        "agent_nav_ums_active_heavy_model",
        1.0 if active_heavy_model_present else 0.0,
        labels=labels,
    )


def render_metrics_text() -> str:
    return _METRICS.render()


def inc_metric_counter(name: str, *, labels: Optional[Dict[str, Any]] = None, value: float = 1.0) -> None:
    _METRICS.inc_counter(name, value=value, labels=labels)


class ObservabilityMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        service_name: str,
        logger: Any,
        post_response_hook: Optional[Callable[[], None]] = None,
    ) -> None:
        self.app = app
        self.service_name = service_name
        self.logger = logger
        self.post_response_hook = post_response_hook

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path") or "/")
        headers = Headers(scope=scope)
        trace_id = headers.get("x-trace-id") or str(uuid.uuid4())[:8]
        scope.setdefault("state", {})["trace_id"] = trace_id
        started_at = begin_http_request(service=self.service_name, method=method, path=path)
        self.logger.info(
            "request_started service=%s method=%s path=%s trace_id=%s",
            self.service_name,
            method,
            path,
            trace_id,
        )
        status_code = 500

        async def send_wrapper(message: Dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                mutable_headers = MutableHeaders(scope=message)
                mutable_headers["X-Trace-Id"] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if self.post_response_hook is not None:
                self.post_response_hook()
            end_http_request(
                service=self.service_name,
                method=method,
                path=path,
                status_code=status_code,
                started_at=started_at,
            )
            self.logger.exception(
                "request_failed service=%s method=%s path=%s trace_id=%s status_code=%s",
                self.service_name,
                method,
                path,
                trace_id,
                status_code,
            )
            raise

        if self.post_response_hook is not None:
            self.post_response_hook()
        end_http_request(
            service=self.service_name,
            method=method,
            path=path,
            status_code=status_code,
            started_at=started_at,
        )
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        self.logger.info(
            "request_completed service=%s method=%s path=%s trace_id=%s status_code=%s duration_ms=%.2f",
            self.service_name,
            method,
            path,
            trace_id,
            status_code,
            duration_ms,
        )
