from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.core.types import HardwareProfile
from runtime.inference.aligner import QwenForcedAlignerEngine
from runtime.inference.asr import QwenASREngine
from runtime.inference.cache import EnginePool
from runtime.inference.model_manager import ModelManager


class InferenceService:
    """Daemon-owned inference resources shared by consecutive pipeline jobs."""

    def __init__(
        self,
        models: ModelManager,
        hardware: HardwareProfile,
        *,
        asr_factory: Callable[[Path, HardwareProfile], Any] = QwenASREngine,
        aligner_factory: Callable[[Path, HardwareProfile], Any] = QwenForcedAlignerEngine,
        cache_policy: str | None = None,
    ) -> None:
        self.models = models
        self.hardware = hardware
        self.engines = EnginePool(
            hardware,
            asr_factory=asr_factory,
            aligner_factory=aligner_factory,
            policy=cache_policy,
        )

    def acquire_asr(self, model_key: str = "asr") -> tuple[Any, float]:
        return self.engines.acquire("asr", self.models.model_path(model_key))

    def acquire_aligner(self) -> tuple[Any, float]:
        return self.engines.acquire(
            "forced_aligner",
            self.models.model_path("forced_aligner"),
        )

    def release_after_stage(self, kind: str) -> None:
        self.engines.release_after_stage(kind)

    def release_idle(self, idle_seconds: float, *, now: float | None = None) -> bool:
        return self.engines.release_idle(idle_seconds, now=now)

    def release_models(self) -> None:
        self.engines.release_models()

    def close(self) -> None:
        self.engines.close()
