from __future__ import annotations

import os
import platform
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.core.types import HardwareProfile

PROCESSORS_ONLY = "processors"
SINGLE_MODEL = "single"
DUAL_MODEL = "dual"
ENGINE_CACHE_POLICIES = frozenset((PROCESSORS_ONLY, SINGLE_MODEL, DUAL_MODEL))


def total_memory_bytes() -> int | None:
    override = os.environ.get("DAVINCI_ASR_TOTAL_MEMORY_BYTES", "").strip()
    if override:
        try:
            return max(0, int(override))
        except ValueError:
            return None
    if platform.system() == "Windows":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            value = MemoryStatus()
            value.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
                return int(value.total_physical)
        except (AttributeError, OSError, ValueError):
            return None
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return pages * page_size
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def select_engine_cache_policy(memory_bytes: int | None = None) -> str:
    override = os.environ.get("DAVINCI_ASR_ENGINE_CACHE_POLICY", "").strip().lower()
    if override in ENGINE_CACHE_POLICIES:
        return override
    total = total_memory_bytes() if memory_bytes is None else memory_bytes
    if total is None or total < 24 * 1024**3:
        return PROCESSORS_ONLY
    if total < 48 * 1024**3:
        return SINGLE_MODEL
    return DUAL_MODEL


class ProcessorCache:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], Any] = {}

    def get(self, kind: str, model_path: str | Path) -> Any:
        path = str(Path(model_path).resolve())
        key = (kind, path)
        if key not in self._values:
            from transformers import AutoProcessor

            self._values[key] = AutoProcessor.from_pretrained(
                path,
                local_files_only=True,
            )
        return self._values[key]

    def clear(self) -> None:
        self._values.clear()

    @property
    def size(self) -> int:
        return len(self._values)


EngineFactory = Callable[[Path, HardwareProfile], Any]


class EnginePool:
    def __init__(
        self,
        hardware: HardwareProfile,
        *,
        asr_factory: EngineFactory,
        aligner_factory: EngineFactory,
        processor_cache: ProcessorCache | None = None,
        policy: str | None = None,
    ) -> None:
        self.hardware = hardware
        self.factories = {"asr": asr_factory, "forced_aligner": aligner_factory}
        self.processor_cache = processor_cache or ProcessorCache()
        self.policy = policy or select_engine_cache_policy()
        if self.policy not in ENGINE_CACHE_POLICIES:
            raise ValueError(f"Unsupported engine cache policy: {self.policy}")
        self._engines: dict[str, Any] = {}
        self._loaded: set[str] = set()
        self.last_used = 0.0

    @property
    def loaded_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._loaded))

    @property
    def has_loaded_models(self) -> bool:
        return bool(self._loaded)

    def _release_engine(self, kind: str, *, clear_processor: bool = False) -> None:
        engine = self._engines.get(kind)
        if engine is None:
            self._loaded.discard(kind)
            return
        release_model = getattr(engine, "release_model", None)
        if callable(release_model):
            release_model()
        else:
            engine.unload()
        if clear_processor and hasattr(engine, "processor"):
            engine.processor = None
        self._loaded.discard(kind)

    def acquire(self, kind: str, model_path: str | Path) -> tuple[Any, float]:
        import time

        if kind not in self.factories:
            raise KeyError(f"Unknown engine kind: {kind}")
        if kind in self._loaded:
            self.last_used = time.monotonic()
            return self._engines[kind], 0.0
        if self.policy == SINGLE_MODEL:
            for loaded_kind in tuple(self._loaded):
                if loaded_kind != kind:
                    self._release_engine(loaded_kind)
        engine = self._engines.get(kind)
        if engine is None:
            path = Path(model_path)
            engine = self.factories[kind](path, self.hardware)
            self._engines[kind] = engine
        if hasattr(engine, "processor") and engine.processor is None:
            engine.processor = self.processor_cache.get(kind, model_path)
        started = time.monotonic()
        try:
            engine.load()
        except Exception:
            self._release_engine(kind)
            raise
        elapsed_ms = (time.monotonic() - started) * 1000.0
        self._loaded.add(kind)
        self.last_used = time.monotonic()
        return engine, elapsed_ms

    def release_after_stage(self, kind: str) -> None:
        import time

        self.last_used = time.monotonic()
        if self.policy == PROCESSORS_ONLY:
            self._release_engine(kind)

    def release_models(self) -> None:
        for kind in tuple(self._loaded):
            self._release_engine(kind)

    def release_idle(self, idle_seconds: float, *, now: float | None = None) -> bool:
        import time

        current = time.monotonic() if now is None else float(now)
        if not self._loaded or current - self.last_used < float(idle_seconds):
            return False
        self.release_models()
        return True

    def close(self) -> None:
        self.release_models()
        for engine in self._engines.values():
            if hasattr(engine, "processor"):
                engine.processor = None
        self._engines.clear()
        self.processor_cache.clear()
