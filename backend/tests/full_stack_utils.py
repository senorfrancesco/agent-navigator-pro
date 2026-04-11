from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Tuple

import requests


Runner = Callable[..., subprocess.CompletedProcess[str]]
RequestGet = Callable[..., Any]
SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


@dataclass(frozen=True)
class ProbeSpec:
    name: str
    url: str
    timeout_s: float = 2.0


@dataclass(frozen=True)
class ProbeOutcome:
    name: str
    url: str
    ok: bool
    detail: str
    status_code: Optional[int] = None


@dataclass(frozen=True)
class CommandOutcome:
    command: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class ReadinessOutcome:
    ready: bool
    attempts: int
    elapsed_s: float
    probe_outcomes: Tuple[ProbeOutcome, ...]


@dataclass(frozen=True)
class SmokeDiagnostics:
    ps: CommandOutcome
    logs: CommandOutcome
    probe_outcomes: Tuple[ProbeOutcome, ...]


@dataclass(frozen=True)
class SmokeRunResult:
    ok: bool
    started: bool
    readiness: ReadinessOutcome
    up: CommandOutcome
    down: CommandOutcome
    diagnostics: Optional[SmokeDiagnostics]
    error: Optional[str]
    elapsed_s: float

    def summary(self) -> str:
        parts = [f"ok={self.ok}", f"started={self.started}", f"elapsed_s={self.elapsed_s:.1f}"]
        if self.error:
            parts.append(f"error={self.error}")
        if self.diagnostics:
            parts.append(f"ps_rc={self.diagnostics.ps.returncode}")
            parts.append(f"logs_rc={self.diagnostics.logs.returncode}")
        return " | ".join(parts)


def default_probe_specs() -> Tuple[ProbeSpec, ...]:
    return (
        ProbeSpec(name="ums_health", url="http://localhost:8090/health"),
        ProbeSpec(name="document_server_health", url="http://localhost:8001/health"),
        ProbeSpec(name="legal_server_health", url="http://localhost:8002/health"),
        ProbeSpec(name="agent_api_status", url="http://localhost:8000/status"),
        ProbeSpec(name="chainlit_ui", url="http://localhost:3000/"),
    )


def docker_available(*, runner: Runner = subprocess.run) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = runner(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return getattr(result, "returncode", 1) == 0


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path,
    runner: Runner = subprocess.run,
) -> CommandOutcome:
    try:
        result = runner(
            list(args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return CommandOutcome(command=tuple(args), returncode=127, stdout="", stderr=str(exc))
    except OSError as exc:
        return CommandOutcome(command=tuple(args), returncode=1, stdout="", stderr=str(exc))

    return CommandOutcome(
        command=tuple(args),
        returncode=getattr(result, "returncode", 1),
        stdout=getattr(result, "stdout", "") or "",
        stderr=getattr(result, "stderr", "") or "",
    )


def probe_url(
    spec: ProbeSpec,
    *,
    request_get: RequestGet = requests.get,
) -> ProbeOutcome:
    try:
        response = request_get(spec.url, timeout=spec.timeout_s)
        status_code = getattr(response, "status_code", None)
        ok = 200 <= int(status_code) < 300 if status_code is not None else False
        detail = "ok" if ok else f"status={status_code}"
        return ProbeOutcome(name=spec.name, url=spec.url, ok=ok, detail=detail, status_code=status_code)
    except Exception as exc:
        return ProbeOutcome(name=spec.name, url=spec.url, ok=False, detail=str(exc), status_code=None)


def wait_for_readiness(
    probe_specs: Sequence[ProbeSpec],
    *,
    timeout_s: float = 300.0,
    poll_interval_s: float = 5.0,
    request_get: RequestGet = requests.get,
    sleep: SleepFn = time.sleep,
    clock: ClockFn = time.monotonic,
) -> ReadinessOutcome:
    start = clock()
    attempts = 0
    last_outcomes: Tuple[ProbeOutcome, ...] = tuple()

    while True:
        attempts += 1
        last_outcomes = tuple(probe_url(spec, request_get=request_get) for spec in probe_specs)
        if all(outcome.ok for outcome in last_outcomes):
            return ReadinessOutcome(
                ready=True,
                attempts=attempts,
                elapsed_s=clock() - start,
                probe_outcomes=last_outcomes,
            )

        if clock() - start >= timeout_s:
            return ReadinessOutcome(
                ready=False,
                attempts=attempts,
                elapsed_s=clock() - start,
                probe_outcomes=last_outcomes,
            )

        sleep(poll_interval_s)


class ComposeSmokeHarness:
    def __init__(
        self,
        *,
        project_root: Optional[Path | str] = None,
        probe_specs: Optional[Sequence[ProbeSpec]] = None,
        runner: Runner = subprocess.run,
        request_get: RequestGet = requests.get,
        sleep: SleepFn = time.sleep,
        clock: ClockFn = time.monotonic,
    ) -> None:
        self.project_root = Path(project_root or Path(__file__).resolve().parents[3])
        self.probe_specs = tuple(probe_specs or default_probe_specs())
        self.runner = runner
        self.request_get = request_get
        self.sleep = sleep
        self.clock = clock

    def up(self) -> CommandOutcome:
        return _run_command(
            ["docker", "compose", "up", "-d", "--build", "--remove-orphans"],
            cwd=self.project_root,
            runner=self.runner,
        )

    def down(self) -> CommandOutcome:
        return _run_command(
            ["docker", "compose", "down", "--remove-orphans"],
            cwd=self.project_root,
            runner=self.runner,
        )

    def ps(self) -> CommandOutcome:
        return _run_command(
            ["docker", "compose", "ps", "--all", "--no-trunc"],
            cwd=self.project_root,
            runner=self.runner,
        )

    def logs(self) -> CommandOutcome:
        return _run_command(
            ["docker", "compose", "logs", "--no-color", "--tail", "200"],
            cwd=self.project_root,
            runner=self.runner,
        )

    def collect_diagnostics(self, probe_outcomes: Sequence[ProbeOutcome]) -> SmokeDiagnostics:
        return SmokeDiagnostics(
            ps=self.ps(),
            logs=self.logs(),
            probe_outcomes=tuple(probe_outcomes),
        )

    def wait_for_readiness(self, *, timeout_s: float = 300.0, poll_interval_s: float = 5.0) -> ReadinessOutcome:
        return wait_for_readiness(
            self.probe_specs,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            request_get=self.request_get,
            sleep=self.sleep,
            clock=self.clock,
        )

    def run(self, *, timeout_s: float = 300.0, poll_interval_s: float = 5.0) -> SmokeRunResult:
        started = False
        error: Optional[str] = None
        diagnostics: Optional[SmokeDiagnostics] = None
        up_result = CommandOutcome(command=("docker", "compose", "up"), returncode=1, stdout="", stderr="")
        down_result = CommandOutcome(command=("docker", "compose", "down"), returncode=1, stdout="", stderr="")
        readiness = ReadinessOutcome(ready=False, attempts=0, elapsed_s=0.0, probe_outcomes=tuple())
        start = self.clock()

        try:
            up_result = self.up()
            started = up_result.ok
            if not up_result.ok:
                error = "docker compose up failed"
                diagnostics = self.collect_diagnostics(())
            else:
                readiness = self.wait_for_readiness(timeout_s=timeout_s, poll_interval_s=poll_interval_s)
                if not readiness.ready:
                    error = "readiness timeout"
                    diagnostics = self.collect_diagnostics(readiness.probe_outcomes)
        except Exception as exc:
            error = str(exc)
            diagnostics = diagnostics or self.collect_diagnostics(readiness.probe_outcomes)
        finally:
            try:
                down_result = self.down()
            except Exception as exc:
                down_result = CommandOutcome(
                    command=("docker", "compose", "down"),
                    returncode=1,
                    stdout="",
                    stderr=str(exc),
                )
                error = error or str(exc)

        ok = bool(started and readiness.ready and down_result.ok and error is None)
        if not ok and diagnostics is None:
            diagnostics = self.collect_diagnostics(readiness.probe_outcomes)
        return SmokeRunResult(
            ok=ok,
            started=started,
            readiness=readiness,
            up=up_result,
            down=down_result,
            diagnostics=diagnostics,
            error=error,
            elapsed_s=self.clock() - start,
        )
