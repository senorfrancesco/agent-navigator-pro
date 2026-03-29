from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def _run_command(command: Sequence[str], *, cwd: Path) -> None:
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _offline_deploy(args: argparse.Namespace) -> int:
    bundle_root = Path(args.bundle_root).resolve()
    script_dir = bundle_root / "scripts"
    compose_file = bundle_root / "compose.offline.yaml"
    env_file = bundle_root / "env.bundle"

    if os.environ.get("AGENT_NAVIGATOR_TEST_MODE") == "1":
        print(
            "operator-shell-compat:test-mode "
            f"entrypoint=offline-deploy bundle_root={bundle_root} "
            f"skip_host_check={int(args.skip_host_check)} skip_image_load={int(args.skip_image_load)}"
        )
        return 0

    if not args.skip_host_check:
        _run_command(["bash", str(script_dir / "check_host.sh")], cwd=bundle_root)
        host_2404 = bundle_root / "host_packages" / "ubuntu-24.04" / "versions.lock.json"
        host_2204 = bundle_root / "host_packages" / "ubuntu-22.04" / "versions.lock.json"
        if host_2404.exists() or host_2204.exists():
            _run_command(["python3", str(script_dir / "check_host_packages.py")], cwd=bundle_root)
            _run_command(["python3", str(script_dir / "verify_host_runtime.py")], cwd=bundle_root)

    _run_command(["python3", str(script_dir / "validate_bundle.py"), "--mode", "deploy"], cwd=bundle_root)
    _run_command(["python3", str(script_dir / "preflight_runtime.py"), "--env-file", str(env_file)], cwd=bundle_root)

    if not args.skip_image_load:
        _run_command(["bash", str(script_dir / "load_images.sh")], cwd=bundle_root)

    _run_command(["bash", str(script_dir / "restore_state.sh")], cwd=bundle_root)
    _run_command(["docker", "compose", "-f", str(compose_file), "--env-file", str(env_file), "up", "-d"], cwd=bundle_root)
    _run_command(["docker", "compose", "-f", str(compose_file), "--env-file", str(env_file), "ps"], cwd=bundle_root)
    _run_command(["bash", str(script_dir / "verify_runtime.sh")], cwd=bundle_root)
    print("deploy:ok")
    return 0


def _offline_run(args: argparse.Namespace) -> int:
    bundle_root = Path(args.bundle_root).resolve()
    script_dir = bundle_root / "scripts"
    compose_file = bundle_root / "compose.offline.yaml"
    env_file = bundle_root / "env.bundle"

    if os.environ.get("AGENT_NAVIGATOR_TEST_MODE") == "1":
        print(
            "operator-shell-compat:test-mode "
            f"entrypoint=offline-run bundle_root={bundle_root} "
            f"with_monitoring={int(args.with_monitoring)} start_tmux={int(not args.no_tmux)} "
            f"attach_tmux={int(args.attach_tmux)} skip_host_check={int(args.skip_host_check)} "
            f"skip_image_load={int(args.skip_image_load)} tmux_session={args.tmux_session}"
        )
        return 0

    if not env_file.exists():
        print("missing:env.bundle", file=sys.stderr)
        print("hint: cp env.bundle.example env.bundle", file=sys.stderr)
        return 1

    deploy_args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "offline-deploy",
        "--bundle-root",
        str(bundle_root),
    ]
    if args.skip_image_load:
        deploy_args.append("--skip-image-load")
    if args.skip_host_check:
        deploy_args.append("--skip-host-check")
    _run_command(deploy_args, cwd=bundle_root)

    if args.with_monitoring:
        _run_command(
            ["docker", "compose", "-f", str(compose_file), "--env-file", str(env_file), "--profile", "monitoring", "up", "-d"],
            cwd=bundle_root,
        )

    if not args.no_tmux:
        _run_command(["bash", str(script_dir / "launch_tmux_workspace.sh"), args.tmux_session], cwd=bundle_root)
        if args.attach_tmux:
            os.execvp("tmux", ["tmux", "attach", "-t", args.tmux_session])

    env_map: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key.strip()] = value.strip().strip("\"'")

    print("run-offline-bundle:ok")
    print(f"Chainlit:   http://localhost:{env_map.get('CHAINLIT_PORT', '3000')}")
    print(f"Agent API:  http://localhost:{env_map.get('AGENT_API_PORT', '8000')}")
    print(f"UMS:        http://localhost:{env_map.get('UMS_PORT', '8090')}")
    if args.with_monitoring:
        print(f"Prometheus: http://localhost:{env_map.get('PROMETHEUS_PORT', '9090')}")
        print(f"Grafana:    http://localhost:{env_map.get('GRAFANA_PORT', '3002')}")
    if not args.no_tmux:
        print(f"tmux:       tmux attach -t {args.tmux_session}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator_shell_compat.py",
        description="Compatibility wrappers for shell entrypoints that now delegate offline deploy/run orchestration to the Python operator control plane.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy_parser = subparsers.add_parser(
        "offline-deploy",
        help="Compatibility wrapper for deploy/offline_bundle/scripts/deploy.sh",
    )
    deploy_parser.add_argument("--bundle-root", required=True)
    deploy_parser.add_argument("--skip-image-load", action="store_true")
    deploy_parser.add_argument("--skip-host-check", action="store_true")
    deploy_parser.set_defaults(func=_offline_deploy)

    run_parser = subparsers.add_parser(
        "offline-run",
        help="Compatibility wrapper for deploy/offline_bundle/scripts/run_offline_bundle.sh",
    )
    run_parser.add_argument("--bundle-root", required=True)
    run_parser.add_argument("--with-monitoring", action="store_true")
    run_parser.add_argument("--no-tmux", action="store_true")
    run_parser.add_argument("--attach-tmux", action="store_true")
    run_parser.add_argument("--skip-image-load", action="store_true")
    run_parser.add_argument("--skip-host-check", action="store_true")
    run_parser.add_argument("--tmux-session", default="agent-nav-offline")
    run_parser.set_defaults(func=_offline_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
