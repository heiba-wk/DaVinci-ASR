from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.constants import RUNTIME_VERSION
from runtime.core.paths import RuntimePaths
from runtime.ipc.atomic import atomic_write_json, read_json

HF_REPO_ID = "Qwen/Qwen3-ASR-0.6B-hf"
MS_REPO_ID = "Qwen/Qwen3-ASR-0.6B-hf"
MS_ALIGNER_REPO_ID = "Qwen/Qwen3-ForcedAligner-0.6B-hf"
MODELSCOPE_REPO_IDS = frozenset((MS_REPO_ID, MS_ALIGNER_REPO_ID))
INCOMPLETE_MARKER = ".download_incomplete"
REPORT_INTERVAL_SECONDS = 0.15
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

CancelCallback = Callable[[], bool]


class DownloadCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    percent: int
    status: str
    message: str
    source: str = ""
    next_source: str = ""
    current_file: str = ""
    model_name: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bps: int = 0


ProgressCallback = Callable[[DownloadProgress], None]


def _source_label(source: str) -> str:
    return {"modelscope": "ModelScope", "huggingface": "Hugging Face"}.get(
        source, source
    )


class _DownloadProgressState:
    def __init__(
        self,
        file_sizes: dict[str, int],
        initial_bytes: dict[str, int],
        callback: ProgressCallback | None,
        logger: logging.Logger,
    ) -> None:
        self._file_sizes = dict(file_sizes)
        self._downloaded = {
            key: min(max(0, int(initial_bytes.get(key, 0))), size)
            for key, size in self._file_sizes.items()
        }
        self._callback = callback
        self._logger = logger
        self._lock = threading.Lock()
        self._callback_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = "connecting"
        self._source = ""
        self._next_source = ""
        self._current_file = ""
        self._model_name = ""
        self._percent = self._calculate_percent_unlocked()
        self._last_logged_bucket = self._percent // 10 - 1
        self._speed_sample_time = time.monotonic()
        self._speed_sample_bytes = sum(self._downloaded.values())
        self._speed_bps = 0.0

    @property
    def total_bytes(self) -> int:
        return sum(self._file_sizes.values())

    def _calculate_percent_unlocked(self) -> int:
        total = max(sum(self._file_sizes.values()), 1)
        downloaded = sum(self._downloaded.values())
        return max(0, min(100, int(downloaded * 100 / total)))

    def _message_unlocked(self) -> str:
        source = _source_label(self._source)
        next_source = _source_label(self._next_source)
        if self._status == "connecting":
            return f"Connecting to {source}" if source else "Preparing model download"
        if self._status == "fallback":
            return f"{source} unavailable, trying {next_source}"
        if self._status == "downloading":
            return f"Downloading {self._model_name or 'model'}"
        if self._status == "verifying":
            return f"Verifying {self._model_name or 'model'}"
        if self._status == "complete":
            return "Model downloaded"
        if self._status == "cancelled":
            return "Model download cancelled"
        if self._status == "failed":
            return "Model download failed"
        return self._status

    def snapshot(self) -> DownloadProgress:
        with self._lock:
            downloaded = sum(self._downloaded.values())
            now = time.monotonic()
            elapsed = now - self._speed_sample_time
            if self._status == "downloading" and elapsed >= REPORT_INTERVAL_SECONDS * 0.8:
                delta = max(0, downloaded - self._speed_sample_bytes)
                instant = delta / elapsed if elapsed > 0 else 0.0
                if delta > 0:
                    self._speed_bps = (
                        instant
                        if self._speed_bps <= 0
                        else (self._speed_bps * 0.65) + (instant * 0.35)
                    )
                else:
                    self._speed_bps *= 0.5
                    if self._speed_bps < 1:
                        self._speed_bps = 0.0
                self._speed_sample_time = now
                self._speed_sample_bytes = downloaded
            elif self._status != "downloading":
                self._speed_bps = 0.0
                self._speed_sample_time = now
                self._speed_sample_bytes = downloaded
            percent = 100 if self._status == "complete" else min(99, self._percent)
            return DownloadProgress(
                percent=percent,
                status=self._status,
                message=self._message_unlocked(),
                source=self._source,
                next_source=self._next_source,
                current_file=self._current_file,
                model_name=self._model_name,
                downloaded_bytes=min(downloaded, self.total_bytes),
                total_bytes=self.total_bytes,
                speed_bps=max(0, int(round(self._speed_bps))),
            )

    def set_status(
        self,
        status: str,
        *,
        source: str = "",
        next_source: str = "",
        model_name: str = "",
    ) -> None:
        with self._lock:
            self._status = status
            self._source = source
            self._next_source = next_source
            if model_name:
                self._model_name = model_name
            if status != "downloading":
                self._speed_bps = 0.0
                self._speed_sample_time = time.monotonic()
                self._speed_sample_bytes = sum(self._downloaded.values())
            if status == "complete":
                for key, size in self._file_sizes.items():
                    self._downloaded[key] = size
                self._percent = 100

    def set_file_bytes(self, key: str, filename: str, absolute_bytes: int) -> None:
        with self._lock:
            if key not in self._file_sizes:
                return
            previous = self._downloaded.get(key, 0)
            current = min(
                self._file_sizes[key], max(previous, max(0, int(absolute_bytes)))
            )
            self._downloaded[key] = current
            self._current_file = filename
            self._status = "downloading"
            new_percent = self._calculate_percent_unlocked()
            self._percent = max(self._percent, new_percent)
            bucket = self._percent // 10
            if bucket > self._last_logged_bucket:
                self._last_logged_bucket = bucket
                self._logger.info(
                    "[ASR] Download progress: %d%%", min(100, bucket * 10)
                )

    def complete_file(self, key: str, filename: str) -> None:
        size = self._file_sizes.get(key)
        if size is not None:
            self.set_file_bytes(key, filename, size)

    def publish_now(self) -> None:
        if not self._callback:
            return
        snapshot = self.snapshot()
        try:
            with self._callback_lock:
                self._callback(snapshot)
        except Exception:
            self._logger.exception("[ASR] Download progress callback failed")

    def start_reporter(self) -> None:
        if not self._callback or self._thread:
            return

        def report() -> None:
            while not self._stop.wait(REPORT_INTERVAL_SECONDS):
                self.publish_now()

        self._thread = threading.Thread(
            target=report, name="DaVinciASRDownloadProgress", daemon=True
        )
        self._thread.start()
        self.publish_now()

    def stop_reporter(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.publish_now()


class ModelManager:
    def __init__(
        self, paths: RuntimePaths, manifest_path: str | Path | None = None
    ) -> None:
        self.paths = paths
        if manifest_path is None:
            frozen_root = getattr(sys, "_MEIPASS", None)
            if frozen_root:
                manifest_path = Path(frozen_root) / "models" / "manifest.json"
            else:
                manifest_path = (
                    Path(__file__).resolve().parents[2] / "models" / "manifest.json"
                )
        self.manifest_path = Path(manifest_path)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.packaged_models_root = self.manifest_path.parent
        self.log = logging.getLogger("davinci_asr.models")
        self._resolved_paths: dict[str, Path] = {}
        self._validation_cache_path = self.paths.settings / "model_validation.json"
        try:
            validation_cache = read_json(self._validation_cache_path)
        except (FileNotFoundError, OSError, ValueError):
            validation_cache = {}
        if validation_cache.get("runtime_version") != RUNTIME_VERSION:
            validation_cache = {}
        self._validation_cache: dict[str, Any] = validation_cache
        self._migrate_legacy_model_directories()

    def model_entry(self, key: str) -> dict[str, Any]:
        for entry in self.manifest["models"]:
            if entry["key"] == key:
                return entry
        raise KeyError(f"Unknown model key: {key}")

    @staticmethod
    def _marker(root: Path) -> Path:
        return root / INCOMPLETE_MARKER

    def _migrate_legacy_model_directories(self) -> None:
        self.paths.models.mkdir(parents=True, exist_ok=True)
        for entry in self.manifest["models"]:
            destination = self.paths.models / entry["directory"]
            legacy_roots = (self.paths.root / "models", self.paths.root)
            for legacy_root in legacy_roots:
                legacy = legacy_root / entry["directory"]
                if legacy == destination or not legacy.is_dir():
                    continue
                ready, _ = self._verify_at(
                    entry,
                    legacy,
                    full_hash=False,
                    ignore_marker=True,
                )
                if not ready:
                    continue
                try:
                    if destination.exists():
                        destination_ready, _ = self._verify_at(
                            entry,
                            destination,
                            full_hash=False,
                            ignore_marker=True,
                        )
                        if destination_ready:
                            break
                        if destination.is_dir() and not destination.is_symlink():
                            shutil.rmtree(destination)
                        else:
                            destination.unlink()
                    legacy.replace(destination)
                except OSError as exc:
                    self.log.warning(
                        "[ASR] Could not migrate legacy model directory: %s", exc
                    )
                else:
                    self.log.info(
                        "[ASR] Migrated legacy model directory to %s", destination
                    )
                    break

    def model_path(self, key: str) -> Path:
        entry = self.model_entry(key)
        cached = self._resolved_paths.get(key)
        if cached is not None and self._verify_at(
            entry, cached, full_hash=False
        )[0]:
            return cached
        external = self.paths.models / entry["directory"]
        legacy = self.paths.root / entry["directory"]
        packaged = self.packaged_models_root / entry["directory"]
        for candidate in (external, legacy, packaged):
            if self._verify_at(entry, candidate, full_hash=False)[0]:
                self._resolved_paths[key] = candidate
                return candidate
        return external

    def _candidate_paths(self, entry: dict[str, Any]) -> tuple[Path, ...]:
        return (
            self.paths.models / entry["directory"],
            self.paths.root / entry["directory"],
            self.packaged_models_root / entry["directory"],
        )

    def _fingerprint(self, entry: dict[str, Any], root: Path) -> str | None:
        if self._marker(root).exists():
            return None
        values: list[tuple[str, int, int, str]] = []
        for file_info in entry["files"]:
            path = root / file_info["path"]
            try:
                stat = path.stat()
            except OSError:
                return None
            expected_size = int(file_info["size"])
            if not path.is_file() or stat.st_size != expected_size:
                return None
            values.append(
                (
                    str(file_info["path"]),
                    stat.st_size,
                    stat.st_mtime_ns,
                    str(file_info.get("sha256") or ""),
                )
            )
        payload = json.dumps(
            {
                "key": entry["key"],
                "revision": entry["revision"],
                "root": str(root.resolve()),
                "files": values,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _validation_matches(
        self, entry: dict[str, Any], root: Path, fingerprint: str
    ) -> bool:
        models = self._validation_cache.get("models")
        cached = models.get(entry["key"]) if isinstance(models, dict) else None
        return bool(
            isinstance(cached, dict)
            and cached.get("revision") == entry["revision"]
            and cached.get("root") == str(root.resolve())
            and cached.get("fingerprint") == fingerprint
        )

    def _record_validation(
        self, entry: dict[str, Any], root: Path, fingerprint: str
    ) -> None:
        models = self._validation_cache.setdefault("models", {})
        if not isinstance(models, dict):
            models = {}
            self._validation_cache["models"] = models
        self._validation_cache["runtime_version"] = RUNTIME_VERSION
        models[entry["key"]] = {
            "revision": entry["revision"],
            "root": str(root.resolve()),
            "fingerprint": fingerprint,
            "verified_at": time.time(),
        }
        atomic_write_json(self._validation_cache_path, self._validation_cache)

    def invalidate_cache(self, key: str | None = None) -> None:
        if key is None:
            self._resolved_paths.clear()
            self._validation_cache = {}
        else:
            self._resolved_paths.pop(key, None)
            models = self._validation_cache.get("models")
            if isinstance(models, dict):
                models.pop(key, None)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _file_is_valid(
        self,
        root: Path,
        file_info: dict[str, Any],
        *,
        full_hash: bool,
    ) -> bool:
        path = root / file_info["path"]
        if not path.is_file() or path.stat().st_size != int(file_info["size"]):
            return False
        expected_hash = file_info.get("sha256")
        return not (full_hash and expected_hash) or self._sha256(path) == expected_hash

    def _verify_at(
        self,
        entry: dict[str, Any],
        root: Path,
        *,
        full_hash: bool,
        ignore_marker: bool = False,
    ) -> tuple[bool, str]:
        if not ignore_marker and self._marker(root).exists():
            return False, "Download incomplete"
        for file_info in entry["files"]:
            path = root / file_info["path"]
            if not path.is_file():
                return False, f"Missing {file_info['path']}"
            if path.stat().st_size != int(file_info["size"]):
                return False, f"Size mismatch for {file_info['path']}"
            expected_hash = file_info.get("sha256")
            if full_hash and expected_hash and self._sha256(path) != expected_hash:
                return False, f"SHA-256 mismatch for {file_info['path']}"
        return True, "Ready"

    def verify(
        self,
        key: str,
        *,
        full_hash: bool = True,
        force: bool = False,
    ) -> tuple[bool, str]:
        entry = self.model_entry(key)
        last_reason = "Not Installed"
        for root in self._candidate_paths(entry):
            if full_hash:
                fingerprint = self._fingerprint(entry, root)
                if fingerprint is None:
                    ready, last_reason = self._verify_at(
                        entry, root, full_hash=False
                    )
                    if not ready:
                        continue
                elif not force and self._validation_matches(
                    entry, root, fingerprint
                ):
                    self._resolved_paths[key] = root
                    return True, "Ready"
                ready, last_reason = self._verify_at(
                    entry, root, full_hash=True
                )
                if ready:
                    self._resolved_paths[key] = root
                    self._record_validation(
                        entry,
                        root,
                        fingerprint or self._fingerprint(entry, root) or "",
                    )
                    return True, last_reason
            else:
                ready, last_reason = self._verify_at(
                    entry, root, full_hash=False
                )
                if ready:
                    self._resolved_paths[key] = root
                    return True, last_reason
        return False, last_reason

    def status(self) -> dict[str, str]:
        return {
            entry["key"]: (
                "Ready"
                if self.verify(entry["key"], full_hash=False)[0]
                else "Not Installed"
            )
            for entry in self.manifest["models"]
        }

    def validation_identity(self) -> dict[str, dict[str, str]]:
        identity: dict[str, dict[str, str]] = {}
        for entry in self.manifest["models"]:
            root = self.model_path(entry["key"])
            fingerprint = self._fingerprint(entry, root)
            if fingerprint:
                identity[entry["key"]] = {
                    "revision": str(entry["revision"]),
                    "fingerprint": fingerprint,
                }
        return identity

    @staticmethod
    def _open_response(request: urllib.request.Request, timeout: int = 60) -> Any:
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as proxy_error:
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            last_error: Exception = proxy_error
            for attempt in range(3):
                try:
                    return direct.open(request, timeout=timeout)
                except urllib.error.HTTPError:
                    raise
                except urllib.error.URLError as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(2**attempt)
            raise last_error

    @staticmethod
    def _download_file(
        url: str,
        destination: Path,
        expected_size: int,
        on_bytes: Callable[[int], None],
        is_cancelled: CancelCallback,
    ) -> None:
        if urllib.parse.urlparse(url).hostname != "huggingface.co":
            raise ValueError("Model downloads are restricted to huggingface.co")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        existing = partial.stat().st_size if partial.exists() else 0
        if existing > expected_size:
            partial.unlink()
            existing = 0
        request = urllib.request.Request(
            url, headers={"User-Agent": "DaVinci-ASR/1.0"}
        )
        if existing:
            request.add_header("Range", f"bytes={existing}-")
        try:
            response = ModelManager._open_response(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing == expected_size:
                os.replace(partial, destination)
                return
            raise
        status = getattr(response, "status", 200)
        if existing and status != 206:
            existing = 0
        mode = "ab" if existing and status == 206 else "wb"
        if existing:
            on_bytes(existing)
        with response, partial.open(mode) as stream:
            while True:
                if is_cancelled():
                    stream.flush()
                    os.fsync(stream.fileno())
                    raise DownloadCancelled("Model download cancelled")
                block = response.read(1024 * 1024)
                if not block:
                    break
                stream.write(block)
                on_bytes(len(block))
            stream.flush()
            os.fsync(stream.fileno())
        if partial.stat().st_size != expected_size:
            raise RuntimeError(f"Incomplete download for {destination.name}")
        os.replace(partial, destination)

    @staticmethod
    def _load_modelscope_sdk() -> tuple[type[Any], type[Any]]:
        from modelscope_hub import (
            HubApi,
            ProgressCallback as ModelScopeProgressCallback,
        )

        return HubApi, ModelScopeProgressCallback

    @staticmethod
    def source_order(ui_language: str) -> tuple[str, str]:
        return (
            ("modelscope", "huggingface")
            if ui_language == "cn"
            else ("huggingface", "modelscope")
        )

    @staticmethod
    def _proxy_environment_present() -> bool:
        return any(os.environ.get(key) for key in PROXY_ENV_KEYS)

    @staticmethod
    def _is_loopback_host(hostname: str) -> bool:
        if hostname.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return False

    @classmethod
    def _loopback_proxy_is_unreachable(cls, timeout: float = 0.25) -> bool:
        endpoints: set[tuple[str, int]] = set()
        for key in PROXY_ENV_KEYS:
            value = os.environ.get(key, "").strip()
            if not value:
                continue
            parsed = urllib.parse.urlparse(
                value if "://" in value else f"http://{value}"
            )
            if not parsed.hostname or not cls._is_loopback_host(parsed.hostname):
                return False
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            endpoints.add((parsed.hostname, port))
        if not endpoints:
            return False
        for endpoint in endpoints:
            try:
                connection = socket.create_connection(endpoint, timeout=timeout)
            except OSError:
                continue
            connection.close()
            return False
        return True

    @staticmethod
    def _is_proxy_failure(exc: Exception) -> bool:
        current: BaseException | None = exc
        for _ in range(8):
            if current is None:
                break
            text = str(current).lower()
            if any(
                token in text
                for token in ("proxy", "connection refused", "errno 61", "errno 10061")
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    @contextmanager
    def _without_proxy_environment():
        previous = {key: os.environ[key] for key in PROXY_ENV_KEYS if key in os.environ}
        for key in PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        try:
            yield
        finally:
            for key in PROXY_ENV_KEYS:
                os.environ.pop(key, None)
            os.environ.update(previous)

    @contextmanager
    def _download_network_environment(self):
        if not self._loopback_proxy_is_unreachable():
            yield
            return
        self.log.warning("[ASR] Ignoring unreachable loopback proxy for model download")
        with self._without_proxy_environment():
            yield

    @staticmethod
    def _can_fallback(exc: Exception) -> bool:
        if isinstance(
            exc, (ConnectionError, TimeoutError, urllib.error.URLError, RuntimeError)
        ):
            return True
        module = type(exc).__module__
        if module.startswith(("modelscope_hub", "requests")):
            return True
        return not isinstance(exc, OSError)

    @staticmethod
    def _file_key(entry: dict[str, Any], path: str) -> str:
        return f"{entry['key']}:{path}"

    @staticmethod
    def _modelscope_observed_bytes(target: Path) -> int | None:
        incomplete = target.with_suffix(target.suffix + ".incomplete")
        candidates: list[int] = []
        if incomplete.is_file():
            candidates.append(incomplete.stat().st_size)
        if target.is_file():
            candidates.append(target.stat().st_size)
        part_sizes = [
            path.stat().st_size
            for path in target.parent.glob(target.name + "_*_*")
            if path.is_file()
        ]
        if part_sizes:
            candidates.append(sum(part_sizes))
        return max(candidates) if candidates else None

    def download_from_huggingface(
        self,
        entry: dict[str, Any],
        target_dir: Path,
        state: _DownloadProgressState,
        is_cancelled: CancelCallback,
    ) -> None:
        repo_id = str(entry["repo_id"])
        self.log.info("[ASR] Download source: Hugging Face")
        self.log.info("[ASR] Repo: %s", repo_id)
        self.log.info("[ASR] Target: %s", target_dir)
        for file_info in entry["files"]:
            if is_cancelled():
                raise DownloadCancelled("Model download cancelled")
            relative_path = str(file_info["path"])
            destination = target_dir / relative_path
            file_key = self._file_key(entry, relative_path)
            if self._file_is_valid(target_dir, file_info, full_hash=True):
                state.complete_file(file_key, relative_path)
                continue
            if destination.exists():
                destination.unlink()
            encoded_path = "/".join(
                urllib.parse.quote(part) for part in relative_path.split("/")
            )
            url = f"https://huggingface.co/{repo_id}/resolve/{entry['revision']}/{encoded_path}"
            last_error: Exception | None = None
            for attempt in range(3):
                backend_bytes = 0

                def on_bytes(amount: int) -> None:
                    nonlocal backend_bytes
                    backend_bytes += max(0, int(amount))
                    state.set_file_bytes(file_key, relative_path, backend_bytes)

                try:
                    self._download_file(
                        url, destination, int(file_info["size"]), on_bytes, is_cancelled
                    )
                    if not self._file_is_valid(target_dir, file_info, full_hash=True):
                        destination.unlink(missing_ok=True)
                        raise RuntimeError(
                            f"Integrity check failed for {relative_path}"
                        )
                    state.complete_file(file_key, relative_path)
                    last_error = None
                    break
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        self.log.warning(
                            "[ASR] Hugging Face retry %d/3 for %s: %s",
                            attempt + 2,
                            relative_path,
                            exc,
                        )
                        time.sleep(2**attempt)
            if last_error is not None:
                raise last_error

    def download_from_modelscope(
        self,
        entry: dict[str, Any],
        target_dir: Path,
        state: _DownloadProgressState,
        is_cancelled: CancelCallback,
    ) -> None:
        repo_id = str(entry.get("modelscope_repo_id") or MS_REPO_ID)
        revision = str(entry.get("modelscope_revision") or "master")
        if repo_id not in MODELSCOPE_REPO_IDS:
            raise ValueError(f"Unexpected ModelScope repo: {repo_id}")
        self.log.info("[ASR] Download source: ModelScope")
        self.log.info("[ASR] Repo: %s", repo_id)
        self.log.info("[ASR] Target: %s", target_dir)
        HubApi, ModelScopeProgressCallback = self._load_modelscope_sdk()
        api = HubApi()
        remote_files = api.list_repo_files(
            repo_id, "model", revision=revision, recursive=True
        )
        remote_by_path = {
            item.path: item
            for item in remote_files
            if not getattr(item, "is_dir", False)
        }
        required_paths = [str(file_info["path"]) for file_info in entry["files"]]
        for file_info in entry["files"]:
            relative_path = str(file_info["path"])
            remote = remote_by_path.get(relative_path)
            if remote is None:
                raise RuntimeError(f"ModelScope repository is missing {relative_path}")
            if int(remote.size) != int(file_info["size"]):
                raise RuntimeError(f"ModelScope size mismatch for {relative_path}")
            expected_hash = file_info.get("sha256")
            remote_hash = getattr(remote, "sha256", None)
            if expected_hash and remote_hash and remote_hash != expected_hash:
                raise RuntimeError(f"ModelScope SHA-256 mismatch for {relative_path}")
            destination = target_dir / relative_path
            if destination.exists() and not self._file_is_valid(
                target_dir, file_info, full_hash=True
            ):
                destination.unlink()

        manager = self

        class CustomProgressCallback(ModelScopeProgressCallback):
            def __init__(self, filename: str, file_size: int) -> None:
                super().__init__(filename, file_size)
                self._seen = 0
                self._file_key = manager._file_key(entry, filename)

            def update(self, size: int) -> None:
                if is_cancelled():
                    raise DownloadCancelled("Model download cancelled")
                target = target_dir / self.filename
                observed = manager._modelscope_observed_bytes(target)
                if observed is None:
                    self._seen += max(0, int(size))
                else:
                    self._seen = max(self._seen, observed)
                state.set_file_bytes(self._file_key, self.filename, self._seen)

            def end(self) -> None:
                state.complete_file(self._file_key, self.filename)

        api.download_repo(
            repo_id,
            "model",
            revision=revision,
            cache_dir=self.paths.cache / "modelscope",
            local_dir=target_dir,
            allow_patterns=required_paths,
            max_workers=4,
            progress_callbacks=[CustomProgressCallback],
        )
        if is_cancelled():
            raise DownloadCancelled("Model download cancelled")
        ok, reason = self._verify_at(
            entry, target_dir, full_hash=True, ignore_marker=True
        )
        if not ok:
            raise RuntimeError(f"ModelScope download incomplete: {reason}")

    def download_model(
        self,
        source: str,
        entry: dict[str, Any],
        target_dir: Path,
        state: _DownloadProgressState,
        is_cancelled: CancelCallback,
    ) -> None:
        if source == "huggingface":
            self.download_from_huggingface(entry, target_dir, state, is_cancelled)
            return
        if source == "modelscope":
            self.download_from_modelscope(entry, target_dir, state, is_cancelled)
            return
        raise ValueError(f"Unsupported model source: {source}")

    def download_model_with_fallback(
        self,
        entry: dict[str, Any],
        target_dir: Path,
        sources: tuple[str, ...],
        state: _DownloadProgressState,
        is_cancelled: CancelCallback,
    ) -> str:
        failures: list[str] = []
        for index, source in enumerate(sources):
            if is_cancelled():
                raise DownloadCancelled("Model download cancelled")
            state.set_status(
                "connecting", source=source, model_name=entry["display_name"]
            )
            state.publish_now()
            try:
                self.download_model(source, entry, target_dir, state, is_cancelled)
                ok, reason = self._verify_at(
                    entry, target_dir, full_hash=True, ignore_marker=True
                )
                if not ok:
                    raise RuntimeError(reason)
                return source
            except DownloadCancelled:
                raise
            except Exception as exc:
                if self._proxy_environment_present() and self._is_proxy_failure(exc):
                    self.log.warning(
                        "[ASR] %s proxy connection failed; retrying the same source directly",
                        _source_label(source),
                    )
                    try:
                        with self._without_proxy_environment():
                            self.download_model(
                                source, entry, target_dir, state, is_cancelled
                            )
                        ok, reason = self._verify_at(
                            entry, target_dir, full_hash=True, ignore_marker=True
                        )
                        if not ok:
                            raise RuntimeError(reason)
                        return source
                    except DownloadCancelled:
                        raise
                    except Exception as direct_exc:
                        self.log.exception(
                            "[ASR] Direct %s retry failed for %s: %s",
                            _source_label(source),
                            entry["repo_id"],
                            direct_exc,
                        )
                        exc = direct_exc
                self.log.exception(
                    "[ASR] %s download failed for %s: %s",
                    _source_label(source),
                    entry["repo_id"],
                    exc,
                )
                if not self._can_fallback(exc):
                    raise
                failures.append(f"{_source_label(source)}: {type(exc).__name__}: {exc}")
                if index + 1 < len(sources):
                    next_source = sources[index + 1]
                    state.set_status(
                        "fallback",
                        source=source,
                        next_source=next_source,
                        model_name=entry["display_name"],
                    )
                    state.publish_now()
                    time.sleep(REPORT_INTERVAL_SECONDS)
        raise RuntimeError("All model download sources failed: " + " | ".join(failures))

    def _repair_completed_marker(self, entry: dict[str, Any], root: Path) -> None:
        marker = self._marker(root)
        if not marker.exists():
            return
        ready, _ = self._verify_at(entry, root, full_hash=True, ignore_marker=True)
        if ready:
            marker.unlink(missing_ok=True)

    def download_all(
        self,
        progress: ProgressCallback | None = None,
        is_cancelled: CancelCallback | None = None,
        *,
        ui_language: str = "en",
        source: str | None = None,
    ) -> None:
        if source is not None and source not in {"huggingface", "modelscope"}:
            raise ValueError(f"Unsupported model source: {source}")
        self.invalidate_cache()
        cancelled = is_cancelled or (lambda: False)
        pending: list[tuple[dict[str, Any], Path]] = []
        for entry in self.manifest["models"]:
            external_root = self.paths.models / entry["directory"]
            self._repair_completed_marker(entry, external_root)
            ready, _ = self.verify(entry["key"], full_hash=True)
            if not ready:
                pending.append((entry, external_root))

        file_sizes: dict[str, int] = {}
        initial_bytes: dict[str, int] = {}
        for entry, root in pending:
            for file_info in entry["files"]:
                key = self._file_key(entry, str(file_info["path"]))
                size = int(file_info["size"])
                file_sizes[key] = size
                if self._file_is_valid(root, file_info, full_hash=True):
                    initial_bytes[key] = size

        state = _DownloadProgressState(file_sizes, initial_bytes, progress, self.log)
        if not pending:
            state.set_status("complete")
            state.publish_now()
            return

        state.start_reporter()
        try:
            with self._download_network_environment():
                for entry, root in pending:
                    if cancelled():
                        raise DownloadCancelled("Model download cancelled")
                    root.mkdir(parents=True, exist_ok=True)
                    self._marker(root).write_text("incomplete\n", encoding="ascii")
                    if source is not None:
                        sources = (source,)
                    elif entry["key"] == "asr" and entry["repo_id"] == HF_REPO_ID:
                        sources: tuple[str, ...] = self.source_order(ui_language)
                    else:
                        sources = ("huggingface",)
                    source_used = self.download_model_with_fallback(
                        entry, root, sources, state, cancelled
                    )
                    state.set_status(
                        "verifying",
                        source=source_used,
                        model_name=entry["display_name"],
                    )
                    state.publish_now()
                    ok, reason = self._verify_at(
                        entry, root, full_hash=True, ignore_marker=True
                    )
                    if not ok:
                        raise RuntimeError(f"Model verification failed: {reason}")
                    self._marker(root).unlink(missing_ok=True)
                    self.invalidate_cache(str(entry["key"]))
                    for file_info in entry["files"]:
                        state.complete_file(
                            self._file_key(entry, str(file_info["path"])),
                            str(file_info["path"]),
                        )
            state.set_status("complete")
            self.log.info("[ASR] Model download complete")
        except DownloadCancelled:
            state.set_status("cancelled")
            raise
        except Exception:
            state.set_status("failed")
            raise
        finally:
            state.stop_reporter()
