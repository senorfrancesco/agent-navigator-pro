from __future__ import annotations

from fastapi.testclient import TestClient

from services.embedding_runtime.server import EmbeddingRuntimeConfig, create_app


class _FakeEmbeddingModel:
    def encode(self, sentences, *, batch_size, normalize_embeddings, convert_to_tensor):
        assert batch_size == 2
        assert normalize_embeddings is True
        assert convert_to_tensor is False
        return [[float(index), float(len(text))] for index, text in enumerate(sentences)]


def test_embedding_runtime_exposes_openai_embeddings_and_status():
    app = create_app(
        EmbeddingRuntimeConfig(
            model_id="labse-embedding",
            model_path="/models/st/LaBSE",
            device="cpu",
            embedding_dim=2,
            normalize=True,
            max_batch_size=2,
            max_concurrency=1,
        ),
        model=_FakeEmbeddingModel(),
    )
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "runtime": "embedding-runtime",
        "model_loaded": True,
        "model_id": "labse-embedding",
        "model_path": "/models/st/LaBSE",
        "device": "cpu",
        "embedding_dim": 2,
        "normalize": True,
        "max_batch_size": 2,
        "max_concurrency": 1,
    }

    models = client.get("/models").json()
    assert models["data"][0]["id"] == "labse-embedding"
    assert models["data"][0]["runtime"] == "embedding-runtime"
    assert models["data"][0]["embedding_dimension"] == 2

    response = client.post(
        "/v1/embeddings",
        json={"model": "labse-embedding", "input": ["alpha", "beta"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": [0.0, 5.0], "index": 0},
            {"object": "embedding", "embedding": [1.0, 4.0], "index": 1},
        ],
        "model": "labse-embedding",
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }


def test_embedding_runtime_rejects_unknown_model_id():
    app = create_app(
        EmbeddingRuntimeConfig(model_id="labse-embedding", model_path="/models/st/LaBSE"),
        model=_FakeEmbeddingModel(),
    )
    client = TestClient(app)

    response = client.post(
        "/v1/embeddings",
        json={"model": "other-embedding", "input": "alpha"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["status"] == "embedding_model_not_loaded"
