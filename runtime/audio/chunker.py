from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from runtime.audio.vad import SpeechProbabilityTrack, SpeechRegion
from runtime.constants import (
    WINDOW_CONTEXT_SECONDS,
    WINDOW_HARD_MAX_SECONDS,
    WINDOW_MIN_CORE_SECONDS,
    WINDOW_SOFT_MAX_SECONDS,
    WINDOW_TARGET_SECONDS,
    WINDOW_VALLEY_MIN_SECONDS,
    WINDOW_VALLEY_SMOOTH_FRAMES,
    WINDOW_VALLEY_THRESHOLD,
)


@dataclass(frozen=True, slots=True)
class InferenceWindow:
    index: int
    core_start_sample: int
    core_end_sample: int
    input_start_sample: int
    input_end_sample: int
    speech_samples: int
    boundary_reason: str
    boundary_probability: float | None = None
    valley_width_samples: int = 0
    valley_mean_probability: float | None = None
    valley_p95_probability: float | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("Window index must be non-negative")
        if self.core_start_sample < 0 or self.core_end_sample <= self.core_start_sample:
            raise ValueError("Invalid core window interval")
        if not 0 <= self.input_start_sample <= self.core_start_sample:
            raise ValueError("Input window must include the core start")
        if self.input_end_sample < self.core_end_sample:
            raise ValueError("Input window must include the core end")
        if self.speech_samples < 0:
            raise ValueError("Window speech sample count cannot be negative")
        for value in (
            self.boundary_probability,
            self.valley_mean_probability,
            self.valley_p95_probability,
        ):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError("Boundary probabilities must be between 0 and 1")
        if self.valley_width_samples < 0:
            raise ValueError("Probability-valley width cannot be negative")

    @property
    def sample_count(self) -> int:
        return self.core_end_sample - self.core_start_sample

    @property
    def input_sample_count(self) -> int:
        return self.input_end_sample - self.input_start_sample

    @property
    def core_region(self) -> SpeechRegion:
        return SpeechRegion(self.core_start_sample, self.core_end_sample, 1.0)

    def to_dict(self, sample_rate: int) -> dict[str, object]:
        return {
            "index": self.index,
            "core_start": round(self.core_start_sample / float(sample_rate), 3),
            "core_end": round(self.core_end_sample / float(sample_rate), 3),
            "input_start": round(self.input_start_sample / float(sample_rate), 3),
            "input_end": round(self.input_end_sample / float(sample_rate), 3),
            "speech_seconds": round(self.speech_samples / float(sample_rate), 3),
            "boundary_reason": self.boundary_reason,
            "boundary_probability": (
                None
                if self.boundary_probability is None
                else round(float(self.boundary_probability), 6)
            ),
            "valley_width_seconds": round(
                self.valley_width_samples / float(sample_rate),
                3,
            ),
            "valley_mean_probability": (
                None
                if self.valley_mean_probability is None
                else round(float(self.valley_mean_probability), 6)
            ),
            "valley_p95_probability": (
                None
                if self.valley_p95_probability is None
                else round(float(self.valley_p95_probability), 6)
            ),
        }


@dataclass(frozen=True, slots=True)
class ProbabilityValley:
    start_sample: int
    end_sample: int
    center_sample: int
    center_probability: float
    mean_probability: float
    p95_probability: float

    @property
    def width_samples(self) -> int:
        return self.end_sample - self.start_sample


@dataclass(frozen=True, slots=True)
class _CorePlan:
    start_sample: int
    end_sample: int
    reason: str
    valley: ProbabilityValley | None = None


def _speech_samples(
    regions: list[SpeechRegion], start_sample: int, end_sample: int
) -> int:
    total = 0
    for region in regions:
        if region.end_sample <= start_sample:
            continue
        if region.start_sample >= end_sample:
            break
        total += max(
            0,
            min(region.end_sample, end_sample)
            - max(region.start_sample, start_sample),
        )
    return total


def _smooth_probabilities(values: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 0:
        raise ValueError("Probability smoothing window must be positive")
    if values.size == 0 or frames == 1:
        return np.asarray(values, dtype=np.float32)
    left = frames // 2
    right = frames - left - 1
    padded = np.pad(values, (left, right), mode="edge")
    kernel = np.full(frames, 1.0 / frames, dtype=np.float64)
    return np.convolve(padded, kernel, mode="valid").astype(np.float32)


def probability_valleys(
    probabilities: SpeechProbabilityTrack,
    *,
    sample_rate: int,
    threshold: float = WINDOW_VALLEY_THRESHOLD,
    min_seconds: float = WINDOW_VALLEY_MIN_SECONDS,
    smooth_frames: int = WINDOW_VALLEY_SMOOTH_FRAMES,
) -> list[ProbabilityValley]:
    if sample_rate <= 0 or probabilities.total_samples < 0:
        raise ValueError("Invalid probability-valley timeline")
    if not 0.0 <= threshold <= 1.0 or min_seconds <= 0:
        raise ValueError("Invalid probability-valley parameters")
    values = _smooth_probabilities(probabilities.values, smooth_frames)
    if values.size == 0:
        return []
    hop = probabilities.frame_hop_samples
    minimum_frames = max(2, int(np.ceil(min_seconds * sample_rate / hop)))
    low = values <= threshold
    transitions = np.flatnonzero(low[1:] != low[:-1]) + 1
    boundaries = np.concatenate(([0], transitions, [values.size]))
    valleys: list[ProbabilityValley] = []
    for start_frame, end_frame in zip(boundaries[:-1], boundaries[1:]):
        if not low[int(start_frame)] or end_frame - start_frame < minimum_frames:
            continue
        frame_values = values[int(start_frame) : int(end_frame)]
        start_sample = min(
            probabilities.total_samples,
            int(start_frame) * hop,
        )
        end_sample = min(
            probabilities.total_samples,
            int(end_frame) * hop,
        )
        if end_sample <= start_sample:
            continue
        center_sample = (start_sample + end_sample) // 2
        center_frame = min(
            int(values.size) - 1,
            max(0, center_sample // hop),
        )
        valleys.append(
            ProbabilityValley(
                start_sample=start_sample,
                end_sample=end_sample,
                center_sample=center_sample,
                center_probability=float(values[center_frame]),
                mean_probability=float(np.mean(frame_values)),
                p95_probability=float(np.percentile(frame_values, 95)),
            )
        )
    return valleys


def _select_probability_valley(
    valleys: list[ProbabilityValley],
    *,
    lower: int,
    soft_upper: int,
    hard_upper: int,
    target: int,
    sample_rate: int,
) -> ProbabilityValley | None:
    eligible = [
        valley
        for valley in valleys
        if lower <= valley.center_sample <= hard_upper
    ]
    before_soft = [
        valley for valley in eligible if valley.center_sample <= soft_upper
    ]
    pool = before_soft or eligible
    if not pool:
        return None

    def score(valley: ProbabilityValley) -> tuple[float, int]:
        distance = abs(valley.center_sample - target) / float(sample_rate)
        search_seconds = max(
            1e-6,
            (soft_upper - lower) / float(sample_rate),
        )
        distance_score = distance / search_seconds
        width_seconds = valley.width_samples / float(sample_rate)
        width_bonus = min(1.0, width_seconds)
        combined = (
            0.65 * distance_score
            + 0.20 * valley.mean_probability
            + 0.10 * valley.p95_probability
            - 0.05 * width_bonus
        )
        return combined, valley.center_sample

    return min(pool, key=score)


def plan_windows(
    *,
    speech_regions: list[SpeechRegion],
    probabilities: SpeechProbabilityTrack,
    total_samples: int,
    sample_rate: int,
    target_seconds: float = WINDOW_TARGET_SECONDS,
    soft_max_seconds: float = WINDOW_SOFT_MAX_SECONDS,
    hard_max_seconds: float = WINDOW_HARD_MAX_SECONDS,
    context_seconds: float = WINDOW_CONTEXT_SECONDS,
    min_core_seconds: float = WINDOW_MIN_CORE_SECONDS,
    valley_threshold: float = WINDOW_VALLEY_THRESHOLD,
    valley_min_seconds: float = WINDOW_VALLEY_MIN_SECONDS,
    valley_smooth_frames: int = WINDOW_VALLEY_SMOOTH_FRAMES,
) -> list[InferenceWindow]:
    if total_samples < 0 or sample_rate <= 0:
        raise ValueError("Invalid audio metadata")
    if total_samples == 0:
        return []
    if not 0 < min_core_seconds <= target_seconds <= soft_max_seconds <= hard_max_seconds:
        raise ValueError("Invalid inference-window durations")
    if context_seconds < 0:
        raise ValueError("Window context cannot be negative")
    if probabilities.total_samples != total_samples:
        raise ValueError("Speech probabilities must cover the complete audio")

    ordered = sorted(speech_regions, key=lambda item: (item.start_sample, item.end_sample))
    if any(
        region.start_sample < 0 or region.end_sample > total_samples
        for region in ordered
    ):
        raise ValueError("Speech regions must be inside the audio timeline")
    target = max(1, int(round(target_seconds * sample_rate)))
    soft_max = max(target, int(round(soft_max_seconds * sample_rate)))
    hard_max = max(soft_max, int(round(hard_max_seconds * sample_rate)))
    minimum = max(1, int(round(min_core_seconds * sample_rate)))
    context = max(0, int(round(context_seconds * sample_rate)))
    valleys = probability_valleys(
        probabilities,
        sample_rate=sample_rate,
        threshold=valley_threshold,
        min_seconds=valley_min_seconds,
        smooth_frames=valley_smooth_frames,
    )

    cores: list[_CorePlan] = []
    start = 0
    while start < total_samples:
        remaining = total_samples - start
        if remaining <= target:
            cores.append(_CorePlan(start, total_samples, "end_of_audio"))
            break

        lower = start + minimum
        soft_upper = min(start + soft_max, total_samples - minimum)
        hard_upper = min(start + hard_max, total_samples - minimum)
        boundary: int | None = None
        reason = "end_of_audio"
        selected: ProbabilityValley | None = None
        if lower <= hard_upper:
            selected = _select_probability_valley(
                valleys,
                lower=lower,
                soft_upper=soft_upper,
                hard_upper=hard_upper,
                target=start + target,
                sample_rate=sample_rate,
            )
            if selected is not None:
                boundary = selected.center_sample
                reason = "probability_valley"

        if boundary is None:
            if remaining > hard_max:
                boundary = hard_upper
                reason = "hard_max"
            else:
                boundary = total_samples
                reason = "end_of_audio"
        cores.append(_CorePlan(start, boundary, reason, selected))
        start = boundary

    if (
        len(cores) > 1
        and cores[-1].end_sample - cores[-1].start_sample < minimum
    ):
        previous = cores[-2]
        tail = cores[-1]
        cores[-2:] = [
            _CorePlan(
                previous.start_sample,
                tail.end_sample,
                tail.reason,
                tail.valley,
            )
        ]

    windows: list[InferenceWindow] = []
    for index, core in enumerate(cores):
        valley = core.valley
        windows.append(
            InferenceWindow(
                index=index,
                core_start_sample=core.start_sample,
                core_end_sample=core.end_sample,
                input_start_sample=max(0, core.start_sample - context),
                input_end_sample=min(total_samples, core.end_sample + context),
                speech_samples=_speech_samples(
                    ordered,
                    core.start_sample,
                    core.end_sample,
                ),
                boundary_reason=core.reason,
                boundary_probability=(
                    None if valley is None else valley.center_probability
                ),
                valley_width_samples=(
                    0 if valley is None else valley.width_samples
                ),
                valley_mean_probability=(
                    None if valley is None else valley.mean_probability
                ),
                valley_p95_probability=(
                    None if valley is None else valley.p95_probability
                ),
            )
        )

    if windows[0].core_start_sample != 0 or windows[-1].core_end_sample != total_samples:
        raise AssertionError("Core windows do not cover the complete audio")
    if any(
        previous.core_end_sample != current.core_start_sample
        for previous, current in zip(windows, windows[1:])
    ):
        raise AssertionError("Core window continuity failed")
    if sum(window.sample_count for window in windows) != total_samples:
        raise AssertionError("Core window sample accounting failed")
    return windows
