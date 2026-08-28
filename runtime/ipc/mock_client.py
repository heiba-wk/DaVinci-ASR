from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from runtime.constants import PROTOCOL_VERSION
from runtime.core.paths import RuntimePaths
from runtime.core.types import JobRequest, SubtitleOptions, TimelineSpec
from runtime.ipc.atomic import read_json
from runtime.ipc.protocol import write_request


def submit_and_wait(
    paths: RuntimePaths,
    audio: str,
    *,
    language: str = "Auto",
    prompt: str = "",
    fps: str = "24",
    timeout: float = 3600,
) -> dict:
    request = JobRequest(
        protocol=PROTOCOL_VERSION,
        job_id=f"mock-{uuid.uuid4().hex[:16]}",
        action="transcribe",
        audio_path=str(Path(audio).expanduser().resolve()),
        language=language,
        prompt=prompt,
        subtitle=SubtitleOptions(),
        timeline=TimelineSpec(fps=fps, start_frame=0),
    )
    files = write_request(paths, request)
    started = time.monotonic()
    previous = ""
    while time.monotonic() - started < timeout:
        try:
            status = read_json(files.status)
        except FileNotFoundError:
            time.sleep(0.2)
            continue
        marker = f"{status.get('state')}:{status.get('progress')}:{status.get('message')}"
        if marker != previous:
            print(marker, flush=True)
            previous = marker
        if status.get("state") == "done":
            return read_json(files.result)
        if status.get("state") in {"cancelled", "error"}:
            raise RuntimeError(status.get("error") or status.get("message"))
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {request.job_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DaVinci ASR File IPC mock client")
    parser.add_argument("audio")
    parser.add_argument("--data-root")
    parser.add_argument("--language", default="Auto")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--fps", default="24")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args(argv)
    paths = RuntimePaths.discover(args.data_root)
    paths.ensure()
    result = submit_and_wait(
        paths,
        args.audio,
        language=args.language,
        prompt=args.prompt,
        fps=args.fps,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
