from __future__ import annotations

import os
from pathlib import Path


class CancelToken:
    def __init__(self, job_directory: str | Path) -> None:
        self.job_directory = Path(job_directory)
        self.flag_path = self.job_directory / "cancel.flag"

    def is_cancelled(self) -> bool:
        return self.flag_path.is_file()

    def request(self) -> None:
        self.job_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.flag_path.with_name("cancel.flag.tmp")
        with temporary.open("wb") as stream:
            stream.write(b"cancel\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.flag_path)

    def clear(self) -> None:
        try:
            self.flag_path.unlink()
        except FileNotFoundError:
            pass

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise InterruptedError("Job cancelled")
