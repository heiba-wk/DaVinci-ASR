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
    VAD_SMOOTH_WINDOW_FRAMES,
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


@dataclass(frozen=True, slots=True)
class SpeechProbabilityTrack:
    values: np.ndarray
    frame_hop_samples: int
    total_samples: int

    def __post_init__(self) -> None:
        values = np.asarray(self.values, dtype=np.float32)
        if values.ndim != 1:
            raise ValueError("Speech probabilities must be one-dimensional")
        if self.frame_hop_samples <= 0 or self.total_samples < 0:
            raise ValueError("Invalid speech-probability timeline")
        if not np.all(np.isfinite(values)):
            raise ValueError("Speech probabilities must be finite")
        if values.size and (float(values.min()) < 0.0 or float(values.max()) > 1.0):
            raise ValueError("Speech probabilities must be between 0 and 1")
        copied = np.ascontiguousarray(values, dtype=np.float32)
        copied.setflags(write=False)
        object.__setattr__(self, "values", copied)

    @property
    def frame_count(self) -> int:
        return int(self.values.size)


@dataclass(frozen=True, slots=True)
class VADAnalysis:
    speech_regions: tuple[SpeechRegion, ...]
    probabilities: SpeechProbabilityTrack


@dataclass(frozen=True, slots=True)
class BlockVADAnalysis:
    regions: tuple[dict[str, Any], ...]
    probabilities: np.ndarray


BlockAnalyzer = Callable[[np.ndarray, int], BlockVADAnalysis]
CancelCallback = Callable[[], bool]

_MODEL_LOCK = threading.Lock()
_MODEL: Any | None = None
_OMNIVAD_FRAMES_PER_SECOND = 100


def _milliseconds_to_frames(milliseconds: int) -> int:
    return max(
        1,
        int(round(milliseconds * _OMNIVAD_FRAMES_PER_SECOND / 1000.0)),
    )


def _omnivad_parameters(*, threshold: float = VAD_THRESHOLD) -> dict[str, int | float]:
    return {
        "threshold": float(threshold),
        "smooth_window_size": VAD_SMOOTH_WINDOW_FRAMES,
        "min_speech_frames": _milliseconds_to_frames(VAD_MIN_SPEECH_MS),
        "max_speech_frames": max(
            1,
            int(round(VAD_ANALYSIS_BLOCK_SECONDS * _OMNIVAD_FRAMES_PER_SECOND)),
        ),
        "min_silence_frames": _milliseconds_to_frames(VAD_MIN_SILENCE_MS),
        "merge_silence_frames": 0,
        "extend_speech_frames": _milliseconds_to_frames(VAD_SPEECH_PAD_MS),
    }


def _omnivad_model() -> Any:
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            from omnivad import OmniVAD as OmniVADModel

            _MODEL = OmniVADModel(**_omnivad_parameters())
    return _MODEL


def _native_timestamp_sample(sample: int, sample_rate: int) -> int:
    """Mirror OmniVAD 0.2.13's float32, millisecond timestamp boundary."""
    seconds = round(float(np.float32(sample / float(sample_rate))), 3)
    return int(round(seconds * sample_rate))


def _causal_moving_average(values: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 0:
        raise ValueError("OmniVAD smoothing window must be positive")
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 1:
        raise ValueError("OmniVAD probabilities must be one-dimensional")
    if values.size == 0 or frames == 1:
        return values.copy()
    padded = np.pad(values, (frames - 1, 0), mode="constant")
    kernel = np.full(frames, 1.0 / frames, dtype=np.float64)
    return np.convolve(padded, kernel, mode="valid").astype(np.float32)


def postprocess_omnivad_probabilities(
    probabilities: np.ndarray,
    *,
    total_samples: int,
    sample_rate: int,
    parameters: dict[str, int | float] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Reproduce the bundled OmniVAD 0.2.13 VAD postprocessor from one prob track.

    The local package exposes inference-only ``detect_probs()`` and a combined
    inference-plus-postprocess ``detect()``.  This state machine was parity
    checked against the latter on the frozen 11-language corpus so production
    can obtain identical speech evidence without running the model twice.
    """
    if total_samples < 0 or sample_rate <= 0:
        raise ValueError("Invalid audio metadata for OmniVAD postprocessing")
    if sample_rate % _OMNIVAD_FRAMES_PER_SECOND:
        raise ValueError("OmniVAD requires a sample rate divisible into 10ms frames")
    config = dict(parameters or _omnivad_parameters())
    values = np.asarray(probabilities, dtype=np.float32)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("OmniVAD returned invalid frame probabilities")
    if values.size and (float(values.min()) < 0.0 or float(values.max()) > 1.0):
        raise ValueError("OmniVAD probabilities must be between 0 and 1")

    threshold = float(config["threshold"])
    smooth_frames = int(config["smooth_window_size"])
    min_speech_frames = int(config["min_speech_frames"])
    max_speech_frames = int(config["max_speech_frames"])
    min_silence_frames = int(config["min_silence_frames"])
    merge_silence_frames = int(config["merge_silence_frames"])
    extend_speech_frames = int(config["extend_speech_frames"])
    if (
        not 0.0 <= threshold <= 1.0
        or min_speech_frames <= 0
        or max_speech_frames <= 0
        or min_silence_frames <= 0
        or merge_silence_frames < 0
        or extend_speech_frames < 0
    ):
        raise ValueError("Invalid OmniVAD postprocessing parameters")

    smoothed = _causal_moving_average(values, smooth_frames)
    is_speech = smoothed >= threshold
    raw_regions: list[tuple[int, int | None]] = []
    active = False
    candidate_start = 0
    speech_frames = 0
    silence_frames = 0
    region_start = 0
    for frame_index, speech in enumerate(is_speech):
        if not active:
            if speech:
                if speech_frames == 0:
                    candidate_start = frame_index
                speech_frames += 1
                # OmniVAD 0.2.13 confirms on the frame after this configured
                # minimum and compensates its causal smoothing latency.
                if speech_frames > min_speech_frames:
                    region_start = max(0, candidate_start - smooth_frames)
                    active = True
                    silence_frames = 0
            else:
                speech_frames = 0
            continue

        if speech:
            silence_frames = 0
        else:
            silence_frames += 1
            if silence_frames >= min_silence_frames:
                raw_regions.append((region_start, frame_index + 1))
                active = False
                speech_frames = 0
                silence_frames = 0
                continue
        if frame_index + 1 - region_start >= max_speech_frames:
            raw_regions.append((region_start, frame_index + 1))
            active = False
            speech_frames = 0
            silence_frames = 0
    if active:
        raw_regions.append((region_start, None))

    frame_hop = sample_rate // _OMNIVAD_FRAMES_PER_SECOND
    extended: list[tuple[int, int]] = []
    for index, (start_frame, end_frame) in enumerate(raw_regions):
        start = max(0, (start_frame - extend_speech_frames) * frame_hop)
        end = (
            total_samples
            if end_frame is None
            else min(total_samples, (end_frame + extend_speech_frames) * frame_hop)
        )
        if index == len(raw_regions) - 1 and total_samples - end <= (
            smooth_frames * frame_hop
        ):
            end = total_samples
        start = max(0, min(_native_timestamp_sample(start, sample_rate), total_samples))
        end = max(start, min(_native_timestamp_sample(end, sample_rate), total_samples))
        if end <= start:
            continue
        merge_gap = merge_silence_frames * frame_hop
        if extended and start <= extended[-1][1] + merge_gap:
            extended[-1] = (extended[-1][0], max(extended[-1][1], end))
        else:
            extended.append((start, end))
    return tuple(
        {"start": start, "end": end, "confidence": 1.0} for start, end in extended
    )


def atomic_speech_regions_from_probabilities(
    probabilities: SpeechProbabilityTrack,
    sample_rate: int,
) -> list[SpeechRegion]:
    """Extract unpadded speech islands from the complete OmniVAD track.

    Padded VAD regions are useful for inference windows, but their context can
    bridge a real short pause. Coverage recovery needs the same threshold and
    confirmation rules without padding so one claimed token cannot hide the
    next acoustic island.
    """
    if sample_rate <= 0 or sample_rate % _OMNIVAD_FRAMES_PER_SECOND:
        raise ValueError("Invalid audio metadata for atomic speech regions")
    expected_hop = sample_rate // _OMNIVAD_FRAMES_PER_SECOND
    if probabilities.frame_hop_samples != expected_hop:
        raise ValueError("Speech-probability frame hop does not match sample rate")

    parameters = _omnivad_parameters()
    smoothed = _causal_moving_average(
        probabilities.values,
        int(parameters["smooth_window_size"]),
    )
    threshold = float(parameters["threshold"])
    min_speech_frames = int(parameters["min_speech_frames"])
    min_silence_frames = int(parameters["min_silence_frames"])
    max_speech_frames = int(parameters["max_speech_frames"])
    smooth_frames = int(parameters["smooth_window_size"])

    output: list[SpeechRegion] = []
    active = False
    candidate_start = 0
    region_start = 0
    speech_frames = 0
    silence_frames = 0

    def append_region(start_frame: int, end_frame: int) -> None:
        start_sample = max(
            0,
            min(start_frame * expected_hop, probabilities.total_samples),
        )
        end_sample = max(
            start_sample,
            min(end_frame * expected_hop, probabilities.total_samples),
        )
        if end_sample > start_sample:
            output.append(SpeechRegion(start_sample, end_sample, 1.0))

    for frame_index, probability in enumerate(smoothed):
        speech = float(probability) >= threshold
        if not active:
            if speech:
                if speech_frames == 0:
                    candidate_start = frame_index
                speech_frames += 1
                if speech_frames > min_speech_frames:
                    region_start = max(0, candidate_start - smooth_frames)
                    active = True
                    silence_frames = 0
            else:
                speech_frames = 0
            continue

        if speech:
            silence_frames = 0
        else:
            silence_frames += 1
            if silence_frames >= min_silence_frames:
                append_region(
                    region_start,
                    frame_index + 1 - silence_frames,
                )
                active = False
                speech_frames = 0
                silence_frames = 0
                continue

        if frame_index + 1 - region_start >= max_speech_frames:
            append_region(region_start, frame_index + 1)
            active = False
            speech_frames = 0
            silence_frames = 0

    if active:
        append_region(region_start, probabilities.frame_count)
    return output


def _analyze_with_omnivad(
    waveform: np.ndarray,
    sample_rate: int,
) -> BlockVADAnalysis:
    contiguous = np.ascontiguousarray(waveform, dtype=np.float32)
    model = _omnivad_model()
    probabilities = np.asarray(
        model.detect_probs(contiguous, sample_rate=sample_rate),
        dtype=np.float32,
    )
    regions = postprocess_omnivad_probabilities(
        probabilities,
        total_samples=int(contiguous.size),
        sample_rate=sample_rate,
    )
    return BlockVADAnalysis(regions=regions, probabilities=probabilities)


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


class OmniVAD:
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
        self._analyze_block = analyze_block or _analyze_with_omnivad
        self.analysis_block_seconds = float(analysis_block_seconds)
        self.analysis_overlap_seconds = float(analysis_overlap_seconds)

    def analyze(
        self,
        store: ReadableAudio,
        *,
        is_cancelled: CancelCallback | None = None,
    ) -> VADAnalysis:
        total = int(store.sample_count)
        sample_rate = int(store.sample_rate)
        if total < 0 or sample_rate <= 0:
            raise ValueError("Invalid audio metadata for VAD")
        if total == 0:
            return VADAnalysis(
                speech_regions=(),
                probabilities=SpeechProbabilityTrack(
                    values=np.empty(0, dtype=np.float32),
                    frame_hop_samples=max(
                        1,
                        int(round(sample_rate / _OMNIVAD_FRAMES_PER_SECOND)),
                    ),
                    total_samples=0,
                ),
            )
        if sample_rate % _OMNIVAD_FRAMES_PER_SECOND:
            raise ValueError(
                "OmniVAD requires a sample rate divisible into 10ms frames"
            )
        block_samples = max(1, int(round(self.analysis_block_seconds * sample_rate)))
        overlap_samples = max(
            0, int(round(self.analysis_overlap_seconds * sample_rate))
        )
        step = block_samples - overlap_samples
        frame_hop_samples = sample_rate // _OMNIVAD_FRAMES_PER_SECOND
        frame_count = (total + frame_hop_samples - 1) // frame_hop_samples
        probability_sums = np.zeros(frame_count, dtype=np.float64)
        probability_counts = np.zeros(frame_count, dtype=np.int32)
        detected: list[SpeechRegion] = []

        for block_start in range(0, total, step):
            if is_cancelled is not None and is_cancelled():
                raise InterruptedError("Job cancelled")
            block_end = min(total, block_start + block_samples)
            waveform = np.asarray(store.read(block_start, block_end), dtype=np.float32)
            if waveform.ndim != 1 or len(waveform) != block_end - block_start:
                raise ValueError("VAD received an incomplete mono audio block")
            analyzed = self._analyze_block(waveform, sample_rate)
            if analyzed.probabilities.ndim != 1:
                raise ValueError("OmniVAD returned invalid frame probabilities")
            global_frame_start = block_start // frame_hop_samples
            available = min(
                int(analyzed.probabilities.size),
                frame_count - global_frame_start,
            )
            if available > 0:
                frame_slice = slice(global_frame_start, global_frame_start + available)
                probability_sums[frame_slice] += analyzed.probabilities[:available]
                probability_counts[frame_slice] += 1
            for item in analyzed.regions:
                relative_start = int(item.get("start", 0))
                relative_end = int(item.get("end", 0))
                start = max(block_start, min(block_start + relative_start, block_end))
                end = max(start, min(block_start + relative_end, block_end))
                if end <= start:
                    continue
                confidence = float(item.get("confidence", item.get("probability", 1.0)))
                detected.append(
                    SpeechRegion(start, end, max(0.0, min(1.0, confidence)))
                )
            if block_end == total:
                break

        probabilities = np.ones(frame_count, dtype=np.float32)
        covered = probability_counts > 0
        probabilities[covered] = (
            probability_sums[covered] / probability_counts[covered]
        ).astype(np.float32)
        speech_regions = merge_speech_regions(
            detected,
            total_samples=total,
            merge_gap_samples=int(round(VAD_MIN_SILENCE_MS * sample_rate / 1000.0)),
            minimum_samples=max(
                1, int(round(VAD_MIN_SPEECH_MS * sample_rate / 1000.0))
            ),
        )
        return VADAnalysis(
            speech_regions=tuple(speech_regions),
            probabilities=SpeechProbabilityTrack(
                values=probabilities,
                frame_hop_samples=frame_hop_samples,
                total_samples=total,
            ),
        )
