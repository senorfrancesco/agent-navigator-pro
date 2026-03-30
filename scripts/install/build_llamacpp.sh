#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
DOC_PATH="$PROJECT_ROOT/docs/scripts/installers.md"
DEFAULT_SOURCE_DIR="$PROJECT_ROOT/deploy/offline_bundle/vendor/llama.cpp"
LLAMA_CPP_REPO="https://github.com/ggml-org/llama.cpp.git"
LLAMA_CPP_REF="master"
SOURCE_DIR="$DEFAULT_SOURCE_DIR"
BUILD_DIR=""
CUDA_ARCHITECTURES="${LLAMA_CPP_CUDA_ARCHITECTURES:-86}"
USE_CUDA="auto"
BUILD_JOBS="$(nproc)"
FORCE_CLONE=0

print_help() {
  cat <<EOF
build_llamacpp.sh

Готовит source tree llama.cpp и собирает локальный бинарь \`llama-server\`.
По умолчанию использует pinned build-side путь:
  $DEFAULT_SOURCE_DIR

Если source tree отсутствует, скрипт может автоматически клонировать:
  $LLAMA_CPP_REPO

Флаги:
  --source-dir=PATH
      Путь к локальному source tree llama.cpp.
  --build-dir=PATH
      Отдельный build directory. По умолчанию: <source-dir>/build.
  --repo=URL
      Git URL для clone fallback.
  --ref=REF
      Git ref/tag/branch для clone fallback.
  --cuda=auto|on|off
      Использовать CUDA build path, если toolkit доступен.
  --cuda-arch=LIST
      Значение для CMAKE_CUDA_ARCHITECTURES, например "86" или "75;86".
  --jobs=N
      Количество параллельных jobs для cmake --build.
  --force-clone
      Удалить существующий source-dir и заново клонировать его из repo/ref.
  -h, --help
      Показать эту справку.

Документация:
  $DOC_PATH
EOF
}

resolve_nvcc_bin() {
  local candidate=""

  if command -v nvcc >/dev/null 2>&1; then
    command -v nvcc
    return 0
  fi

  for candidate in \
    "/usr/local/cuda/bin/nvcc" \
    "/usr/local/cuda-12.8/bin/nvcc"
  do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done

  candidate="$(find /usr/local -maxdepth 3 -path '*/bin/nvcc' -type f 2>/dev/null | sort -V | tail -n 1)"
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    echo "$candidate"
    return 0
  fi

  return 1
}

prepare_source_tree() {
  if [ "$FORCE_CLONE" -eq 1 ] && [ -e "$SOURCE_DIR" ]; then
    rm -rf "$SOURCE_DIR"
  fi

  if [ -f "$SOURCE_DIR/CMakeLists.txt" ]; then
    echo "llamacpp-source:existing path=$SOURCE_DIR"
    return 0
  fi

  if [ -e "$SOURCE_DIR" ] && [ ! -f "$SOURCE_DIR/CMakeLists.txt" ]; then
    echo "existing path is not a llama.cpp checkout: $SOURCE_DIR" >&2
    exit 1
  fi

  mkdir -p "$(dirname "$SOURCE_DIR")"
  echo "llamacpp-source:clone repo=$LLAMA_CPP_REPO ref=$LLAMA_CPP_REF path=$SOURCE_DIR"
  git clone --depth 1 --branch "$LLAMA_CPP_REF" "$LLAMA_CPP_REPO" "$SOURCE_DIR"
  test -f "$SOURCE_DIR/CMakeLists.txt" || (echo "llama.cpp checkout is invalid: $SOURCE_DIR" >&2 && exit 1)
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      print_help
      exit 0
      ;;
    --source-dir=*)
      SOURCE_DIR="${arg#*=}"
      ;;
    --build-dir=*)
      BUILD_DIR="${arg#*=}"
      ;;
    --repo=*)
      LLAMA_CPP_REPO="${arg#*=}"
      ;;
    --ref=*)
      LLAMA_CPP_REF="${arg#*=}"
      ;;
    --cuda=*)
      USE_CUDA="${arg#*=}"
      ;;
    --cuda-arch=*)
      CUDA_ARCHITECTURES="${arg#*=}"
      ;;
    --jobs=*)
      BUILD_JOBS="${arg#*=}"
      ;;
    --force-clone)
      FORCE_CLONE=1
      ;;
    *)
      echo "Неизвестный аргумент build_llamacpp.sh: $arg" >&2
      exit 1
      ;;
  esac
done

if [ -z "$BUILD_DIR" ]; then
  BUILD_DIR="$SOURCE_DIR/build"
fi

case "$USE_CUDA" in
  auto|on|off) ;;
  *)
    echo "Неподдерживаемое значение --cuda: $USE_CUDA" >&2
    exit 1
    ;;
esac

prepare_source_tree

mkdir -p "$BUILD_DIR"

NVCC_BIN=""
if NVCC_BIN="$(resolve_nvcc_bin)"; then
  export PATH="$(dirname "$NVCC_BIN"):$PATH"
fi

declare -a cmake_args
cmake_args=(
  -S "$SOURCE_DIR"
  -B "$BUILD_DIR"
  -DCMAKE_BUILD_TYPE=Release
  -DLLAMA_BUILD_EXAMPLES=OFF
  -DLLAMA_BUILD_TESTS=OFF
)

if [ "$USE_CUDA" = "on" ] || { [ "$USE_CUDA" = "auto" ] && [ -n "$NVCC_BIN" ]; }; then
  cmake_args+=(
    -DGGML_CUDA=ON
    -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCHITECTURES"
    -DCMAKE_EXE_LINKER_FLAGS="-L/usr/local/cuda/lib64/stubs -lcuda"
  )
  echo "llamacpp-build:backend=cuda nvcc=$NVCC_BIN cuda_arch=$CUDA_ARCHITECTURES"
else
  cmake_args+=(-DGGML_CUDA=OFF)
  echo "llamacpp-build:backend=cpu"
fi

cmake "${cmake_args[@]}"
cmake --build "$BUILD_DIR" --config Release --target llama-server -j"$BUILD_JOBS"

LLAMA_SERVER_BIN="$BUILD_DIR/bin/llama-server"
if [ ! -x "$LLAMA_SERVER_BIN" ]; then
  echo "llama-server binary not found after build: $LLAMA_SERVER_BIN" >&2
  exit 1
fi

"$LLAMA_SERVER_BIN" --help >/dev/null 2>&1 || true

echo "build-llamacpp:ok source=$SOURCE_DIR build=$BUILD_DIR bin=$LLAMA_SERVER_BIN"
