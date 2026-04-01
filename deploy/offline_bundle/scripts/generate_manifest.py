from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BUNDLE_ROOT = SCRIPT_DIR.parent
TEMPLATE_PATH = BUNDLE_ROOT / "manifest.template.json"
OUTPUT_PATH = BUNDLE_ROOT / "manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError:
        return ""
    return digest.hexdigest()


def git_output(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def main() -> None:
    payload = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    payload["build"]["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["build"]["source_commit"] = git_output(["git", "rev-parse", "HEAD"])
    payload["build"]["source_branch"] = git_output(["git", "branch", "--show-current"])
    payload["build"]["built_by_host"] = socket.gethostname()

    checksums: dict[str, str] = {}
    for rel_path in ("images", "state", "models", "wheelhouse", "host_packages"):
        root = BUNDLE_ROOT / rel_path
        if not root.exists():
            continue
        for file_path in sorted(path for path in root.rglob("*") if path.is_file() and path.name != ".gitkeep"):
            checksum = sha256_file(file_path)
            if checksum:
                checksums[str(file_path.relative_to(BUNDLE_ROOT))] = checksum

    payload["checksums"] = checksums
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest-written:{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
