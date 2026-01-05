# Models Directory

This directory is intended for storing local language models (GGUF format) used by the Unified Model Server (UMS).

## Usage

Place your model files here:
- `*.gguf` - Quantized models for llama-cpp-python
- `mmproj-*.gguf` - Multimodal projection files for vision models

## Configuration

Set environment variables to point to your models:

```bash
export MODEL_PATH_QWEN14B="./models/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
export MODEL_PATH_QWENVL="./models/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
export MODEL_PATH_LABSE="./models/LaBSE-Q4_K_M.gguf"
export MMPROJ_PATH="./models/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
```

## Note

All model files are ignored by git (see `.gitignore`). Download models separately from sources like:
- [Hugging Face](https://huggingface.co/)
- [TheBloke's GGUF models](https://huggingface.co/TheBloke)
