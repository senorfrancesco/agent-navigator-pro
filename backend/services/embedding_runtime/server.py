from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from typing import Any, List, Optional, Union

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel


DEFAULT_MODEL_ID = "labse-embedding"
DEFAULT_PORT = 8092


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_optional_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    model_id: str = DEFAULT_MODEL_ID
    model_path: str = ""
    device: str = "cpu"
    embedding_dim: Optional[int] = None
    normalize: bool = True
    max_batch_size: int = 8
    max_concurrency: int = 4

    @classmethod
    def from_env(cls) -> "EmbeddingRuntimeConfig":
        return cls(
            model_id=os.getenv("EMBEDDING_MODEL_ID", DEFAULT_MODEL_ID),
            model_path=os.getenv("EMBEDDING_MODEL_PATH")
            or os.getenv("MODEL_PATH_EMBEDDING_RETRIEVAL", ""),
            device=os.getenv("EMBEDDING_DEVICE")
            or os.getenv("RETRIEVAL_EMBEDDER_DEVICE_MODE", "cpu"),
            embedding_dim=_read_optional_int("EMBEDDING_DIM"),
            normalize=_read_bool("EMBEDDING_NORMALIZE", True),
            max_batch_size=max(1, _read_int("EMBEDDING_MAX_BATCH_SIZE", 8)),
            max_concurrency=max(1, _read_int("EMBEDDING_MAX_CONCURRENCY", 4)),
        )


class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: Optional[str] = None
    encoding_format: Optional[str] = "float"


class _RuntimeMetrics:
    def __init__(self) -> None:
        self.requests_total = 0
        self.errors_total = 0
        self.inflight = 0

    def render(self, config: EmbeddingRuntimeConfig) -> str:
        labels = f'model_id="{config.model_id}"'
        return "\n".join(
            [
                f"llm_tools_embedding_runtime_requests_total{{{labels}}} {self.requests_total}",
                f"llm_tools_embedding_runtime_errors_total{{{labels}}} {self.errors_total}",
                f"llm_tools_embedding_runtime_inflight{{{labels}}} {self.inflight}",
                "",
            ]
        )


def _normalize_sentences(value: Union[str, List[str]]) -> List[str]:
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _to_vector_list(raw_embeddings: Any) -> List[List[float]]:
    if hasattr(raw_embeddings, "tolist"):
        raw_embeddings = raw_embeddings.tolist()
    vectors: List[List[float]] = []
    for item in raw_embeddings:
        if hasattr(item, "tolist"):
            item = item.tolist()
        vectors.append([float(value) for value in item])
    return vectors


def _count_prompt_tokens(sentences: List[str]) -> int:
    return sum(len(text.split()) for text in sentences)


def _health_payload(config: EmbeddingRuntimeConfig, *, model_loaded: bool) -> dict[str, Any]:
    return {
        "status": "ok" if model_loaded else "initializing",
        "runtime": "embedding-runtime",
        "model_loaded": model_loaded,
        "model_id": config.model_id,
        "model_path": config.model_path,
        "device": config.device,
        "embedding_dim": config.embedding_dim,
        "normalize": config.normalize,
        "max_batch_size": config.max_batch_size,
        "max_concurrency": config.max_concurrency,
    }


def create_app(config: Optional[EmbeddingRuntimeConfig] = None, *, model: Any = None) -> FastAPI:
    runtime_config = config or EmbeddingRuntimeConfig.from_env()
    app = FastAPI(title="Embedding Runtime", version="1.0.0")
    app.state.embedding_config = runtime_config
    app.state.embedding_model = model
    app.state.embedding_metrics = _RuntimeMetrics()
    app.state.embedding_semaphore = asyncio.Semaphore(runtime_config.max_concurrency)

    @app.get("/health")
    def health() -> dict[str, Any]:
        if app.state.embedding_model is None:
            raise HTTPException(status_code=503, detail=_health_payload(runtime_config, model_loaded=False))
        return _health_payload(runtime_config, model_loaded=True)

    @app.get("/models")
    def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": runtime_config.model_id,
                    "object": "model",
                    "owned_by": "agent-navigator-pro",
                    "runtime": "embedding-runtime",
                    "path": runtime_config.model_path,
                    "device": runtime_config.device,
                    "embedding_dimension": runtime_config.embedding_dim,
                    "normalize": runtime_config.normalize,
                }
            ],
        }

    @app.post("/v1/embeddings")
    async def embeddings(request: EmbeddingRequest) -> dict[str, Any]:
        requested_model = str(request.model or runtime_config.model_id)
        if requested_model != runtime_config.model_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "status": "embedding_model_not_loaded",
                    "requested_model_id": requested_model,
                    "ready_model_id": runtime_config.model_id if app.state.embedding_model is not None else None,
                    "runtime": "embedding-runtime",
                },
            )
        if app.state.embedding_model is None:
            raise HTTPException(status_code=503, detail=_health_payload(runtime_config, model_loaded=False))

        sentences = _normalize_sentences(request.input)
        metrics: _RuntimeMetrics = app.state.embedding_metrics
        sem: asyncio.Semaphore = app.state.embedding_semaphore
        async with sem:
            metrics.requests_total += 1
            metrics.inflight += 1
            try:
                raw_embeddings = await asyncio.to_thread(
                    app.state.embedding_model.encode,
                    sentences,
                    batch_size=runtime_config.max_batch_size,
                    normalize_embeddings=runtime_config.normalize,
                    convert_to_tensor=False,
                )
                vectors = _to_vector_list(raw_embeddings)
            except Exception:
                metrics.errors_total += 1
                raise
            finally:
                metrics.inflight -= 1

        prompt_tokens = _count_prompt_tokens(sentences)
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "embedding": vector, "index": index}
                for index, vector in enumerate(vectors)
            ],
            "model": runtime_config.model_id,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }

    @app.get("/metrics")
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            app.state.embedding_metrics.render(runtime_config),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


def _resolve_device(device: str) -> str:
    normalized = str(device or "cpu").strip().lower()
    if not normalized.startswith("cuda"):
        return normalized or "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return normalized
    except Exception:
        pass
    return "cpu"


def load_sentence_transformer(config: EmbeddingRuntimeConfig) -> Any:
    if not config.model_path:
        raise RuntimeError("EMBEDDING_MODEL_PATH is required")
    from sentence_transformers import SentenceTransformer

    device = _resolve_device(config.device)
    return SentenceTransformer(config.model_path, device=device)


def build_arg_parser() -> argparse.ArgumentParser:
    defaults = EmbeddingRuntimeConfig.from_env()
    parser = argparse.ArgumentParser(description="Standalone OpenAI-compatible embedding runtime")
    parser.add_argument("--model-id", default=defaults.model_id)
    parser.add_argument("--model", "--model-path", dest="model_path", default=defaults.model_path)
    parser.add_argument("--port", type=int, default=_read_int("EMBEDDING_RUNTIME_PORT", DEFAULT_PORT))
    parser.add_argument("--host", default=os.getenv("EMBEDDING_RUNTIME_HOST", "0.0.0.0"))
    parser.add_argument("--device", default=defaults.device)
    parser.add_argument("--dim", type=int, default=defaults.embedding_dim)
    parser.add_argument("--max-batch-size", type=int, default=defaults.max_batch_size)
    parser.add_argument("--max-concurrency", type=int, default=defaults.max_concurrency)
    normalize_group = parser.add_mutually_exclusive_group()
    normalize_group.add_argument("--normalize", action="store_true", default=defaults.normalize)
    normalize_group.add_argument("--no-normalize", action="store_false", dest="normalize")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = EmbeddingRuntimeConfig(
        model_id=args.model_id,
        model_path=args.model_path,
        device=_resolve_device(args.device),
        embedding_dim=args.dim,
        normalize=bool(args.normalize),
        max_batch_size=max(1, int(args.max_batch_size)),
        max_concurrency=max(1, int(args.max_concurrency)),
    )
    print(f"Loading embedding model {config.model_id} from {config.model_path} on {config.device}...")
    model = load_sentence_transformer(config)
    print("Embedding model loaded successfully.")
    uvicorn.run(create_app(config, model=model), host=args.host, port=int(args.port))


if __name__ == "__main__":
    main()

