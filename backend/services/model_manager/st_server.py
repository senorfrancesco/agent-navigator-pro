"""
Sentence Transformers Server
Легковесный сервер для запуска моделей SentenceTransformers (как LaBSE).
Имитирует OpenAI API /v1/embeddings.
"""

import argparse
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Union, Optional
from sentence_transformers import SentenceTransformer
import torch

app = FastAPI(title="ST Server")
model = None

class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: Optional[str] = None
    encoding_format: Optional[str] = "float"

@app.post("/v1/embeddings")
async def embeddings(request: EmbeddingRequest):
    global model
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    try:
        sentences = request.input
        if isinstance(sentences, str):
            sentences = [sentences]
            
        # Генерация эмбеддингов с внутренним батчингом
        # normalize_embeddings=True для косинусного сходства
        embeddings = model.encode(sentences, batch_size=8, normalize_embeddings=True, convert_to_tensor=False)
        
        # Формируем ответ в стиле OpenAI
        data = []
        for i, emb in enumerate(embeddings):
            data.append({
                "object": "embedding",
                "embedding": emb.tolist(),
                "index": i
            })
            
        return {
            "object": "list",
            "data": data,
            "model": "sentence-transformers",
            "usage": {
                "prompt_tokens": 0,
                "total_tokens": 0
            }
        }
    except Exception as e:
        print(f"Error generating embeddings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    if model is None:
        raise HTTPException(status_code=503, detail="Model initializing")
    return {"status": "ok", "model_loaded": True}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True, help="Path to the model directory")
    parser.add_argument("--port", type=int, default=8093, help="Port to run the server on")
    parser.add_argument("--device", type=str, default="cpu", help="Device to use (cpu, cuda)")
    args = parser.parse_args()

    print(f"Loading model from {args.model} on {args.device}...")
    try:
        device = args.device
        if device == "cuda" and not torch.cuda.is_available():
            print("CUDA not available, falling back to CPU")
            device = "cpu"
            
        model = SentenceTransformer(args.model, device=device)
        print("Model loaded successfully.")
        
        uvicorn.run(app, host="0.0.0.0", port=args.port)
    except Exception as e:
        print(f"Failed to start server: {e}")
        exit(1)
