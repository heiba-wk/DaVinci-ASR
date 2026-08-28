from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from pathlib import Path

from runtime.constants import ALIGNMENT_LANGUAGES, PROTOCOL_VERSION, RUNTIME_VERSION
from runtime.core.hardware import select_hardware
from runtime.core.diagnostics import diagnostics_payload
from runtime.core.paths import RuntimePaths
from runtime.core.types import JobRequest, SubtitleOptions, TimelineSpec
from runtime.inference.model_manager import ModelManager


def _configure_logging(paths: RuntimePaths, debug: bool = False) -> None:
    paths.ensure()
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(paths.logs / "runtime.log", encoding="utf-8")],
    )


def _paths(args: argparse.Namespace) -> RuntimePaths:
    value = RuntimePaths.discover(args.data_root, args.models_root)
    value.ensure()
    return value


def command_doctor(args: argparse.Namespace) -> int:
    paths = _paths(args)
    profile = select_hardware(paths, force_cpu=args.cpu)
    models = ModelManager(paths)
    model_status = models.status()
    payload = {
        "runtime_version": RUNTIME_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "python": sys.version.split()[0],
        "hardware": profile.to_dict(),
        "model_status": model_status,
        "diagnostics": diagnostics_payload(profile, model_status),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_download(args: argparse.Namespace) -> int:
    from runtime.inference.model_manager import DownloadProgress

    paths = _paths(args)
    manager = ModelManager(paths)

    def report(progress: DownloadProgress) -> None:
        print(
            f"{progress.percent:3d}% {progress.message} "
            f"({progress.downloaded_bytes}/{progress.total_bytes} bytes)",
            flush=True,
        )

    manager.download_all(report, ui_language=args.ui_language, source=args.source)
    return 0


def _require_local_model(manager: ModelManager, key: str) -> None:
    ready, reason = manager.verify(key, full_hash=True)
    if not ready:
        raise RuntimeError(f"{key} model is not completely installed: {reason}")


def command_transcribe(args: argparse.Namespace) -> int:
    from runtime.audio.normalize import load_and_normalize
    from runtime.inference.asr import QwenASREngine

    paths = _paths(args)
    profile = select_hardware(paths, force_cpu=args.cpu)
    manager = ModelManager(paths)
    _require_local_model(manager, "asr")
    waveform, _ = load_and_normalize(args.audio)
    engine = QwenASREngine(manager.model_path("asr"), profile)
    engine.load()
    try:
        result = engine.transcribe(
            waveform,
            language=None if args.language == "Auto" else args.language,
            prompt=args.prompt,
        )
    finally:
        engine.unload()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_align(args: argparse.Namespace) -> int:
    from runtime.audio.normalize import load_and_normalize
    from runtime.inference.aligner import QwenForcedAlignerEngine

    if args.language not in ALIGNMENT_LANGUAGES:
        raise ValueError("align requires one of the 11 forced-alignment languages")
    paths = _paths(args)
    profile = select_hardware(paths, force_cpu=args.cpu)
    manager = ModelManager(paths)
    _require_local_model(manager, "forced_aligner")
    waveform, _ = load_and_normalize(args.audio)
    engine = QwenForcedAlignerEngine(manager.model_path("forced_aligner"), profile)
    engine.load()
    try:
        result = engine.align(waveform, args.text, args.language)
    finally:
        engine.unload()
    print(json.dumps([item.to_dict() for item in result], ensure_ascii=False, indent=2))
    return 0


def command_full(args: argparse.Namespace) -> int:
    from runtime.pipeline import TranscriptionPipeline

    paths = _paths(args)
    profile = select_hardware(paths, force_cpu=args.cpu)
    manager = ModelManager(paths)
    _require_local_model(manager, "asr")
    _require_local_model(manager, "forced_aligner")
    request = JobRequest(
        protocol=PROTOCOL_VERSION,
        job_id=f"cli-{uuid.uuid4().hex[:16]}",
        action="transcribe",
        audio_path=str(Path(args.audio).expanduser().resolve()),
        language=args.language,
        prompt=args.prompt,
        subtitle=SubtitleOptions(
            max_chars=args.max_chars,
            remove_gaps=args.remove_gaps,
            trim_end_punctuation=args.trim_end_punctuation,
        ),
        timeline=TimelineSpec(fps=args.fps, start_frame=0),
    )
    pipeline = TranscriptionPipeline(paths, profile, model_manager=manager)
    started = time.monotonic()
    result = pipeline.run(
        request,
        status=lambda state, progress, message: print(
            f"[{progress:3d}%] {state}: {message}", file=sys.stderr, flush=True
        ),
    )
    payload = result.to_dict(include_transcript=True)
    payload["subtitle_count"] = len(result.blocks)
    payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_self_test(args: argparse.Namespace) -> int:
    from runtime.selftest import validate_hardware_with_fallback

    paths = _paths(args)
    profile = validate_hardware_with_fallback(paths, force_cpu=args.cpu)
    print(
        json.dumps(
            {"hardware": profile.to_dict(), "status": "passed"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_daemon(args: argparse.Namespace) -> int:
    from runtime.ipc.daemon import DaemonAlreadyRunning, RuntimeDaemon

    paths = _paths(args)
    if args.owner_file and not Path(args.owner_file).expanduser().is_file():
        return 0
    daemon = RuntimeDaemon(paths, owner_file=args.owner_file, debug=args.debug)
    try:
        if args.once:
            daemon.run_once()
        else:
            daemon.run_forever()
    except DaemonAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="DaVinci ASR")
    parser.add_argument(
        "--data-root", help="Development/test override for the runtime data root"
    )
    parser.add_argument(
        "--models-root",
        help="Model directory supplied by the Resolve-side Lua plugin",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--owner-file",
        help="Exit the daemon when this Resolve-side lifetime file disappears",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--cpu", action="store_true")
    doctor.set_defaults(func=command_doctor)

    download = sub.add_parser("download-models")
    download.add_argument("--ui-language", choices=("cn", "en"), default="en")
    download.add_argument(
        "--source", choices=("huggingface", "modelscope"), required=True
    )
    download.set_defaults(func=command_download)

    transcribe = sub.add_parser("transcribe")
    transcribe.add_argument("audio")
    transcribe.add_argument(
        "--language", choices=("Auto", *ALIGNMENT_LANGUAGES), default="Auto"
    )
    transcribe.add_argument("--prompt", default="")
    transcribe.add_argument("--cpu", action="store_true")
    transcribe.set_defaults(func=command_transcribe)

    align = sub.add_parser("align")
    align.add_argument("audio")
    align.add_argument("text")
    align.add_argument("--language", choices=ALIGNMENT_LANGUAGES, required=True)
    align.add_argument("--cpu", action="store_true")
    align.set_defaults(func=command_align)

    full = sub.add_parser("full")
    full.add_argument("audio")
    full.add_argument(
        "--language", choices=("Auto", *ALIGNMENT_LANGUAGES), default="Auto"
    )
    full.add_argument("--prompt", default="")
    full.add_argument("--max-chars", type=int, default=42)
    full.add_argument("--remove-gaps", action="store_true")
    full.add_argument("--trim-end-punctuation", action="store_true")
    full.add_argument("--fps", default="24")
    full.add_argument("--cpu", action="store_true")
    full.set_defaults(func=command_full)

    self_test = sub.add_parser("self-test")
    self_test.add_argument("--cpu", action="store_true")
    self_test.set_defaults(func=command_self_test)

    daemon = sub.add_parser("daemon")
    daemon.add_argument(
        "--once", action="store_true", help="Process at most one queued request"
    )
    daemon.set_defaults(func=command_daemon)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = _paths(args)
    _configure_logging(paths, args.debug)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Cancelled", file=sys.stderr)
        return 130
    except Exception as exc:
        logger = logging.getLogger("davinci_asr.cli")
        safe_error = str(exc).replace(str(Path.home()), "<home>")
        if args.debug:
            logger.exception("command=%s failed", args.command)
        else:
            logger.error(
                "command=%s error=%s: %s", args.command, type(exc).__name__, safe_error
            )
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
