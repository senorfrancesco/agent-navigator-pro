from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

try:
    from orchestrator.operator_jobs import OperatorJob, OperatorJobStore
except ModuleNotFoundError:  # pragma: no cover - direct module import fallback
    from backend.orchestrator.operator_jobs import OperatorJob, OperatorJobStore

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
OFFLINE_SCRIPTS_ROOT = REPO_ROOT / "deploy" / "offline_bundle" / "scripts"
DEFAULT_BUNDLE_ARCHIVE = REPO_ROOT / "agent-nav-offline-bundle_v1.0.tar.gz"


from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class OperatorActionSpec:
    action_id: str
    title: str
    description: str
    group: str
    command: List[str]
    cwd: str
    privileged: bool = False
    runnable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_action_catalog() -> Dict[str, OperatorActionSpec]:
    repo = str(REPO_ROOT)
    offline_repo = str(REPO_ROOT / "deploy" / "offline_bundle")
    return {
        "runtime.native.launch": OperatorActionSpec(
            action_id="runtime.native.launch",
            title="Launch Native Runtime",
            description="Run canonical launcher flow for the native runtime path.",
            group="runtime",
            command=["bash", str(SCRIPTS_ROOT / "launcher.sh"), "--target", "native", "--profile", "adaptive"],
            cwd=repo,
        ),
        "runtime.container.launch": OperatorActionSpec(
            action_id="runtime.container.launch",
            title="Launch Dev Container Runtime",
            description="Legacy dev-only container launcher that builds from the current repository checkout.",
            group="runtime",
            command=["bash", str(SCRIPTS_ROOT / "launcher.sh"), "--target", "container", "--profile", "default"],
            cwd=repo,
        ),
        "deploy.bundle.build": OperatorActionSpec(
            action_id="deploy.bundle.build",
            title="Build Bundle",
            description="Run canonical build/export orchestration for the offline bundle.",
            group="deploy-build",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "build_bundle.sh")],
            cwd=offline_repo,
        ),
        "deploy.host_packages.build": OperatorActionSpec(
            action_id="deploy.host_packages.build",
            title="Build Host APT Bundle",
            description="Build exact-version offline apt bundle for the target distro matrix.",
            group="deploy-build",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "build_host_apt_bundle.sh")],
            cwd=offline_repo,
        ),
        "deploy.images.export": OperatorActionSpec(
            action_id="deploy.images.export",
            title="Export Images",
            description="Build and export offline Docker image archives.",
            group="deploy-build",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "export_images.sh")],
            cwd=offline_repo,
        ),
        "deploy.bundle.manifest": OperatorActionSpec(
            action_id="deploy.bundle.manifest",
            title="Generate Manifest",
            description="Regenerate manifest.json checksums for the offline bundle.",
            group="deploy-build",
            command=["python3", str(OFFLINE_SCRIPTS_ROOT / "generate_manifest.py")],
            cwd=offline_repo,
        ),
        "deploy.bundle.validate": OperatorActionSpec(
            action_id="deploy.bundle.validate",
            title="Validate Bundle",
            description="Validate deploy/offline_bundle completeness and runtime contract.",
            group="deploy-runtime",
            command=["python3", str(OFFLINE_SCRIPTS_ROOT / "validate_bundle.py"), "--mode", "deploy"],
            cwd=offline_repo,
        ),
        "deploy.bundle.pack": OperatorActionSpec(
            action_id="deploy.bundle.pack",
            title="Pack tar.gz",
            description="Pack deploy/offline_bundle into a single portable tar.gz archive.",
            group="deploy-build",
            command=["tar", "-czf", str(DEFAULT_BUNDLE_ARCHIVE), "-C", str((REPO_ROOT / "deploy").resolve()), "offline_bundle"],
            cwd=repo,
        ),
        "deploy.host.check": OperatorActionSpec(
            action_id="deploy.host.check",
            title="Check Host",
            description="Check host prerequisites for offline bundle deployment.",
            group="deploy-runtime",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "check_host.sh")],
            cwd=offline_repo,
        ),
        "deploy.host.install": OperatorActionSpec(
            action_id="deploy.host.install",
            title="Install Host APT Bundle",
            description="Install offline host package matrix and runtime configuration.",
            group="deploy-runtime",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "install_host_apt_bundle.sh")],
            cwd=offline_repo,
            privileged=True,
        ),
        "deploy.images.load": OperatorActionSpec(
            action_id="deploy.images.load",
            title="Load Images",
            description="Load Docker images from offline bundle archives.",
            group="deploy-runtime",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "load_images.sh")],
            cwd=offline_repo,
        ),
        "deploy.state.restore": OperatorActionSpec(
            action_id="deploy.state.restore",
            title="Restore State",
            description="Restore bundle state layout before compose deployment.",
            group="deploy-runtime",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "restore_state.sh")],
            cwd=offline_repo,
        ),
        "deploy.runtime.deploy": OperatorActionSpec(
            action_id="deploy.runtime.deploy",
            title="Deploy Runtime",
            description="Run canonical deploy orchestration for the offline bundle.",
            group="deploy-runtime",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "deploy.sh")],
            cwd=offline_repo,
        ),
        "deploy.runtime.run": OperatorActionSpec(
            action_id="deploy.runtime.run",
            title="Run Offline Bundle",
            description="Run convenience wrapper around deploy/start/tmux workflow.",
            group="deploy-runtime",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "run_offline_bundle.sh")],
            cwd=offline_repo,
        ),
        "deploy.runtime.verify": OperatorActionSpec(
            action_id="deploy.runtime.verify",
            title="Verify Runtime",
            description="Run post-start verification for the offline bundle runtime.",
            group="deploy-runtime",
            command=["bash", str(OFFLINE_SCRIPTS_ROOT / "verify_runtime.sh")],
            cwd=offline_repo,
        ),
    }


ACTION_CATALOG = build_action_catalog()
JOB_STORE = OperatorJobStore()


def list_actions() -> List[Dict[str, Any]]:
    return [spec.to_dict() for spec in ACTION_CATALOG.values()]


def get_action(action_id: str) -> OperatorActionSpec:
    if action_id not in ACTION_CATALOG:
        raise KeyError(action_id)
    return ACTION_CATALOG[action_id]


def get_job(job_id: str) -> OperatorJob | None:
    return JOB_STORE.get(job_id)


async def _stream_reader(stream: asyncio.StreamReader, job: OperatorJob, prefix: str) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        JOB_STORE.append_log(job.job_id, job.current_stage or "process", text, stream=prefix)


async def _run_job(job: OperatorJob, spec: OperatorActionSpec) -> None:
    JOB_STORE.mark_running(job.job_id)
    JOB_STORE.set_stage(job.job_id, spec.group, "running")
    JOB_STORE.append_log(job.job_id, spec.group, f"starting:{spec.action_id}", stream="system")
    JOB_STORE.append_log(job.job_id, spec.group, f"cwd:{spec.cwd}", stream="system")
    JOB_STORE.append_log(job.job_id, spec.group, f"command:{' '.join(spec.command)}", stream="system")
    process = await asyncio.create_subprocess_exec(
        *spec.command,
        cwd=spec.cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_task = asyncio.create_task(_stream_reader(process.stdout, job, "stdout"))
    stderr_task = asyncio.create_task(_stream_reader(process.stderr, job, "stderr"))
    exit_code = await process.wait()
    await asyncio.gather(stdout_task, stderr_task)
    JOB_STORE.set_stage(job.job_id, spec.group, "completed" if exit_code == 0 else "failed")
    JOB_STORE.finish_job(job.job_id, exit_code=exit_code)
    JOB_STORE.append_log(
        job.job_id,
        spec.group,
        f"{'completed' if exit_code == 0 else 'failed'}:{spec.action_id}:exit={exit_code}",
        stream="system",
    )


async def start_action_job(action_id: str, *, allow_privileged: bool = False) -> OperatorJob:
    spec = get_action(action_id)
    if spec.privileged and not allow_privileged:
        raise PermissionError(action_id)
    if not spec.runnable:
        raise RuntimeError(f"action-not-runnable:{action_id}")

    job = JOB_STORE.create_job(
        action_id=spec.action_id,
        title=spec.title,
        command=list(spec.command),
        cwd=spec.cwd,
        privileged=spec.privileged,
    )
    JOB_STORE.append_log(job.job_id, spec.group, f"queued:{spec.action_id}", stream="system")
    loop = asyncio.get_running_loop()
    loop.create_task(_run_job(job, spec))
    return job
