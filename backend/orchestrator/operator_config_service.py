from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Dict, Iterable, List

try:
    from orchestrator.operator_runtime_service import OperatorRuntimeService, env_value
except ModuleNotFoundError:  # pragma: no cover - direct module import fallback
    from backend.orchestrator.operator_runtime_service import OperatorRuntimeService, env_value


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


class OperatorConfigService:
    LOCAL_SAFE_PORTS = {
        "AGENT_API_PORT": "18000",
        "CHAINLIT_PORT": "13000",
        "UMS_PORT": "18090",
        "PROMETHEUS_PORT": "19090",
        "GRAFANA_PORT": "13002",
    }

    TARGET_DEFAULT_PORTS = {
        "AGENT_API_PORT": "8000",
        "CHAINLIT_PORT": "3000",
        "UMS_PORT": "8090",
        "PROMETHEUS_PORT": "9090",
        "GRAFANA_PORT": "3002",
    }

    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = Path(repo_root or DEFAULT_REPO_ROOT)
        self.runtime_service = OperatorRuntimeService(self.repo_root)

    def _field(
        self,
        env: Dict[str, str],
        key: str,
        label: str,
        label_en: str,
        source: str,
        *,
        suggested: str,
        default: str | None = None,
        applied_default: str | None = None,
        recommended_reason: str,
        recommended_reason_en: str,
        editable: bool = True,
        control: str = "text",
        options: List[Dict[str, str]] | None = None,
        description: str | None = None,
        description_en: str | None = None,
        secret: bool = False,
        path_policy: str | None = None,
        path_example: str | None = None,
    ) -> Dict[str, object]:
        applied = env_value(env, key, default=applied_default or default or suggested)
        validation = self._validate_field_value(
            key=key,
            value=applied,
            source=source,
            path_policy=path_policy,
        )
        return {
            "key": key,
            "label": label,
            "labelEn": label_en,
            "value": applied,
            "suggested": suggested,
            "applied": applied,
            "source": source,
            "description": description or recommended_reason,
            "descriptionEn": description_en or recommended_reason_en,
            "recommendedReason": recommended_reason,
            "recommendedReasonEn": recommended_reason_en,
            "editable": editable,
            "control": control,
            "options": options or [],
            "secret": secret,
            "pathPolicy": path_policy,
            "pathExample": path_example,
            "validation": validation,
        }

    def _validate_field_value(
        self,
        *,
        key: str,
        value: str,
        source: str,
        path_policy: str | None,
    ) -> Dict[str, str] | None:
        if not path_policy:
            return None

        raw_value = str(value).strip()
        if not raw_value:
            return {
                "status": "missing",
                "message": "Value is empty and must be set explicitly.",
            }

        if path_policy == "host_path_flexible":
            candidate = Path(raw_value)
            if not candidate.is_absolute():
                candidate = (self.repo_root / raw_value).resolve()

            if candidate.exists():
                return {
                    "status": "ok",
                    "message": f"Resolved host path exists: {candidate}",
                }
            return {
                "status": "missing",
                "message": f"Resolved host path does not exist yet: {candidate}",
            }

        if path_policy == "bundle_internal_path":
            return {
                "status": "info",
                "message": "This value is interpreted inside the offline bundle/container layout.",
            }

        return None

    def _select_options(self, values: List[str]) -> List[Dict[str, str]]:
        return [{"value": value, "label": value, "labelEn": value} for value in values]

    def _boolean_options(self) -> List[Dict[str, str]]:
        return [
            {"value": "true", "label": "true", "labelEn": "true"},
            {"value": "false", "label": "false", "labelEn": "false"},
        ]

    def _variant(
        self,
        variant_id: str,
        title: str,
        title_en: str,
        description: str,
        description_en: str,
        groups: List[Dict[str, object]],
        presets: List[Dict[str, object]],
    ) -> Dict[str, object]:
        return {
            "variantId": variant_id,
            "title": title,
            "titleEn": title_en,
            "description": description,
            "descriptionEn": description_en,
            "groups": groups,
            "presets": presets,
        }

    def _preset(
        self,
        preset_id: str,
        title: str,
        title_en: str,
        description: str,
        description_en: str,
        variant_id: str,
        updates: Dict[str, str],
        *,
        recommended: bool = False,
        reason: str,
        reason_en: str,
    ) -> Dict[str, object]:
        return {
            "presetId": preset_id,
            "title": title,
            "titleEn": title_en,
            "description": description,
            "descriptionEn": description_en,
            "variantId": variant_id,
            "updates": updates,
            "recommended": recommended,
            "reason": reason,
            "reasonEn": reason_en,
        }

    def get_config_state(self, runtime_paths: Dict[str, Dict[str, object]] | None = None) -> Dict[str, Dict[str, object]]:
        runtime_paths = runtime_paths or self.runtime_service.get_runtime_paths()
        envs = self.runtime_service.load_env_payloads()
        backend_env = envs["backend_env"]
        backend_env_native = envs["backend_env_native"]
        backend_env_runtime = envs["backend_env_runtime"]
        backend_env_override = envs["backend_env_override"]
        bundle_env = envs["bundle_env"]

        native_variants = [
            self._variant(
                "runtime",
                "Runtime / Profile",
                "Runtime / Profile",
                "Профиль запуска, backend mode и общий режим устройства.",
                "Launch profile, backend mode, and overall device mode.",
                [
                    {
                        "groupId": "runtime_profile",
                        "title": "Runtime / Profile",
                        "titleEn": "Runtime / Profile",
                        "fields": [
                            self._field(
                                backend_env_runtime or backend_env,
                                "UMS_RUNTIME_PROFILE",
                                "Runtime profile",
                                "Runtime profile",
                                "backend/.env.runtime",
                                suggested="adaptive",
                                recommended_reason="Рекомендуется для стандартного локального запуска с autodetect железа.",
                                recommended_reason_en="Recommended for the standard local runtime with hardware autodetect.",
                                control="select",
                                options=self._select_options(["adaptive", "conservative", "balanced"]),
                            ),
                            self._field(
                                backend_env_runtime or backend_env,
                                "DEVICE_MODE",
                                "Device mode",
                                "Device mode",
                                "backend/.env.runtime",
                                suggested="cpu+gpu hybrid",
                                recommended_reason="Рекомендуется для смешанного CPU/GPU path на локальной машине.",
                                recommended_reason_en="Recommended for a mixed CPU/GPU path on the local machine.",
                                control="select",
                                options=self._select_options(["cpu+gpu hybrid", "cpu", "gpu"]),
                            ),
                            self._field(
                                backend_env,
                                "BACKEND_MODE",
                                "Backend mode",
                                "Backend mode",
                                "backend/.env",
                                suggested=env_value(backend_env, "BACKEND_MODE", default="llama-server"),
                                recommended_reason="Используй текущий backend mode репозитория, если нет отдельного performance requirement.",
                                recommended_reason_en="Keep the repository backend mode unless you have a separate performance requirement.",
                                control="select",
                                options=self._select_options(["llama-cpp-python", "llama-server", "vllm"]),
                            ),
                        ],
                    }
                ],
                [
                    self._preset(
                        "native-adaptive",
                        "Adaptive recommended",
                        "Adaptive Recommended",
                        "Подставляет рекомендованные значения для обычного локального запуска.",
                        "Stages the recommended values for a standard local runtime.",
                        "runtime",
                        {
                            "UMS_RUNTIME_PROFILE": "adaptive",
                            "DEVICE_MODE": "cpu+gpu hybrid",
                        },
                        recommended=True,
                        reason="Безопасный default для локального developer path.",
                        reason_en="Safe default for the local developer path.",
                    ),
                    self._preset(
                        "native-cpu-fallback",
                        "CPU fallback",
                        "CPU Fallback",
                        "Переводит runtime в CPU-oriented режим, когда GPU path нежелателен.",
                        "Moves the runtime into a CPU-oriented mode when the GPU path is not desirable.",
                        "runtime",
                        {
                            "UMS_RUNTIME_PROFILE": "conservative",
                            "DEVICE_MODE": "cpu",
                        },
                        reason="Используй при нестабильном GPU path или для CPU-only smoke.",
                        reason_en="Use when the GPU path is unstable or for CPU-only smoke tests.",
                    ),
                ],
            ),
            self._variant(
                "models",
                "Model Registry & Paths",
                "Model Registry & Paths",
                "Пути к моделям и registry-конфигу. Здесь обычно правки ручные, но рекомендации видны рядом.",
                "Model and registry paths. These are usually edited manually, with recommendations shown next to the fields.",
                [
                    {
                        "groupId": "model_paths",
                        "title": "Model Registry & Paths",
                        "titleEn": "Model Registry & Paths",
                        "fields": [
                            self._field(
                                backend_env,
                                "MODEL_REGISTRY_CONFIG_PATH",
                                "Model registry config",
                                "Model registry config",
                                "backend/.env",
                                suggested=env_value(backend_env, "MODEL_REGISTRY_CONFIG_PATH", default="backend/config/models.yaml"),
                                recommended_reason="Обычно должен указывать на repo-local models.yaml.",
                                recommended_reason_en="Normally this should point to the repo-local models.yaml.",
                                path_policy="host_path_flexible",
                                path_example=str((self.repo_root / "backend" / "config" / "models.yaml").resolve()),
                            ),
                            self._field(
                                backend_env_native or backend_env,
                                "MODEL_PATH_LLM",
                                "LLM artifact path",
                                "LLM artifact path",
                                "backend/.env.native",
                                suggested=env_value(backend_env_native or backend_env, "MODEL_PATH_LLM", default="/models/qwen14b.gguf"),
                                recommended_reason="Укажи основной LLM artifact для runtime path.",
                                recommended_reason_en="Set the primary LLM artifact for this runtime path.",
                                path_policy="host_path_flexible",
                                path_example="/mnt/models/qwen14b.gguf",
                            ),
                            self._field(
                                backend_env_native or backend_env,
                                "MODEL_PATH_VLM",
                                "VLM artifact path",
                                "VLM artifact path",
                                "backend/.env.native",
                                suggested=env_value(backend_env_native or backend_env, "MODEL_PATH_VLM", default="/models/qwenvl.gguf"),
                                recommended_reason="Заполняется только если multimodal path реально используется.",
                                recommended_reason_en="Fill this only if the multimodal path is actually used.",
                                path_policy="host_path_flexible",
                                path_example="/mnt/models/qwenvl.gguf",
                            ),
                            self._field(
                                backend_env_native or backend_env,
                                "MODEL_PATH_EMBEDDING_INTENT",
                                "Intent embedder path",
                                "Intent embedder path",
                                "backend/.env.native",
                                suggested=env_value(backend_env_native or backend_env, "MODEL_PATH_EMBEDDING_INTENT", default="/models/qwen3-embedding-06b"),
                                recommended_reason="Должен указывать на intent embedder текущего runtime path.",
                                recommended_reason_en="Should point to the intent embedder used by the current runtime path.",
                                path_policy="host_path_flexible",
                                path_example="/mnt/models/Qwen3-Embedding-0.6B",
                            ),
                            self._field(
                                backend_env_native or backend_env,
                                "MODEL_PATH_EMBEDDING_RETRIEVAL",
                                "Retrieval embedder path",
                                "Retrieval embedder path",
                                "backend/.env.native",
                                suggested=env_value(backend_env_native or backend_env, "MODEL_PATH_EMBEDDING_RETRIEVAL", default="/models/labse"),
                                recommended_reason="Должен указывать на retrieval embedder текущего runtime path.",
                                recommended_reason_en="Should point to the retrieval embedder used by the current runtime path.",
                                path_policy="host_path_flexible",
                                path_example="/mnt/models/LaBSE",
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "gpu",
                "GPU / Placement",
                "GPU / Placement",
                "Размещение моделей и ограничения по GPU. Здесь часть параметров меняется пресетами, часть вручную.",
                "Model placement and GPU limits. Some values come from presets, others remain manual.",
                [
                    {
                        "groupId": "gpu_placement",
                        "title": "GPU / Placement",
                        "titleEn": "GPU / Placement",
                        "fields": [
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "LLM_DEVICE_MODE",
                                "LLM device mode",
                                "LLM device mode",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "LLM_DEVICE_MODE", default="gpu"),
                                recommended_reason="Определяет, должен ли heavy LLM path идти в CPU, GPU или hybrid-режиме.",
                                recommended_reason_en="Controls whether the heavy LLM path should run in CPU, GPU, or hybrid mode.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "VLM_DEVICE_MODE",
                                "VLM device mode",
                                "VLM device mode",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "VLM_DEVICE_MODE", default="gpu"),
                                recommended_reason="Используй отдельно, только если multimodal path действительно включён.",
                                recommended_reason_en="Tune this separately only when the multimodal path is actually enabled.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "INTENT_EMBEDDER_DEVICE_MODE",
                                "Intent embedder device mode",
                                "Intent embedder device mode",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "INTENT_EMBEDDER_DEVICE_MODE", default="cpu"),
                                recommended_reason="Обычно intent-embedder оставляют на CPU, чтобы не конкурировать с heavy LLM path.",
                                recommended_reason_en="Intent embedder usually stays on CPU to avoid competing with the heavy LLM path.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "RETRIEVAL_EMBEDDER_DEVICE_MODE",
                                "Retrieval embedder device mode",
                                "Retrieval embedder device mode",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "RETRIEVAL_EMBEDDER_DEVICE_MODE", default="cpu"),
                                recommended_reason="Обычно retrieval-embedder оставляют на CPU, чтобы GPU оставался доступен для generation path.",
                                recommended_reason_en="Retrieval embedder usually stays on CPU so GPU remains available for the generation path.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "GPU_LAYERS_MODE",
                                "GPU layers mode",
                                "GPU layers mode",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "GPU_LAYERS_MODE", default="max"),
                                recommended_reason="Определяет стратегию offload слоёв: auto, max или manual.",
                                recommended_reason_en="Controls the layer offload strategy: auto, max, or manual.",
                                control="select",
                                options=self._select_options(["auto", "max", "manual"]),
                            ),
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "UMS_LLM_GPU_INDICES",
                                "LLM GPU indices",
                                "LLM GPU indices",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "UMS_LLM_GPU_INDICES", default="0"),
                                recommended_reason="Указывай конкретные GPU только если хочешь жёстко закрепить heavy-model path.",
                                recommended_reason_en="Set explicit GPUs only when you want to pin the heavy-model path.",
                            ),
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "UMS_LLM_MIN_FREE_VRAM_GB",
                                "Minimum free VRAM",
                                "Minimum free VRAM",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "UMS_LLM_MIN_FREE_VRAM_GB", default="10"),
                                recommended_reason="Ограничение защищает от старта heavy path при нехватке VRAM.",
                                recommended_reason_en="This threshold prevents the heavy path from starting when VRAM is too low.",
                            ),
                            self._field(
                                backend_env_override or backend_env_runtime or backend_env,
                                "N_GPU_LAYERS_OVERRIDE",
                                "Global GPU layers override",
                                "Global GPU layers override",
                                "backend/.env.hardware.override",
                                suggested=env_value(backend_env_override or backend_env_runtime, "N_GPU_LAYERS_OVERRIDE", default="-1"),
                                recommended_reason="Грубый глобальный override для heavy LLM path. Используй только для forcing/debug.",
                                recommended_reason_en="A coarse global override for the heavy LLM path. Use only for forcing or debugging.",
                            ),
                        ],
                    }
                ],
                [
                    self._preset(
                        "native-single-gpu",
                        "Single GPU",
                        "Single GPU",
                        "Закрепляет LLM path на первом GPU.",
                        "Pins the LLM path to the first GPU.",
                        "gpu",
                        {"UMS_LLM_GPU_INDICES": "0"},
                        recommended=True,
                        reason="Рекомендуется как безопасный GPU default на большинстве локальных машин.",
                        reason_en="Recommended as a safe GPU default on most local machines.",
                    ),
                    self._preset(
                        "native-multi-gpu",
                        "Multi GPU",
                        "Multi GPU",
                        "Разрешает два первых GPU для LLM path.",
                        "Allows the first two GPUs for the LLM path.",
                        "gpu",
                        {"UMS_LLM_GPU_INDICES": "0,1"},
                        reason="Используй только если runtime реально должен распределяться по нескольким GPU.",
                        reason_en="Use only when the runtime really needs to spread across multiple GPUs.",
                    ),
                ],
            ),
            self._variant(
                "runtime_tuning",
                "LLM / Context",
                "LLM / Context",
                "Бюджет контекста и генерации для текущего native runtime path.",
                "Context and generation budget for the current native runtime path.",
                [
                    {
                        "groupId": "runtime_tuning",
                        "title": "LLM / Context",
                        "titleEn": "LLM / Context",
                        "fields": [
                            self._field(
                                backend_env_runtime or backend_env,
                                "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS",
                                "Effective context tokens",
                                "Effective context tokens",
                                "backend/.env.runtime",
                                suggested=env_value(backend_env_runtime, "UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS", default="8192"),
                                recommended_reason="Это фактический budget контекста, который runtime применяет после планирования.",
                                recommended_reason_en="This is the effective context budget the runtime applies after planning.",
                            ),
                            self._field(
                                backend_env_runtime or backend_env,
                                "UMS_RETRIEVED_CONTEXT_RATIO",
                                "Retrieved context ratio",
                                "Retrieved context ratio",
                                "backend/.env.runtime",
                                suggested=env_value(backend_env_runtime, "UMS_RETRIEVED_CONTEXT_RATIO", default="0.6"),
                                recommended_reason="Задаёт долю контекста, которую можно занять retrieved chunks.",
                                recommended_reason_en="Controls how much of the context window can be occupied by retrieved chunks.",
                            ),
                            self._field(
                                backend_env_runtime or backend_env,
                                "UMS_GENERATION_TOKENS_RESERVE",
                                "Generation reserve",
                                "Generation reserve",
                                "backend/.env.runtime",
                                suggested=env_value(backend_env_runtime, "UMS_GENERATION_TOKENS_RESERVE", default="1024"),
                                recommended_reason="Резервирует токены под генерацию ответа, чтобы retrieval не занял всё окно.",
                                recommended_reason_en="Reserves response-generation tokens so retrieval does not consume the entire window.",
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "model_runtime",
                "Model Runtime",
                "Model Runtime",
                "Per-model runtime knobs для контекста и числа GPU-слоёв.",
                "Per-model runtime knobs for context size and GPU layer count.",
                [
                    {
                        "groupId": "model_runtime",
                        "title": "Model Runtime",
                        "titleEn": "Model Runtime",
                        "fields": [
                            self._field(
                                backend_env,
                                "CONTEXT_SIZE_QWEN14B",
                                "Qwen14B context size",
                                "Qwen14B context size",
                                "backend/.env",
                                suggested=env_value(backend_env, "CONTEXT_SIZE_QWEN14B", default="16384"),
                                recommended_reason="Определяет верхнюю границу контекстного окна для основного heavy LLM path.",
                                recommended_reason_en="Sets the context-window ceiling for the main heavy LLM path.",
                            ),
                            self._field(
                                backend_env,
                                "N_GPU_LAYERS_QWEN14B",
                                "Qwen14B GPU layers",
                                "Qwen14B GPU layers",
                                "backend/.env",
                                suggested=env_value(backend_env, "N_GPU_LAYERS_QWEN14B", default="-1"),
                                recommended_reason="Per-model override числа GPU-слоёв для qwen-14b-llm.",
                                recommended_reason_en="Per-model GPU-layer override for qwen-14b-llm.",
                            ),
                            self._field(
                                backend_env,
                                "CONTEXT_SIZE_QWENVL",
                                "QwenVL context size",
                                "QwenVL context size",
                                "backend/.env",
                                suggested=env_value(backend_env, "CONTEXT_SIZE_QWENVL", default="16384"),
                                recommended_reason="Полезно только если multimodal runtime path действительно используется.",
                                recommended_reason_en="Useful only when the multimodal runtime path is actually used.",
                            ),
                            self._field(
                                backend_env,
                                "CONTEXT_SIZE_LABSE",
                                "LaBSE context size",
                                "LaBSE context size",
                                "backend/.env",
                                suggested=env_value(backend_env, "CONTEXT_SIZE_LABSE", default="512"),
                                recommended_reason="Контекст embedders обычно невелик; увеличивай только при реальной необходимости.",
                                recommended_reason_en="Embedder context is usually small; increase it only when needed.",
                            ),
                            self._field(
                                backend_env,
                                "CONTEXT_SIZE_QWEN3_EMBEDDING_06B",
                                "Qwen3 embedding context size",
                                "Qwen3 embedding context size",
                                "backend/.env",
                                suggested=env_value(backend_env, "CONTEXT_SIZE_QWEN3_EMBEDDING_06B", default="512"),
                                recommended_reason="Контекст intent-embedder имеет смысл увеличивать только под длинные фрагменты.",
                                recommended_reason_en="Increase the intent-embedder context only for genuinely long fragments.",
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "ports",
                "Ports & URLs",
                "Ports & URLs",
                "Порты локального native path и service URLs.",
                "Ports for the local native path and service URLs.",
                [
                    {
                        "groupId": "native_ports",
                        "title": "Ports & URLs",
                        "titleEn": "Ports & URLs",
                        "fields": [
                            self._field(
                                backend_env,
                                "CHAINLIT_PORT",
                                "Chainlit port",
                                "Chainlit port",
                                "backend/.env",
                                suggested=env_value(backend_env, "CHAINLIT_PORT", default="3000"),
                                recommended_reason="Оставляй стандартный порт, если рядом нет другого локального UI.",
                                recommended_reason_en="Keep the default port unless another local UI already uses it.",
                            ),
                            self._field(
                                backend_env,
                                "UMS_PORT",
                                "UMS port",
                                "UMS port",
                                "backend/.env",
                                suggested=env_value(backend_env, "UMS_PORT", default="8090"),
                                recommended_reason="Оставляй дефолт, если локальный runtime не делит хост с другим UMS.",
                                recommended_reason_en="Keep the default unless the local runtime shares the host with another UMS.",
                            ),
                            self._field(
                                backend_env,
                                "DOCUMENT_SERVER_URL",
                                "Document server URL",
                                "Document server URL",
                                "backend/.env",
                                suggested=env_value(backend_env, "DOCUMENT_SERVER_URL", default="http://127.0.0.1:8001"),
                                recommended_reason="Должен указывать на локальный document server path.",
                                recommended_reason_en="Should point to the local document server path.",
                            ),
                            self._field(
                                backend_env,
                                "LEGAL_SERVER_URL",
                                "Legal server URL",
                                "Legal server URL",
                                "backend/.env",
                                suggested=env_value(backend_env, "LEGAL_SERVER_URL", default="http://127.0.0.1:8002"),
                                recommended_reason="Должен указывать на локальный legal server path.",
                                recommended_reason_en="Should point to the local legal server path.",
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "native_secrets",
                "Secrets / Access",
                "Secrets / Access",
                "Логины, пароли и auth secrets для локального admin/operator path.",
                "Logins, passwords, and auth secrets for the local admin/operator path.",
                [
                    {
                        "groupId": "native_secrets",
                        "title": "Secrets / Access",
                        "titleEn": "Secrets / Access",
                        "fields": [
                            self._field(
                                backend_env,
                                "CHAINLIT_ADMIN_USER",
                                "Chainlit admin user",
                                "Chainlit admin user",
                                "backend/.env",
                                suggested=env_value(backend_env, "CHAINLIT_ADMIN_USER", default="admin"),
                                recommended_reason="Оставляй понятный admin user, а пароль меняй отдельно.",
                                recommended_reason_en="Keep a clear admin user and change the password separately.",
                            ),
                            self._field(
                                backend_env,
                                "CHAINLIT_ADMIN_PASSWORD",
                                "Chainlit admin password",
                                "Chainlit admin password",
                                "backend/.env",
                                suggested=env_value(backend_env, "CHAINLIT_ADMIN_PASSWORD", default="admin"),
                                recommended_reason="Для локального dev path можно использовать временный пароль, но не оставляй дефолт на общем хосте.",
                                recommended_reason_en="A temporary password is acceptable for local dev, but do not keep the default on a shared host.",
                                secret=True,
                            ),
                            self._field(
                                backend_env,
                                "CHAINLIT_AUTH_SECRET",
                                "Chainlit auth secret",
                                "Chainlit auth secret",
                                "backend/.env",
                                suggested=env_value(backend_env, "CHAINLIT_AUTH_SECRET", default="agent-navigator-secret-key-change-me"),
                                recommended_reason="Должен быть задан явно, чтобы session/auth path не жили на дефолтном секрете.",
                                recommended_reason_en="Must be set explicitly so session/auth does not rely on a default secret.",
                                secret=True,
                            ),
                            self._field(
                                backend_env,
                                "GF_SECURITY_ADMIN_USER",
                                "Grafana admin user",
                                "Grafana admin user",
                                "backend/.env",
                                suggested=env_value(backend_env, "GF_SECURITY_ADMIN_USER", default="admin"),
                                recommended_reason="Используй явный admin user для локального observability path.",
                                recommended_reason_en="Use an explicit admin user for the local observability path.",
                            ),
                            self._field(
                                backend_env,
                                "GF_SECURITY_ADMIN_PASSWORD",
                                "Grafana admin password",
                                "Grafana admin password",
                                "backend/.env",
                                suggested=env_value(backend_env, "GF_SECURITY_ADMIN_PASSWORD", default="change-me-grafana"),
                                recommended_reason="Даже на локальной машине лучше менять дефолтный пароль Grafana.",
                                recommended_reason_en="Even on a local machine, the default Grafana password should be changed.",
                                secret=True,
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "chainlit",
                "Chainlit Profiles",
                "Chainlit Profiles",
                "Профили Chainlit. Обычно редактируются вручную по конкретному workflow.",
                "Chainlit profiles. These are usually edited manually for a specific workflow.",
                [
                    {
                        "groupId": "chainlit_profiles",
                        "title": "Chainlit Profiles",
                        "titleEn": "Chainlit Profiles",
                        "fields": [
                            self._field(
                                backend_env,
                                "CHAINLIT_DEFAULT_MODEL_PROFILE",
                                "Default chat profile",
                                "Default chat profile",
                                "backend/.env",
                                suggested=env_value(backend_env, "CHAINLIT_DEFAULT_MODEL_PROFILE", default="default-chat"),
                                recommended_reason="Используй стабильный профиль по умолчанию для чата.",
                                recommended_reason_en="Use a stable default profile for chat.",
                            ),
                            self._field(
                                backend_env,
                                "CHAINLIT_LEGAL_COMPARE_MODEL_PROFILE",
                                "Legal compare profile",
                                "Legal compare profile",
                                "backend/.env",
                                suggested=env_value(backend_env, "CHAINLIT_LEGAL_COMPARE_MODEL_PROFILE", default="legal-compare"),
                                recommended_reason="Выделяй отдельный профиль для legal compare path.",
                                recommended_reason_en="Keep a separate profile for the legal compare path.",
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "parsing",
                "Parsing / Compare",
                "Parsing / Compare",
                "Параметры strict JSON и compare path. Здесь полезны safe/strict пресеты.",
                "Strict JSON and compare-path settings. Safe and strict presets are useful here.",
                [
                    {
                        "groupId": "parsing_compare",
                        "title": "Parsing / Compare",
                        "titleEn": "Parsing / Compare",
                        "fields": [
                            self._field(
                                backend_env,
                                "COMPARE_ANALYSIS_MAX_TOKENS",
                                "Compare analysis max tokens",
                                "Compare analysis max tokens",
                                "backend/.env",
                                suggested=env_value(backend_env, "COMPARE_ANALYSIS_MAX_TOKENS", default="1024"),
                                recommended_reason="Ограничивает стоимость compare path и размер анализа.",
                                recommended_reason_en="Caps the compare-path cost and the size of the analysis output.",
                            ),
                            self._field(
                                backend_env,
                                "COMPARE_SINGLE_ITEM_STRICT_JSON",
                                "Single-item strict JSON",
                                "Single-item strict JSON",
                                "backend/.env",
                                suggested=env_value(backend_env, "COMPARE_SINGLE_ITEM_STRICT_JSON", default="true"),
                                recommended_reason="Strict JSON полезен для стабильного parser contract.",
                                recommended_reason_en="Strict JSON helps keep the parser contract stable.",
                                control="select",
                                options=self._boolean_options(),
                            ),
                            self._field(
                                backend_env,
                                "COMPARE_SINGLE_ITEM_RETRY_COUNT",
                                "Single-item retry count",
                                "Single-item retry count",
                                "backend/.env",
                                suggested=env_value(backend_env, "COMPARE_SINGLE_ITEM_RETRY_COUNT", default="1"),
                                recommended_reason="Минимальный retry защищает parser path без избыточной задержки.",
                                recommended_reason_en="A small retry count protects the parser path without too much extra latency.",
                                control="select",
                                options=self._select_options(["0", "1", "2", "3"]),
                            ),
                        ],
                    }
                ],
                [
                    self._preset(
                        "parser-safe",
                        "Parser safe mode",
                        "Parser Safe Mode",
                        "Снижает риск parser failures за счёт более мягкого контракта.",
                        "Reduces parser failure risk by using a softer contract.",
                        "parsing",
                        {
                            "COMPARE_SINGLE_ITEM_STRICT_JSON": "false",
                            "COMPARE_SINGLE_ITEM_RETRY_COUNT": "2",
                        },
                        reason="Полезно, когда важнее устойчивость, чем строгий JSON contract.",
                        reason_en="Useful when resilience matters more than a strict JSON contract.",
                    ),
                    self._preset(
                        "parser-strict",
                        "Parser strict mode",
                        "Parser Strict Mode",
                        "Возвращает строгий JSON contract и минимальный retry.",
                        "Restores the strict JSON contract with a minimal retry count.",
                        "parsing",
                        {
                            "COMPARE_SINGLE_ITEM_STRICT_JSON": "true",
                            "COMPARE_SINGLE_ITEM_RETRY_COUNT": "1",
                        },
                        recommended=True,
                        reason="Рекомендуется как канонический compare/parser contract.",
                        reason_en="Recommended as the canonical compare/parser contract.",
                    ),
                ],
            ),
        ]

        container_variants = [
            self._variant(
                "local_safe_ports",
                "Local Safe Ports",
                "Local Safe Ports",
                "Профиль для локального запуска рядом с текущим operator UI без конфликта published ports.",
                "Profile for local execution next to the current operator UI without published-port conflicts.",
                [
                    {
                        "groupId": "local_safe_ports",
                        "title": "Local Safe Ports",
                        "titleEn": "Local Safe Ports",
                        "fields": [
                            self._field(bundle_env, "AGENT_API_PORT", "Agent API port", "Agent API port", "deploy/offline_bundle/env.bundle", suggested=self.LOCAL_SAFE_PORTS["AGENT_API_PORT"], applied_default=self.TARGET_DEFAULT_PORTS["AGENT_API_PORT"], recommended_reason="Нужен отдельный порт, чтобы bundle не пытался занять тот же `8000`, что и текущий operator UI.", recommended_reason_en="Use a separate port so the bundle does not try to take the same `8000` used by the current operator UI."),
                            self._field(bundle_env, "CHAINLIT_PORT", "Chainlit port", "Chainlit port", "deploy/offline_bundle/env.bundle", suggested=self.LOCAL_SAFE_PORTS["CHAINLIT_PORT"], applied_default=self.TARGET_DEFAULT_PORTS["CHAINLIT_PORT"], recommended_reason="Safe local порт для bundle Chainlit рядом с локальным dev UI.", recommended_reason_en="Safe local port for the bundle Chainlit UI next to the local dev UI."),
                            self._field(bundle_env, "UMS_PORT", "UMS port", "UMS port", "deploy/offline_bundle/env.bundle", suggested=self.LOCAL_SAFE_PORTS["UMS_PORT"], applied_default=self.TARGET_DEFAULT_PORTS["UMS_PORT"], recommended_reason="Safe local порт для bundle UMS без конфликта со стандартным `8090`.", recommended_reason_en="Safe local port for the bundle UMS without conflicting with the standard `8090`."),
                            self._field(bundle_env, "PROMETHEUS_PORT", "Prometheus port", "Prometheus port", "deploy/offline_bundle/env.bundle", suggested=self.LOCAL_SAFE_PORTS["PROMETHEUS_PORT"], applied_default=self.TARGET_DEFAULT_PORTS["PROMETHEUS_PORT"], recommended_reason="Safe local порт для мониторинга bundle рядом с локальными сервисами.", recommended_reason_en="Safe local port for bundle monitoring next to local services."),
                            self._field(bundle_env, "GRAFANA_PORT", "Grafana port", "Grafana port", "deploy/offline_bundle/env.bundle", suggested=self.LOCAL_SAFE_PORTS["GRAFANA_PORT"], applied_default=self.TARGET_DEFAULT_PORTS["GRAFANA_PORT"], recommended_reason="Safe local порт для Grafana bundle рядом с локальным monitoring stack.", recommended_reason_en="Safe local port for bundle Grafana next to the local monitoring stack."),
                        ],
                    }
                ],
                [
                    self._preset(
                        "bundle-local-safe-ports",
                        "Local safe ports",
                        "Local Safe Ports",
                        "Подставляет безопасные локальные порты для запуска рядом с текущим operator UI.",
                        "Stages safe local ports for bundle execution next to the current operator UI.",
                        "local_safe_ports",
                        deepcopy(self.LOCAL_SAFE_PORTS),
                        recommended=True,
                        reason="Рекомендуется для локального теста bundle на той же машине, где уже работает operator UI.",
                        reason_en="Recommended when testing the bundle on the same machine where the operator UI already runs.",
                    ),
                ],
            ),
            self._variant(
                "target_default_ports",
                "Target Default Ports",
                "Target Default Ports",
                "Канонический портовый профиль для target-host и offline release contract.",
                "Canonical port profile for the target host and the offline release contract.",
                [
                    {
                        "groupId": "target_default_ports",
                        "title": "Target Default Ports",
                        "titleEn": "Target Default Ports",
                        "fields": [
                            self._field(bundle_env, "AGENT_API_PORT", "Agent API port", "Agent API port", "deploy/offline_bundle/env.bundle", suggested=self.TARGET_DEFAULT_PORTS["AGENT_API_PORT"], recommended_reason="Канонический release порт `agent-api` для target-host.", recommended_reason_en="Canonical release port for `agent-api` on the target host."),
                            self._field(bundle_env, "CHAINLIT_PORT", "Chainlit port", "Chainlit port", "deploy/offline_bundle/env.bundle", suggested=self.TARGET_DEFAULT_PORTS["CHAINLIT_PORT"], recommended_reason="Канонический release порт Chainlit для target-host.", recommended_reason_en="Canonical release port for Chainlit on the target host."),
                            self._field(bundle_env, "UMS_PORT", "UMS port", "UMS port", "deploy/offline_bundle/env.bundle", suggested=self.TARGET_DEFAULT_PORTS["UMS_PORT"], recommended_reason="Канонический release порт UMS для target-host.", recommended_reason_en="Canonical release port for UMS on the target host."),
                            self._field(bundle_env, "PROMETHEUS_PORT", "Prometheus port", "Prometheus port", "deploy/offline_bundle/env.bundle", suggested=self.TARGET_DEFAULT_PORTS["PROMETHEUS_PORT"], recommended_reason="Канонический release порт Prometheus для target-host.", recommended_reason_en="Canonical release port for Prometheus on the target host."),
                            self._field(bundle_env, "GRAFANA_PORT", "Grafana port", "Grafana port", "deploy/offline_bundle/env.bundle", suggested=self.TARGET_DEFAULT_PORTS["GRAFANA_PORT"], recommended_reason="Канонический release порт Grafana для target-host.", recommended_reason_en="Canonical release port for Grafana on the target host."),
                        ],
                    }
                ],
                [
                    self._preset(
                        "bundle-target-default-ports",
                        "Target default ports",
                        "Target Default Ports",
                        "Возвращает bundle к каноническому release-профилю published ports.",
                        "Restores the bundle to the canonical release profile for published ports.",
                        "target_default_ports",
                        deepcopy(self.TARGET_DEFAULT_PORTS),
                        reason="Используй перед упаковкой release bundle и для настоящего target-host.",
                        reason_en="Use before packing a release bundle and for a real target host.",
                    ),
                ],
            ),
            self._variant(
                "monitoring",
                "Monitoring",
                "Monitoring",
                "Настройки observability для Prometheus и Grafana внутри offline bundle.",
                "Observability settings for Prometheus and Grafana inside the offline bundle.",
                [
                    {
                        "groupId": "monitoring_ports",
                        "title": "Monitoring",
                        "titleEn": "Monitoring",
                        "fields": [
                            self._field(bundle_env, "PROMETHEUS_PORT", "Prometheus port", "Prometheus port", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "PROMETHEUS_PORT", default=self.LOCAL_SAFE_PORTS["PROMETHEUS_PORT"]), recommended_reason="Выбирай порт в зависимости от local-safe или target profile.", recommended_reason_en="Pick the port based on whether you use the local-safe or target profile."),
                            self._field(bundle_env, "GRAFANA_PORT", "Grafana port", "Grafana port", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "GRAFANA_PORT", default=self.LOCAL_SAFE_PORTS["GRAFANA_PORT"]), recommended_reason="Выбирай порт в зависимости от local-safe или target profile.", recommended_reason_en="Pick the port based on whether you use the local-safe or target profile."),
                            self._field(bundle_env, "GF_SECURITY_ADMIN_USER", "Grafana admin user", "Grafana admin user", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "GF_SECURITY_ADMIN_USER", default="admin"), recommended_reason="Оставляй понятный admin user, но пароль меняй отдельно при target deploy.", recommended_reason_en="Keep a clear admin user, but change the password separately before target deploy."),
                            self._field(bundle_env, "GF_SECURITY_ADMIN_PASSWORD", "Grafana admin password", "Grafana admin password", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "GF_SECURITY_ADMIN_PASSWORD", default="change-me-grafana"), recommended_reason="Для реального target-host меняй секрет вручную перед deploy.", recommended_reason_en="Change this secret manually before a real target-host deploy.", secret=True),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "runtime_backend",
                "Runtime / Backend",
                "Runtime / Backend",
                "Режим backend, runtime profile и общая device policy для offline bundle.",
                "Backend mode, runtime profile, and global device policy for the offline bundle.",
                [
                    {
                        "groupId": "bundle_runtime_backend",
                        "title": "Runtime / Backend",
                        "titleEn": "Runtime / Backend",
                        "fields": [
                            self._field(
                                bundle_env,
                                "BACKEND_MODE",
                                "Backend mode",
                                "Backend mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "BACKEND_MODE", default="llama-server"),
                                recommended_reason="Оставляй release runtime на каноническом backend mode, если не тестируешь другой serving path.",
                                recommended_reason_en="Keep the release runtime on the canonical backend mode unless you are testing a different serving path.",
                                control="select",
                                options=self._select_options(["llama-cpp-python", "llama-server", "vllm"]),
                            ),
                            self._field(
                                bundle_env,
                                "UMS_RUNTIME_PROFILE",
                                "Runtime profile",
                                "Runtime profile",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "UMS_RUNTIME_PROFILE", default="adaptive"),
                                recommended_reason="Adaptive profile остаётся лучшим общим default для target-host с неизвестной текущей нагрузкой.",
                                recommended_reason_en="The adaptive profile remains the best general default for a target host with unknown current load.",
                                control="select",
                                options=self._select_options(["adaptive", "conservative", "balanced"]),
                            ),
                            self._field(
                                bundle_env,
                                "DEVICE_MODE",
                                "Device mode",
                                "Device mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "DEVICE_MODE", default="hybrid"),
                                recommended_reason="Hybrid policy даёт наиболее безопасный default для release bundle на mixed CPU/GPU host.",
                                recommended_reason_en="The hybrid policy gives the safest default for a release bundle on a mixed CPU/GPU host.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "bundle_gpu",
                "GPU / Placement",
                "GPU / Placement",
                "Размещение LLM, VLM и embedders по GPU, а также стратегия offload слоёв.",
                "Placement for the LLM, VLM, and embedders across GPUs, plus the layer offload strategy.",
                [
                    {
                        "groupId": "bundle_gpu_placement",
                        "title": "GPU / Placement",
                        "titleEn": "GPU / Placement",
                        "fields": [
                            self._field(
                                bundle_env,
                                "LLM_DEVICE_MODE",
                                "LLM device mode",
                                "LLM device mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "LLM_DEVICE_MODE", default="hybrid"),
                                recommended_reason="Hybrid или GPU имеет смысл для heavy LLM path; CPU оставляй только как degraded fallback.",
                                recommended_reason_en="Hybrid or GPU makes sense for the heavy LLM path; keep CPU only as a degraded fallback.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                bundle_env,
                                "VLM_DEVICE_MODE",
                                "VLM device mode",
                                "VLM device mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "VLM_DEVICE_MODE", default="hybrid"),
                                recommended_reason="Меняй только если multimodal path реально включён в deploy contract.",
                                recommended_reason_en="Change this only when the multimodal path is actually part of the deploy contract.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                bundle_env,
                                "INTENT_EMBEDDER_DEVICE_MODE",
                                "Intent embedder device mode",
                                "Intent embedder device mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "INTENT_EMBEDDER_DEVICE_MODE", default="cpu"),
                                recommended_reason="Intent embedder часто выгодно держать на CPU, если GPU нужен heavy path.",
                                recommended_reason_en="It is often better to keep the intent embedder on CPU if the GPU is reserved for the heavy path.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                bundle_env,
                                "RETRIEVAL_EMBEDDER_DEVICE_MODE",
                                "Retrieval embedder device mode",
                                "Retrieval embedder device mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "RETRIEVAL_EMBEDDER_DEVICE_MODE", default="cpu"),
                                recommended_reason="Retrieval embedder разумно оставлять на CPU, пока GPU бюджет важнее для LLM.",
                                recommended_reason_en="It is reasonable to keep the retrieval embedder on CPU while GPU budget matters more for the LLM.",
                                control="select",
                                options=self._select_options(["cpu", "gpu", "hybrid"]),
                            ),
                            self._field(
                                bundle_env,
                                "UMS_LLM_GPU_INDICES",
                                "LLM GPU indices",
                                "LLM GPU indices",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "UMS_LLM_GPU_INDICES", default=""),
                                recommended_reason="Указывай индексы только если хочешь жёстко закрепить heavy-model path на конкретных GPU.",
                                recommended_reason_en="Set indices only when you want to pin the heavy-model path to specific GPUs.",
                            ),
                            self._field(
                                bundle_env,
                                "GPU_LAYERS_MODE",
                                "GPU layers mode",
                                "GPU layers mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "GPU_LAYERS_MODE", default="auto"),
                                recommended_reason="Auto безопаснее как release default; manual используй только для явного forcing/debug.",
                                recommended_reason_en="Auto is safer as a release default; use manual only for explicit forcing or debugging.",
                                control="select",
                                options=self._select_options(["auto", "max", "manual"]),
                            ),
                            self._field(
                                bundle_env,
                                "N_GPU_LAYERS_OVERRIDE",
                                "Global GPU layer override",
                                "Global GPU layer override",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "N_GPU_LAYERS_OVERRIDE", default=""),
                                recommended_reason="Это грубый global override; применяй только когда действительно хочешь переопределить tier selection.",
                                recommended_reason_en="This is a coarse global override; use it only when you really want to override tier selection.",
                            ),
                            self._field(
                                bundle_env,
                                "N_GPU_LAYERS_QWEN14B",
                                "Qwen14B GPU layers",
                                "Qwen14B GPU layers",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "N_GPU_LAYERS_QWEN14B", default=""),
                                recommended_reason="Предпочитай per-model override для qwen-14b, если не нужен глобальный forcing.",
                                recommended_reason_en="Prefer the per-model override for qwen-14b unless you need a global forcing rule.",
                            ),
                            self._field(
                                bundle_env,
                                "VLLM_TENSOR_PARALLEL_SIZE",
                                "vLLM tensor parallel size",
                                "vLLM tensor parallel size",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "VLLM_TENSOR_PARALLEL_SIZE", default="1"),
                                recommended_reason="Поднимай значение только когда действительно используешь multi-GPU serving через vLLM.",
                                recommended_reason_en="Increase this only when you really use multi-GPU serving through vLLM.",
                            ),
                            self._field(
                                bundle_env,
                                "VLLM_GPU_MEMORY_UTILIZATION",
                                "vLLM GPU memory utilization",
                                "vLLM GPU memory utilization",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "VLLM_GPU_MEMORY_UTILIZATION", default="0.9"),
                                recommended_reason="Оставляй небольшой VRAM headroom, чтобы reduce startup failures и pressure spikes.",
                                recommended_reason_en="Keep a small VRAM headroom to reduce startup failures and pressure spikes.",
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "secrets_access",
                "Secrets / Access",
                "Secrets / Access",
                "Логины, пароли и API keys для offline bundle сервисов.",
                "Logins, passwords, and API keys for offline bundle services.",
                [
                    {
                        "groupId": "bundle_secrets_access",
                        "title": "Secrets / Access",
                        "titleEn": "Secrets / Access",
                        "fields": [
                            self._field(
                                bundle_env,
                                "CHAINLIT_AUTH_SECRET",
                                "Chainlit auth secret",
                                "Chainlit auth secret",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_AUTH_SECRET", default="change-me-before-deploy"),
                                recommended_reason="Перед target deploy секрет нужно менять вручную; локально можно оставить временное значение только для smoke.",
                                recommended_reason_en="Change this secret manually before a target deploy; locally you may keep a temporary value only for smoke testing.",
                                secret=True,
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_ADMIN_USER",
                                "Chainlit admin user",
                                "Chainlit admin user",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_ADMIN_USER", default="admin"),
                                recommended_reason="Понятный admin login упрощает handoff, если пароль меняется отдельно.",
                                recommended_reason_en="A clear admin login makes handoff easier if the password is rotated separately.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_ADMIN_PASSWORD",
                                "Chainlit admin password",
                                "Chainlit admin password",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_ADMIN_PASSWORD", default="change-me-before-deploy"),
                                recommended_reason="Для реального target-host пароль меняй вручную перед deploy и не держи дефолт в release workflow.",
                                recommended_reason_en="Change this password manually before a real target-host deploy and do not keep the default in a release workflow.",
                                secret=True,
                            ),
                            self._field(
                                bundle_env,
                                "GF_SECURITY_ADMIN_USER",
                                "Grafana admin user",
                                "Grafana admin user",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "GF_SECURITY_ADMIN_USER", default="admin"),
                                recommended_reason="Оставляй понятный admin user, но пароль меняй отдельно при target deploy.",
                                recommended_reason_en="Keep a clear admin user, but change the password separately before target deploy.",
                            ),
                            self._field(
                                bundle_env,
                                "GF_SECURITY_ADMIN_PASSWORD",
                                "Grafana admin password",
                                "Grafana admin password",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "GF_SECURITY_ADMIN_PASSWORD", default="change-me-grafana"),
                                recommended_reason="Меняй секрет вручную перед deploy на целевой хост.",
                                recommended_reason_en="Rotate this secret manually before deploying to the target host.",
                                secret=True,
                            ),
                            self._field(
                                bundle_env,
                                "VLLM_API_KEY",
                                "vLLM API key",
                                "vLLM API key",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "VLLM_API_KEY", default=""),
                                recommended_reason="Оставляй пустым только если vLLM path не требует отдельной авторизации в твоём окружении.",
                                recommended_reason_en="Keep it empty only if the vLLM path does not require separate authorization in your environment.",
                                secret=True,
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "artifact_mounts",
                "Artifact Mounts",
                "Artifact Mounts",
                "Пути к моделям, uploads и reports внутри offline bundle layout.",
                "Paths for models, uploads, and reports inside the offline bundle layout.",
                [
                    {
                        "groupId": "artifact_mounts",
                        "title": "Artifact Mounts",
                        "titleEn": "Artifact Mounts",
                        "fields": [
                            self._field(bundle_env, "BUNDLE_MODEL_ROOT", "Bundle model root", "Bundle model root", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "BUNDLE_MODEL_ROOT", default="/opt/agent-nav/models"), recommended_reason="Путь должен совпадать с runtime layout bundle.", recommended_reason_en="This path must stay aligned with the bundle runtime layout.", path_policy="bundle_internal_path", path_example="/opt/agent-nav/models"),
                            self._field(bundle_env, "BUNDLE_UPLOADS_ROOT", "Uploads root", "Uploads root", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "BUNDLE_UPLOADS_ROOT", default="/opt/agent-nav/uploads"), recommended_reason="Путь должен совпадать с runtime layout bundle.", recommended_reason_en="This path must stay aligned with the bundle runtime layout.", path_policy="bundle_internal_path", path_example="/opt/agent-nav/uploads"),
                            self._field(bundle_env, "BUNDLE_REPORTS_ROOT", "Reports root", "Reports root", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "BUNDLE_REPORTS_ROOT", default="/opt/agent-nav/reports"), recommended_reason="Путь должен совпадать с runtime layout bundle.", recommended_reason_en="This path must stay aligned with the bundle runtime layout.", path_policy="bundle_internal_path", path_example="/opt/agent-nav/reports"),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "model_policy",
                "Embedders / Models",
                "Embedders / Models",
                "Модельные идентификаторы, retrieval policy и intent-classifier contract внутри env.bundle.",
                "Model identifiers, retrieval policy, and the intent-classifier contract inside env.bundle.",
                [
                    {
                        "groupId": "bundle_model_policy",
                        "title": "Embedders / Models",
                        "titleEn": "Embedders / Models",
                        "fields": [
                            self._field(
                                bundle_env,
                                "INTENT_CLASSIFIER_MODE",
                                "Intent classifier mode",
                                "Intent classifier mode",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "INTENT_CLASSIFIER_MODE", default="embedder"),
                                recommended_reason="Embedder mode остаётся более дешёвым default, если не нужен LLM-based intent routing.",
                                recommended_reason_en="Embedder mode remains the cheaper default unless you need LLM-based intent routing.",
                                control="select",
                                options=self._select_options(["embedder", "llm"]),
                            ),
                            self._field(
                                bundle_env,
                                "INTENT_CLASSIFIER_EMBEDDER_MODEL",
                                "Intent embedder model",
                                "Intent embedder model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "INTENT_CLASSIFIER_EMBEDDER_MODEL", default="qwen3-embedding-0.6b"),
                                recommended_reason="Держи classifier и Chainlit intent profile на одном embedder, если не тестируешь split setup.",
                                recommended_reason_en="Keep the classifier and the Chainlit intent profile on the same embedder unless you are testing a split setup.",
                            ),
                            self._field(
                                bundle_env,
                                "INTENT_CLASSIFIER_LLM_MODEL",
                                "Intent LLM model",
                                "Intent LLM model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "INTENT_CLASSIFIER_LLM_MODEL", default="qwen-14b-llm"),
                                recommended_reason="Используй тот же heavy LLM path, если classifier нужно эскалировать к LLM.",
                                recommended_reason_en="Use the same heavy LLM path when the classifier needs to escalate to an LLM.",
                            ),
                            self._field(
                                bundle_env,
                                "LEGAL_EMBEDDER_MODEL",
                                "Legal embedder model",
                                "Legal embedder model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "LEGAL_EMBEDDER_MODEL", default="labse-embedding"),
                                recommended_reason="Согласовывай retrieval legal default с profile-конфигом Chainlit.",
                                recommended_reason_en="Keep the legal retrieval default aligned with the Chainlit profile config.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL",
                                "Chainlit intent profile model",
                                "Chainlit intent profile model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL", default="qwen3-embedding-0.6b"),
                                recommended_reason="Профиль intent embedder в UI должен совпадать с фактическим classifier/embedder contract.",
                                recommended_reason_en="The UI intent-embedder profile should match the actual classifier and embedder contract.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL",
                                "Legal retrieval profile model",
                                "Legal retrieval profile model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL", default="labse-embedding"),
                                recommended_reason="Legal retrieval profile лучше держать на той же retrieval-модели, что и runtime policy.",
                                recommended_reason_en="It is better to keep the legal retrieval profile on the same retrieval model as the runtime policy.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL",
                                "Low-VRAM retrieval profile model",
                                "Low-VRAM retrieval profile model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL", default="labse-embedding"),
                                recommended_reason="Низковрамный retrieval profile должен оставаться на lightweight embedder path.",
                                recommended_reason_en="The low-VRAM retrieval profile should stay on a lightweight embedder path.",
                            ),
                            self._field(
                                bundle_env,
                                "RAG_MODE_OVERRIDE",
                                "RAG mode override",
                                "RAG mode override",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "RAG_MODE_OVERRIDE", default="auto"),
                                recommended_reason="Оставляй auto, если не фиксируешь retrieval policy специально под тестовый сценарий.",
                                recommended_reason_en="Keep auto unless you are pinning the retrieval policy for a dedicated test scenario.",
                                control="select",
                                options=self._select_options(["auto", "agentic", "simple"]),
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "chainlit_profiles",
                "Chainlit Profiles",
                "Chainlit Profiles",
                "Параметры Chainlit-профилей, влияющие на default temperature, top-p, max tokens и выбор модельных профилей.",
                "Chainlit profile settings that control default temperature, top-p, max tokens, and model profile selection.",
                [
                    {
                        "groupId": "bundle_chainlit_profiles",
                        "title": "Chainlit Profiles",
                        "titleEn": "Chainlit Profiles",
                        "fields": [
                            self._field(
                                bundle_env,
                                "CHAINLIT_DEFAULT_TEMPERATURE",
                                "Default temperature",
                                "Default temperature",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_DEFAULT_TEMPERATURE", default="0.7"),
                                recommended_reason="Стандартный default chat profile не должен быть слишком креативным для operator-facing сценариев.",
                                recommended_reason_en="The default chat profile should not be too creative for operator-facing scenarios.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_DEFAULT_TOP_P",
                                "Default top-p",
                                "Default top-p",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_DEFAULT_TOP_P", default="0.9"),
                                recommended_reason="Оставляй предсказуемый sampling window для общей стабильности UI.",
                                recommended_reason_en="Keep a predictable sampling window for overall UI stability.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_DEFAULT_MAX_TOKENS",
                                "Default max tokens",
                                "Default max tokens",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_DEFAULT_MAX_TOKENS", default="2048"),
                                recommended_reason="Балансирует полноту ответа и latency в основном profile path.",
                                recommended_reason_en="Balances answer completeness and latency in the main profile path.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_MODEL_PROFILE_DEFAULT_CHAT_MODEL",
                                "Default chat model",
                                "Default chat model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_MODEL_PROFILE_DEFAULT_CHAT_MODEL", default="qwen-14b-llm"),
                                recommended_reason="Дефолтный chat model должен совпадать с основным LLM path release runtime.",
                                recommended_reason_en="The default chat model should match the main LLM path of the release runtime.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_MODEL_PROFILE_LONG_CONTEXT_MODEL",
                                "Long-context model",
                                "Long-context model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_MODEL_PROFILE_LONG_CONTEXT_MODEL", default="qwen-14b-llm"),
                                recommended_reason="Long-context profile имеет смысл отделять только если реально есть другой model contract.",
                                recommended_reason_en="The long-context profile should differ only if there is a genuinely different model contract.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_MODEL_PROFILE_LEGAL_COMPARE_MODEL",
                                "Legal-compare model",
                                "Legal-compare model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_MODEL_PROFILE_LEGAL_COMPARE_MODEL", default="qwen-14b-llm"),
                                recommended_reason="Legal compare profile лучше держать на том же heavy model path, пока нет отдельной tuned модели.",
                                recommended_reason_en="It is better to keep the legal-compare profile on the same heavy model path until there is a dedicated tuned model.",
                            ),
                            self._field(
                                bundle_env,
                                "CHAINLIT_MODEL_PROFILE_LOW_VRAM_MODEL",
                                "Low-VRAM model",
                                "Low-VRAM model",
                                "deploy/offline_bundle/env.bundle",
                                suggested=env_value(bundle_env, "CHAINLIT_MODEL_PROFILE_LOW_VRAM_MODEL", default="qwen-14b-llm"),
                                recommended_reason="Меняй только если low-VRAM profile реально переключается на другой serving path.",
                                recommended_reason_en="Change this only when the low-VRAM profile actually switches to a different serving path.",
                            ),
                        ],
                    }
                ],
                [],
            ),
            self._variant(
                "parsing",
                "Parsing / Compare",
                "Parsing / Compare",
                "Offline parser/compare contract внутри env.bundle.",
                "Offline parser and compare contract inside env.bundle.",
                [
                    {
                        "groupId": "bundle_parsing",
                        "title": "Parsing / Compare",
                        "titleEn": "Parsing / Compare",
                        "fields": [
                            self._field(bundle_env, "OFFLINE_COMPARE_STRICT_JSON", "Offline strict JSON", "Offline strict JSON", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "OFFLINE_COMPARE_STRICT_JSON", default="true"), recommended_reason="Рекомендуется как канонический parser contract для offline bundle.", recommended_reason_en="Recommended as the canonical parser contract for the offline bundle.", control="select", options=self._boolean_options()),
                            self._field(bundle_env, "BUNDLE_PREFLIGHT_REQUIRED", "Preflight required", "Preflight required", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "BUNDLE_PREFLIGHT_REQUIRED", default="true"), recommended_reason="Держи preflight обязательным для target deploy.", recommended_reason_en="Keep preflight required for target deploys.", control="select", options=self._boolean_options()),
                            self._field(bundle_env, "BUNDLE_PARITY_SMOKE_REQUIRED", "Parity smoke required", "Parity smoke required", "deploy/offline_bundle/env.bundle", suggested=env_value(bundle_env, "BUNDLE_PARITY_SMOKE_REQUIRED", default="true"), recommended_reason="Полезно для проверки parity после импорта bundle.", recommended_reason_en="Useful for validating parity after importing a bundle.", control="select", options=self._boolean_options()),
                        ],
                    }
                ],
                [],
            ),
        ]

        return {
            "native": {
                "sourceFiles": runtime_paths["native"]["configSources"],
                "defaultVariantId": "runtime",
                "variants": native_variants,
            },
            "container": {
                "sourceFiles": runtime_paths["container"]["configSources"],
                "defaultVariantId": "local_safe_ports",
                "variants": container_variants,
            },
        }

    def get_config_variants(self, path_key: str) -> Dict[str, object]:
        state = self.get_config_state()
        if path_key not in state:
            raise KeyError(path_key)
        return state[path_key]

    def preview_preset(self, path_key: str, preset_id: str) -> Dict[str, object]:
        path_state = self.get_config_variants(path_key)
        presets = {
            preset["presetId"]: preset
            for variant in path_state["variants"]
            for preset in variant["presets"]
        }
        preset = presets.get(preset_id)
        if preset is None:
            raise KeyError(preset_id)

        field_map = self._field_map(path_state["variants"])
        changed = []
        for key, staged in preset["updates"].items():
            field = field_map.get(key)
            if field is None:
                continue
            changed.append(
                {
                    "key": key,
                    "label": field["label"],
                    "labelEn": field.get("labelEn", field["label"]),
                    "applied": field["applied"],
                    "staged": staged,
                    "source": field["source"],
                    "recommended": field["suggested"],
                }
            )

        return {
            "pathKey": path_key,
            "presetId": preset["presetId"],
            "variantId": preset["variantId"],
            "title": preset["title"],
            "titleEn": preset.get("titleEn", preset["title"]),
            "description": preset["description"],
            "descriptionEn": preset.get("descriptionEn", preset["description"]),
            "reason": preset["reason"],
            "reasonEn": preset.get("reasonEn", preset["reason"]),
            "recommended": preset["recommended"],
            "updates": preset["updates"],
            "changedFields": changed,
        }

    def apply_config(self, path_key: str, updates: Dict[str, str]) -> Dict[str, object]:
        runtime_paths = self.runtime_service.get_runtime_paths()
        config_state = self.get_config_state(runtime_paths)
        if path_key not in config_state:
            raise KeyError(path_key)

        field_map = self._field_map(config_state[path_key]["variants"])
        updated_keys: list[str] = []
        for key, value in updates.items():
            field = field_map.get(key)
            if field is None or not field.get("editable", True):
                continue
            source_path = self.repo_root / str(field["source"])
            self._upsert_env_value(source_path, key, value)
            updated_keys.append(key)

        refreshed_runtime_paths = self.runtime_service.get_runtime_paths()
        refreshed_config = self.get_config_state(refreshed_runtime_paths)[path_key]
        return {
            "pathKey": path_key,
            "updatedKeys": updated_keys,
            "config": refreshed_config,
        }

    def _field_map(self, variants: Iterable[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
        field_map: Dict[str, Dict[str, object]] = {}
        for variant in variants:
            for group in variant["groups"]:
                for field in group["fields"]:
                    field_map[str(field["key"])] = field
        return field_map

    def _upsert_env_value(self, path: Path, key: str, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()

        replacement = f"{key}={value}"
        updated = False
        next_lines = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith(f"{key}="):
                next_lines.append(replacement)
                updated = True
            else:
                next_lines.append(raw_line)

        if not updated:
            next_lines.append(replacement)

        path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
