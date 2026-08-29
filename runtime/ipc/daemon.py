from __future__ import annotations

import logging
import os
import platform
import signal
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.constants import (
    ACTIVE_POLL_SECONDS,
    ACTIVE_POLL_WINDOW_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    IDLE_MODEL_RELEASE_SECONDS,
    IDLE_POLL_SECONDS,
    IPC_CLEANUP_INTERVAL_SECONDS,
    IPC_DIAGNOSTIC_TTL_SECONDS,
    IPC_MAX_TERMINAL_JOBS,
    IPC_SUCCESS_TTL_SECONDS,
    PROTOCOL_VERSION,
    RUNTIME_VERSION,
)
from runtime.core.cancel import CancelToken
from runtime.core.diagnostics import diagnostics_payload
from runtime.core.paths import RuntimePaths
from runtime.core.types import HardwareProfile, JobRequest
from runtime.inference.model_manager import (
    DownloadCancelled,
    DownloadProgress,
    ModelManager,
)
from runtime.inference.validation_cache import SelfTestValidationCache
from runtime.ipc.atomic import atomic_write_json_fast, read_json
from runtime.ipc.protocol import (
    JobFiles,
    cleanup_job_temp,
    read_request,
    terminal_state,
    write_result,
    write_status,
)
PipelineFactory = Callable[..., Any]
SelfTest = Callable[[RuntimePaths], HardwareProfile]


class DaemonAlreadyRunning(RuntimeError):
    pass


class RuntimeDaemon:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        hardware: HardwareProfile | None = None,
        model_manager: ModelManager | None = None,
        pipeline_factory: PipelineFactory | None = None,
        self_test: SelfTest | None = None,
        owner_file: str | Path | None = None,
        debug: bool = False,
    ) -> None:
        self.paths = paths
        self.paths.ensure()
        self.hardware = hardware or HardwareProfile(
            platform=platform.system(),
            architecture=platform.machine(),
            device="pending",
            backend="Detecting",
            dtype="pending",
            name="",
        )
        self._hardware_initialized = hardware is not None
        self.models = model_manager or ModelManager(paths)
        self.pipeline_factory = pipeline_factory
        self.self_test = self_test
        self.debug = debug
        self.log = logging.getLogger("davinci_asr.daemon")
        self.running = False
        self.last_activity = time.monotonic()
        self.models_released = True
        self.last_job = "None"
        self.last_metrics: dict[str, Any] = {}
        self.cached_model_status = {
            "asr": "Checking",
            "asr_1_7b": "Checking",
            "forced_aligner": "Checking",
        }
        self._model_status_initialized = False
        self.inference: Any | None = None
        self.validation_cache = SelfTestValidationCache(paths)
        self.lock_path = self.paths.root / "runtime.lock"
        self.status_path = self.paths.root / "runtime_status.json"
        self.owner_file = Path(owner_file).expanduser().resolve() if owner_file else None
        self.stop_event = threading.Event()
        self.heartbeat_thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._active_job_id: str | None = None
        self._terminal_job_ids: set[str] = set()
        self._last_cleanup = 0.0
        self.status_write_count = 0
        if hardware is not None:
            self.refresh_model_status()

    def _safe_error(self, error: Exception, request: JobRequest | None = None) -> str:
        value = f"{type(error).__name__}: {error}"
        value = value.replace(str(Path.home()), "<home>")
        if request and request.audio_path:
            value = value.replace(request.audio_path, "<audio>")
        return value[:1000]

    def _acquire_lock(self) -> None:
        payload = f"{os.getpid()}\n{time.time()}\n".encode("ascii")
        try:
            descriptor = os.open(
                self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            try:
                heartbeat = float(read_json(self.status_path).get("heartbeat", 0))
            except (FileNotFoundError, OSError, ValueError):
                heartbeat = 0
            if time.time() - heartbeat < 10:
                raise DaemonAlreadyRunning("DaVinci ASR is already running")
            self.lock_path.unlink(missing_ok=True)
            descriptor = os.open(
                self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def _release_lock(self) -> None:
        try:
            first_line = self.lock_path.read_text(encoding="ascii").splitlines()[0]
            if int(first_line) == os.getpid():
                self.lock_path.unlink(missing_ok=True)
        except (FileNotFoundError, OSError, ValueError, IndexError):
            pass

    def refresh_model_status(self) -> dict[str, str]:
        self.cached_model_status = dict(self.models.status())
        self._model_status_initialized = True
        return self.cached_model_status

    def _model_identity(self) -> dict[str, Any]:
        identity = getattr(self.models, "validation_identity", None)
        if callable(identity):
            return dict(identity())
        return {}

    def _ensure_hardware(self) -> HardwareProfile:
        if not self._hardware_initialized:
            from runtime.core.hardware import select_hardware

            self.hardware = select_hardware(self.paths)
            self._hardware_initialized = True
        if not self._model_status_initialized:
            self.refresh_model_status()
        if not self.hardware.validated and self.validation_cache.is_valid(
            self.hardware,
            self._model_identity(),
        ):
            self.hardware.validated = True
        return self.hardware

    def _run_self_test(self, asr_model: str = "asr") -> HardwareProfile:
        if self.self_test is not None:
            profile = self.self_test(self.paths)
        else:
            from runtime.selftest import validate_hardware_with_fallback

            profile = validate_hardware_with_fallback(
                self.paths, asr_model=asr_model
            )
        profile.validated = True
        self.hardware = profile
        self._hardware_initialized = True
        self.validation_cache.record(profile, self._model_identity())
        if self.inference is not None:
            self.inference.close()
            self.inference = None
        return profile

    def _ensure_inference(self) -> Any:
        self._ensure_hardware()
        if self.inference is None:
            from runtime.inference.service import InferenceService

            self.inference = InferenceService(self.models, self.hardware)
        return self.inference

    def _build_pipeline(self) -> Any:
        inference = self._ensure_inference()
        if self.pipeline_factory is None:
            from runtime.pipeline import TranscriptionPipeline

            factory: PipelineFactory = TranscriptionPipeline
        else:
            factory = self.pipeline_factory
        return factory(
            self.paths,
            self.hardware,
            model_manager=self.models,
            inference_service=inference,
        )

    def write_runtime_status(self) -> None:
        with self._status_lock:
            payload = {
                "runtime_version": RUNTIME_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "pid": os.getpid(),
                "heartbeat": time.time(),
                "running": self.running,
                "hardware": self.hardware.to_dict(),
                "backend": self.hardware.backend,
                "model_status": dict(self.cached_model_status),
                "models_released": self.models_released,
                "engine_cache_policy": getattr(
                    getattr(self.inference, "engines", None), "policy", "pending"
                ),
                "diagnostics": diagnostics_payload(
                    self.hardware,
                    self.cached_model_status,
                    last_job=self.last_job,
                    performance=self.last_metrics,
                ),
            }
            atomic_write_json_fast(self.status_path, payload)
            self.status_write_count += 1

    def _owner_is_alive(self) -> bool:
        return self.owner_file is None or self.owner_file.is_file()

    def _is_cancelled(self, cancel: CancelToken) -> bool:
        return self.stop_event.is_set() or cancel.is_cancelled()

    def _raise_if_cancelled(self, cancel: CancelToken) -> None:
        if self._is_cancelled(cancel):
            raise InterruptedError("Job cancelled")

    def _job_candidates(self) -> list[JobFiles]:
        candidates: list[JobFiles] = []
        for directory in self.paths.ipc.iterdir():
            if (
                not directory.is_dir()
                or directory.name in self._terminal_job_ids
                or directory.name == self._active_job_id
            ):
                continue
            files = JobFiles.for_job(self.paths, directory.name)
            if files.request.is_file() and terminal_state(files) is None:
                candidates.append(files)
        return sorted(candidates, key=lambda item: item.request.stat().st_mtime)

    def _remove_job_history(self, files: JobFiles) -> None:
        if files.directory.name == self._active_job_id:
            return
        cleanup_job_temp(self.paths, files.directory.name)
        shutil.rmtree(files.directory, ignore_errors=True)
        self._terminal_job_ids.discard(files.directory.name)

    def cleanup_ipc_history(self, *, force: bool = False) -> int:
        monotonic_now = time.monotonic()
        if (
            not force
            and monotonic_now - self._last_cleanup < IPC_CLEANUP_INTERVAL_SECONDS
        ):
            return 0
        self._last_cleanup = monotonic_now
        now = time.time()
        terminal: list[tuple[float, JobFiles, str]] = []
        removed = 0
        for directory in self.paths.ipc.iterdir():
            if not directory.is_dir() or directory.name == self._active_job_id:
                continue
            files = JobFiles.for_job(self.paths, directory.name)
            state = terminal_state(files)
            if state is None:
                continue
            self._terminal_job_ids.add(directory.name)
            try:
                modified = files.status.stat().st_mtime
            except OSError:
                modified = now
            if files.ack.is_file():
                self._remove_job_history(files)
                removed += 1
                continue
            ttl = (
                IPC_SUCCESS_TTL_SECONDS
                if state == "done"
                else IPC_DIAGNOSTIC_TTL_SECONDS
            )
            if now - modified >= ttl:
                self._remove_job_history(files)
                removed += 1
                continue
            terminal.append((modified, files, state))
        terminal.sort(key=lambda item: item[0], reverse=True)
        for _modified, files, _state in terminal[IPC_MAX_TERMINAL_JOBS:]:
            self._remove_job_history(files)
            removed += 1
        return removed

    def _process_transcription(
        self, request: JobRequest, files: JobFiles, cancel: CancelToken
    ) -> None:
        self._raise_if_cancelled(cancel)
        self._ensure_hardware()
        required_models = (request.asr_model, "forced_aligner")
        for key in required_models:
            ready, reason = self.models.verify(key, full_hash=False)
            if not ready:
                raise RuntimeError(f"{key} model is not installed: {reason}")
        if not self.hardware.validated:
            write_status(
                files, "preparing", 2, "Verifying local models and hardware"
            )
            for key in required_models:
                ready, reason = self.models.verify(key, full_hash=True)
                if not ready:
                    raise RuntimeError(f"{key} model verification failed: {reason}")
            if self.validation_cache.is_valid(
                self.hardware,
                self._model_identity(),
            ):
                self.hardware.validated = True
            else:
                self._run_self_test(request.asr_model)
            self._raise_if_cancelled(cancel)
        pipeline = self._build_pipeline()
        result = pipeline.run(
            request,
            status=lambda state, progress, message: write_status(
                files, state, progress, message
            ),
            is_cancelled=lambda: self._is_cancelled(cancel),
        )
        self._raise_if_cancelled(cancel)
        write_result(files, result.to_dict(include_transcript=False))
        self.last_metrics = dict(result.metrics)
        self.last_job = "Success"
        write_status(files, "done", 100, "Subtitles ready")

    def _process_download(
        self, request: JobRequest, files: JobFiles, cancel: CancelToken
    ) -> None:
        self._ensure_hardware()
        latest: DownloadProgress | None = None

        def publish(progress: DownloadProgress) -> None:
            nonlocal latest
            latest = progress
            write_status(
                files,
                "preparing",
                progress.percent,
                progress.message,
                download_status=progress.status,
                download_source=progress.source,
                download_next_source=progress.next_source,
                download_current_file=progress.current_file,
                download_model_name=progress.model_name,
                downloaded_bytes=progress.downloaded_bytes,
                total_bytes=progress.total_bytes,
                download_speed_bps=progress.speed_bps,
            )

        write_status(
            files,
            "preparing",
            0,
            "Preparing model download",
            download_status="connecting",
        )
        self.models.download_all(
            publish,
            lambda: self._is_cancelled(cancel),
            ui_language=request.ui_language,
            source=request.download_source,
            model_keys=(request.asr_model, "forced_aligner"),
        )
        self._raise_if_cancelled(cancel)
        invalidate = getattr(self.models, "invalidate_cache", None)
        if callable(invalidate):
            invalidate()
        self.validation_cache.invalidate()
        self.refresh_model_status()
        write_status(
            files,
            "preparing",
            99,
            "Running ASR and Forced Aligner model self test",
            download_status="verifying",
            download_source=latest.source if latest else "",
            download_model_name=latest.model_name if latest else "",
            downloaded_bytes=latest.downloaded_bytes if latest else 0,
            total_bytes=latest.total_bytes if latest else 0,
            download_speed_bps=0,
        )
        self._run_self_test(request.asr_model)
        write_result(
            files,
            {
                "protocol": PROTOCOL_VERSION,
                "job_id": files.directory.name,
                "action": "download_models",
                "model_status": dict(self.cached_model_status),
                "hardware": self.hardware.to_dict(),
            },
        )
        self.last_job = "Models Ready"
        write_status(
            files,
            "done",
            100,
            "Models downloaded and self-tested",
            download_status="complete",
            downloaded_bytes=latest.total_bytes if latest else 0,
            total_bytes=latest.total_bytes if latest else 0,
            download_speed_bps=0,
        )

    def _process_self_test(
        self, request: JobRequest, files: JobFiles, cancel: CancelToken
    ) -> None:
        self._raise_if_cancelled(cancel)
        write_status(files, "preparing", 10, "Running runtime model self test")
        self._ensure_hardware()
        for key in (request.asr_model, "forced_aligner"):
            ready, reason = self.models.verify(key, full_hash=True, force=True)
            if not ready:
                raise RuntimeError(f"{key} model verification failed: {reason}")
        self.validation_cache.invalidate()
        self._run_self_test(request.asr_model)
        self.refresh_model_status()
        self._raise_if_cancelled(cancel)
        write_result(
            files,
            {
                "protocol": PROTOCOL_VERSION,
                "job_id": files.directory.name,
                "action": "self_test",
                "hardware": self.hardware.to_dict(),
            },
        )
        self.last_job = "Self Test Success"
        write_status(files, "done", 100, "Runtime model self test passed")

    @staticmethod
    def _remove_temp_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _process_cleanup(self, files: JobFiles) -> None:
        write_status(files, "preparing", 10, "Cleaning temporary files")
        self.paths.temp.mkdir(parents=True, exist_ok=True)
        for path in self.paths.temp.iterdir():
            self._remove_temp_path(path)
        for directory in self.paths.ipc.iterdir():
            if directory == files.directory or not directory.is_dir():
                continue
            if terminal_state(JobFiles.for_job(self.paths, directory.name)) is not None:
                shutil.rmtree(directory)
        write_result(
            files,
            {
                "protocol": PROTOCOL_VERSION,
                "job_id": files.directory.name,
                "action": "cleanup_temp",
            },
        )
        write_status(files, "done", 100, "Temporary files removed")
        shutil.rmtree(files.directory)

    def _process_shutdown(self, files: JobFiles) -> None:
        self._process_cleanup(files)
        self.running = False

    def process_job(self, files: JobFiles) -> None:
        request: JobRequest | None = None
        cancel = CancelToken(files.directory)
        self._active_job_id = files.directory.name
        write_status(files, "queued", 0, "Queued")
        try:
            request = read_request(files.request)
            if request.job_id != files.directory.name:
                raise ValueError("job_id does not match its IPC directory")
            self._raise_if_cancelled(cancel)
            self.models_released = False
            self.write_runtime_status()
            if request.action == "transcribe":
                self._process_transcription(request, files, cancel)
            elif request.action == "download_models":
                self._process_download(request, files, cancel)
            elif request.action == "self_test":
                self._process_self_test(request, files, cancel)
            elif request.action == "cleanup_temp":
                self._process_cleanup(files)
            elif request.action == "shutdown":
                self._process_shutdown(files)
            else:
                raise ValueError(f"Unsupported action: {request.action}")
        except (InterruptedError, DownloadCancelled):
            cleanup_job_temp(self.paths, files.directory.name)
            self.last_job = "Cancelled"
            write_status(
                files,
                "cancelled",
                100,
                "Cancelled",
                download_status="cancelled"
                if request and request.action == "download_models"
                else "",
            )
        except Exception as exc:
            cleanup_job_temp(self.paths, files.directory.name)
            safe = self._safe_error(exc, request)
            self.last_job = "Error"
            if request and request.action in {"transcribe", "self_test"} and isinstance(
                exc, (OSError, RuntimeError)
            ):
                invalidate = getattr(self.models, "invalidate_cache", None)
                if callable(invalidate):
                    invalidate()
                self.validation_cache.invalidate()
                self.hardware.validated = False
                if self.inference is not None:
                    self.inference.release_models()
            if self.debug:
                self.log.exception("job=%s error", files.directory.name)
            else:
                self.log.error("job=%s error=%s", files.directory.name, safe)
            try:
                self.refresh_model_status()
            except Exception:
                self.log.exception("Could not refresh model status after job error")
            is_download = request and request.action == "download_models"
            write_status(
                files,
                "error",
                100,
                "Model download failed" if is_download else "Task failed",
                safe,
                download_status="failed" if is_download else "",
            )
        finally:
            self._active_job_id = None
            self._terminal_job_ids.add(files.directory.name)
            self.models_released = not bool(
                self.inference
                and self.inference.engines.has_loaded_models
            )
            self.last_activity = time.monotonic()
            self.write_runtime_status()

    def run_once(self) -> bool:
        self.cleanup_ipc_history()
        candidates = self._job_candidates()
        if not candidates:
            released = bool(
                self.inference
                and self.inference.release_idle(IDLE_MODEL_RELEASE_SECONDS)
            )
            if released:
                self.models_released = True
                self.write_runtime_status()
            return False
        self.process_job(candidates[0])
        return True

    def stop(self, *_args: Any) -> None:
        self.running = False
        self.stop_event.set()

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(HEARTBEAT_INTERVAL_SECONDS):
            if not self._owner_is_alive():
                self.stop()
                return
            try:
                self.write_runtime_status()
            except Exception:
                self.log.exception("Could not update runtime heartbeat")

    def run_forever(self, poll_seconds: float | None = None) -> None:
        if not self._owner_is_alive():
            return
        self._acquire_lock()
        self.running = True
        self.stop_event.clear()
        try:
            signal.signal(signal.SIGINT, self.stop)
            signal.signal(signal.SIGTERM, self.stop)
            self.heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="DaVinciASRHeartbeat",
                daemon=True,
            )
            self.heartbeat_thread.start()
            self.write_runtime_status()
            try:
                self._ensure_hardware()
            except Exception as exc:
                self.last_job = "Runtime Error"
                self.log.error("runtime initialization error=%s", self._safe_error(exc))
            self.write_runtime_status()
            while self.running:
                if not self._owner_is_alive():
                    self.stop()
                    break
                handled = self.run_once()
                if not handled:
                    if poll_seconds is not None:
                        wait_seconds = poll_seconds
                    elif (
                        time.monotonic() - self.last_activity
                        <= ACTIVE_POLL_WINDOW_SECONDS
                    ):
                        wait_seconds = ACTIVE_POLL_SECONDS
                    else:
                        wait_seconds = IDLE_POLL_SECONDS
                    self.stop_event.wait(wait_seconds)
        finally:
            self.stop_event.set()
            if self.heartbeat_thread:
                self.heartbeat_thread.join(timeout=2.0)
                self.heartbeat_thread = None
            if self.inference is not None:
                self.inference.close()
                self.inference = None
            self.models_released = True
            self.running = False
            try:
                self.write_runtime_status()
            except Exception:
                self.log.exception("Could not write final runtime status")
            self._release_lock()
