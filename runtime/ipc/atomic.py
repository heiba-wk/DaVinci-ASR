from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


_IS_WINDOWS = os.name == "nt"
_WINDOWS_REPLACE_RETRY_SECONDS = 2.0
_WINDOWS_REPLACE_INITIAL_DELAY = 0.002
_WINDOWS_REPLACE_MAX_DELAY = 0.1
_WINDOWS_RETRYABLE_REPLACE_ERRORS = {5, 32}


def _is_retryable_replace_error(error: OSError) -> bool:
    return _IS_WINDOWS and getattr(error, "winerror", None) in (
        _WINDOWS_RETRYABLE_REPLACE_ERRORS
    )


def _replace_with_retry(source: Path, destination: Path) -> None:
    if not _IS_WINDOWS:
        os.replace(source, destination)
        return

    deadline = time.monotonic() + _WINDOWS_REPLACE_RETRY_SECONDS
    delay = _WINDOWS_REPLACE_INITIAL_DELAY
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if not _is_retryable_replace_error(error) or time.monotonic() >= deadline:
                raise
        time.sleep(delay)
        delay = min(delay * 2, _WINDOWS_REPLACE_MAX_DELAY)


def _atomic_write_json(
    path: str | Path,
    payload: dict[str, Any] | list[Any],
    *,
    durable: bool,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        _replace_with_retry(temporary, destination)
        if durable and os.name != "nt":
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def atomic_write_json(path: str | Path, payload: dict[str, Any] | list[Any]) -> Path:
    """Atomically persist durable protocol data, including file and directory fsync."""
    return _atomic_write_json(path, payload, durable=True)


def atomic_write_json_fast(
    path: str | Path, payload: dict[str, Any] | list[Any]
) -> Path:
    """Atomically replace ephemeral status data without forcing storage sync."""
    return _atomic_write_json(path, payload, durable=False)


def read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {Path(path).name}")
    return value
