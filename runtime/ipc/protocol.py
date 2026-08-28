from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.constants import PROTOCOL_VERSION
from runtime.core.paths import RuntimePaths
from runtime.core.types import JobRequest, JobStatus
from runtime.ipc.atomic import atomic_write_json, atomic_write_json_fast, read_json


@dataclass(frozen=True, slots=True)
class JobFiles:
    directory: Path
    request: Path
    status: Path
    result: Path
    cancel: Path
    ack: Path

    @classmethod
    def for_job(cls, paths: RuntimePaths, job_id: str) -> "JobFiles":
        directory = paths.ipc / job_id
        return cls(
            directory=directory,
            request=directory / "request.json",
            status=directory / "status.json",
            result=directory / "result.json",
            cancel=directory / "cancel.flag",
            ack=directory / "ack.flag",
        )


def write_request(paths: RuntimePaths, request: JobRequest) -> JobFiles:
    files = JobFiles.for_job(paths, request.job_id)
    files.directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(files.request, request.to_dict())
    return files


def read_request(path: str | Path) -> JobRequest:
    return JobRequest.from_dict(read_json(path))


def write_status(
    files: JobFiles,
    state: str,
    progress: int,
    message: str = "",
    error: str = "",
    download_status: str = "",
    download_source: str = "",
    download_next_source: str = "",
    download_current_file: str = "",
    download_model_name: str = "",
    downloaded_bytes: int = 0,
    total_bytes: int = 0,
    download_speed_bps: int = 0,
) -> JobStatus:
    status = JobStatus(
        protocol=PROTOCOL_VERSION,
        job_id=files.directory.name,
        state=state,
        progress=progress,
        message=message,
        error=error,
        download_status=download_status,
        download_source=download_source,
        download_next_source=download_next_source,
        download_current_file=download_current_file,
        download_model_name=download_model_name,
        downloaded_bytes=downloaded_bytes,
        total_bytes=total_bytes,
        download_speed_bps=download_speed_bps,
        updated_at=time.time(),
    )
    atomic_write_json_fast(files.status, status.to_dict())
    return status


def terminal_state(files: JobFiles) -> str | None:
    try:
        state = str(read_json(files.status).get("state", ""))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return state if state in {"done", "cancelled", "error"} else None


def cleanup_job_temp(paths: RuntimePaths, job_id: str) -> None:
    target = paths.temp / job_id
    if target.is_dir() and target.parent == paths.temp:
        shutil.rmtree(target)


def write_result(files: JobFiles, payload: dict[str, Any]) -> None:
    atomic_write_json(files.result, payload)
