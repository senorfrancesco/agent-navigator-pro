Здесь хранятся ссылки и contract notes для third-party исходников, которые нужны
на build-side этапе offline bundle.

Для `release/v1.0` `UMS` image собирает `llama-server` из локального checkout
`deploy/offline_bundle/vendor/llama.cpp/`.

Upstream source:

  https://github.com/ggml-org/llama.cpp

Этот checkout живёт только как build-side dependency, фиксируется по пути внутри
bundle и не должен попадать в source commit.
