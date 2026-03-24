Здесь хранятся ссылки и contract notes для third-party исходников, которые нужны
на build-side этапе offline bundle.

Для `release/v1.0` `UMS` image собирает `llama-server` из официального
репозитория `llama.cpp`:

  https://github.com/ggml-org/llama.cpp

Источник и ref задаются через build args:
- `LLAMA_CPP_REPO`
- `LLAMA_CPP_REF`

В git фиксируется только ссылка и build contract, а не vendored source tree.
