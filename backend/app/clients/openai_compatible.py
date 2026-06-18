from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

import httpx


class ConfigurationError(RuntimeError):
    """Ошибка настройки внешнего provider модели."""


@dataclass(frozen=True)
class OpenAICompatibleClientConfig:
    base_url: str
    api_key: str
    model_id: str


def _clean(value: Optional[str]) -> str:
    return str(value or "").strip()


def _read_env(env: Optional[Mapping[str, str]], name: str) -> str:
    source = os.environ if env is None else env
    return _clean(source.get(name))


def resolve_model_id(
    *,
    current_model_id: Optional[str],
    fallback_model_id: Optional[str],
) -> str:
    selected_model_id = _clean(current_model_id)
    if selected_model_id:
        return selected_model_id

    fallback = _clean(fallback_model_id)
    if fallback:
        return fallback

    raise ConfigurationError("Не настроена LLM-модель: передайте current_model_id или задайте LLM_MODEL_ID.")


def build_chat_client_config(
    *,
    current_model_id: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> OpenAICompatibleClientConfig:
    base_url = _read_env(env, "LLM_BASE_URL")
    if not base_url:
        raise ConfigurationError("Не настроен LLM_BASE_URL.")

    return OpenAICompatibleClientConfig(
        base_url=base_url.rstrip("/"),
        api_key=_read_env(env, "LLM_API_KEY") or "none",
        model_id=resolve_model_id(
            current_model_id=current_model_id,
            fallback_model_id=_read_env(env, "LLM_MODEL_ID"),
        ),
    )


def _extract_chat_message_content(payload: Mapping[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""

    message = first_choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content

    text = first_choice.get("text")
    if isinstance(text, str):
        return text

    return ""


async def chat_completion(
    *,
    prompt: str,
    current_model_id: Optional[str] = None,
    system_prompt: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> str:
    config = build_chat_client_config(current_model_id=current_model_id)
    messages = []
    if _clean(system_prompt):
        messages.append({"role": "system", "content": _clean(system_prompt)})
    messages.append({"role": "user", "content": prompt})

    client_kwargs = {
        "base_url": config.base_url,
        "headers": {"Authorization": f"Bearer {config.api_key}"},
        "timeout": httpx.Timeout(120.0, connect=10.0),
    }
    if transport is not None:
        client_kwargs["transport"] = transport

    async with httpx.AsyncClient(**client_kwargs) as client:
        response = await client.post(
            "/chat/completions",
            json={
                "model": config.model_id,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        return _extract_chat_message_content(response.json())
