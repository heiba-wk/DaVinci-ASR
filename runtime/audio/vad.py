from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from runtime.constants import (
    VAD_ANALYSIS_BLOCK_SECONDS,
    VAD_ANALYSIS_OVERLAP_SECONDS,
    VAD_MIN_SILENCE_MS,
    VAD_MIN_SPEECH_MS,
    VAD_SPEECH_PAD_MS,
    VAD_THRESHOLD,
)


class ReadableAudio(Protocol):
    sample_count: int
    sample_rate: int

    def read(self, start_sample: int, end_sample: int) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class SpeechRegion:
    start_sample: int
    end_sample: int
    confidence: float

    def __post_init__(self) -> None:
        if self.start_sample < 0 or self.end_sample <= self.start_sample:
            raise ValueError(
                f"Invalid speech region: {self.start_sample} -> {self.end_sample}"
            )
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Speech confidence must be between 0 and 1")

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample

    def to_dict(self, sample_rate: int) -> dict[str, float]:
        return {
            "start": round(self.start_sample / float(sample_rate), 3),
            "end": round(self.end_sample / float(sample_rate), 3),
            "confidence": round(float(self.confidence), 4),
        }


BlockAnalyzer = Callable[[np.ndarray, int], list[dict[str, Any]]]
CancelCallback = Callable[[], bool]

_MODEL_LOCK = threading.Lock()
_MODEL: Any | None = None
_TIMESTAMP_FUNCTION: Callable[..., Any] | None = None


def _silero_components() -> tuple[Any, Callable[..., Any]]:
    global _MODEL, _TIMESTAMP_FUNCTION
    if _MODEL is not None and _TIMESTAMP_FUNCTION is not None:
        return _MODEL, _TIMESTAMP_FUNCTION
    with _MODEL_LOCK:
        if _MODEL is None or _TIMESTAMP_FUNCTION is None:
            from silero_vad import get_speech_timestamps, load_silero_vad

            # The pip package ships the JIT weight. This path never uses torch.hub.
            _MODEL = load_silero_vad(onnx=False)
            _TIMESTAMP_FUNCTION = get_speech_timestamps
    return _MODEL, _TIMESTAMP_FUNCTION


def _analyze_with_silero(waveform: np.ndarray, sample_rate: int) -> list[dict[str, Any]]:
    import torch

    model, get_speech_timestamps = _silero_components()
    audio = torch.from_numpy(np.ascontiguousarray(waveform, dtype=np.float32))
    result = get_speech_timestamps(
        audio,
        model,
        threshold=VAD_THRESHOLD,
        sampling_rate=sample_rate,
        min_speech_duration_ms=VAD_MIN_SPEECH_MS,
        min_silence_duration_ms=VAD_MIN_SILENCE_MS,
        speech_pad_ms=VAD_SPEECH_PAD_MS,
        return_seconds=False,
    )
    return [dict(item) for item in result]


def merge_speech_regions(
    regions: list[SpeechRegion],
    *,
    total_samples: int,
    merge_gap_samples: int,
    minimum_samples: int = 1,
) -> list[SpeechRegion]:
    if total_samples < 0 or merge_gap_samples < 0 or minimum_samples <= 0:
        raise ValueError("Invalid speech-region merge bounds")
    clipped: list[SpeechRegion] = []
    for region in regions:
        start = max(0, min(int(region.start_sample), total_samples))
        end = max(start, min(int(region.end_sample), total_samples))
        if end > start:
            clipped.append(SpeechRegion(start, end, float(region.confidence)))
    clipped.sort(key=lambda item: (item.start_sample, item.end_sample))

    merged: list[SpeechRegion] = []
    for region in clipped:
        if (
            not merged
            or region.start_sample - merged[-1].end_sample >= merge_gap_samples
        ):
            merged.append(region)
            continue
        previous = merged[-1]
        merged[-1] = SpeechRegion(
            previous.start_sample,
            max(previous.end_sample, region.end_sample),
            max(previous.confidence, region.confidence),
        )
    return [region for region in merged if region.sample_count >= minimum_samples]


class SileroVAD:
    def __init__(
        self,
        *,
        analyze_block: BlockAnalyzer | None = None,
        analysis_block_seconds: float = VAD_ANALYSIS_BLOCK_SECONDS,
        analysis_overlap_seconds: float = VAD_ANALYSIS_OVERLAP_SECONDS,
    ) -> None:
        if analysis_block_seconds <= 0:
            raise ValueError("VAD analysis block duration must be positive")
        if not 0 <= analysis_overlap_seconds < analysis_block_seconds:
            raise ValueError("VAD overlap must be shorter than its analysis block")
        self._analyze_block = analyze_block or _analyze_with_silero
        self.analysis_block_seconds = float(analysis_block_seconds)
        self.analysis_overlap_seconds = float(analysis_overlap_seconds)

    def detect(
        self,
        store: ReadableAudio,
        *,
        is_cancelled: CancelCallback | None = None,
    ) -> list[SpeechRegion]:
        total = int(store.sample_count)
        sample_rate = int(store.sample_rate)
        if total < 0 or sample_rate <= 0:
            raise ValueError("Invalid audio metadata for VAD")
        if total == 0:
            return []
        block_samples = max(1, int(round(self.analysis_block_seconds * sample_rate)))
        overlap_samples = max(
            0, int(round(self.analysis_overlap_seconds * sample_rate))
        )
        step = block_samples - overlap_samples
        detected: list[SpeechRegion] = []

        for block_start in range(0, total, step):
            if is_cancelled is not None and is_cancelled():
                raise InterruptedError("Job cancelled")
            block_end = min(total, block_start + block_samples)
            waveform = np.asarray(store.read(block_start, block_end), dtype=np.float32)
            if waveform.ndim != 1 or len(waveform) != block_end - block_start:
                raise ValueError("VAD received an incomplete mono audio block")
            for item in self._analyze_block(waveform, sample_rate):
                relative_start = int(item.get("start", 0))
                relative_end = int(item.get("end", 0))
                start = max(block_start, min(block_start + relative_start, block_end))
                end = max(start, min(block_start + relative_end, block_end))
                if end <= start:
                    continue
                confidence = float(
                    item.get("confidence", item.get("probability", 1.0))
                )
                detected.append(
                    SpeechRegion(start, end, max(0.0, min(1.0, confidence)))
                )
            if block_end == total:
                break

        return merge_speech_regions(
            detected,
            total_samples=total,
            merge_gap_samples=int(round(VAD_MIN_SILENCE_MS * sample_rate / 1000.0)),
            minimum_samples=max(
                1, int(round(VAD_MIN_SPEECH_MS * sample_rate / 1000.0))
            ),
        )
