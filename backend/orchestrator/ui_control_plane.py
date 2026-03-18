import copy
import os
from typing import Any, Dict, Mapping, Optional

from services.model_manager.model_selection import resolve_model_selection

ASSISTANT_MODE_ITEMS: Dict[str, str] = {
    "general_chat": "General Chat",
    "coding": "Coding Assistant",
    "agentic": "Agentic (iterative)",
    "specific_tasks": "Specific Tasks",
    "rag_qa": "RAG Q&A",
}

RUNTIME_MODE_ITEMS: Dict[str, str] = {
    "auto": "Авто: backend сам выбирает маршрут",
    "chat_only": "Общий чат: без workflow",
    "specialized_tasks": "Агентный режим: приоритет task-routing",
}

RAG_SCOPE_ITEMS: Dict[str, str] = {
    "off": "Off",
    "session_rag": "Session RAG",
    "knowledge_base_rag": "Knowledge Base + Session Overlay",
}

MODEL_PROFILE_ITEMS: Dict[str, str] = {
    "default-chat": "Default Chat",
    "long-context": "Long Context",
    "legal-compare": "Legal Compare",
    "low-vram": "Low VRAM",
}

MODEL_PROFILE_ALIASES: Dict[str, str] = {
    "coder": "long-context",
    "agentic": "long-context",
    "analyst": "legal-compare",
}

MODEL_PROFILE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "default-chat": {
        "label": "Default Chat",
        "model_role_key": "llm.default_chat",
        "device_mode": "auto",
        "context_budget_profile": "standard",
        "generation": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 2048},
    },
    "long-context": {
        "label": "Long Context",
        "model_role_key": "llm.long_context",
        "device_mode": "prefer-gpu",
        "context_budget_profile": "long-context",
        "generation": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 3072},
    },
    "legal-compare": {
        "label": "Legal Compare",
        "model_role_key": "llm.legal_compare",
        "device_mode": "prefer-gpu",
        "context_budget_profile": "legal-compare",
        "generation": {"temperature": 0.2, "top_p": 0.8, "max_tokens": 2048},
    },
    "low-vram": {
        "label": "Low VRAM",
        "model_role_key": "llm.low_vram",
        "device_mode": "low-vram",
        "context_budget_profile": "compact",
        "generation": {"temperature": 0.2, "top_p": 0.8, "max_tokens": 1024},
    },
}

MODEL_PROFILE_ENV_PREFIXES: Dict[str, str] = {
    "default-chat": "CHAINLIT_MODEL_PROFILE_DEFAULT_CHAT",
    "long-context": "CHAINLIT_MODEL_PROFILE_LONG_CONTEXT",
    "legal-compare": "CHAINLIT_MODEL_PROFILE_LEGAL_COMPARE",
    "low-vram": "CHAINLIT_MODEL_PROFILE_LOW_VRAM",
}

INTENT_EMBEDDER_PROFILE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "intent-default": {
        "model_role_key": "embedder.intent.default",
    },
}

RETRIEVAL_EMBEDDER_PROFILE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "legal-default": {
        "model_role_key": "embedder.retrieval.legal_default",
    },
    "low-vram": {
        "model_role_key": "embedder.retrieval.low_vram",
    },
}

PROMPT_PROFILE_ITEMS: Dict[str, str] = {
    "default-assistant": "Default Assistant",
    "coding-assistant": "Coding Assistant",
    "tool-using-agent": "Tool-Using Agent",
    "task-router": "Task Router",
    "strict-grounded-doc-qa": "Strict Grounded Doc QA",
}

TOOL_SCOPE_ITEMS: Dict[str, str] = {
    "chat": "Chat",
    "coding": "Coding",
    "agentic": "Agentic (iterative)",
    "domain_tasks": "Domain Tasks",
    "document_qa": "Document QA",
}

PROMPT_PROFILE_SYSTEM_MESSAGES: Dict[str, str] = {
    "default-assistant": (
        "Ты — Agent Navigator. Отвечай естественно, кратко и по делу, как универсальный чат-ассистент. "
        "Если пользователь общается в общем чате (приветствие, small talk, общие вопросы), "
        "не навязывай загрузку документов и не перечисляй специализацию без запроса.\n\n"
        "Когда пользователь явно просит анализ/сравнение/поиск по документам, переходи в профильную роль:\n"
        "- Сравнение двух документов (договоров, технических заданий, редакций) с выявлением изменений\n"
        "- Анализ соответствия сметы или КП техническому заданию (ТЗ vs смета/КП)\n"
        "- Ответы на вопросы по содержимому загруженных документов\n"
        "- Поиск конкретных условий, цифр и требований в документах\n\n"
        "Не выдумывай факты и не заявляй о доступе к интернету/новостям/курсам/погоде, если такого доступа нет."
    ),
    "coding-assistant": (
        "Ты code assistant Agent Navigator. Отвечай конкретно, показывай работающие решения, "
        "объясняй компромиссы кратко и не придумывай факты о кодовой базе, если не видел контекст."
    ),
    "tool-using-agent": (
        "Ты агентный ассистент Agent Navigator. Строй ответы как план действий, явно отмечай ограничения, "
        "не скрывай неопределённость и не придумывай результаты вызова инструментов."
    ),
    "task-router": (
        "Ты task-oriented ассистент Agent Navigator. Приоритет — практические задачи по документам, "
        "анализу и сравнению. Если контекста недостаточно, говори, что именно нужно для уверенного вывода."
    ),
    "strict-grounded-doc-qa": (
        "Ты grounded document QA ассистент Agent Navigator. Отвечай только по подтверждённым данным из источников, "
        "не выдумывай факты и явно отмечай ограничения доказательной базы."
    ),
}

DEFAULT_GENERATION = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 2048,
}

GENERATION_LIMITS = {
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "max_tokens": (1, 4096),
}

CONTROL_PLANE_HARD_DEFAULTS: Dict[str, Any] = {
    "assistant_mode": "general_chat",
    "runtime_mode": "auto",
    "rag_scope": "off",
    "knowledge_collection_id": None,
    "model_profile": "default-chat",
    "prompt_profile": "default-assistant",
    "tool_scope": "chat",
    "custom_system_prompt": None,
    "generation": copy.deepcopy(DEFAULT_GENERATION),
    "device_mode": "auto",
    "context_budget_profile": "standard",
    "profile_generation_defaults": copy.deepcopy(DEFAULT_GENERATION),
    "intent_embedder_profile": "intent-default",
    "retrieval_embedder_profile": "legal-default",
}

ASSISTANT_MODE_PRESETS: Dict[str, Dict[str, Any]] = {
    "general_chat": {
        "assistant_mode": "general_chat",
        "runtime_mode": "chat_only",
        "rag_scope": "off",
        "model_profile": "default-chat",
        "prompt_profile": "default-assistant",
        "tool_scope": "chat",
        "generation": copy.deepcopy(DEFAULT_GENERATION),
    },
    "coding": {
        "assistant_mode": "coding",
        "runtime_mode": "chat_only",
        "rag_scope": "off",
        "model_profile": "long-context",
        "prompt_profile": "coding-assistant",
        "tool_scope": "coding",
        "generation": copy.deepcopy(MODEL_PROFILE_DEFINITIONS["long-context"]["generation"]),
    },
    "agentic": {
        "assistant_mode": "agentic",
        "runtime_mode": "specialized_tasks",
        "rag_scope": "off",
        "model_profile": "long-context",
        "prompt_profile": "tool-using-agent",
        "tool_scope": "agentic",
        "generation": copy.deepcopy(MODEL_PROFILE_DEFINITIONS["long-context"]["generation"]),
    },
    "specific_tasks": {
        "assistant_mode": "specific_tasks",
        "runtime_mode": "specialized_tasks",
        "rag_scope": "session_rag",
        "model_profile": "legal-compare",
        "prompt_profile": "task-router",
        "tool_scope": "domain_tasks",
        "generation": copy.deepcopy(MODEL_PROFILE_DEFINITIONS["legal-compare"]["generation"]),
    },
    "rag_qa": {
        "assistant_mode": "rag_qa",
        "runtime_mode": "specialized_tasks",
        "rag_scope": "knowledge_base_rag",
        "model_profile": "legal-compare",
        "prompt_profile": "strict-grounded-doc-qa",
        "tool_scope": "document_qa",
        "generation": copy.deepcopy(MODEL_PROFILE_DEFINITIONS["legal-compare"]["generation"]),
    },
}

ASSISTANT_MODE_ENV_PREFIXES: Dict[str, str] = {
    "general_chat": "CHAINLIT_GENERAL_CHAT",
    "coding": "CHAINLIT_CODING",
    "agentic": "CHAINLIT_AGENTIC",
    "specific_tasks": "CHAINLIT_SPECIFIC_TASKS",
    "rag_qa": "CHAINLIT_RAG_QA",
}


def _normalize_choice(value: Any, allowed: Mapping[str, Any]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate in allowed:
        return candidate
    return None


def _normalize_model_profile(value: Any) -> Optional[str]:
    normalized = _normalize_choice(value, MODEL_PROFILE_ITEMS)
    if normalized is not None:
        return normalized
    alias = _normalize_text(value)
    if alias is None:
        return None
    return MODEL_PROFILE_ALIASES.get(alias)


def _normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _read_float_env(*names: str, default: float) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return default


def _read_int_env(*names: str, default: int) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return default


def get_generation_defaults(assistant_mode: Optional[str] = None) -> Dict[str, Any]:
    assistant_mode_defaults = {
        "general_chat": DEFAULT_GENERATION,
        "coding": {"temperature": 0.2, "top_p": 0.9, "max_tokens": 2048},
        "agentic": {"temperature": 0.2, "top_p": 0.9, "max_tokens": 2048},
        "specific_tasks": {"temperature": 0.2, "top_p": 0.8, "max_tokens": 2048},
        "rag_qa": {"temperature": 0.2, "top_p": 0.8, "max_tokens": 2048},
    }
    defaults = assistant_mode_defaults.get(assistant_mode or "general_chat", DEFAULT_GENERATION)
    prefix = ASSISTANT_MODE_ENV_PREFIXES.get(assistant_mode or "", "")
    return {
        "temperature": _read_float_env(
            f"{prefix}_TEMPERATURE" if prefix else "",
            "CHAINLIT_DEFAULT_TEMPERATURE",
            "TEMPERATURE",
            default=float(defaults["temperature"]),
        ),
        "top_p": _read_float_env(
            f"{prefix}_TOP_P" if prefix else "",
            "CHAINLIT_DEFAULT_TOP_P",
            "TOP_P",
            default=float(defaults["top_p"]),
        ),
        "max_tokens": _read_int_env(
            f"{prefix}_MAX_TOKENS" if prefix else "",
            "CHAINLIT_DEFAULT_MAX_TOKENS",
            "MAX_TOKENS",
            default=int(defaults["max_tokens"]),
        ),
    }


def get_model_profile_generation_defaults(model_profile: Optional[str]) -> Dict[str, Any]:
    normalized = _normalize_model_profile(model_profile) or CONTROL_PLANE_HARD_DEFAULTS["model_profile"]
    definition = MODEL_PROFILE_DEFINITIONS[normalized]
    defaults = definition["generation"]
    prefix = MODEL_PROFILE_ENV_PREFIXES[normalized]
    return {
        "temperature": _read_float_env(
            f"{prefix}_TEMPERATURE",
            "CHAINLIT_DEFAULT_TEMPERATURE" if normalized == "default-chat" else "",
            default=float(defaults["temperature"]),
        ),
        "top_p": _read_float_env(
            f"{prefix}_TOP_P",
            "CHAINLIT_DEFAULT_TOP_P" if normalized == "default-chat" else "",
            default=float(defaults["top_p"]),
        ),
        "max_tokens": _read_int_env(
            f"{prefix}_MAX_TOKENS",
            "CHAINLIT_DEFAULT_MAX_TOKENS" if normalized == "default-chat" else "",
            default=int(defaults["max_tokens"]),
        ),
    }


def clamp_generation_overrides(overrides: Optional[Mapping[str, Any]], base: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    effective = copy.deepcopy(base or DEFAULT_GENERATION)
    if not overrides:
        return effective

    if "temperature" in overrides and overrides.get("temperature") is not None:
        raw = float(overrides["temperature"])
        lo, hi = GENERATION_LIMITS["temperature"]
        effective["temperature"] = max(lo, min(hi, raw))

    if "top_p" in overrides and overrides.get("top_p") is not None:
        raw = float(overrides["top_p"])
        lo, hi = GENERATION_LIMITS["top_p"]
        effective["top_p"] = max(lo, min(hi, raw))

    if "max_tokens" in overrides and overrides.get("max_tokens") is not None:
        raw = int(overrides["max_tokens"])
        lo, hi = GENERATION_LIMITS["max_tokens"]
        effective["max_tokens"] = max(lo, min(hi, raw))

    return effective


def build_preset_state(assistant_mode: str) -> Dict[str, Any]:
    preset = ASSISTANT_MODE_PRESETS[assistant_mode]
    model_profile = _normalize_model_profile(preset["model_profile"]) or CONTROL_PLANE_HARD_DEFAULTS["model_profile"]
    generation_defaults = get_model_profile_generation_defaults(model_profile)
    return {
        "assistant_mode": preset["assistant_mode"],
        "runtime_mode": preset["runtime_mode"],
        "rag_scope": preset["rag_scope"],
        "model_profile": model_profile,
        "prompt_profile": preset["prompt_profile"],
        "tool_scope": preset["tool_scope"],
        "generation_overrides": generation_defaults,
    }


def build_initial_control_plane_state(runtime_mode: Optional[str] = None) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    normalized_runtime_mode = _normalize_choice(runtime_mode, RUNTIME_MODE_ITEMS)
    if normalized_runtime_mode is not None:
        state["runtime_mode"] = normalized_runtime_mode
    return state


def merge_control_plane_state(
    base_state: Optional[Mapping[str, Any]],
    settings: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    merged = copy.deepcopy(dict(base_state or {}))
    payload = dict(settings or {})

    assistant_mode = _normalize_choice(payload.get("assistant_mode"), ASSISTANT_MODE_ITEMS)
    if assistant_mode is not None:
        merged = build_preset_state(assistant_mode)

    for key, allowed in (
        ("assistant_mode", ASSISTANT_MODE_ITEMS),
        ("runtime_mode", RUNTIME_MODE_ITEMS),
        ("rag_scope", RAG_SCOPE_ITEMS),
        ("prompt_profile", PROMPT_PROFILE_ITEMS),
        ("tool_scope", TOOL_SCOPE_ITEMS),
    ):
        normalized = _normalize_choice(payload.get(key), allowed)
        if normalized is not None:
            merged[key] = normalized
    model_profile = _normalize_model_profile(payload.get("model_profile"))
    if model_profile is not None:
        merged["model_profile"] = model_profile

    knowledge_collection_id = _normalize_text(payload.get("knowledge_collection_id"))
    if "knowledge_collection_id" in payload:
        merged["knowledge_collection_id"] = knowledge_collection_id

    if "custom_system_prompt" in payload:
        merged["custom_system_prompt"] = _normalize_text(payload.get("custom_system_prompt"))

    generation_overrides = copy.deepcopy(merged.get("generation_overrides") or {})
    for key in ("temperature", "top_p", "max_tokens"):
        if key in payload and payload.get(key) is not None:
            generation_overrides[key] = payload.get(key)
    if generation_overrides:
        merged["generation_overrides"] = generation_overrides

    return merged


def resolve_effective_settings(
    state: Optional[Mapping[str, Any]],
    enforced_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    raw_state = dict(state or {})
    effective = copy.deepcopy(CONTROL_PLANE_HARD_DEFAULTS)
    effective["generation"] = get_generation_defaults()

    assistant_mode = _normalize_choice(raw_state.get("assistant_mode"), ASSISTANT_MODE_ITEMS)
    if assistant_mode is not None:
        preset = ASSISTANT_MODE_PRESETS[assistant_mode]
        effective.update(
            {
                "assistant_mode": preset["assistant_mode"],
                "runtime_mode": preset["runtime_mode"],
                "rag_scope": preset["rag_scope"],
                "model_profile": preset["model_profile"],
                "prompt_profile": preset["prompt_profile"],
                "tool_scope": preset["tool_scope"],
            }
        )
        effective["generation"] = copy.deepcopy(MODEL_PROFILE_DEFINITIONS[preset["model_profile"]]["generation"])

    for key, allowed in (
        ("runtime_mode", RUNTIME_MODE_ITEMS),
        ("rag_scope", RAG_SCOPE_ITEMS),
        ("prompt_profile", PROMPT_PROFILE_ITEMS),
        ("tool_scope", TOOL_SCOPE_ITEMS),
    ):
        normalized = _normalize_choice(raw_state.get(key), allowed)
        if normalized is not None:
            effective[key] = normalized
    model_profile = _normalize_model_profile(raw_state.get("model_profile"))
    if model_profile is not None:
        effective["model_profile"] = model_profile

    profile_definition = MODEL_PROFILE_DEFINITIONS[effective["model_profile"]]
    effective["generation"] = get_model_profile_generation_defaults(effective["model_profile"])
    effective["device_mode"] = profile_definition["device_mode"]
    effective["context_budget_profile"] = profile_definition["context_budget_profile"]
    effective["profile_generation_defaults"] = get_model_profile_generation_defaults(effective["model_profile"])
    effective["intent_embedder_profile"] = "intent-default"
    effective["retrieval_embedder_profile"] = "low-vram" if effective["model_profile"] == "low-vram" else "legal-default"

    knowledge_collection_id = _normalize_text(raw_state.get("knowledge_collection_id"))
    if knowledge_collection_id is not None:
        effective["knowledge_collection_id"] = knowledge_collection_id

    effective["custom_system_prompt"] = _normalize_text(raw_state.get("custom_system_prompt"))
    effective["generation"] = clamp_generation_overrides(
        raw_state.get("generation_overrides"),
        base=effective.get("generation"),
    )

    if enforced_overrides:
        forced = dict(enforced_overrides)
        if "runtime_mode" in forced:
            normalized = _normalize_choice(forced.get("runtime_mode"), RUNTIME_MODE_ITEMS)
            if normalized is not None:
                effective["runtime_mode"] = normalized
        if "rag_scope" in forced:
            normalized = _normalize_choice(forced.get("rag_scope"), RAG_SCOPE_ITEMS)
            if normalized is not None:
                effective["rag_scope"] = normalized
        if "model_profile" in forced:
            normalized = _normalize_model_profile(forced.get("model_profile"))
            if normalized is not None:
                effective["model_profile"] = normalized
        if "prompt_profile" in forced:
            normalized = _normalize_choice(forced.get("prompt_profile"), PROMPT_PROFILE_ITEMS)
            if normalized is not None:
                effective["prompt_profile"] = normalized
        if "tool_scope" in forced:
            normalized = _normalize_choice(forced.get("tool_scope"), TOOL_SCOPE_ITEMS)
            if normalized is not None:
                effective["tool_scope"] = normalized
        if "knowledge_collection_id" in forced:
            effective["knowledge_collection_id"] = _normalize_text(forced.get("knowledge_collection_id"))
        if "custom_system_prompt" in forced:
            effective["custom_system_prompt"] = _normalize_text(forced.get("custom_system_prompt"))
        if "generation_overrides" in forced:
            effective["generation"] = clamp_generation_overrides(
                forced.get("generation_overrides"),
                base=effective.get("generation"),
            )

    profile_definition = MODEL_PROFILE_DEFINITIONS[effective["model_profile"]]
    effective["device_mode"] = profile_definition["device_mode"]
    effective["context_budget_profile"] = profile_definition["context_budget_profile"]
    effective["profile_generation_defaults"] = get_model_profile_generation_defaults(effective["model_profile"])
    model_selection = resolve_model_id_selection(effective.get("model_profile"))
    intent_selection = resolve_intent_embedder_model_selection(effective.get("intent_embedder_profile"))
    retrieval_selection = resolve_retrieval_embedder_model_selection(effective.get("retrieval_embedder_profile"))
    effective["resolved_model_id"] = model_selection["resolved_model_id"]
    effective["resolved_model_resolution"] = model_selection
    effective["resolved_intent_embedder_model_id"] = intent_selection["resolved_model_id"]
    effective["resolved_intent_embedder_resolution"] = intent_selection
    effective["resolved_retrieval_embedder_model_id"] = retrieval_selection["resolved_model_id"]
    effective["resolved_retrieval_embedder_resolution"] = retrieval_selection
    return effective


def get_prompt_profile_system_message(prompt_profile: Optional[str]) -> str:
    normalized = _normalize_choice(prompt_profile, PROMPT_PROFILE_ITEMS) or CONTROL_PLANE_HARD_DEFAULTS["prompt_profile"]
    return PROMPT_PROFILE_SYSTEM_MESSAGES[normalized]


def resolve_model_id(model_profile: Optional[str]) -> str:
    return resolve_model_id_selection(model_profile)["resolved_model_id"]


def resolve_model_id_selection(model_profile: Optional[str]) -> Dict[str, Any]:
    normalized = _normalize_model_profile(model_profile) or CONTROL_PLANE_HARD_DEFAULTS["model_profile"]
    definition = MODEL_PROFILE_DEFINITIONS[normalized]
    return resolve_model_selection(definition["model_role_key"]).to_dict()


def normalize_inference_device_mode(device_mode: Optional[str]) -> str:
    normalized = _normalize_text(device_mode)
    if normalized in {"cpu", "gpu", "hybrid"}:
        return normalized
    if normalized == "prefer-gpu":
        return "gpu"
    if normalized == "low-vram":
        return "cpu"
    return "hybrid"


def resolve_intent_embedder_model_id(intent_embedder_profile: Optional[str]) -> str:
    return resolve_intent_embedder_model_selection(intent_embedder_profile)["resolved_model_id"]


def resolve_intent_embedder_model_selection(intent_embedder_profile: Optional[str]) -> Dict[str, Any]:
    normalized = str(intent_embedder_profile or CONTROL_PLANE_HARD_DEFAULTS["intent_embedder_profile"]).strip()
    definition = INTENT_EMBEDDER_PROFILE_DEFINITIONS.get(
        normalized,
        INTENT_EMBEDDER_PROFILE_DEFINITIONS[CONTROL_PLANE_HARD_DEFAULTS["intent_embedder_profile"]],
    )
    return resolve_model_selection(definition["model_role_key"]).to_dict()


def resolve_retrieval_embedder_model_id(retrieval_embedder_profile: Optional[str]) -> str:
    return resolve_retrieval_embedder_model_selection(retrieval_embedder_profile)["resolved_model_id"]


def resolve_retrieval_embedder_model_selection(retrieval_embedder_profile: Optional[str]) -> Dict[str, Any]:
    normalized = str(retrieval_embedder_profile or CONTROL_PLANE_HARD_DEFAULTS["retrieval_embedder_profile"]).strip()
    definition = RETRIEVAL_EMBEDDER_PROFILE_DEFINITIONS.get(
        normalized,
        RETRIEVAL_EMBEDDER_PROFILE_DEFINITIONS[CONTROL_PLANE_HARD_DEFAULTS["retrieval_embedder_profile"]],
    )
    return resolve_model_selection(definition["model_role_key"]).to_dict()
