from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BUNDLE_ROOT = SCRIPT_DIR.parent
MANIFEST_PATH = BUNDLE_ROOT / "manifest.json"
ENV_PATH = BUNDLE_ROOT / "env.bundle"

REQUIRED_FILES = [
    "compose.offline.yaml",
    "env.bundle",
    "manifest.json",
    "scripts/check_host_packages.py",
    "scripts/deploy.sh",
    "scripts/install_host_apt_bundle.sh",
    "scripts/preflight_runtime.py",
    "scripts/run_offline_bundle.sh",
    "scripts/restore_state.sh",
    "scripts/verify_host_runtime.py",
    "scripts/verify_runtime.sh",
]

REQUIRED_DIRS = [
    "state/backend-data",
    "state/chainlit-data",
    "state/uploads",
    "state/exported-env",
]

REQUIRED_MODEL_FILE_ENV_KEYS = [
    "MODEL_PATH_LLM",
]

OPTIONAL_MODEL_FILE_ENV_KEYS = [
    "MODEL_PATH_VLM",
    "MMPROJ_PATH",
]

REQUIRED_MODEL_DIR_ENV_KEYS = [
    "MODEL_PATH_EMBEDDING_INTENT",
    "MODEL_PATH_EMBEDDING_RETRIEVAL",
]

OPTIONAL_MODEL_DIR_ENV_KEYS = [
    "MODEL_PATH_E5_LEGAL",
    "MODEL_PATH_RUBERT",
]

MODEL_SOURCE_MODES = {"bundle_layout", "external_host_mounts"}

REQUIRED_EXTERNAL_FILE_ENV_KEYS = [
    "HOST_MODEL_PATH_LLM",
]

OPTIONAL_EXTERNAL_FILE_ENV_KEYS = [
    "HOST_MODEL_PATH_VLM",
    "HOST_MMPROJ_PATH",
]

REQUIRED_EXTERNAL_DIR_ENV_KEYS = [
    "HOST_MODEL_PATH_EMBEDDING_INTENT",
    "HOST_MODEL_PATH_EMBEDDING_RETRIEVAL",
]


def parse_env(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip().strip("\"'")
    return payload


def to_bundle_model_path(container_path: str, models_root: Path) -> Path:
    prefix = "/app/backend/models/"
    if not container_path.startswith(prefix):
        raise ValueError(f"model-path-outside-bundle-layout:{container_path}")
    suffix = container_path[len(prefix):].strip("/")
    return models_root / suffix


def validate_required_paths(errors: list[str], bundle_root: Path, env_path: Path, manifest_path: Path, models_root: Path) -> None:
    for rel_path in REQUIRED_FILES:
        if rel_path == "env.bundle":
            path = env_path
        elif rel_path == "manifest.json":
            path = manifest_path
        else:
            path = bundle_root / rel_path
        if not path.exists():
            errors.append(f"missing-required-file:{rel_path}")
    for rel_path in REQUIRED_DIRS:
        path = bundle_root / rel_path
        if not path.exists():
            errors.append(f"missing-required-dir:{rel_path}")


def validate_manifest(errors: list[str], bundle_root: Path, manifest_path: Path, require_images: bool) -> None:
    if not manifest_path.exists():
        errors.append("missing-required-file:manifest.json")
        return
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel_path in payload.get("required_state_paths", []):
        if not (bundle_root / rel_path).exists():
            errors.append(f"missing-required-state-path:{rel_path}")
    for rel_path in payload.get("required_state_files", []):
        if not (bundle_root / rel_path).exists():
            errors.append(f"missing-required-state-file:{rel_path}")
    if require_images:
        for image in payload.get("images", []):
            archive = str(image.get("archive") or "").strip()
            if archive and not (bundle_root / archive).exists():
                errors.append(f"missing-image-archive:{archive}")
    for rel_path in payload.get("checksums", {}).keys():
        if not (bundle_root / rel_path).exists():
            errors.append(f"missing-checksummed-path:{rel_path}")


def validate_host_packages(errors: list[str], bundle_root: Path) -> None:
    for distro in ("ubuntu-22.04", "ubuntu-24.04"):
        host_root = bundle_root / "host_packages" / distro
        if not host_root.exists():
            continue
        for rel_name in ("pool", "versions.lock.json", "manifest.json", "Packages", "Packages.gz", "Release"):
            path = host_root / rel_name
            if not path.exists():
                errors.append(f"missing-host-package-path:host_packages/{distro}/{rel_name}")


def validate_container_target(errors: list[str], key: str, value: str, *, expect_file: bool) -> None:
    raw = value.strip()
    if not raw:
        errors.append(f"missing-env:{key}")
        return
    if not raw.startswith("/"):
        errors.append(f"invalid-container-path:{key}:{raw}")
        return
    if expect_file and raw.endswith("/"):
        errors.append(f"invalid-container-file-path:{key}:{raw}")


def validate_host_source(errors: list[str], key: str, value: str, *, expect_file: bool) -> None:
    raw = value.strip()
    if not raw:
        errors.append(f"missing-env:{key}")
        return
    candidate = Path(raw)
    if not candidate.is_absolute():
        errors.append(f"invalid-host-path-not-absolute:{key}:{raw}")
        return
    if expect_file and not candidate.is_file():
        errors.append(f"missing-host-model-file:{key}:{candidate}")
    if not expect_file and not candidate.is_dir():
        errors.append(f"missing-host-model-dir:{key}:{candidate}")


def validate_env_and_models(errors: list[str], bundle_root: Path, env_path: Path, models_root: Path) -> None:
    if not env_path.exists():
        errors.append("missing-required-file:env.bundle")
        return

    payload = parse_env(env_path)

    model_source_mode = payload.get("MODEL_SOURCE_MODE", "bundle_layout").strip() or "bundle_layout"
    if model_source_mode not in MODEL_SOURCE_MODES:
        errors.append(f"invalid-model-source-mode:{model_source_mode}")
        return

    vlm_path = payload.get("MODEL_PATH_VLM", "").strip()
    mmproj_path = payload.get("MMPROJ_PATH", "").strip()
    if bool(vlm_path) != bool(mmproj_path):
        errors.append("incomplete-vlm-config:MODEL_PATH_VLM-and-MMPROJ_PATH-must-both-be-set-or-empty")

    if model_source_mode == "bundle_layout":
        if not models_root.exists():
            errors.append("missing-required-dir:models")
        for key in REQUIRED_MODEL_FILE_ENV_KEYS:
            value = payload.get(key, "").strip()
            if not value:
                errors.append(f"missing-env:{key}")
                continue
            try:
                bundle_path = to_bundle_model_path(value, models_root)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not bundle_path.is_file():
                errors.append(f"missing-model-file:{key}:{bundle_path.relative_to(bundle_root)}")

        for key in OPTIONAL_MODEL_FILE_ENV_KEYS:
            value = payload.get(key, "").strip()
            if not value:
                continue
            try:
                bundle_path = to_bundle_model_path(value, models_root)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not bundle_path.is_file():
                errors.append(f"missing-model-file:{key}:{bundle_path.relative_to(bundle_root)}")

        for key in REQUIRED_MODEL_DIR_ENV_KEYS:
            value = payload.get(key, "").strip()
            if not value:
                errors.append(f"missing-env:{key}")
                continue
            try:
                bundle_path = to_bundle_model_path(value, models_root)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not bundle_path.is_dir():
                errors.append(f"missing-model-dir:{key}:{bundle_path.relative_to(bundle_root)}")

        for key in OPTIONAL_MODEL_DIR_ENV_KEYS:
            value = payload.get(key, "").strip()
            if not value:
                continue
            try:
                bundle_path = to_bundle_model_path(value, models_root)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not bundle_path.is_dir():
                errors.append(f"missing-model-dir:{key}:{bundle_path.relative_to(bundle_root)}")
        return

    for key in REQUIRED_MODEL_FILE_ENV_KEYS:
        validate_container_target(errors, key, payload.get(key, ""), expect_file=True)
    for key in OPTIONAL_MODEL_FILE_ENV_KEYS:
        value = payload.get(key, "").strip()
        if value:
            validate_container_target(errors, key, value, expect_file=True)
    for key in REQUIRED_MODEL_DIR_ENV_KEYS:
        validate_container_target(errors, key, payload.get(key, ""), expect_file=False)
    for key in OPTIONAL_MODEL_DIR_ENV_KEYS:
        value = payload.get(key, "").strip()
        if value:
            validate_container_target(errors, key, value, expect_file=False)

    for key in REQUIRED_EXTERNAL_FILE_ENV_KEYS:
        validate_host_source(errors, key, payload.get(key, ""), expect_file=True)
    host_vlm = payload.get("HOST_MODEL_PATH_VLM", "").strip()
    host_mmproj = payload.get("HOST_MMPROJ_PATH", "").strip()
    if bool(host_vlm) != bool(host_mmproj):
        errors.append("incomplete-external-vlm-config:HOST_MODEL_PATH_VLM-and-HOST_MMPROJ_PATH-must-both-be-set-or-empty")
    for key in OPTIONAL_EXTERNAL_FILE_ENV_KEYS:
        value = payload.get(key, "").strip()
        if value:
            validate_host_source(errors, key, value, expect_file=True)
    for key in REQUIRED_EXTERNAL_DIR_ENV_KEYS:
        validate_host_source(errors, key, payload.get(key, ""), expect_file=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate offline bundle completeness and self-contained runtime contract.")
    parser.add_argument(
        "--mode",
        choices=("build", "deploy"),
        default="deploy",
        help="build: validate bundle after export; deploy: require image archives too.",
    )
    parser.add_argument(
        "--skip-manifest",
        action="store_true",
        help="Skip manifest-based validation and only check bundle files/env/models.",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
        help="Path to env bundle file to validate (default: deploy/offline_bundle/env.bundle).",
    )
    parser.add_argument(
        "--models-dir",
        default=str(BUNDLE_ROOT / "models"),
        help="Path to models directory to validate against env container paths.",
    )
    args = parser.parse_args()

    bundle_root = BUNDLE_ROOT
    env_path = Path(args.env_file).resolve()
    manifest_path = MANIFEST_PATH
    models_root = Path(args.models_dir).resolve()

    errors: list[str] = []
    validate_required_paths(errors, bundle_root, env_path, manifest_path, models_root)
    if not args.skip_manifest:
        validate_manifest(errors, bundle_root, manifest_path, require_images=args.mode == "deploy")
    validate_host_packages(errors, bundle_root)
    validate_env_and_models(errors, bundle_root, env_path, models_root)

    if errors:
        for error in errors:
            print(error)
        return 1

    print(f"bundle-validate:{args.mode}:ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
