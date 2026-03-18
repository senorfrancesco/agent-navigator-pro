"""UMS Client - HTTP-клиент для взаимодействия с Unified Model Server."""

import asyncio
import inspect
import json
import logging
import os
import random
import time
from contextlib import suppress
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Mapping, Optional

import httpx
import numpy as np
import requests

from services.model_manager.model_selection import ModelSelection, resolve_execution_plan
from services.observability import inc_metric_counter

UMS_URL = os.getenv("UMS_URL", "http://localhost:8090")
logger = logging.getLogger("ums_client")

UMS_CLIENT_TIMEOUT_S = float(os.getenv("UMS_CLIENT_TIMEOUT_S", "300.0"))
UMS_CLIENT_CONNECT_TIMEOUT_S = float(os.getenv("UMS_CLIENT_CONNECT_TIMEOUT_S", "10.0"))
UMS_CLIENT_KEEPALIVE_CONNECTIONS = int(os.getenv("UMS_CLIENT_KEEPALIVE_CONNECTIONS", "10"))
UMS_CLIENT_MAX_CONNECTIONS = int(os.getenv("UMS_CLIENT_MAX_CONNECTIONS", "50"))
UMS_SYNC_INFER_RETRIES = int(os.getenv("UMS_SYNC_INFER_RETRIES", "3"))
UMS_SWITCH_MODEL_TIMEOUT_S = float(os.getenv("UMS_SWITCH_MODEL_TIMEOUT_S", "120.0"))
UMS_STATUS_TIMEOUT_S = float(os.getenv("UMS_STATUS_TIMEOUT_S", "10.0"))
UMS_EMBED_PROBE_TIMEOUT_S = float(os.getenv("UMS_EMBED_PROBE_TIMEOUT_S", "10.0"))
UMS_EMBED_BATCH_SIZE = int(os.getenv("UMS_EMBED_BATCH_SIZE", "10"))
UMS_EMBED_BATCH_TIMEOUT_S = float(os.getenv("UMS_EMBED_BATCH_TIMEOUT_S", "120.0"))
UMS_EMBED_BATCH_CONNECT_TIMEOUT_S = float(os.getenv("UMS_EMBED_BATCH_CONNECT_TIMEOUT_S", "10.0"))
UMS_EMBED_BATCH_RETRIES = int(os.getenv("UMS_EMBED_BATCH_RETRIES", "3"))
UMS_EMBED_BATCH_RETRY_DELAY_S = float(os.getenv("UMS_EMBED_BATCH_RETRY_DELAY_S", "1.0"))


BUSY_STATUS_CODE = 429
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
NON_FALLBACK_STATUS_CODES = {400, 401, 403, 404, 409, 422}


def _resolve_default_llm_selection() -> ModelSelection:
    return resolve_execution_plan(role_key="llm.default_chat")


def _resolve_default_retrieval_embedder_selection() -> ModelSelection:
    return resolve_execution_plan(role_key="legal.embedder")


def _resolve_execution_plan_for_model(model_id: Optional[str] = None, *, role_key: Optional[str] = None) -> ModelSelection:
    return resolve_execution_plan(role_key=role_key, requested_model_id=model_id)


def _build_model_execution_metadata(
    selection: ModelSelection,
    *,
    used_model_id: str,
    status: str,
    attempt_count: int,
    fallback_used: bool = False,
    fallback_stage: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    error: Optional[Exception | str] = None,
    source: str = "client",
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "status": status,
        "requested_model_id": selection.requested_model_id,
        "requested_model_key": selection.requested_model_key,
        "role_key": selection.role_key,
        "role_label": selection.role_label,
        "primary_model_key": selection.primary_model_key,
        "fallback_model_key": selection.fallback_model_key,
        "primary_model_id": selection.primary_model_id,
        "fallback_model_id": selection.fallback_model_id,
        "resolved_primary_model_id": selection.resolved_model_id,
        "resolved_model_id": used_model_id,
        "used_model_id": used_model_id,
        "fallback_available": selection.fallback_available,
        "fallback_used": fallback_used,
        "fallback_stage": fallback_stage,
        "fallback_reason": fallback_reason,
        "attempt_count": attempt_count,
        "selection_source": selection.source,
        "source": source,
    }
    if selection.warning is not None:
        metadata["selection_warning"] = selection.warning
    if error is not None:
        metadata["error"] = str(error)
    return metadata


def _status_code_from_exception(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None
    return None


def _is_busy_exception(exc: Exception) -> bool:
    status_code = _status_code_from_exception(exc)
    return status_code == BUSY_STATUS_CODE or isinstance(exc, UMSBusyError)


def _is_cancelled_exception(exc: Exception) -> bool:
    return isinstance(exc, asyncio.CancelledError)


def _is_retryable_sync_exception(exc: Exception) -> bool:
    if _is_busy_exception(exc) or _is_cancelled_exception(exc):
        return False
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = _status_code_from_exception(exc)
        return status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, requests.exceptions.RequestException):
        status_code = _status_code_from_exception(exc)
        return status_code in RETRYABLE_STATUS_CODES or status_code is None
    return False


def _is_retryable_async_exception(exc: Exception) -> bool:
    if _is_busy_exception(exc) or _is_cancelled_exception(exc):
        return False
    if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = _status_code_from_exception(exc)
        return status_code in RETRYABLE_STATUS_CODES
    return False


def _is_fallback_eligible_exception(exc: Exception) -> bool:
    if _is_busy_exception(exc) or _is_cancelled_exception(exc):
        return False
    status_code = _status_code_from_exception(exc)
    if status_code is not None:
        return status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, (requests.exceptions.RequestException, httpx.RequestError, httpx.TimeoutException)):
        return True
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        return "ums returned error" in message or "failed to connect to ums" in message or "load" in message
    return False


def _extract_result_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if data.get("status") == "success":
        result = data.get("result", {})
        return result if isinstance(result, dict) else {"value": result}
    raise RuntimeError(f"UMS returned error: {data}")


def _emit_fallback_metric(
    *,
    component: str,
    fallback: str,
    stage: str,
    primary_model_id: str,
    fallback_model_id: str,
) -> None:
    inc_metric_counter(
        "agent_nav_fallback_events_total",
        labels={
            "component": component,
            "fallback": fallback,
            "source": "client",
            "stage": stage,
            "primary_model": primary_model_id,
            "fallback_model": fallback_model_id,
        },
    )


def _selection_model_candidates(selection: ModelSelection) -> List[str]:
    candidates: List[str] = []
    primary_model_id = selection.resolved_model_id or selection.primary_model_id
    if primary_model_id:
        candidates.append(primary_model_id)
    fallback_model_id = selection.fallback_model_id
    if selection.fallback_available and fallback_model_id and fallback_model_id not in candidates:
        candidates.append(fallback_model_id)
    return candidates


class UMSBusyError(RuntimeError):
    """UMS rejected the request because runtime concurrency is saturated."""

# Shared async client для повторного использования соединений
_async_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def _get_async_client() -> httpx.AsyncClient:
    """Shared httpx.AsyncClient singleton для UMS."""
    global _async_client
    if _async_client is None:
        async with _client_lock:
            if _async_client is None:
                _async_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(UMS_CLIENT_TIMEOUT_S, connect=UMS_CLIENT_CONNECT_TIMEOUT_S),
                    limits=httpx.Limits(
                        max_keepalive_connections=UMS_CLIENT_KEEPALIVE_CONNECTIONS,
                        max_connections=UMS_CLIENT_MAX_CONNECTIONS,
                    ),
                )
    return _async_client


def _should_retry_async_infer(exc: Exception) -> bool:
    """Retry only on transient transport/backend failures."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


def _extract_sse_text(line: str) -> str:
    """Парсит одну SSE-строку от llama-server/UMS и достает текстовый токен."""
    line = line.strip()
    if not line or not line.startswith("data: "):
        return ""

    data_str = line[6:]
    if data_str == "[DONE]":
        return ""

    try:
        chunk = json.loads(data_str)
    except json.JSONDecodeError:
        return ""

    choices = chunk.get("choices", [])
    if not choices:
        return ""

    return choices[0].get("text", "") or choices[0].get("delta", {}).get("content", "")


class UMSClient:
    """HTTP-клиент для взаимодействия с UMS."""
    def __init__(self, base_url: str = UMS_URL):
        self.base_url = base_url
        self.last_model_execution: Optional[Dict[str, Any]] = None

    def _record_model_execution(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        self.last_model_execution = dict(metadata)
        return self.last_model_execution

    def _model_candidates(self, selection: ModelSelection) -> List[str]:
        return _selection_model_candidates(selection)

    def _single_sync_infer(self, model_id: str, payload: Dict[str, Any], device_mode: str) -> Dict[str, Any]:
        url = f"{self.base_url}/infer"
        request_body = {
            "model_id": model_id,
            "payload": payload,
            "device_mode": device_mode,
            "priority": "normal",
        }
        last_error: Optional[Exception] = None
        for attempt in range(UMS_SYNC_INFER_RETRIES):
            try:
                print(f"[UMS_CLIENT] Sending inference request for model: {model_id} (attempt {attempt + 1})")
                response = requests.post(url, json=request_body, timeout=UMS_CLIENT_TIMEOUT_S)
                response.raise_for_status()
                data = response.json()
                return _extract_result_payload(data)
            except requests.exceptions.HTTPError as exc:
                last_error = exc
                if _status_code_from_exception(exc) == BUSY_STATUS_CODE:
                    raise UMSBusyError(f"UMS is busy for model {model_id}: {exc}") from exc
                if attempt < UMS_SYNC_INFER_RETRIES - 1 and _is_retryable_sync_exception(exc):
                    logger.warning(
                        "UMS client infer retry model=%s attempt=%s/%s: %s",
                        model_id,
                        attempt + 1,
                        UMS_SYNC_INFER_RETRIES,
                        exc,
                    )
                    inc_metric_counter(
                        "agent_nav_fallback_events_total",
                        labels={"component": "ums_client", "fallback": "infer_retry", "source": "client"},
                    )
                    time.sleep(2)
                    continue
                break
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < UMS_SYNC_INFER_RETRIES - 1 and _is_retryable_sync_exception(exc):
                    logger.warning(
                        "UMS client infer retry model=%s attempt=%s/%s: %s",
                        model_id,
                        attempt + 1,
                        UMS_SYNC_INFER_RETRIES,
                        exc,
                    )
                    inc_metric_counter(
                        "agent_nav_fallback_events_total",
                        labels={"component": "ums_client", "fallback": "infer_retry", "source": "client"},
                    )
                    time.sleep(2)
                    continue
                break
        if last_error is None:
            raise RuntimeError(f"Failed to infer with model {model_id}: no response returned")
        raise last_error

    async def _single_async_infer(self, model_id: str, payload: Dict[str, Any], device_mode: str) -> Dict[str, Any]:
        url = f"{self.base_url}/infer"
        request_body = {
            "model_id": model_id,
            "payload": payload,
            "device_mode": device_mode,
            "priority": "normal",
        }
        retries = int(os.getenv("UMS_INFER_RETRIES", "4"))
        base_delay_s = float(os.getenv("UMS_RETRY_BASE_DELAY_S", "1.0"))
        max_delay_s = float(os.getenv("UMS_RETRY_MAX_DELAY_S", "10.0"))
        timeout_s = float(os.getenv("UMS_INFER_TIMEOUT_S", "300.0"))
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            try:
                print(f"[UMS_CLIENT] Async inference for model: {model_id} (attempt {attempt + 1})")
                client = await _get_async_client()
                response = await client.post(url, json=request_body, timeout=timeout_s)
                response.raise_for_status()
                data = response.json()
                return _extract_result_payload(data)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if _status_code_from_exception(exc) == BUSY_STATUS_CODE:
                    raise UMSBusyError(f"UMS is busy for model {model_id}: {exc}") from exc
                if attempt < retries - 1 and _is_retryable_async_exception(exc):
                    logger.warning(
                        "UMS client async retry model=%s attempt=%s/%s: %s",
                        model_id,
                        attempt + 1,
                        retries,
                        exc,
                    )
                    inc_metric_counter(
                        "agent_nav_fallback_events_total",
                        labels={"component": "ums_client", "fallback": "async_infer_retry", "source": "client"},
                    )
                    delay = min(max_delay_s, base_delay_s * (2 ** attempt))
                    jitter = random.uniform(0.0, min(0.5, delay * 0.2))
                    await asyncio.sleep(delay + jitter)
                    continue
                break
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_error = exc
                if attempt < retries - 1 and _is_retryable_async_exception(exc):
                    logger.warning(
                        "UMS client async retry model=%s attempt=%s/%s: %s",
                        model_id,
                        attempt + 1,
                        retries,
                        exc,
                    )
                    inc_metric_counter(
                        "agent_nav_fallback_events_total",
                        labels={"component": "ums_client", "fallback": "async_infer_retry", "source": "client"},
                    )
                    delay = min(max_delay_s, base_delay_s * (2 ** attempt))
                    jitter = random.uniform(0.0, min(0.5, delay * 0.2))
                    await asyncio.sleep(delay + jitter)
                    continue
                break
            except Exception as exc:
                last_error = exc
                break
        if last_error is None:
            raise RuntimeError(f"Failed to infer with model {model_id}: no response returned")
        raise last_error

    def infer(self, model_id: str, payload: Dict[str, Any], device_mode: str = "hybrid") -> Dict[str, Any]:
        selection = _resolve_execution_plan_for_model(model_id)
        candidates = self._model_candidates(selection)
        last_error: Optional[Exception] = None
        for attempt_index, candidate_model_id in enumerate(candidates):
            try:
                result = self._single_sync_infer(candidate_model_id, payload, device_mode)
                metadata = _build_model_execution_metadata(
                    selection,
                    used_model_id=candidate_model_id,
                    status="success",
                    attempt_count=attempt_index + 1,
                    fallback_used=attempt_index > 0,
                    fallback_stage="infer",
                    fallback_reason=None if attempt_index == 0 else "primary_model_failed",
                    source="client",
                )
                self._record_model_execution(metadata)
                payload_result = dict(result)
                payload_result["model_execution"] = metadata
                return payload_result
            except Exception as exc:
                last_error = exc
                if _is_busy_exception(exc):
                    metadata = _build_model_execution_metadata(
                        selection,
                        used_model_id=candidate_model_id,
                        status="failed",
                        attempt_count=attempt_index + 1,
                        fallback_used=attempt_index > 0,
                        fallback_stage="infer",
                        fallback_reason="busy",
                        error=exc,
                        source="client",
                    )
                    self._record_model_execution(metadata)
                    raise UMSBusyError(f"Failed to infer with model {candidate_model_id}: {exc}") from exc
                if _is_cancelled_exception(exc):
                    metadata = _build_model_execution_metadata(
                        selection,
                        used_model_id=candidate_model_id,
                        status="failed",
                        attempt_count=attempt_index + 1,
                        fallback_used=attempt_index > 0,
                        fallback_stage="infer",
                        fallback_reason="cancelled",
                        error=exc,
                        source="client",
                    )
                    self._record_model_execution(metadata)
                    raise
                if attempt_index == 0 and len(candidates) > 1 and _is_fallback_eligible_exception(exc):
                    metadata = _build_model_execution_metadata(
                        selection,
                        used_model_id=candidate_model_id,
                        status="failed",
                        attempt_count=attempt_index + 1,
                        fallback_used=False,
                        fallback_stage="infer",
                        fallback_reason="primary_model_failed",
                        error=exc,
                        source="client",
                    )
                    self._record_model_execution(metadata)
                    _emit_fallback_metric(
                        component="ums_client",
                        fallback="infer_model_failover",
                        stage="infer",
                        primary_model_id=candidates[0],
                        fallback_model_id=candidates[1],
                    )
                    logger.warning(
                        "UMS client infer failover primary=%s fallback=%s: %s",
                        candidates[0],
                        candidates[1],
                        exc,
                    )
                    continue
                metadata = _build_model_execution_metadata(
                    selection,
                    used_model_id=candidate_model_id,
                    status="failed",
                    attempt_count=attempt_index + 1,
                    fallback_used=attempt_index > 0,
                    fallback_stage="infer",
                    fallback_reason="fallback_failed" if attempt_index > 0 else "primary_failed",
                    error=exc,
                    source="client",
                )
                self._record_model_execution(metadata)
                break

        raise RuntimeError(
            f"Failed to connect to UMS after {len(candidates)} model attempts: {last_error}"
        )

    async def async_infer(self, model_id: str, payload: Dict[str, Any], device_mode: str = "hybrid") -> Dict[str, Any]:
        selection = _resolve_execution_plan_for_model(model_id)
        candidates = self._model_candidates(selection)
        last_error: Optional[Exception] = None
        for attempt_index, candidate_model_id in enumerate(candidates):
            try:
                result = await self._single_async_infer(candidate_model_id, payload, device_mode)
                metadata = _build_model_execution_metadata(
                    selection,
                    used_model_id=candidate_model_id,
                    status="success",
                    attempt_count=attempt_index + 1,
                    fallback_used=attempt_index > 0,
                    fallback_stage="infer",
                    fallback_reason=None if attempt_index == 0 else "primary_model_failed",
                    source="client",
                )
                self._record_model_execution(metadata)
                payload_result = dict(result)
                payload_result["model_execution"] = metadata
                return payload_result
            except Exception as exc:
                last_error = exc
                if _is_busy_exception(exc):
                    metadata = _build_model_execution_metadata(
                        selection,
                        used_model_id=candidate_model_id,
                        status="failed",
                        attempt_count=attempt_index + 1,
                        fallback_used=attempt_index > 0,
                        fallback_stage="infer",
                        fallback_reason="busy",
                        error=exc,
                        source="client",
                    )
                    self._record_model_execution(metadata)
                    raise UMSBusyError(f"Failed to infer with model {candidate_model_id}: {exc}") from exc
                if _is_cancelled_exception(exc):
                    metadata = _build_model_execution_metadata(
                        selection,
                        used_model_id=candidate_model_id,
                        status="failed",
                        attempt_count=attempt_index + 1,
                        fallback_used=attempt_index > 0,
                        fallback_stage="infer",
                        fallback_reason="cancelled",
                        error=exc,
                        source="client",
                    )
                    self._record_model_execution(metadata)
                    raise
                if attempt_index == 0 and len(candidates) > 1 and _is_fallback_eligible_exception(exc):
                    metadata = _build_model_execution_metadata(
                        selection,
                        used_model_id=candidate_model_id,
                        status="failed",
                        attempt_count=attempt_index + 1,
                        fallback_used=False,
                        fallback_stage="infer",
                        fallback_reason="primary_model_failed",
                        error=exc,
                        source="client",
                    )
                    self._record_model_execution(metadata)
                    _emit_fallback_metric(
                        component="ums_client",
                        fallback="async_infer_model_failover",
                        stage="infer",
                        primary_model_id=candidates[0],
                        fallback_model_id=candidates[1],
                    )
                    logger.warning(
                        "UMS client async failover primary=%s fallback=%s: %s",
                        candidates[0],
                        candidates[1],
                        exc,
                    )
                    continue
                metadata = _build_model_execution_metadata(
                    selection,
                    used_model_id=candidate_model_id,
                    status="failed",
                    attempt_count=attempt_index + 1,
                    fallback_used=attempt_index > 0,
                    fallback_stage="infer",
                    fallback_reason="fallback_failed" if attempt_index > 0 else "primary_failed",
                    error=exc,
                    source="client",
                )
                self._record_model_execution(metadata)
                break

        raise RuntimeError(
            f"Failed to connect to UMS after {len(candidates)} model attempts: {last_error}"
        )

    async def async_infer_stream(self, model_id: str, payload: Dict[str, Any], device_mode: str = "hybrid") -> AsyncGenerator[str, None]:
        selection = _resolve_execution_plan_for_model(model_id)
        candidates = self._model_candidates(selection)
        url = f"{self.base_url}/infer"
        last_error: Optional[Exception] = None

        for attempt_index, candidate_model_id in enumerate(candidates):
            request_body = {
                "model_id": candidate_model_id,
                "payload": payload,
                "device_mode": device_mode,
                "priority": "normal",
                "stream": True,
            }
            print(f"[UMS_CLIENT] Stream inference for model: {candidate_model_id}")
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
            emitted_any = False
            stream_error: Optional[Exception] = None

            async def producer() -> None:
                client = await _get_async_client()
                try:
                    async with client.stream("POST", url, json=request_body) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            stripped = line.strip()
                            if stripped == "data: [DONE]":
                                return
                            text = _extract_sse_text(line)
                            if text:
                                await queue.put(("token", text))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await queue.put(("error", exc))
                finally:
                    await queue.put(("done", None))

            producer_task = asyncio.create_task(producer(), name=f"ums-stream:{candidate_model_id}")
            try:
                while True:
                    kind, value = await queue.get()
                    if kind == "token":
                        emitted_any = True
                        yield value
                        continue
                    if kind == "error":
                        stream_error = value
                        break
                    if kind == "done":
                        stream_error = None
                        break
            finally:
                if not producer_task.done():
                    producer_task.cancel()
                with suppress(asyncio.CancelledError):
                    await producer_task

            if stream_error is None:
                metadata = _build_model_execution_metadata(
                    selection,
                    used_model_id=candidate_model_id,
                    status="success",
                    attempt_count=attempt_index + 1,
                    fallback_used=attempt_index > 0,
                    fallback_stage="stream",
                    fallback_reason=None if attempt_index == 0 else "primary_model_failed",
                    source="client",
                )
                self._record_model_execution(metadata)
                return

            last_error = stream_error
            metadata = _build_model_execution_metadata(
                selection,
                used_model_id=candidate_model_id,
                status="failed",
                attempt_count=attempt_index + 1,
                fallback_used=attempt_index > 0,
                fallback_stage="stream",
                fallback_reason="stream_started" if emitted_any else "primary_model_failed",
                error=stream_error,
                source="client",
            )
            self._record_model_execution(metadata)
            _emit_fallback_metric(
                component="ums_client",
                fallback="stream_error",
                stage="stream",
                primary_model_id=candidates[0],
                fallback_model_id=candidates[1] if len(candidates) > 1 else candidate_model_id,
            )

            if emitted_any or _is_busy_exception(stream_error) or _is_cancelled_exception(stream_error):
                if _is_busy_exception(stream_error):
                    raise UMSBusyError(f"Failed to stream from model {candidate_model_id}: {stream_error}") from stream_error
                raise stream_error

            if attempt_index == 0 and len(candidates) > 1 and _is_fallback_eligible_exception(stream_error):
                _emit_fallback_metric(
                    component="ums_client",
                    fallback="stream_model_failover",
                    stage="stream",
                    primary_model_id=candidates[0],
                    fallback_model_id=candidates[1],
                )
                logger.warning(
                    "UMS client stream failover primary=%s fallback=%s: %s",
                    candidates[0],
                    candidates[1],
                    stream_error,
                )
                continue

            raise stream_error

        if last_error is None:
            return
        raise last_error

    async def async_infer_stream_to_callback(
        self,
        model_id: str,
        payload: Dict[str, Any],
        on_token: Callable[[str], Awaitable[None] | None],
        device_mode: str = "hybrid",
    ) -> None:
        """
        Асинхронный стриминг инференса через callback без промежуточного async-generator
        на стороне вызывающего кода. Это снижает риск GeneratorExit/cancel-scope конфликтов
        в UI-интеграциях, которые рано завершают consumer task.
        """
        async for text in self.async_infer_stream(model_id, payload, device_mode=device_mode):
            result = on_token(text)
            if inspect.isawaitable(result):
                await result

    def switch_model(self, model_id: str, device_mode: str = "hybrid") -> Dict[str, Any]:
        """Переключает активную модель на UMS."""
        url = f"{self.base_url}/switch_model"
        
        request_body = {
            "model_id": model_id,
            "device_mode": device_mode
        }
        
        try:
            print(f"[UMS_CLIENT] Switching to model: {model_id}")
            response = requests.post(url, json=request_body, timeout=UMS_SWITCH_MODEL_TIMEOUT_S)
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.RequestException as e:
            print(f"[UMS_CLIENT] Error switching model: {e}")
            raise RuntimeError(f"Failed to switch model: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Получает статус UMS."""
        url = f"{self.base_url}/status"
        
        try:
            response = requests.get(url, timeout=UMS_STATUS_TIMEOUT_S)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[UMS_CLIENT] Error getting status: {e}")
            return {"error": str(e)}

# Глобальный экземпляр клиента
ums_client = UMSClient()

# ============================================================================
# Вспомогательные функции для MCP-серверов
# ============================================================================

def generate_text_via_ums(prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
    """
    Генерирует текст через UMS, используя env-resolved LLM.
    
    Используется MCP Legal Server для анализа.
    """
    payload = {
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.9
    }
    
    try:
        selection = _resolve_default_llm_selection()
        response = ums_client.infer(selection.resolved_model_id, payload, device_mode="hybrid")
        
        # Парсим ответ от llama-server
        if "choices" in response:
            return response["choices"][0].get("text", "")
        elif "content" in response:
            return response["content"]
        else:
            return str(response)
    
    except Exception as e:
        print(f"[UMS_CLIENT] Error generating text: {e}")
        raise

def get_embeddings_via_ums(
    text: str,
    normalize: bool = True,
    *,
    model_id: Optional[str] = None,
) -> List[float]:
    """
    Получает эмбеддинги текста через UMS.
    
    По умолчанию используется env-resolved retrieval/legal embedder.
    """
    payload = {
        "input": text,
        "normalize": normalize
    }
    
    try:
        selection = _resolve_default_retrieval_embedder_selection() if model_id is None else _resolve_execution_plan_for_model(model_id)
        response = ums_client.infer(
            selection.resolved_model_id,
            payload,
            device_mode="cpu",
        )
        
        # Парсим ответ от llama-server
        if "data" in response:
            return response["data"][0].get("embedding", [])
        elif "embedding" in response:
            return response["embedding"]
        else:
            return []
    
    except Exception as e:
        print(f"[UMS_CLIENT] Error getting embeddings: {e}")
        raise

def create_ums_embed_fn(
    base_url: str = None,
    *,
    model_id: Optional[str] = None,
) -> Optional[Callable]:
    """
    Фабрика embed_fn для AdaptiveRAGPipeline.

    Возвращает функцию (List[str]) -> np.ndarray или None если UMS недоступен.
    Синхронный requests.post — вызывается изнутри sync кода HybridRetriever.
    Retry с exponential backoff при ошибках.
    """
    url = (base_url or UMS_URL).rstrip("/") + "/v1/embeddings"
    selection = _resolve_default_retrieval_embedder_selection() if model_id is None else _resolve_execution_plan_for_model(model_id)
    candidates = _selection_model_candidates(selection)
    last_model_execution: Optional[Dict[str, Any]] = None

    def _post_embeddings(
        texts: List[str],
        candidate_model_id: str,
        *,
        timeout_s: float,
        connect_timeout_s: Optional[float] = None,
    ) -> np.ndarray:
        request_body = {"input": texts, "model": candidate_model_id}
        request_timeout: Any = timeout_s if connect_timeout_s is None else (connect_timeout_s, timeout_s)
        response = requests.post(url, json=request_body, timeout=request_timeout)
        response.raise_for_status()
        data = response.json().get("data", [])
        data.sort(key=lambda item: item.get("index", 0))
        return np.array([item["embedding"] for item in data], dtype=np.float32)

    def _execute_embeddings_with_failover(
        texts: List[str],
        *,
        stage: str,
        timeout_s: float,
        connect_timeout_s: Optional[float] = None,
    ) -> np.ndarray:
        nonlocal last_model_execution
        last_error: Optional[Exception] = None
        for attempt_index, candidate_model_id in enumerate(candidates):
            for batch_attempt in range(UMS_EMBED_BATCH_RETRIES):
                try:
                    embeddings = _post_embeddings(
                        texts,
                        candidate_model_id,
                        timeout_s=timeout_s,
                        connect_timeout_s=connect_timeout_s,
                    )
                    metadata = _build_model_execution_metadata(
                        selection,
                        used_model_id=candidate_model_id,
                        status="success",
                        attempt_count=attempt_index + 1,
                        fallback_used=attempt_index > 0,
                        fallback_stage=stage,
                        fallback_reason=None if attempt_index == 0 else "primary_model_failed",
                        source="client",
                    )
                    last_model_execution = metadata
                    ums_client.last_model_execution = metadata
                    return embeddings
                except requests.exceptions.HTTPError as exc:
                    last_error = exc
                    if _status_code_from_exception(exc) == BUSY_STATUS_CODE:
                        metadata = _build_model_execution_metadata(
                            selection,
                            used_model_id=candidate_model_id,
                            status="failed",
                            attempt_count=attempt_index + 1,
                            fallback_used=attempt_index > 0,
                            fallback_stage=stage,
                            fallback_reason="busy",
                            error=exc,
                            source="client",
                        )
                        last_model_execution = metadata
                        ums_client.last_model_execution = metadata
                        raise UMSBusyError(f"UMS is busy for model {candidate_model_id}: {exc}") from exc
                    if batch_attempt < UMS_EMBED_BATCH_RETRIES - 1 and _is_retryable_sync_exception(exc):
                        time.sleep(UMS_EMBED_BATCH_RETRY_DELAY_S)
                        continue
                    break
                except requests.exceptions.RequestException as exc:
                    last_error = exc
                    if batch_attempt < UMS_EMBED_BATCH_RETRIES - 1 and _is_retryable_sync_exception(exc):
                        time.sleep(UMS_EMBED_BATCH_RETRY_DELAY_S)
                        continue
                    break
                except Exception as exc:
                    last_error = exc
                    break

            metadata = _build_model_execution_metadata(
                selection,
                used_model_id=candidate_model_id,
                status="failed",
                attempt_count=attempt_index + 1,
                fallback_used=attempt_index > 0,
                fallback_stage=stage,
                fallback_reason="primary_model_failed" if attempt_index == 0 else "fallback_failed",
                error=last_error or "unknown error",
                source="client",
            )
            last_model_execution = metadata
            ums_client.last_model_execution = metadata
            if attempt_index == 0 and len(candidates) > 1 and last_error is not None and _is_fallback_eligible_exception(last_error):
                _emit_fallback_metric(
                    component="ums_client",
                    fallback="embedding_model_failover",
                    stage=stage,
                    primary_model_id=candidates[0],
                    fallback_model_id=candidates[1],
                )
                logger.warning(
                    "UMS client embedding failover primary=%s fallback=%s: %s",
                    candidates[0],
                    candidates[1],
                    last_error,
                )
                continue
            break

        if last_error is None:
            raise RuntimeError("Embedding execution failed without exception")
        raise last_error

    def embed_fn(texts: List[str]) -> np.ndarray:
        """Синхронная обертка с батчингом (TD-Batching)."""
        if not texts:
            return np.array([], dtype=np.float32)

        try:
            all_embeddings: List[np.ndarray] = []
            for i in range(0, len(texts), UMS_EMBED_BATCH_SIZE):
                batch = texts[i : i + UMS_EMBED_BATCH_SIZE]
                batch_embeddings = _execute_embeddings_with_failover(
                    batch,
                    stage="batch",
                    timeout_s=UMS_EMBED_BATCH_TIMEOUT_S,
                    connect_timeout_s=UMS_EMBED_BATCH_CONNECT_TIMEOUT_S,
                )
                all_embeddings.append(batch_embeddings)
            result = np.vstack(all_embeddings) if all_embeddings else np.array([], dtype=np.float32)
            embed_fn.last_model_execution = last_model_execution  # type: ignore[attr-defined]
            return result
        except Exception as e:
            print(f"[UMS_CLIENT] Critical embedding error: {e}")
            raise RuntimeError(f"embed_fn failed: {e}")

    try:
        _execute_embeddings_with_failover(
            ["test"],
            stage="probe",
            timeout_s=UMS_EMBED_PROBE_TIMEOUT_S,
        )
    except Exception:
        return None

    embed_fn.last_model_execution = last_model_execution  # type: ignore[attr-defined]
    return embed_fn


def process_vision_via_ums(image_path: str, prompt: str) -> str:
    """
    Обрабатывает изображение через UMS, используя Vision-модель (Qwen-VL).
    
    Используется MCP Document Server для OCR и анализа изображений.
    """
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_path}},
                    {"type": "text", "text": prompt}
                ]
            }
        ]
    }
    
    try:
        selection = _resolve_execution_plan_for_model("qwen-vl-8b")
        response = ums_client.infer(selection.resolved_model_id, payload, device_mode="hybrid")
        
        # Парсим ответ от llama-server
        if "choices" in response:
            return response["choices"][0].get("message", {}).get("content", "")
        else:
            return str(response)
    
    except Exception as e:
        print(f"[UMS_CLIENT] Error processing vision: {e}")
        raise
