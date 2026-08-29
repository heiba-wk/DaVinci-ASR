from __future__ import annotations

import numpy as np

from runtime.constants import SAMPLE_RATE
from runtime.core.hardware import (
    mark_hardware_validated,
    mark_mps_failed,
    select_hardware,
)
from runtime.core.paths import RuntimePaths
from runtime.core.types import HardwareProfile
from runtime.inference.aligner import QwenForcedAlignerEngine
from runtime.inference.asr import QwenASREngine
from runtime.inference.model_manager import ModelManager


def run_model_self_test(
    paths: RuntimePaths, profile: HardwareProfile, *, asr_model: str = "asr"
) -> dict[str, str | int]:
    models = ModelManager(paths)
    duration_seconds = 1
    waveform = np.zeros(SAMPLE_RATE * duration_seconds, dtype=np.float32)
    asr = QwenASREngine(models.model_path(asr_model), profile)
    asr.load()
    try:
        # This intentionally exercises real model generation on the selected device.
        transcription = asr.transcribe(
            waveform,
            language="English",
            prompt="self test",
            max_new_tokens=16,
        )
    finally:
        asr.unload()
    if transcription.get("language") != "English":
        raise RuntimeError("ASR self test returned invalid language metadata")
    aligner = QwenForcedAlignerEngine(models.model_path("forced_aligner"), profile)
    aligner.load()
    try:
        aligned = aligner.align(waveform, "test", "English")
    finally:
        aligner.unload()
    if not aligned:
        raise RuntimeError("Forced aligner self test returned no timestamps")
    return {
        "backend": profile.backend,
        "alignment_count": len(aligned),
        "audio_seconds": duration_seconds,
    }


def validate_hardware_with_fallback(
    paths: RuntimePaths, *, force_cpu: bool = False, asr_model: str = "asr"
) -> HardwareProfile:
    profile = select_hardware(paths, force_cpu=force_cpu)
    try:
        run_model_self_test(paths, profile, asr_model=asr_model)
        profile.validated = True
        mark_hardware_validated(paths, profile)
        return profile
    except Exception as exc:
        if profile.device != "mps":
            raise
        mark_mps_failed(paths, f"{type(exc).__name__}: {exc}")
        fallback = select_hardware(paths, force_cpu=True)
        run_model_self_test(paths, fallback, asr_model=asr_model)
        fallback.validated = True
        mark_hardware_validated(paths, fallback)
        return fallback
