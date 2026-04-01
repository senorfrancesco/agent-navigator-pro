#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/host_bundle_common.sh"

DRIVER_PACKAGE="nvidia-driver-590-server"
MANUAL_DRIVER_VERSION="590.48.01"
NVIDIA_CONTAINER_TOOLKIT_VERSION="1.19.0-1"
DISTRO="ubuntu-24.04"
CHECK_ONLY=0
DRY_RUN=0
APT_RETRIES=5

HOST_ROOT=""
POOL_DIR=""
LOCK_PATH=""
MANIFEST_PATH=""
EXTRA_PACKAGES=()

usage() {
  cat <<'EOF'
build_host_apt_bundle.sh

Собирает локальный apt-bundle для Ubuntu 22.04 или Ubuntu 24.04.
Сборка идёт внутри target-контейнера Ubuntu, чтобы exact версии пакетов
разрешались под нужный release, а не под текущий build-хост.

Что делает:
  - поднимает временный контейнер ubuntu:<release>
  - подключает Docker repo и NVIDIA Container Toolkit repo
  - включает universe
  - скачивает .deb пакеты и зависимости
  - собирает Packages и Packages.gz
  - фиксирует exact версии и sha256 в versions.lock.json

Flags:
  --distro <name>           ubuntu-22.04 или ubuntu-24.04 (default: ubuntu-24.04)
  --driver-package <name>   nvidia-driver-590-server (default) или nvidia-driver-590
  --output-root <path>      Переопределить host_packages/<distro>
  --extra-package <name>    Добавить явный пакет в bundle (флаг можно повторять)
  --check-only              Только проверить prerequisites и apt candidates
  --dry-run                 Показать, что будет скачано, без скачивания

Жёстко фиксируем:
  - пакетный драйверный baseline: nvidia-driver-590-server или nvidia-driver-590
  - ручной NVIDIA Data Center Driver baseline: 590.48.01
  - NVIDIA Container Toolkit: 1.19.0-1

Примечание:
  Для fully-offline установки NVIDIA на target-host могут понадобиться
  kernel-specific пакеты. Их можно явно добавить через повторяющийся
  --extra-package.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --distro)
      DISTRO="$2"
      shift 2
      ;;
    --distro=*)
      DISTRO="${1#*=}"
      shift
      ;;
    --driver-package)
      DRIVER_PACKAGE="$2"
      shift 2
      ;;
    --driver-package=*)
      DRIVER_PACKAGE="${1#*=}"
      shift
      ;;
    --output-root)
      HOST_ROOT="$2"
      shift 2
      ;;
    --output-root=*)
      HOST_ROOT="${1#*=}"
      shift
      ;;
    --extra-package)
      EXTRA_PACKAGES+=("$2")
      shift 2
      ;;
    --extra-package=*)
      EXTRA_PACKAGES+=("${1#*=}")
      shift
      ;;
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "unknown-arg:$1" >&2
      exit 1
      ;;
  esac
done

assert_supported_host_bundle_distro "$DISTRO"

case "$DRIVER_PACKAGE" in
  nvidia-driver-590|nvidia-driver-590-server)
    ;;
  *)
    echo "unsupported-driver-package:$DRIVER_PACKAGE" >&2
    exit 1
    ;;
esac

if [[ -z "$HOST_ROOT" ]]; then
  HOST_ROOT="$(host_bundle_root_for_distro "$BUNDLE_ROOT" "$DISTRO")"
fi
POOL_DIR="$HOST_ROOT/pool"
LOCK_PATH="$HOST_ROOT/versions.lock.json"
MANIFEST_PATH="$HOST_ROOT/manifest.json"

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing-build-command:$cmd" >&2
    exit 1
  }
}

for cmd in docker mktemp python3; do
  require_cmd "$cmd"
done
docker info >/dev/null 2>&1 || {
  echo "docker-daemon-unavailable" >&2
  exit 1
}

mkdir -p "$POOL_DIR"

docker_tag=""
case "$DISTRO" in
  ubuntu-22.04)
    docker_tag="22.04"
    ;;
  ubuntu-24.04)
    docker_tag="24.04"
    ;;
esac

BASE_PACKAGES=(
  docker-ce
  docker-ce-cli
  containerd.io
  docker-buildx-plugin
  docker-compose-plugin
  dpkg-dev
  make
  patch
  lsb-release
  "$DRIVER_PACKAGE"
  nvidia-container-toolkit
  nvidia-container-toolkit-base
  libnvidia-container-tools
  libnvidia-container1
  python3
  curl
  tmux
  btop
)
if [[ ${#EXTRA_PACKAGES[@]} -gt 0 ]]; then
  BASE_PACKAGES+=("${EXTRA_PACKAGES[@]}")
fi

tmp_runner="$(mktemp)"
trap 'rm -f "$tmp_runner"' EXIT
cat > "$tmp_runner" <<'EOS'
#!/usr/bin/env bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

log_stage() {
  echo "stage:$1"
}

apt_get() {
  apt-get -o Acquire::Retries="${APT_RETRIES}" "$@"
}

log_stage apt-update-base
apt_get update
log_stage apt-install-bootstrap
apt_get install -y --no-install-recommends \
  apt-utils \
  ca-certificates \
  curl \
  dpkg-dev \
  gnupg \
  python3 \
  software-properties-common
log_stage enable-universe
add-apt-repository -y universe

install -m 0755 -d /etc/apt/keyrings /usr/share/keyrings
log_stage add-docker-repo
curl --retry "${APT_RETRIES}" --retry-delay 2 --retry-connrefused -fsSL \
  https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
  > /etc/apt/sources.list.d/docker.list

log_stage add-nvidia-repo
curl --retry "${APT_RETRIES}" --retry-delay 2 --retry-connrefused -fsSL \
  https://nvidia.github.io/libnvidia-container/gpgkey | \
  gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl --retry "${APT_RETRIES}" --retry-delay 2 --retry-connrefused -s -L \
  https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list

log_stage apt-update-with-third-party-repos
apt_get update

candidate_version() {
  local pkg="$1"
  apt-cache policy "$pkg" | awk '/Candidate:/ {print $2; exit}'
}

resolve_install_plan() {
  python3 - <<'PY' "$@"
import re
import subprocess
import sys

cmd = [
    'apt-get',
    '-s',
    '-o', 'APT::Get::Download-Only=true',
    '--no-install-recommends',
    'install',
    *sys.argv[1:],
]
result = subprocess.run(cmd, capture_output=True, text=True, check=False)
if result.returncode != 0:
    sys.stderr.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)

pattern = re.compile(r'^Inst\s+(\S+)(?:\s+\[[^\]]+\])?\s+\(([^ )]+)')
seen = set()
for raw_line in result.stdout.splitlines():
    line = raw_line.strip()
    match = pattern.match(line)
    if not match:
        continue
    pkg, version = match.groups()
    if pkg in seen:
        continue
    seen.add(pkg)
    print(f'{pkg}={version}')
PY
}

for pkg in "$@"; do
  version="$(candidate_version "$pkg")"
  if [[ -z "$version" || "$version" == "(none)" ]]; then
    echo "missing-apt-candidate:$pkg" >&2
    exit 1
  fi
  case "$pkg" in
    nvidia-container-toolkit|nvidia-container-toolkit-base|libnvidia-container-tools|libnvidia-container1)
      if [[ "$version" != "${NVIDIA_CONTAINER_TOOLKIT_VERSION}" ]]; then
        echo "unexpected-toolkit-version:$pkg=$version expected=${NVIDIA_CONTAINER_TOOLKIT_VERSION}" >&2
        exit 1
      fi
      ;;
  esac
done

log_stage resolve-install-plan
mapfile -t install_plan < <(resolve_install_plan "$@")
if [[ ${#install_plan[@]} -eq 0 ]]; then
  echo "empty-install-plan" >&2
  exit 1
fi

for entry in "${install_plan[@]}"; do
  echo "candidate:${entry}"
done

if [[ "${CHECK_ONLY:-0}" == "1" ]]; then
  echo "host-apt-bundle-check:ok"
  exit 0
fi

mkdir -p /out/pool

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "dry-run:pool-dir:/out/pool"
  for entry in "${install_plan[@]}"; do
    echo "would-download:${entry}"
  done
  exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
mkdir -p "$tmp_dir/partial"

requested_with_versions=()
for pkg in "$@"; do
  requested_with_versions+=("${pkg}=$(candidate_version "$pkg")")
done

log_stage download-debs
apt_get install -y --download-only --no-install-recommends \
  -o Dir::Cache::archives="$tmp_dir" \
  "${requested_with_versions[@]}"

find /out/pool -mindepth 1 -maxdepth 1 -type f -name '*.deb' -delete
find "$tmp_dir" -maxdepth 1 -type f -name '*.deb' -exec cp -a {} /out/pool/ \;

if ! find /out/pool -maxdepth 1 -type f -name '*.deb' | grep -q .; then
  echo "no-deb-files-downloaded" >&2
  exit 1
fi

(
  cd /out
  log_stage write-package-indexes
  dpkg-scanpackages pool /dev/null > Packages
  gzip -9c Packages > Packages.gz
  apt-ftparchive release . > Release
)

log_stage write-lock-and-manifest
python3 - <<'PY' \
  "/out/pool" \
  "/out/versions.lock.json" \
  "/out/manifest.json" \
  "${DRIVER_PACKAGE}" \
  "${TARGET_DISTRO}" \
  "${MANUAL_DRIVER_VERSION}" \
  "${NVIDIA_CONTAINER_TOOLKIT_VERSION}" \
  "$@"
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

pool = Path(sys.argv[1])
lock_path = Path(sys.argv[2])
manifest_path = Path(sys.argv[3])
driver_package = sys.argv[4]
target_distro = sys.argv[5]
manual_driver_version = sys.argv[6]
nvidia_container_toolkit_version = sys.argv[7]
requested_packages = list(sys.argv[8:])

packages = []
for deb in sorted(pool.glob("*.deb")):
    pkg_name = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Package"], text=True).strip()
    version = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Version"], text=True).strip()
    arch = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Architecture"], text=True).strip()
    sha256 = hashlib.sha256(deb.read_bytes()).hexdigest()
    packages.append(
        {
            "name": pkg_name,
            "version": version,
            "architecture": arch,
            "filename": f"pool/{deb.name}",
            "sha256": sha256,
        }
    )

created_at = dt.datetime.now(dt.timezone.utc).isoformat()
lock_payload = {
    "schema_version": 1,
    "distro": target_distro,
    "driver_package": driver_package,
    "manual_driver_version": manual_driver_version,
    "nvidia_container_toolkit_version": nvidia_container_toolkit_version,
    "created_at_utc": created_at,
    "requested_packages": requested_packages,
    "packages": packages,
}
lock_path.write_text(json.dumps(lock_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

manifest_payload = {
    "schema_version": 1,
    "distro": target_distro,
    "created_at_utc": created_at,
    "driver_package": driver_package,
    "manual_driver_version": manual_driver_version,
    "nvidia_container_toolkit_version": nvidia_container_toolkit_version,
    "package_count": len(packages),
    "pool_dir": "pool",
    "packages_index": "Packages.gz",
    "release_index": "Release",
    "versions_lock": "versions.lock.json",
    "requested_packages": requested_packages,
}
manifest_path.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

chown -R "${HOST_UID}:${HOST_GID}" /out
log_stage done
echo "host-apt-bundle:ok"
EOS
chmod +x "$tmp_runner"

docker run --rm \
  -e CHECK_ONLY="$CHECK_ONLY" \
  -e DRY_RUN="$DRY_RUN" \
  -e DRIVER_PACKAGE="$DRIVER_PACKAGE" \
  -e MANUAL_DRIVER_VERSION="$MANUAL_DRIVER_VERSION" \
  -e NVIDIA_CONTAINER_TOOLKIT_VERSION="$NVIDIA_CONTAINER_TOOLKIT_VERSION" \
  -e TARGET_DISTRO="$DISTRO" \
  -e APT_RETRIES="$APT_RETRIES" \
  -e HOST_UID="$(id -u)" \
  -e HOST_GID="$(id -g)" \
  --network host \
  -v "$HOST_ROOT":/out \
  -v "$tmp_runner":/tmp/build_host_apt_bundle_inner.sh:ro \
  "ubuntu:${docker_tag}" \
  bash /tmp/build_host_apt_bundle_inner.sh "${BASE_PACKAGES[@]}"
