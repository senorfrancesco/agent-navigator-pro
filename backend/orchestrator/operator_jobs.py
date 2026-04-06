from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OperatorJobLogEntry:
    timestamp: str
    stage: str
    stream: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperatorJobStage:
    stage: str
    status: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperatorJob:
    job_id: str
    action_id: str
    title: str
    command: List[str]
    cwd: str
    status: str
    privileged: bool
    created_at: str
    current_stage: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    stages: List[OperatorJobStage] = field(default_factory=list)
    logs: List[OperatorJobLogEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["stages"] = [stage.to_dict() for stage in self.stages]
        payload["logs"] = [entry.to_dict() for entry in self.logs]
        return payload


class OperatorJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, OperatorJob] = {}

    def create_job(
        self,
        *,
        action_id: str,
        title: str,
        command: List[str],
        cwd: str,
        privileged: bool,
    ) -> OperatorJob:
        job = OperatorJob(
            job_id=str(uuid.uuid4()),
            action_id=action_id,
            title=title,
            command=list(command),
            cwd=cwd,
            status="queued",
            privileged=privileged,
            created_at=utc_now(),
        )
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Optional[OperatorJob]:
        return self._jobs.get(job_id)

    def mark_running(self, job_id: str) -> OperatorJob:
        job = self._require(job_id)
        job.status = "running"
        job.started_at = utc_now()
        return job

    def mark_cancelling(self, job_id: str) -> OperatorJob:
        job = self._require(job_id)
        job.status = "cancelling"
        return job

    def set_stage(self, job_id: str, stage: str, status: str) -> OperatorJob:
        job = self._require(job_id)
        job.current_stage = stage
        job.stages.append(
            OperatorJobStage(
                stage=stage,
                status=status,
                timestamp=utc_now(),
            )
        )
        return job

    def append_log(self, job_id: str, stage: str, message: str, *, stream: str) -> OperatorJob:
        job = self._require(job_id)
        job.logs.append(
            OperatorJobLogEntry(
                timestamp=utc_now(),
                stage=stage,
                stream=stream,
                message=message,
            )
        )
        if len(job.logs) > 2000:
            del job.logs[:500]
        return job

    def finish_job(self, job_id: str, *, exit_code: int) -> OperatorJob:
        job = self._require(job_id)
        job.exit_code = exit_code
        job.finished_at = utc_now()
        job.status = "completed" if exit_code == 0 else "failed"
        return job

    def finish_cancelled(self, job_id: str) -> OperatorJob:
        job = self._require(job_id)
        job.exit_code = None
        job.finished_at = utc_now()
        job.status = "cancelled"
        return job

    def _require(self, job_id: str) -> OperatorJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job
