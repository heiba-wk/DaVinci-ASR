from __future__ import annotations

from dataclasses import dataclass

from runtime.audio.vad import SpeechRegion
from runtime.constants import (
    WINDOW_CONTEXT_SECONDS,
    WINDOW_HARD_MAX_SECONDS,
    WINDOW_MIN_CORE_SECONDS,
    WINDOW_SOFT_MAX_SECONDS,
    WINDOW_TARGET_SECONDS,
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
        }


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


def _silence_midpoints(
    regions: list[SpeechRegion],
    *,
    lower: int,
    upper: int,
) -> list[int]:
    candidates: list[int] = []
    for previous, current in zip(regions, regions[1:]):
        if current.start_sample <= previous.end_sample:
            continue
        midpoint = (previous.end_sample + current.start_sample) // 2
        if lower <= midpoint <= upper:
            candidates.append(midpoint)
    return candidates


def plan_windows(
    *,
    speech_regions: list[SpeechRegion],
    total_samples: int,
    sample_rate: int,
    target_seconds: float = WINDOW_TARGET_SECONDS,
    soft_max_seconds: float = WINDOW_SOFT_MAX_SECONDS,
    hard_max_seconds: float = WINDOW_HARD_MAX_SECONDS,
    context_seconds: float = WINDOW_CONTEXT_SECONDS,
    min_core_seconds: float = WINDOW_MIN_CORE_SECONDS,
) -> list[InferenceWindow]:
    if total_samples < 0 or sample_rate <= 0:
        raise ValueError("Invalid audio metadata")
    if total_samples == 0:
        return []
    if not 0 < min_core_seconds <= target_seconds <= soft_max_seconds <= hard_max_seconds:
        raise ValueError("Invalid inference-window durations")
    if context_seconds < 0:
        raise ValueError("Window context cannot be negative")

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

    cores: list[tuple[int, int, str]] = []
    start = 0
    while start < total_samples:
        remaining = total_samples - start
        if remaining <= target:
            cores.append((start, total_samples, "end_of_audio"))
            break

        lower = start + minimum
        upper = min(start + hard_max, total_samples - minimum)
        boundary: int | None = None
        reason = "end_of_audio"
        if lower <= upper:
            candidates = _silence_midpoints(ordered, lower=lower, upper=upper)
            if candidates:
                target_sample = start + target
                before_soft = [item for item in candidates if item <= start + soft_max]
                pool = before_soft or candidates
                boundary = min(pool, key=lambda item: (abs(item - target_sample), item))
                reason = "natural_silence"

        if boundary is None:
            if remaining > hard_max:
                boundary = start + hard_max
                reason = "hard_max"
            else:
                boundary = total_samples
                reason = "end_of_audio"
        cores.append((start, boundary, reason))
        start = boundary

    if len(cores) > 1 and cores[-1][1] - cores[-1][0] < minimum:
        previous_start, _, _ = cores[-2]
        _, tail_end, tail_reason = cores[-1]
        cores[-2:] = [(previous_start, tail_end, tail_reason)]

    windows: list[InferenceWindow] = []
    for index, (core_start, core_end, reason) in enumerate(cores):
        windows.append(
            InferenceWindow(
                index=index,
                core_start_sample=core_start,
                core_end_sample=core_end,
                input_start_sample=max(0, core_start - context),
                input_end_sample=min(total_samples, core_end + context),
                speech_samples=_speech_samples(ordered, core_start, core_end),
                boundary_reason=reason,
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
